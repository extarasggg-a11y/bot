import os
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup
)
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
import re
import time

if shutil.which("ffmpeg") is None:
    raise RuntimeError("ffmpeg не установлен! Проверьте Dockerfile или логи.")

load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
telegram_token = os.getenv("TELEGRAM_TOKEN")
groq_client = Groq(api_key=groq_api_key)
chat_history = {}

# Model fallback hierarchy
FALLBACK_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant"
]

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

def chat_with_fallback(messages, temperature=0.12, max_tokens=512, models=None):
    """
    Вызов Groq Chat API с автоматическим переключением на резервные модели при ошибке 429.
    
    Args:
        messages: Список сообщений для чата
        temperature: Температура генерации
        max_tokens: Максимум токенов в ответе
        models: Список моделей для перебора (по умолчанию FALLBACK_MODELS)
    
    Returns:
        Объект response от Groq API
    
    Raises:
        Exception: Если все модели вернули ошибку
    """
    if models is None:
        models = FALLBACK_MODELS
    
    last_error = None
    
    for model in models:
        try:
            print(f"🔄 Попытка использовать модель: {model}")
            response = groq_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            print(f"✅ Успешный ответ от модели: {model}")
            return response
            
        except Exception as e:
            error_str = str(e)
            print(f"⚠️ Ошибка для модели {model}: {error_str}")
            
            # Проверяем код ошибки 429 (Rate Limit)
            if "429" in error_str or "rate_limit" in error_str.lower():
                print(f"⏳ Rate limit достигнут для {model}, переключаюсь на следующую модель...")
                last_error = e
                time.sleep(1)  # Короткая пауза перед переключением
                continue
            
            # Другие ошибки также пробуем обработать с фолбэком
            elif "503" in error_str or "502" in error_str or "500" in error_str:
                print(f"🔧 Серверная ошибка для {model}, переключаюсь...")
                last_error = e
                time.sleep(2)
                continue
            
            # Если ошибка не связана с перегрузкой, пробрасываем её
            else:
                last_error = e
                continue
    
    # Если все модели не сработали
    raise Exception(f"❌ Все модели недоступны. Последняя ошибка: {last_error}")

def remove_emojis(text):
    # Очистка текста от эмодзи перед озвучкой
    emoji_pattern = re.compile("[\U00010000-\U0010ffff]", flags=re.UNICODE)
    text = emoji_pattern.sub(r'', text)
    # Удаляем спец значки (простые)
    text = re.sub(r'[▶️📝🔊🗂️❓✏️🤖📦🎤💡⛑️👍]', '', text)
    return text.strip()

async def synthesize_voice(text, filename="voice.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural"):
    # Перед озвучкой чистим от эмодзи
    text_clean = remove_emojis(text)
    communicate = Communicate(text_clean, voice=voice)
    await communicate.save(filename)

