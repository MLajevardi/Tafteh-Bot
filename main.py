import logging
import httpx
import os
from enum import Enum
from dotenv import load_dotenv
import threading # برای اجرای Flask در یک ترد جداگانه
from flask import Flask # وارد کردن Flask
import asyncio # برای مدیریت اجرای async ربات

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    Application
)

# بارگذاری متغیرهای محیطی از فایل .env
load_dotenv()

# تنظیمات لاگ‌گیری در ابتدای برنامه
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()] # اطمینان از خروجی به کنسول
)
logger = logging.getLogger(__name__)

logger.info("اسکریپت main.py شروع به کار کرد. در حال بررسی متغیرهای محیطی...")

# توکن‌ها و تنظیمات از متغیرهای محیطی
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "openai/gpt-3.5-turbo")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg")
URL_TAFTEH_WEBSITE = "https://tafteh.ir/"

# بررسی وجود توکن‌های ضروری
if not TELEGRAM_TOKEN:
    logger.error("!!! بحرانی: توکن تلگرام (BOT_TOKEN) در متغیرهای محیطی یافت نشد. برنامه خارج می‌شود.")
    exit(1) # خروج با کد خطا
else:
    logger.info("توکن تلگرام با موفقیت بارگذاری شد.")

if not OPENROUTER_API_KEY:
    logger.error("!!! بحرانی: کلید API اوپن‌روتر (OPENROUTER_API_KEY) در متغیرهای محیطی یافت نشد. برنامه خارج می‌شود.")
    exit(1) # خروج با کد خطا
else:
    logger.info("کلید API اوپن‌روتر با موفقیت بارگذاری شد.")

# تعریف حالت‌های مکالمه
class States(Enum):
    MAIN_MENU = 1
    AWAITING_AGE = 2
    AWAITING_GENDER = 3
    DOCTOR_CONVERSATION = 4

# منوها
MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"]],
    resize_keyboard=True
)
BACK_TO_MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["🔙 بازگشت به منوی اصلی"]],
    resize_keyboard=True,
    one_time_keyboard=True
)

# --- توابع کمکی ---
async def ask_openrouter(prompt: str, age: int = None, gender: str = None) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    # اصلاح پرامپت سیستمی
    system_message_content = (
        "شما یک پزشک عمومی متخصص هستید. لطفاً به تمام سوالات پزشکی کاربران به زبان فارسی، دقیق، علمی، محترمانه و ساده پاسخ دهید. "
        "از دادن توصیه‌های غیرپزشکی خودداری کنید. اگر سوالی کاملا غیرپزشکی بود، به کاربر اطلاع دهید که فقط به سوالات پزشکی پاسخ می‌دهید. "
        "پاسخ‌های خود را مستقیماً و بدون هیچگونه مقدمه‌ای مانند 'بله'، 'خب'، 'البته' یا مشابه آن شروع کنید."
    )
    user_message = f"کاربر {age if age else ''} ساله و جنسیت {gender if gender else ''} دارد و می‌پرسد: {prompt}" if age and gender else prompt

    body = {
        "model": OPENROUTER_MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_message_content},
            {"role": "user", "content": user_message}
        ]
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            logger.info(f"ارسال درخواست به OpenRouter برای مدل: {OPENROUTER_MODEL_NAME}")
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            if data.get("choices") and data["choices"][0].get("message") and data["choices"][0]["message"].get("content"):
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"پاسخ غیرمنتظره از OpenRouter: {data}")
                return "❌ ساختار پاسخ دریافت شده از سرویس نامعتبر است."
        except httpx.HTTPStatusError as e:
            logger.error(f"خطای HTTP از OpenRouter: {e.response.status_code} - {e.response.text}")
            return f"❌ مشکل در ارتباط با سرویس پزشک مجازی (کد خطا: {e.response.status_code}). لطفاً بعداً تلاش کنید."
        except httpx.RequestError as e:
            logger.error(f"خطای درخواست به OpenRouter: {e}")
            return "❌ مشکل در برقراری ارتباط با سرویس پزشک مجازی. لطفاً از اتصال اینترنت خود مطمئن شوید و دوباره تلاش کنید."
        except Exception as e:
            logger.error(f"خطای ناشناخته در ask_openrouter: {e}", exc_info=True)
            return "❌ مشکلی پیش‌بینی نشده در دریافت پاسخ پیش آمده است. لطفاً دوباره تلاش کنید."

