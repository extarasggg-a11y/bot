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

if shutil.which("ffmpeg") is None:
    raise RuntimeError("ffmpeg не установлен! Проверьте Dockerfile или логи.")

load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
telegram_token = os.getenv("TELEGRAM_TOKEN")
groq_client = Groq(api_key=groq_api_key)

# История чата: user_id -> list of dicts: {"origin": оригинал, "fixed": исправленный (или None), "answer": ответ}
chat_history = {}

def convert_ogg_to_mp3(in_file, out_file):
    (
        ffmpeg
        .input(in_file)
        .output(out_file, format='mp3', acodec='libmp3lame')
        .run(overwrite_output=True, quiet=True)
    )

def transcribe_whisper_groq(audio_path, fallback_models=None):
    if fallback_models is None:
        fallback_models = [
            "whisper-large-v3",
            "whisper-large-v3-turbo",
            "whisper-medium",
            "whisper-medium-turbo",
        ]
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
                text = response.text.strip()
                text = text.replace('\n', ' ').replace('\r', '').replace('\t', ' ').strip()
                if response.status_code == 200 and text and len(text) > 4:
                    return text
            except Exception as e:
                print(f"Groq Whisper {model} failed:", e)
    return ""

async def synthesize_voice(text, filename="voice.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural"):
    communicate = Communicate(text, voice=voice)
    await communicate.save(filename)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("▶️ Старт", callback_data="start"),
            InlineKeyboardButton("📝 Исправить", callback_data="fix_transcript"),
        ],
        [
            InlineKeyboardButton("🔊 Озвучить исправленное", callback_data="voice_fixed"),
        ],
        [
            InlineKeyboardButton("🗂️ История чата", callback_data="history"),
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "👋 Добро пожаловать!\n\n"
        "🎤 Присылайте голосовое — получите транскрипцию, ответ и озвучку.\n\n"
        "Меню ниже ⬇️"
    )
    await update.message.reply_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "start":
        await query.message.reply_text("▶️ Готов к новым сообщениям! Просто пришлите голосовое 👇")
    elif query.data == "history":
        history = chat_history.get(user_id, [])
        if not history:
            await query.message.reply_text("🗂️ История пуста! Начните диалог — пришлите голос или текст.")
        else:
            blocks = []
            for h in history:
                part = f"🎤 {h['origin']}"
                if h.get("fixed"):
                    part += f"\n✏️ Исправлено: {h['fixed']}"
                part += f"\n🤖 Ответ: {h['answer']}"
                blocks.append(part)
            text = "\n\n━━━━━━━━━━\n\n".join(blocks)
            await query.message.reply_text(f"🗂️ Ваша история чата:\n\n{text}")
    elif query.data == "fix_transcript":
        context.user_data["fix_mode"] = True
        await query.message.reply_text("📝 Введите исправленный текст для транскрипции:")
    elif query.data == "voice_fixed":
        fixed = context.user_data.get("fixed_transcript")
        if not fixed:
            await query.message.reply_text("❗ Нет исправленной транскрипции. Сначала введите её через кнопку '📝 Исправить'.")
        else:
            await synthesize_voice(fixed, filename="fixed.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
            with open("fixed.mp3", "rb") as f:
                await query.message.reply_voice(voice=f)
            await query.message.reply_text("🔊 Ваша исправленная транскрипция озвучена!")
    elif query.data == "help":
        help_text = (
            "❓ Что умеет бот:\n"
            "• Голос → транскрипция + GPT-ответ + озвучка\n"
            "• Исправление и озвучка транскрипции\n"
            "• Вся история ваших сообщений и ответов\n"
            "• Красивые иконки для вашего удобства\n\n"
            "Пришлите голосовое или текст — получите сразу полный ответ!"
        )
        await query.message.reply_text(help_text)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    voice = await update.message.voice.get_file()
    await voice.download_to_drive("voice.ogg")
    convert_ogg_to_mp3("voice.ogg", "voice.mp3")
    audio_path = "voice.mp3"

    # Транскрипция голосового
    prompt = transcribe_whisper_groq(audio_path)
    if not prompt or len(prompt) < 4:
        await update.message.reply_text(
            "❗ Не удалось хорошо распознать ваш голос. Воспользуйтесь кнопкой '📝 Исправить', чтобы вручную ввести текст!"
        )
        context.user_data["fix_mode"] = True
        return

    await update.message.reply_text(f"🎤 Транскрипция:\n{prompt}")
    context.user_data["last_transcript"] = prompt
    context.user_data["fixed_transcript"] = None  # новый голос — сбрасываем исправленное

    # GPT — только русский, без спецформатирования
    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": "Ты дружелюбный помощник, всегда отвечай обычным русским текстом, без Markdown, без ##, без **. Добавляй уместные эмодзи для красоты и структуры (например: 🤖, ✏️, 📦, 📝, 🎤, 🔊, 💡, ⛑️, 🗂️, 👍)"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.12,
        max_tokens=512
    )
    answer_text = response.choices[0].message.content

    await update.message.reply_text(f"🤖 Ответ:\n{answer_text}")
    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
    with open("answer.mp3", "rb") as f:
        await update.message.reply_voice(voice=f)

    chat_history.setdefault(user_id, []).append({"origin": prompt, "fixed": None, "answer": answer_text})

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if context.user_data.get("fix_mode"):
        context.user_data["fixed_transcript"] = text
        context.user_data["fix_mode"] = False
        await update.message.reply_text(f"✏️ Исправленная транскрипция сохранена: {text}")

        # GPT — только русский, без спецформата; эмодзи по ситуации
        response = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": "Ты дружелюбный помощник, всегда отвечай обычным русским текстом, без Markdown, без ##, без **. Добавляй уместные эмодзи для красоты и структуры (например: 🤖, ✏️, 📦, 📝, 🎤, 🔊, 💡, ⛑️, 🗂️, 👍)"},
                {"role": "user", "content": text}
            ],
            temperature=0.12,
            max_tokens=512
        )
        answer_text = response.choices[0].message.content
        await update.message.reply_text(f"🤖 Ответ:\n{answer_text}")
        await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
        with open("answer.mp3", "rb") as f:
            await update.message.reply_voice(voice=f)

        chat_history.setdefault(user_id, []).append({
            "origin": context.user_data.get("last_transcript", ""),
            "fixed": text,
            "answer": answer_text
        })
        return

    # Обычный текстовый запрос
    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": "Ты дружелюбный помощник, всегда отвечай обычным русским текстом, без Markdown, без ##, без **. Добавляй уместные эмодзи для красоты и структуры (например: 🤖, ✏️, 📦, 📝, 🎤, 🔊, 💡, ⛑️, 🗂️, 👍)"},
            {"role": "user", "content": text}
        ],
        temperature=0.12,
        max_tokens=512
    )
    answer_text = response.choices[0].message.content
    await update.message.reply_text(f"🤖 Ответ:\n{answer_text}")
    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
    with open("answer.mp3", "rb") as f:
        await update.message.reply_voice(voice=f)

    chat_history.setdefault(user_id, []).append({"origin": text, "fixed": None, "answer": answer_text})

if __name__ == "__main__":
    app = ApplicationBuilder().token(telegram_token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling()
