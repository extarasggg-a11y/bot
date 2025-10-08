import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from pydub import AudioSegment
from groq import Groq
import requests
import asyncio
from edge_tts import Communicate

load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
telegram_token = os.getenv("TELEGRAM_TOKEN")
groq_client = Groq(api_key=groq_api_key)

def transcribe_whisper_groq(audio_path, fallback_models=["whisper-large-v3", "whisper-large-v3-turbo"]):
    for model in fallback_models:
        files = {'file': open(audio_path, 'rb')}
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {groq_api_key}"}
        data = {
            "model": model,
            "language": "ru",
            "response_format": "text"
        }
        try:
            response = requests.post(url, files=files, headers=headers, data=data, timeout=60)
            if response.status_code == 200 and response.text.strip():
                return response.text.strip()
        except Exception as e:
            print(f"Groq Whisper {model} failed:", e)
        finally:
            files['file'].close()
    return ""

async def synthesize_voice(text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural"):
    communicate = Communicate(text, voice=voice)
    await communicate.save(filename)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = await update.message.voice.get_file()
    voice.download("voice.ogg")
    sound = AudioSegment.from_ogg("voice.ogg")
    sound.export("voice.mp3", format="mp3")
    audio_path = "voice.mp3"

    prompt = transcribe_whisper_groq(audio_path)
    if not prompt:
        await update.message.reply_text("Не удалось распознать голосовое сообщение.")
        return

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.12,
        max_tokens=512
    )
    answer_text = response['choices'][0]['message']['content']

    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
    await update.message.reply_text(answer_text)
    with open("answer.mp3", "rb") as f:
        await update.message.reply_voice(voice=f)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": user_text}],
        temperature=0.12,
        max_tokens=512
    )
    answer_text = response['choices'][0]['message']['content']
    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
    await update.message.reply_text(answer_text)
    with open("answer.mp3", "rb") as f:
        await update.message.reply_voice(voice=f)

if __name__ == "__main__":
    app = ApplicationBuilder().token(telegram_token).build()
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT, text_handler))
    app.run_polling()