# --- کنترل‌کننده‌های مکالمه ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    logger.info(f"کاربر {user.id} ({user.full_name}) ربات را با /start شروع کرد.")
    context.user_data.clear()
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=WELCOME_IMAGE_URL,
            caption=f"سلام {user.first_name}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    except Exception as e:
        logger.error(f"خطا در ارسال تصویر خوش‌آمدگویی: {e}")
        await update.message.reply_text(
            f"سلام {user.first_name}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    return States.MAIN_MENU

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    user = update.effective_user
    logger.info(f"کاربر {user.id} در منوی اصلی گزینه '{text}' را انتخاب کرد.")

    if text == "👨‍⚕️ دکتر تافته":
        await update.message.reply_text(
            "بسیار خب. برای اینکه بتوانم بهتر به شما کمک کنم، لطفاً سن خود را وارد کنید:",
            reply_markup=ReplyKeyboardRemove()
        )
        return States.AWAITING_AGE
    elif text == "📦 راهنمای محصولات":
        keyboard = [[InlineKeyboardButton("مشاهده وب‌سایت تافته", url=URL_TAFTEH_WEBSITE)]]
        reply_markup_inline = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "برای مشاهده محصولات و وب‌سایت تافته، روی دکمه زیر کلیک کنید:",
            reply_markup=reply_markup_inline
        )
        return States.MAIN_MENU
    else:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های موجود در منو را انتخاب کنید.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return States.MAIN_MENU

async def request_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    age_text = update.message.text
    user = update.effective_user

    if not age_text.isdigit() or not (1 <= int(age_text) <= 120):
        await update.message.reply_text("❗️ لطفاً یک سن معتبر (عدد بین ۱ تا ۱۲۰) وارد کنید.")
        return States.AWAITING_AGE

    context.user_data["age"] = int(age_text)
    logger.info(f"کاربر {user.id} سن خود را {age_text} وارد کرد.")
    await update.message.reply_text("متشکرم. حالا لطفاً جنسیت خود را وارد کنید (مثلاً: زن یا مرد):")
    return States.AWAITING_GENDER

async def request_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    gender_text = update.message.text.strip().lower()
    user = update.effective_user

    if gender_text not in ["زن", "مرد", "خانم", "آقا"]:
        await update.message.reply_text("❗️ لطفاً جنسیت خود را به صورت «زن» یا «مرد» وارد کنید.")
        return States.AWAITING_GENDER

    context.user_data["gender"] = "زن" if gender_text in ["زن", "خانم"] else "مرد"
    logger.info(f"کاربر {user.id} جنسیت خود را {context.user_data['gender']} وارد کرد.")

    await update.message.reply_text(
        f"✅ مشخصات شما ثبت شد:\n"
        f"سن: {context.user_data['age']} سال\n"
        f"جنسیت: {context.user_data['gender']}\n\n"
        "اکنون می‌توانید سوال پزشکی خود را از دکتر تافته بپرسید. "
        "برای بازگشت به منوی اصلی، از دکمه زیر استفاده کنید یا /cancel را ارسال کنید.",
        reply_markup=BACK_TO_MAIN_MENU_KEYBOARD
    )
    return States.DOCTOR_CONVERSATION

