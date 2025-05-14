import os
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# پیام خوش‌آمد
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! من دکتر تافته هستم. سوال پزشکی‌ات رو بپرس 🌿")

# گرفتن پاسخ از OpenRouter
def get_ai_response(message: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "تو یک پزشک متخصص عمومی هستی که به سوالات پزشکی کاربران پاسخ می‌دهی."},
            {"role": "user", "content": message}
        ]
    }
    response = requests.post(url, headers=headers, json=payload)
    result = response.json()

    try:
        return result["choices"][0]["message"]["content"].strip()
    except:
        return "متأسفم، مشکلی در پاسخ‌دهی به وجود آمده."

# پاسخ به پیام کاربر
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.reply_text("⏳ در حال بررسی سوال شما...")

    answer = get_ai_response(user_text)
    await update.message.reply_text(answer)

# تابع main بدون async برای سازگاری با Render
def main():
    print("🤖 Doctor Tafta is running...")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
