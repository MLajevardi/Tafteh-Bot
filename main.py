import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی از فایل .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# تنظیم لاگر
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# پیام خوش‌آمدگویی
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! من دکتر تافته هستم.\nسوال پزشکی‌ات رو از من بپرس."
    )

# پاسخ به پیام‌های کاربر
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    # در اینجا پاسخ ساختگی برای تست داده می‌شود.
    response = f"🤖 دکتر تافته: سوالت رو گرفتم!\n«{user_message}»\nولی من فعلاً یه بات آزمایشی‌ام."

    await update.message.reply_text(response)

# تابع اصلی برای اجرای ربات
def main():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
