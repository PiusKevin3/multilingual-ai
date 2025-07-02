import os
import io
import wave
import base64
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from google import genai
from google.genai import types
from random import randint

# Load environment variables
load_dotenv()

app = FastAPI()

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Map language to Gemini voice
VOICE_MAPPING = {
    "en": "achernar",
    "es": "achird",
    "fr": "algenib",
    "de": "algieba",
    "ja": "alnilam",
    "zh": "aoede",
    "hi": "autonoe",
    "ar": "callirrhoe",
    "ru": "charon",
    "pt": "despina"
}

# Configure Gemini Client
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("❌ GEMINI_API_KEY not set in environment.")
client = genai.Client(api_key=API_KEY)

# WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active_connections = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket

    def disconnect(self, session_id: str):
        self.active_connections.pop(session_id, None)

    async def send_audio(self, data: bytes, session_id: str):
        socket = self.active_connections.get(session_id)
        if socket:
            await socket.send_bytes(data)

manager = ConnectionManager()

# Convert PCM to WAV
def pcm_to_wav(pcm_bytes: bytes, sample_rate=16000) -> bytes:
    with io.BytesIO() as wav_buffer:
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        return wav_buffer.getvalue()

# Generate TTS audio (using Gemini Client API)
def generate_tts(prompt: str, voice: str) -> bytes:
    print(f"🗣 Prompt for TTS: {prompt}")

    def call_model():
        try:
            return client.models.generate_content(
                model="models/gemini-2.5-flash-preview-tts",
                contents=[{"parts": [{"text": prompt}]}],
                config=types.GenerateContentConfig(
                    response_modalities=["audio"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice
                            )
                        )
                    )
                )
            )
        except Exception as e:
            print(f"❌ Error during API call: {e}")
            return None # Return None in case of API call failure

    try:
        response = call_model()

        if response is None:  # Check if the API call failed
            print("❌ API call failed, cannot generate TTS.")
            return b""

        print("TTS raw response:", response)

        candidate = response.candidates[0]

        if candidate.finish_reason != types.FinishReason.STOP: #Check the finish_reason

          print(f"❌ TTS generation incomplete, finish_reason = {candidate.finish_reason}.  Returning empty audio.")
          return b""


        if candidate.content is None or candidate.content.parts is None or not candidate.content.parts:
            print("❌ Incomplete TTS response, missing content or parts. Returning empty audio.")
            return b""


        part = candidate.content.parts[0]
        raw_pcm = part.inline_data.data

        # ✅ Convert raw PCM to WAV for browser playback
        wav_data = pcm_to_wav(raw_pcm, sample_rate=24000)

        return wav_data

    except Exception as e:
        print(f"❌ TTS processing error: {e}")
        return b""



# WebSocket endpoint
@app.websocket("/ws/{session_id}/{language}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, language: str):
    await manager.connect(websocket, session_id)
    voice = VOICE_MAPPING.get(language, "achernar")
    audio_chunks = []

    try:
        while True:
            audio_data = await websocket.receive_bytes()

            if len(audio_data) == 0:
                if not audio_chunks:
                    print("⚠️ No audio chunks yet.")
                    continue

                full_audio = b"".join(audio_chunks)
                print(f"🎧 Received {len(full_audio)} bytes")

                # Convert PCM to WAV
                wav_data = pcm_to_wav(full_audio)

                # Transcribe using Gemini
                stt_response = client.models.generate_content(
                    model="models/gemini-1.5-flash",
                    contents=[
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "inline_data": {
                                        "mime_type": "audio/wav",
                                        "data": base64.b64encode(wav_data).decode("utf-8")
                                    }
                                }
                            ]
                        }
                    ]
                )

                transcript = stt_response.text
                print(f"📝 Transcript: {transcript}")

                # Prepare TTS response
                current_time = datetime.now().strftime("%H:%M")
                reply = f"The current time is {current_time}. Request ID: {randint(1000,9999)}"
                print(f"🔊 Generating TTS: {reply}")
                tts_audio = generate_tts(reply, voice)
                print(f"📤 Sending audio response: {len(tts_audio)} bytes")

                await manager.send_audio(tts_audio, session_id)
                audio_chunks = []
            else:
                audio_chunks.append(audio_data)

    except WebSocketDisconnect:
        print("🔌 WebSocket disconnected")
        manager.disconnect(session_id)
    except Exception as e:
        print(f"❌ Error: {e}")
        manager.disconnect(session_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
