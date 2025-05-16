from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from flask import Flask
import threading
import os

# حالت‌ها برای ConversationHandler
AGE, GENDER, ASK_QUESTION = range(3)

# حافظه موقت کاربران
user_data = {}

# ساخت سرور Flask برای زنده نگه‌داشتن در Render
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "✅ Doctor Tafteh bot is running."

def run():
    app_web.run(host='0.0.0.0', port=8080)

threading.Thread(target=run).start()

# دکمه‌ها
main_menu_keyboard = [
    [InlineKeyboardButton("👨‍⚕️ مشاوره پزشکی", callback_data='doctor')],
    [InlineKeyboardButton("🛍 مشاهده محصولات", url="https://www.tafteh.com/")],
]
main_menu_markup = InlineKeyboardMarkup(main_menu_keyboard)

back_to_menu_keyboard = [
    [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data='back')]
]
back_to_menu_markup = InlineKeyboardMarkup(back_to_menu_keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {}
    await update.message.reply_text("به ربات دکتر تافته خوش آمدید! سن خود را وارد کنید:")
    return AGE

async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]["age"] = update.message.text
    await update.message.reply_text("جنسیت خود را وارد کنید (زن/مرد):")
    return GENDER

async def get_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]["gender"] = update.message.text
    await update.message.reply_text("لطفاً سوال پزشکی خود را وارد کنید:")
    return ASK_QUESTION

async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    question = update.message.text
    age = user_data[user_id].get("age")
    gender = user_data[user_id].get("gender")

    # اینجا می‌تونی هوش مصنوعی یا هر پاسخ دهنده‌ای اضافه کنی
    response = f"سوال شما: {question}\nسن: {age}، جنسیت: {gender}\nپاسخ: لطفاً برای پاسخ دقیق‌تر به پزشک مراجعه کنید."

    await update.message.reply_text(response, reply_markup=back_to_menu_markup)
    return ConversationHandler.END

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'doctor':
        await query.message.reply_text("سن خود را وارد کنید:")
        return AGE
    elif query.data == 'back':
        await query.message.reply_text("به منوی اصلی برگشتید:", reply_markup=main_menu_markup)
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لغو شد. برای شروع دوباره /start را بزنید.")
    return ConversationHandler.END

def main():
    TOKEN = os.getenv("BOT_TOKEN")  # توکن در محیط تعریف شده است
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)],
            GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_gender)],
            ASK_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, answer_question)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(menu_callback))

    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