async def doctor_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user_question = update.message.text
    user = update.effective_user
    age = context.user_data.get("age")
    gender = context.user_data.get("gender")

    if user_question == "🔙 بازگشت به منوی اصلی":
        logger.info(f"کاربر {user.id} از مکالمه با دکتر به منوی اصلی بازگشت.")
        context.user_data.clear()
        await update.message.reply_text("به منوی اصلی بازگشتید.", reply_markup=MAIN_MENU_KEYBOARD)
        return States.MAIN_MENU

    logger.info(f"کاربر {user.id} (سن: {age}, جنسیت: {gender}) سوال پزشکی پرسید: {user_question}")
    await update.message.reply_text("⏳ دکتر تافته در حال بررسی سوال شماست، لطفاً کمی صبر کنید...")

    answer = await ask_openrouter(user_question, age, gender)

    await update.message.reply_text(answer, parse_mode="Markdown", reply_markup=BACK_TO_MAIN_MENU_KEYBOARD)
    return States.DOCTOR_CONVERSATION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    logger.info(f"کاربر {user.id} ({user.full_name}) مکالمه را با /cancel لغو کرد.")
    context.user_data.clear()
    await update.message.reply_text(
        "درخواست شما لغو شد. به منوی اصلی بازگشتید.",
        reply_markup=MAIN_MENU_KEYBOARD,
        reply_to_message_id=None
    )
    return States.MAIN_MENU

async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.warning(f"کاربر {user.id} پیام نامعتبر یا در حالت نامناسب ارسال کرد: {update.message.text}")
    await update.message.reply_text(
        "متوجه نشدم چه گفتید. لطفاً از گزینه‌های منو استفاده کنید.",
        reply_markup=MAIN_MENU_KEYBOARD
    )

# --- بخش وب سرور Flask ---
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    """یک اندپوینت ساده برای اینکه Render تشخیص دهد سرویس فعال است و به پورت گوش می‌دهد."""
    # این لاگ در هر بار دسترسی به این اندپوینت توسط Render ثبت می‌شود
    logger.info("درخواست Health Check به اندپوینت '/' Flask دریافت شد.")
    return 'ربات تلگرام تافته فعال است و به پورت گوش می‌دهد!', 200

def run_flask_app():
    """Flask app را در پورتی که Render مشخص می‌کند اجرا می‌کند."""
    port = int(os.environ.get('PORT', 8080))
    # این لاگ مهم است: نشان می‌دهد که ترد Flask سعی در شروع وب سرور دارد
    logger.info(f"ترد Flask: در حال تلاش برای شروع وب سرور روی هاست 0.0.0.0 و پورت {port}")
    try:
        flask_app.run(host='0.0.0.0', port=port)
        logger.info(f"ترد Flask: وب سرور Flask روی پورت {port} متوقف شد.") # اگر run به هر دلیلی خاتمه یابد
    except Exception as e:
        logger.error(f"ترد Flask: خطایی در اجرای وب سرور Flask رخ داد: {e}", exc_info=True)


# --- مدیریت اجرای همزمان Flask و ربات ---
if __name__ == '__main__':
    logger.info("بلوک اصلی برنامه (__name__ == '__main__') شروع شد.")
    
    logger.info("در حال تنظیم و شروع ترد Flask...")
    flask_thread = threading.Thread(target=run_flask_app, name="FlaskThread")
    flask_thread.daemon = True # با بسته شدن برنامه اصلی، ترد هم بسته شود
    flask_thread.start()
    logger.info("ترد Flask شروع به کار کرد.")

    logger.info("در حال ساخت اپلیکیشن ربات تلگرام...")
    telegram_application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # تعریف ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            States.MAIN_MENU: [
                MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات)<span class="math-inline">"\), main\_menu\_handler\),
MessageHandler\(filters\.Regex\("^🔙 بازگشت به منوی اصلی</span>"), start)
            ],
            States.AWAITING_AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, request_age_handler)
            ],
            States.AWAITING_GENDER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, request_gender_handler)
            ],
            States.DOCTOR_CONVERSATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, doctor_conversation_handler)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start),
        ],
    )

    telegram_application.add_handler(conv_handler)
    telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    
    logger.info("ربات تلگرام در حال شروع polling (این یک عملیات بلاک کننده است)...")
    try:
        telegram_application.run_polling()
        # این خط فقط زمانی اجرا می‌شود که run_polling به طور صحیح خاتمه یابد (مثلاً با سیگنال خارجی)
        logger.info("Polling ربات تلگرام متوقف شد.")
    except Exception as e:
        logger.error(f"خطایی در حین اجرای run_polling رخ داد: {e}", exc_info=True)
    finally:
        logger.info("برنامه در حال بسته شدن است. ترد Flask نیز به دلیل daemon=True بسته خواهد شد.")