def get_reply_keyboard():
    keyboard = [["Меню"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard_inline = [
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
    reply_markup_inline = InlineKeyboardMarkup(keyboard_inline)
    reply_markup_keyboard = get_reply_keyboard()
    text = (
        "👋 Добро пожаловать!\n\n"
        "🎤 Пришлите голосовое — бот покажет транскрипцию, ответит и озвучит его.\n"
        "Меню ниже ⬇️"
    )
    await update.message.reply_text(text, reply_markup=reply_markup_inline)
    await update.message.reply_text(
        "Для быстрого доступа всегда используйте кнопку «Меню» ⬇️ под строкой ввода.",
        reply_markup=reply_markup_keyboard
    )

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_handler(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    reply_markup_keyboard = get_reply_keyboard()

    if query.data == "start":
        await query.message.reply_text("▶️ Готов к новым сообщениям! Просто пришлите голосовое 👇", reply_markup=reply_markup_keyboard)
    elif query.data == "history":
        history = chat_history.get(user_id, [])
        if not history:
            await query.message.reply_text("🗂️ История пуста! Начните диалог — пришлите голос или текст.", reply_markup=reply_markup_keyboard)
        else:
            blocks = []
            for h in history:
                part = f"🎤 {h['origin']}"
                if h.get("fixed"):
                    part += f"\n✏️ Исправлено: {h['fixed']}"
                part += f"\n🤖 Ответ: {h['answer']}"
                blocks.append(part)
            text = "\n\n━━━━━━━━━━\n\n".join(blocks)
            # Разбиваем длинную историю на части и отправляем последовательно
            max_len = 4000
            messages = [text[i:i+max_len] for i in range(0, len(text), max_len)]
            for msg in messages:
                await query.message.reply_text(f"🗂️ Ваша история чата:\n\n{msg}", reply_markup=reply_markup_keyboard)
    elif query.data == "fix_transcript":
        context.user_data["fix_mode"] = True
        await query.message.reply_text("📝 Введите исправленный текст для транскрипции:", reply_markup=reply_markup_keyboard)
    elif query.data == "voice_fixed":
        fixed = context.user_data.get("fixed_transcript")
        if not fixed:
            await query.message.reply_text("❗ Нет исправленной транскрипции. Введите её через кнопку '📝 Исправить'.", reply_markup=reply_markup_keyboard)
        else:
            await synthesize_voice(fixed, filename="fixed.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
            with open("fixed.mp3", "rb") as f:
                await query.message.reply_voice(voice=f)
            await query.message.reply_text("🔊 Ваша исправленная транскрипция озвучена!", reply_markup=reply_markup_keyboard)
    elif query.data == "help":
        help_text = (
            "❓ Что умеет бот:\n"
            "• Голос → транскрипция + GPT-ответ + озвучка\n"
            "• Исправление и озвучка транскрипции\n"
            "• Вся история ваших сообщений и ответов\n"
            "• Красивые иконки для вашего удобства\n\n"
            "Пришлите голосовое или текст — получите сразу полный ответ!"
        )
        await query.message.reply_text(help_text, reply_markup=reply_markup_keyboard)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    voice = await update.message.voice.get_file()
    await voice.download_to_drive("voice.ogg")
    convert_ogg_to_mp3("voice.ogg", "voice.mp3")
    audio_path = "voice.mp3"

    prompt = transcribe_whisper_groq(audio_path)
    if not prompt or len(prompt) < 4:
        await update.message.reply_text(
            "❗ Не удалось хорошо распознать ваш голос. Воспользуйтесь кнопкой '📝 Исправить', чтобы вручную ввести текст!",
            reply_markup=get_reply_keyboard()
        )
        context.user_data["fix_mode"] = True
        return

    await update.message.reply_text(f"🎤 Транскрипция:\n{prompt}", reply_markup=get_reply_keyboard())
    context.user_data["last_transcript"] = prompt
    context.user_data["fixed_transcript"] = None

    try:
        response = chat_with_fallback(
            messages=[
                {"role": "system", "content": "Ты дружелюбный помощник, всегда отвечай обычным русским текстом, без Markdown, без ##, без **. Добавляй уместные эмодзи для красоты и структуры (например: 🤖, ✏️, 📦, 📝, 🎤, 🔊, 💡, ⛑️, 🗂️, 👍)"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.12,
            max_tokens=512
        )
        answer_text = response.choices[0].message.content
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при генерации ответа: {e}", reply_markup=get_reply_keyboard())
        return

    await update.message.reply_text(f"🤖 Ответ:\n{answer_text}", reply_markup=get_reply_keyboard())
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
        await update.message.reply_text(f"✏️ Исправленная транскрипция сохранена: {text}", reply_markup=get_reply_keyboard())

        try:
            response = chat_with_fallback(
                messages=[
                    {"role": "system", "content": "Ты дружелюбный помощник, всегда отвечай обычным русским текстом, без Markdown, без ##, без **. Добавляй уместные эмодзи для красоты и структуры (например: 🤖, ✏️, 📦, 📝, 🎤, 🔊, 💡, ⛑️, 🗂️, 👍)"},
                    {"role": "user", "content": text}
                ],
                temperature=0.12,
                max_tokens=512
            )
            answer_text = response.choices[0].message.content
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при генерации ответа: {e}", reply_markup=get_reply_keyboard())
            return

        await update.message.reply_text(f"🤖 Ответ:\n{answer_text}", reply_markup=get_reply_keyboard())
        await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
        with open("answer.mp3", "rb") as f:
            await update.message.reply_voice(voice=f)

        chat_history.setdefault(user_id, []).append({
            "origin": context.user_data.get("last_transcript", ""),
            "fixed": text,
            "answer": answer_text
        })
        return

    try:
        response = chat_with_fallback(
            messages=[
                {"role": "system", "content": "Ты дружелюбный помощник, всегда отвечай обычным русским текстом, без Markdown, без ##, без **. Добавляй уместные эмодзи для красоты и структуры (например: 🤖, ✏️, 📦, 📝, 🎤, 🔊, 💡, ⛑️, 🗂️, 👍)"},
                {"role": "user", "content": text}
            ],
            temperature=0.12,
            max_tokens=512
        )
        answer_text = response.choices[0].message.content
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при генерации ответа: {e}", reply_markup=get_reply_keyboard())
        return

    await update.message.reply_text(f"🤖 Ответ:\n{answer_text}", reply_markup=get_reply_keyboard())
    await synthesize_voice(answer_text, filename="answer.mp3", lang="ru-RU", voice="ru-RU-DmitryNeural")
    with open("answer.mp3", "rb") as f:
        await update.message.reply_voice(voice=f)

    chat_history.setdefault(user_id, []).append({"origin": text, "fixed": None, "answer": answer_text})

if __name__ == "__main__":
    app = ApplicationBuilder().token(telegram_token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^Меню$"), menu_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^Меню$"), text_handler))
    app.run_polling()
