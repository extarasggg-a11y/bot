import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
import ffmpeg
from groq import Groq
import requests
from edge_tts import Communicate
import shutil

# Проверка наличия ffmpeg
if shutil.which("ffmpeg") is None:
    raise RuntimeError("ffmpeg не установлен! Проверьте Dockerfile или логи.")

load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
telegram_token = os.getenv("TELEGRAM_TOKEN")
groq_client = Groq(api_key=groq_api_key)

# Глобальная история чата (user_id -> list of (prompt, answer))
chat_history = {}

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

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("▶️ Старт", callback_data="start"),
            InlineKeyboardButton("📝 Исправить транскрипцию", callback_data="fix_transcript"),
        ],
        [
            InlineKeyboardButton("🔊 Озвучить исправленное", callback_data="voice_fixed"),
        ],
        [
            InlineKeyboardButton("📜 История чата", callback_data="history"),
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "👋 *Добро пожаловать!*\n\n"
        "Отправьте голосовое сообщение — бот сразу покажет транскрипцию, ответит текстом и голосом.\n\n"
        "Выберите действие из меню:"
    )
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "start":
        await query.message.reply_text(
            "▶️ Вы можете отправить новое голосовое сообщение!"
        )
    elif query.data == "history":
        history = chat_history.get(user_id, [])
        if not history:
            await query.message.reply_text("📜 История пуста!")
        else:
            text = "\n\n".join([f"👤 *Вы:* {h[0]}\n🤖 *Бот:* {h[1]}" for h in history])
            await query.message.reply_text(f"Ваша история чата:\n\n{text}", parse_mode="Markdown")
    elif query.data == "fix_transcript":
        context.user_data["fix_mode"] = True
        await query.message.reply_text("📝 Введите исправленный текст транскрипции:")
    elif query.data == "voice_fixed":
        fixed = context.user_data.get("fixed_transcript")
        if not fixed:
            await query.message.reply_text("🔖 Нет исправленной транскрипции. Введите её через '📝 Исправить транскрипцию'.")
        else:
            await synthesize_voice(fixed, filename="fixed.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
            with open("fixed.mp3", "rb") as f:
                await query.message.reply_voice(voice=f)
            await query.message.reply_text("🔊 Готово, исправленный текст озвучен!")
    elif query.data == "help":
        help_text = (
            "❓ *Что умеет бот:*\n\n"
            "- Получать голосовое и выводить транскрипцию\n"
            "- Отвечать GPT прямо в чате\n"
            "- Озвучивать ответы и ваши исправленные транскрипции\n"
            "- Показывать всю вашу историю общения\n"
            "- Для исправления транскрипции воспользуйтесь соответствующей кнопкой!"
        )
        await query.message.reply_text(help_text, parse_mode="Markdown")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "start":
        await query.message.reply_text("Привет! Отправьте голосовое, чтобы получить транскрипцию и ответ.")
    elif query.data == "history":
        history = chat_history.get(user_id, [])
        if not history:
            await query.message.reply_text("История пуста!")
        else:
            text = "\n---\n".join([f"Вы: {h[0]}\nБот: {h[1]}" for h in history])
            await query.message.reply_text(f"Ваша история чата:\n{text}")
    elif query.data == "fix_transcript":
        context.user_data["fix_mode"] = True
        await query.message.reply_text("Введите исправленную транскрипцию текстом:")
    elif query.data == "voice_fixed":
        fixed = context.user_data.get("fixed_transcript")
        if not fixed:
            await query.message.reply_text("Нет исправленной транскрипции. Введите её через 'Исправить транскрипцию'.")
        else:
            await synthesize_voice(fixed, filename="fixed.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
            with open("fixed.mp3", "rb") as f:
                await query.message.reply_voice(voice=f)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    voice = await update.message.voice.get_file()
    await voice.download_to_drive("voice.ogg")
    convert_ogg_to_mp3("voice.ogg", "voice.mp3")
    audio_path = "voice.mp3"

    prompt = transcribe_whisper_groq(audio_path)
    if not prompt:
        await update.message.reply_text("Не удалось распознать голосовое сообщение.")
        return

    await update.message.reply_text(f"Транскрипция:\n{prompt}")

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.12,
        max_tokens=512
    )
    answer_text = response.choices[0].message.content

    await update.message.reply_text(answer_text)
    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
    with open("answer.mp3", "rb") as f:
        await update.message.reply_voice(voice=f)

    chat_history.setdefault(user_id, []).append((prompt, answer_text))
    context.user_data["last_transcript"] = prompt

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if context.user_data.get("fix_mode"):
        context.user_data["fixed_transcript"] = text
        context.user_data["fix_mode"] = False
        await update.message.reply_text(f"Исправленная транскрипция сохранена: {text}")
        return

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": text}],
        temperature=0.12,
        max_tokens=512
    )
    answer_text = response.choices[0].message.content
    await update.message.reply_text(answer_text)
    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
    with open("answer.mp3", "rb") as f:
        await update.message.reply_voice(voice=f)

    chat_history.setdefault(user_id, []).append((text, answer_text))

if __name__ == "__main__":
    app = ApplicationBuilder().token(telegram_token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling()
