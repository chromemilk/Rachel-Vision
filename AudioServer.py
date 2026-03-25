import socket
import pvporcupine
import struct
import os
import time
import wave
import requests
import base64
import pyttsx3
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

PICOVOICE_API_KEY = os.getenv("PICOVOICE_API")
MODEL_PATH = "Rachel_en_windows_v4_0_0.ppn" 

GROQ_API_KEY = os.getenv("GROQ_API")
# Change this to the IP of the ESP32 camera module
ESP32_CAM_URL = "http://172.20.10.4/capture" 

UDP_PORT = 8002
# Change this to modfiy the amount of time the progam listens for commands 
COMMAND_RECORD_TIME = 4.0 
TEMP_WAV = "temp_command.wav"

engine = pyttsx3.init()
groq_client = Groq(api_key=GROQ_API_KEY)

handle = pvporcupine.create(
    access_key=PICOVOICE_API_KEY,
    keyword_paths=[MODEL_PATH]
)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", UDP_PORT))
sock.settimeout(1.0) 

def speak(text):
    print(f"LLM RESPONSE GOOD: {text}")
    engine.say(text)
    engine.runAndWait()

def drain_udp_buffer():
    sock.setblocking(False)
    try:
        while True:
            sock.recv(2048)
    except BlockingIOError:
        pass
    sock.setblocking(True)
    sock.settimeout(1.0)

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
                print("Processing...")
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
                messages = []

                if "identify" in user_prompt or "solve" in user_prompt:
                    print("Keyword detected. Fetching camera snapshot...")
                    try:
                        cam_response = requests.get(ESP32_CAM_URL, timeout=5)
                        if cam_response.status_code == 200:
                            base64_image = base64.b64encode(cam_response.content).decode('utf-8')
                            
                            messages = [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": f"Keep your answer concise. {command_text}"},
                                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                                    ]
                                }
                            ]
                            # Corrected Vision Model
                            model_to_use = "meta-llama/llama-4-scout-17b-16e-instruct" 
                        else:
                            speak("I couldn't reach the camera.")
                            continue
                    except Exception as e:
                        print(f"Camera Error: {e}")
                        speak("Camera connection failed.")
                        continue
                else:
                    messages = [{"role": "user", "content": f"Answer concisely: {command_text}"}]
                    # Corrected Text Model
                    model_to_use = "openai/gpt-oss-120b"

                try:
                    print(f"Sending Request ({model_to_use})...")
                    chat_completion = groq_client.chat.completions.create(
                        messages=messages,
                        model=model_to_use,
                        max_tokens=150
                    )
                    ai_response = chat_completion.choices[0].message.content
                    speak(ai_response)
                except Exception as e:
                    print(f"Groq API Error: {e}")
                    speak("I had trouble thinking of a response.")

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