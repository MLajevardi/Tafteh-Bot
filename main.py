import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import httpx
import os
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی
load_dotenv()

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# پیام خوش‌آمدگویی و منو
WELCOME_MESSAGE = """
سلام! 👋  
من «ربات تافته» هستم 🤖  
لطفاً یکی از گزینه‌های زیر را انتخاب کنید:
"""
WELCOME_IMAGE_URL = "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg"

MAIN_MENU = ReplyKeyboardMarkup(
    [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"]],
    resize_keyboard=True
)
BACK_MENU = ReplyKeyboardMarkup(
    [["🔙 بازگشت به منوی اصلی"]],
    resize_keyboard=True
)

# پرسش از OpenRouter
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
                "content": "شما یک پزشک عمومی متخصص هستید. لطفاً به تمام سوالات پزشکی کاربران به زبان فارسی، دقیق، علمی، محترمانه و ساده پاسخ دهید. از دادن توصیه‌های غیرپزشکی خودداری کنید."
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
            return "❌ مشکلی در دریافت پاسخ پیش آمده است. لطفاً دوباره تلاش کنید."

# بررسی اینکه سوال پزشکی هست یا نه
async def is_medical_question(text: str) -> bool:
    prompt = f"آیا این سوال پزشکی است؟ فقط با 'بله' یا 'خیر' پاسخ بده: {text}"
    answer = await ask_openrouter(prompt)
    return "بله" in answer.strip().lower()

# فرمان /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=WELCOME_IMAGE_URL,
        caption=WELCOME_MESSAGE,
        reply_markup=MAIN_MENU
    )

# مدیریت پیام‌ها
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "👨‍⚕️ دکتر تافته":
        context.user_data["mode"] = "doctor"
        await update.message.reply_text(
            "🩺 سوالات پزشکی خود را از دکتر تافته بپرسید. توجه داشته باشید که پاسخ‌ها هر هفته توسط پزشک مجرب بررسی می‌شوند.",
            parse_mode='Markdown',
            reply_markup=BACK_MENU
        )
        return

    if text == "📦 راهنمای محصولات":
        context.user_data["mode"] = "products"
        await update.message.reply_text(
            "برای مشاهده محصولات، روی لینک زیر کلیک کنید:\n[🌐 مشاهده محصولات تافته](https://tafteh.ir)",
            parse_mode='Markdown',
            reply_markup=BACK_MENU
        )
        return

    if text == "🔙 بازگشت به منوی اصلی":
        context.user_data["mode"] = "menu"
        await update.message.reply_text("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=MAIN_MENU)
        return

    if context.user_data.get("mode") == "doctor":
        if not await is_medical_question(text):
            await update.message.reply_text("❗️ لطفاً فقط سوالات پزشکی مطرح کنید.", reply_markup=BACK_MENU)
            return

        await update.message.reply_text("⏳ لطفاً منتظر بمانید...")
        answer = await ask_openrouter(text)
        await update.message.reply_text(answer, parse_mode='Markdown', reply_markup=BACK_MENU)
        return

    await update.message.reply_text("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=MAIN_MENU)

# راه‌اندازی ربات
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Bot is running...")
    app.run_polling()
