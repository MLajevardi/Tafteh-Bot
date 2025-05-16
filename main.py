import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import httpx
import os
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی
load_dotenv()

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# منوها
MAIN_MENU = ReplyKeyboardMarkup(
    [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"]],
    resize_keyboard=True
)
BACK_MENU = ReplyKeyboardMarkup(
    [["🔙 بازگشت به منوی اصلی"]],
    resize_keyboard=True
)

# تصویر خوش‌آمدگویی
WELCOME_IMAGE_URL = "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg"

# سوال از مدل GPT
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

# چک پزشکی بودن سؤال
async def is_medical_question(text: str) -> bool:
    prompt = f"آیا این سوال پزشکی است؟ فقط با 'بله' یا 'خیر' پاسخ بده: {text}"
    answer = await ask_openrouter(prompt)
    return "بله" in answer.strip().lower()

# دستور /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=WELCOME_IMAGE_URL,
        caption="سلام! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
        reply_markup=MAIN_MENU
    )

# هندل کردن پیام‌ها
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_data = context.user_data

    if text == "🔙 بازگشت به منوی اصلی":
        user_data.clear()
        await update.message.reply_text("به منوی اصلی بازگشتید ⬇️", reply_markup=MAIN_MENU)
        return

    # انتخاب دکتر تافته
    if text == "👨‍⚕️ دکتر تافته":
        user_data["state"] = "awaiting_age"
        await update.message.reply_text("لطفاً سن خود را وارد کنید:", reply_markup=ReplyKeyboardRemove())
        return

    # دریافت سن
    if user_data.get("state") == "awaiting_age":
        if not text.isdigit() or not (1 <= int(text) <= 120):
            await update.message.reply_text("❗️ لطفاً یک سن معتبر وارد کنید.")
            return
        user_data["age"] = int(text)
        user_data["state"] = "awaiting_gender"
        await update.message.reply_text("جنسیت خود را وارد کنید (مثلاً: زن یا مرد):")
        return

    # دریافت جنسیت
    if user_data.get("state") == "awaiting_gender":
        if text.strip() not in ["زن", "مرد"]:
            await update.message.reply_text("❗️ لطفاً فقط بنویسید «زن» یا «مرد».")
            return
        user_data["gender"] = text.strip()
        user_data["state"] = "doctor_ready"
        await update.message.reply_text(
            f"✅ مشخصات ثبت شد.\nسن: {user_data['age']} سال\nجنسیت: {user_data['gender']}\n\nاکنون سوال پزشکی خود را بپرسید:",
            reply_markup=BACK_MENU
        )
        return

    # دریافت سؤال پزشکی
    if user_data.get("state") == "doctor_ready":
        if not await is_medical_question(text):
            await update.message.reply_text("❗️ لطفاً فقط سوالات پزشکی مطرح کنید.")
            return
        await update.message.reply_text("⏳ لطفاً منتظر پاسخ باشید...")
        answer = await ask_openrouter(text)
        await update.message.reply_text(answer, parse_mode="Markdown", reply_markup=BACK_MENU)
        return

    # انتخاب راهنمای محصولات ➡ باز کردن لینک سایت
    if text == "📦 راهنمای محصولات":
        await update.message.reply_text(
            "برای مشاهده محصولات، روی لینک زیر کلیک کنید:\n[🌐 مشاهده سایت تافته](https://tafteh.ir)",
            parse_mode="Markdown",
            reply_markup=BACK_MENU
        )
        return

    # حالت پیش‌فرض
    await update.message.reply_text("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=MAIN_MENU)

# راه‌اندازی ربات
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Bot is running...")
    app.run_polling()
