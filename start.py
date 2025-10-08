import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import ffmpeg
from groq import Groq
import requests
from edge_tts import Communicate

load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
telegram_token = os.getenv("TELEGRAM_TOKEN")
groq_client = Groq(api_key=groq_api_key)

def convert_ogg_to_mp3(in_file, out_file):
    (
        ffmpeg
        .input(in_file)
        .output(out_file, format='mp3', acodec='libmp3lame')
        .run(overwrite_output=True, quiet=True)
    )

def transcribe_whisper_groq(audio_path, fallback_models=["whisper-large-v3", "whisper-large-v3-turbo"]):
    for model in fallback_models:
        with open(audio_path, 'rb') as f:
            files = {'file': f}
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
    return ""

async def synthesize_voice(text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural"):
    communicate = Communicate(text, voice=voice)
    await communicate.save(filename)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Скачиваем голосовое сообщение правильно (async)
    voice = await update.message.voice.get_file()
    await voice.download_to_drive("voice.ogg")
    convert_ogg_to_mp3("voice.ogg", "voice.mp3")
    audio_path = "voice.mp3"

    # Транскрипция голоса
    prompt = transcribe_whisper_groq(audio_path)
    if not prompt:
        await update.message.reply_text("Не удалось распознать голосовое сообщение.")
        return

    # Показываем транскрипцию
    await update.message.reply_text(f"Транскрипция вашего сообщения:\n{prompt}")

    # GPT-ответ
    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.12,
        max_tokens=512
    )
    answer_text = response.choices[0].message.content

    # Отправляем текстовый ответ
    await update.message.reply_text(answer_text)
    # Озвучка и отправка голосом
    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
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
    answer_text = response.choices[0].message.content
    await update.message.reply_text(answer_text)
    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
    with open("answer.mp3", "rb") as f:
        await update.message.reply_voice(voice=f)

if __name__ == "__main__":
    app = ApplicationBuilder().token(telegram_token).build()
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT, text_handler))
    app.run_polling()
