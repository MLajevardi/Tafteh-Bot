import logging
import os
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import httpx
from flask import Flask
import threading

# بارگذاری متغیرهای محیطی
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO)

# تعریف عکس و منو
WELCOME_IMAGE_URL = "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg"
MAIN_MENU = ReplyKeyboardMarkup(
    [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"]],
    resize_keyboard=True
)
BACK_MENU = ReplyKeyboardMarkup(
    [["🔙 بازگشت به منوی اصلی"]],
    resize_keyboard=True
)

WELCOME_MESSAGE = """
سلام! 👋  
من «ربات تافته» هستم 🤖  
لطفاً یکی از گزینه‌های زیر را انتخاب کنید:
"""

# Flask برای پینگ UptimeRobot
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "🤖 DrTafteh is alive!"

def run_flask():
    flask_app.run(host='0.0.0.0', port=8080)

threading.Thread(target=run_flask).start()

# گرفتن پاسخ از openrouter
async def ask_openrouter(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": "شما یک پزشک عمومی متخصص هستید. لطفاً به تمام سوالات پزشکی کاربران به زبان فارسی، دقیق، علمی، محترمانه و ساده پاسخ دهید."
            },
            {"role": "user", "content": prompt}
        ]
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception:
            return "❌ مشکلی در دریافت پاسخ پیش آمده است."

async def is_medical_question(text: str) -> bool:
    check_prompt = f"آیا این سوال پزشکی است؟ فقط با 'بله' یا 'خیر' پاسخ بده: {text}"
    answer = await ask_openrouter(check_prompt)
    return "بله" in answer.strip().lower()

# شروع ربات
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=WELCOME_IMAGE_URL,
        caption=WELCOME_MESSAGE,
        reply_markup=MAIN_MENU
    )

# هندل پیام‌ها
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "👨‍⚕️ دکتر تافته":
        context.user_data["mode"] = "doctor"
        context.user_data["step"] = "ask_age"
        await update.message.reply_text("لطفاً سن خود را وارد کنید:", reply_markup=ReplyKeyboardRemove())
        return

    if text == "📦 راهنمای محصولات":
        await update.message.reply_text(
            "در حال هدایت به سایت تافته... 🌐",
            reply_markup=ReplyKeyboardRemove()
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="https://tafteh.ir"
        )
        return

    if text == "🔙 بازگشت به منوی اصلی":
        context.user_data.clear()
        await update.message.reply_text("لطفاً یکی از موارد زیر را انتخاب کنید:", reply_markup=MAIN_MENU)
        return

    if context.user_data.get("mode") == "doctor":
        # جمع‌آوری اطلاعات اولیه
        if context.user_data.get("step") == "ask_age":
            context.user_data["age"] = text
            context.user_data["step"] = "ask_gender"
            await update.message.reply_text("جنسیت خود را وارد کنید (مرد / زن):")
            return

        elif context.user_data.get("step") == "ask_gender":
            context.user_data["gender"] = text
            context.user_data["step"] = "ready"
            await update.message.reply_text("✅ ممنون! حالا سوال پزشکی‌تان را بپرسید:")
            return

        elif context.user_data.get("step") == "ready":
            if not await is_medical_question(text):
                await update.message.reply_text("⚠️ لطفاً فقط سوالات پزشکی مطرح کنید.")
                return

            await update.message.reply_text("⏳ در حال دریافت پاسخ...")
            full_prompt = f"سن: {context.user_data['age']}\nجنسیت: {context.user_data['gender']}\nسوال: {text}"
            answer = await ask_openrouter(full_prompt)
            await update.message.reply_text(answer)
            return

    # حالت پیش‌فرض
    await update.message.reply_text("لطفاً یکی از گزینه‌های منو را انتخاب کنید:", reply_markup=MAIN_MENU)

# اجرای ربات
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 DrTafteh is running...")
    app.run_polling()
