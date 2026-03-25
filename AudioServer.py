import re
import socket
import pvporcupine
import struct
import os
import time
import wave
import requests
import base64
import pyttsx3
import json
import edge_tts
import asyncio
import pygame
import tempfile
from groq import Groq
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

PICOVOICE_API_KEY = os.getenv("PICOVOICE_API")
MODEL_PATH = "Rachel_en_windows_v4_0_0.ppn" 

GROQ_API_KEY = os.getenv("GROQ_API")
TAVILY_API_KEY = os.getenv("TAVILY_API")

# Change this to the IP of the ESP32 camera module
ESP32_CAM_URL = "http://172.20.10.4/capture" 

pygame.mixer.init()

UDP_PORT = 8002
COMMAND_RECORD_TIME = 4.0 
TEMP_WAV = "temp_command.wav"

MAX_HISTORY_LENGTH = 10 
system_prompt = {
    "role": "system", 
    "content": "You are Rachel, a highly capable AI voice assistant. Keep your answers concise, natural, and easy to understand when spoken aloud."
}
conversation_history = [system_prompt]

groq_client = Groq(api_key=GROQ_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

handle = pvporcupine.create(
    access_key=PICOVOICE_API_KEY,
    keyword_paths=[MODEL_PATH]
)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", UDP_PORT))
sock.settimeout(1.0) 

def speak(text):
    print(f"Rachel: {text}")
    
    clean_text = text.replace("’", "'").replace("‘", "'")
    clean_text = clean_text.replace("—", "-").replace("–", "-")
    clean_text = clean_text.replace('"', "")
    clean_text = re.sub(r'[*_~#`]', '', clean_text)
    
    async def _generate_and_play():
        # You can also try "en-US-JennyNeural" or "en-GB-SoniaNeural" (British)
        communicate = edge_tts.Communicate(clean_text, "en-US-AriaNeural")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
            temp_path = fp.name
            
        await communicate.save(temp_path)
        
        pygame.mixer.music.load(temp_path)
        pygame.mixer.music.play()
        
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
            
        pygame.mixer.music.unload()
        os.remove(temp_path)

    try:
        asyncio.run(_generate_and_play())
    except Exception as e:
        print(f"Edge TTS Error: {e}")

def drain_udp_buffer():
    sock.setblocking(False)
    try:
        while True:
            sock.recv(2048)
    except BlockingIOError:
        pass
    sock.setblocking(True)
    sock.settimeout(1.0)

def route_intent(prompt_text):
    routing_prompt = f"""
    Analyze the following user command and determine two things:
    1. The appropriate model to handle it:
       - "vision" if the user wants you to look at, identify, or read something in the real world.
       - "reasoning" for complex logic, math, coding, or deep analytical thinking.
       - "light" for general conversation, follow-up questions, simple questions, or basic tasks.
    2. Whether it requires searching the web for up-to-date, real-time, or factual information (true/false).
    
    Respond ONLY with a valid JSON object in this exact format:
    {{"model_type": "light" | "vision" | "reasoning", "use_web_search": true | false}}
    
    User command: "{prompt_text}"
    """
    
    try:
        response = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": routing_prompt}],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=50
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Routing Error: {e}")
        return {"model_type": "light", "use_web_search": False}

def fetch_web_context(query):
    try:
        response = tavily_client.search(query, search_depth="basic", max_results=3)
        context = "\n".join([result['content'] for result in response.get('results', [])])
        return f"\n\n--- Web Search Context ---\n{context}\n--------------------------"
    except Exception as e:
        print(f"Tavily Search Error: {e}")
        return ""

print(f"Rachel is online. Listening on port {UDP_PORT}...")

is_recording_command = False
command_frames = []
record_start_time = 0
audio_buffer = b""

try:
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue

        if is_recording_command:
            command_frames.append(data)
            
            if time.time() - record_start_time >= COMMAND_RECORD_TIME:
                print("Processing Audio...")
                is_recording_command = False
                
                with wave.open(TEMP_WAV, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(b''.join(command_frames))
                
                try:
                    with open(TEMP_WAV, "rb") as file:
                        transcription = groq_client.audio.transcriptions.create(
                          file=(TEMP_WAV, file.read()),
                          model="whisper-large-v3-turbo",
                        )
                    command_text = transcription.text.strip()
                    print(f"You said: '{command_text}'")
                except Exception as e:
                    print(f"STT Error: {e}")
                    drain_udp_buffer()
                    continue

                if len(command_text) < 2:
                    drain_udp_buffer()
                    continue

                user_prompt = command_text.lower()
                
                print("Routing intent...")
                routing_decision = route_intent(user_prompt)
                model_type = routing_decision.get("model_type", "light")
                use_web = routing_decision.get("use_web_search", False)
                print(f"Decision -> Model: {model_type.upper()} | Web Search: {use_web}")

                web_context = ""
                if use_web:
                    print("Fetching web data...")
                    web_context = fetch_web_context(user_prompt)

                final_prompt = f"{command_text}\n{web_context}" if web_context else command_text
                current_message = None

                if model_type == "vision":
                    print("Fetching camera snapshot...")
                    try:
                        cam_response = requests.get(ESP32_CAM_URL, timeout=5)
                        if cam_response.status_code == 200:
                            base64_image = base64.b64encode(cam_response.content).decode('utf-8')
                            
                            print("Extracting scene details with Vision model...")
                            vision_messages = [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": f"Describe this image in detail. Pay special attention to anything relevant to this user query: '{command_text}'"},
                                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                                    ]
                                }
                            ]
                            
                            vision_completion = groq_client.chat.completions.create(
                                messages=vision_messages,
                                model="meta-llama/llama-4-scout-17b-16e-instruct",
                                max_tokens=300
                            )
                            image_description = vision_completion.choices[0].message.content
                            print(f"Vision Extracted: {image_description}")

                            combined_prompt = f"User Command: {command_text}\n\nCamera Image Description: {image_description}\n\n{web_context}"
                            current_message = {"role": "user", "content": combined_prompt}
                            
                            model_to_use = "openai/gpt-oss-120b" 

                        else:
                            speak("I couldn't reach the camera.")
                            drain_udp_buffer()
                            continue
                    except Exception as e:
                        print(f"Camera/Vision Error: {e}")
                        speak("Camera connection or vision processing failed.")
                        drain_udp_buffer()
                        continue
                        
                elif model_type == "reasoning":
                    current_message = {"role": "user", "content": final_prompt}
                    model_to_use = "openai/gpt-oss-120b" 
                    
                else: # light
                    current_message = {"role": "user", "content": final_prompt}
                    model_to_use = "moonshotai/kimi-k2-instruct-0905" 

                if current_message:
                    conversation_history.append(current_message)

                if len(conversation_history) > MAX_HISTORY_LENGTH + 1:
                    conversation_history = [conversation_history[0]] + conversation_history[-MAX_HISTORY_LENGTH:]

                try:
                    print(f"Sending Request to {model_to_use}...")
                    
                    chat_completion = groq_client.chat.completions.create(
                        messages=conversation_history,
                        model=model_to_use,
                        max_tokens=150
                    )
                    ai_response = chat_completion.choices[0].message.content
                    speak(ai_response)
                    
                    conversation_history.append({"role": "assistant", "content": ai_response})
                    
                except Exception as e:
                    print(f"Groq API Error: {e}")
                    speak("I had trouble thinking of a response.")
                    if len(conversation_history) > 1:
                         conversation_history.pop()

                print("\nRachel is listening for wake word...")
                audio_buffer = b""
                command_frames = []
                drain_udp_buffer() 

        else:
            audio_buffer += data
            frame_byte_size = handle.frame_length * 2 

            while len(audio_buffer) >= frame_byte_size:
                frame = audio_buffer[:frame_byte_size]
                audio_buffer = audio_buffer[frame_byte_size:]
                pcm = struct.unpack_from("h" * handle.frame_length, frame)

                if handle.process(pcm) >= 0:
                    print("\nWake Word Detected -> Listening for command...")
                    speak("Yes?")
                    is_recording_command = True
                    record_start_time = time.time()
                    command_frames = []
                    audio_buffer = b""
                    break 

except KeyboardInterrupt:
    print("\nStopping Rachel...")
finally:
    sock.close()
    handle.delete()