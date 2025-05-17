import logging
import httpx
import os
from enum import Enum
from dotenv import load_dotenv
import threading # برای اجرای Flask در یک ترد جداگانه
from flask import Flask # وارد کردن Flask
import asyncio # برای مدیریت اجرای async ربات (هرچند run_polling خودش مدیریت می‌کند)

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    Application # برای type hinting اگر لازم باشد
)

# بارگذاری متغیرهای محیطی از فایل .env (برای اجرای محلی مفید است، در Render از متغیرهای محیطی داشبورد استفاده می‌شود)
load_dotenv()

# تنظیمات لاگ‌گیری در ابتدای برنامه
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()] # اطمینان از خروجی به کنسول (stdout/stderr)
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
    exit(1) # خروج با کد خطا نشان‌دهنده مشکل
else:
    logger.info(f"توکن تلگرام با موفقیت بارگذاری شد (بخشی از توکن: ...{TELEGRAM_TOKEN[-6:]}).") # فقط بخش کوچکی از توکن برای تایید لاگ می‌شود

if not OPENROUTER_API_KEY:
    logger.error("!!! بحرانی: کلید API اوپن‌روتر (OPENROUTER_API_KEY) در متغیرهای محیطی یافت نشد. برنامه خارج می‌شود.")
    exit(1) # خروج با کد خطا
else:
    logger.info(f"کلید API اوپن‌روتر با موفقیت بارگذاری شد (بخشی از کلید: sk-...{OPENROUTER_API_KEY[-4:]}).") # فقط بخش کوچکی از کلید برای تایید لاگ می‌شود


# تعریف حالت‌های مکالمه
class States(Enum):
    MAIN_MENU = 1
    AWAITING_AGE = 2
    AWAITING_GENDER = 3
    DOCTOR_CONVERSATION = 4

# منوها
MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"]],
    resize_keyboard=True # باعث می‌شود منو پایدارتر باشد و پس از هر پیام ناپدید نشود
)
BACK_TO_MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["🔙 بازگشت به منوی اصلی"]],
    resize_keyboard=True,
    one_time_keyboard=True # این منو پس از انتخاب ناپدید می‌شود
)

# --- توابع کمکی ---
async def ask_openrouter(prompt: str, age: int = None, gender: str = None) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    # اصلاح پرامپت سیستمی برای جلوگیری از "بله،"
    system_message_content = (
        "شما یک پزشک عمومی متخصص هستید. لطفاً به تمام سوالات پزشکی کاربران به زبان فارسی، دقیق، علمی، محترمانه و ساده پاسخ دهید. "
        "از دادن توصیه‌های غیرپزشکی خودداری کنید. اگر سوالی کاملا غیرپزشکی بود، به کاربر اطلاع دهید که فقط به سوالات پزشکی پاسخ می‌دهید. "
        "پاسخ‌های خود را مستقیماً و بدون هیچگونه مقدمه‌ای مانند 'بله'، 'خب'، 'البته' یا مشابه آن شروع کنید."
    )
    user_message = f"کاربر با سن {age if age else 'نامشخص'} و جنسیت {gender if gender else 'نامشخص'} می‌پرسد: {prompt}" if age or gender else prompt


    body = {
        "model": OPENROUTER_MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_message_content},
            {"role": "user", "content": user_message}
        ]
    }
    logger.info(f"آماده‌سازی درخواست برای OpenRouter با مدل: {OPENROUTER_MODEL_NAME}")
    async with httpx.AsyncClient(timeout=60.0) as client: # افزایش تایم‌اوت به ۶۰ ثانیه
        try:
            logger.debug(f"ارسال درخواست به OpenRouter. Body: {body}") # لاگ کردن body برای دیباگ (می‌توانید بعدا حذف کنید)
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
            resp.raise_for_status()  # بررسی خطاهای HTTP (4xx یا 5xx)
            data = resp.json()
            logger.debug(f"پاسخ دریافت شده از OpenRouter: {data}")
            if data.get("choices") and data["choices"][0].get("message") and data["choices"][0]["message"].get("content"):
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"ساختار پاسخ دریافت شده از OpenRouter نامعتبر است: {data}")
                return "❌ ساختار پاسخ دریافت شده از سرویس نامعتبر است."
        except httpx.HTTPStatusError as e:
            logger.error(f"خطای HTTP از OpenRouter: {e.response.status_code} - {e.response.text}")
            return f"❌ مشکل در ارتباط با سرویس پزشک مجازی (کد خطا: {e.response.status_code}). لطفاً بعداً تلاش کنید."
        except httpx.RequestError as e:
            logger.error(f"خطای درخواست به OpenRouter (ممکن است مشکل شبکه باشد): {e}")
            return "❌ مشکل در برقراری ارتباط با سرویس پزشک مجازی. لطفاً از اتصال اینترنت خود مطمئن شوید و دوباره تلاش کنید."
        except Exception as e:
            logger.error(f"خطای پیش‌بینی نشده در تابع ask_openrouter: {e}", exc_info=True) # exc_info=True برای نمایش traceback
            return "❌ مشکلی پیش‌بینی نشده در دریافت پاسخ پیش آمده است. لطفاً دوباره تلاش کنید."

# --- کنترل‌کننده‌های مکالمه ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    logger.info(f"کاربر {user.id} ({user.full_name if user.full_name else user.username}) ربات را با /start شروع کرد.")
    context.user_data.clear() # پاک کردن اطلاعات قبلی کاربر برای شروع تازه
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=WELCOME_IMAGE_URL,
            caption=f"سلام {user.first_name if user.first_name else 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    except Exception as e:
        logger.error(f"خطا در ارسال تصویر خوش‌آمدگویی برای کاربر {user.id}: {e}", exc_info=True)
        await update.message.reply_text(
            f"سلام {user.first_name if user.first_name else 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
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
            reply_markup=ReplyKeyboardRemove() # حذف منوی قبلی برای ورود متن
        )
        return States.AWAITING_AGE
    elif text == "📦 راهنمای محصولات":
        # ایجاد دکمه شیشه‌ای (Inline Button) برای لینک وب‌سایت
        keyboard = [[InlineKeyboardButton("مشاهده وب‌سایت تافته", url=URL_TAFTEH_WEBSITE)]]
        reply_markup_inline = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "برای مشاهده محصولات و وب‌سایت تافته، روی دکمه زیر کلیک کنید:",
            reply_markup=reply_markup_inline
        )
        # کاربر در منوی اصلی باقی می‌ماند و منوی اصلی همچنان باید فعال باشد
        return States.MAIN_MENU
    else:
        # این حالت نباید رخ دهد اگر Regex در MessageHandler درست باشد، اما برای اطمینان
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
        return States.AWAITING_AGE # در همین حالت باقی بماند تا ورودی صحیح دریافت شود

    context.user_data["age"] = int(age_text)
    logger.info(f"کاربر {user.id} سن خود را {age_text} وارد کرد.")
    await update.message.reply_text("متشکرم. حالا لطفاً جنسیت خود را وارد کنید (مثلاً: زن یا مرد):")
    return States.AWAITING_GENDER

async def request_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    gender_text = update.message.text.strip().lower() # .lower() برای یکسان سازی ورودی
    user = update.effective_user

    # کمی انعطاف‌پذیری بیشتر در ورودی جنسیت
    if gender_text not in ["زن", "مرد", "خانم", "آقا", "مونث", "مذکر"]:
        await update.message.reply_text("❗️ لطفاً جنسیت خود را به صورت «زن» یا «مرد» وارد کنید.")
        return States.AWAITING_GENDER

    # استانداردسازی مقدار ذخیره شده
    if gender_text in ["زن", "خانم", "مونث"]:
        context.user_data["gender"] = "زن"
    else: # مرد، آقا، مذکر
        context.user_data["gender"] = "مرد"
        
    logger.info(f"کاربر {user.id} جنسیت خود را '{context.user_data['gender']}' وارد کرد (ورودی اولیه: '{gender_text}').")

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

    # بررسی اینکه آیا پیام "بازگشت به منوی اصلی" است یا سوال پزشکی
    if user_question == "🔙 بازگشت به منوی اصلی":
        logger.info(f"کاربر {user.id} از مکالمه با دکتر به منوی اصلی بازگشت.")
        context.user_data.clear() # پاک کردن سن و جنسیت
        await update.message.reply_text("به منوی اصلی بازگشتید.", reply_markup=MAIN_MENU_KEYBOARD)
        return States.MAIN_MENU

    logger.info(f"کاربر {user.id} (سن: {age}, جنسیت: {gender}) سوال پزشکی پرسید: '{user_question}'")
    await update.message.reply_text("⏳ دکتر تافته در حال بررسی سوال شماست، لطفاً کمی صبر کنید...")

    answer = await ask_openrouter(user_question, age, gender)

    await update.message.reply_text(answer, parse_mode="Markdown", reply_markup=BACK_TO_MAIN_MENU_KEYBOARD)
    return States.DOCTOR_CONVERSATION # کاربر می‌تواند سوالات بیشتری بپرسد

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    logger.info(f"کاربر {user.id} ({user.full_name if user.full_name else user.username}) مکالمه را با /cancel لغو کرد.")
    context.user_data.clear() # پاک کردن تمام داده‌های کاربر در مکالمه فعلی
    await update.message.reply_text(
        "درخواست شما لغو شد. به منوی اصلی بازگشتید.",
        reply_markup=MAIN_MENU_KEYBOARD,
        reply_to_message_id=None # جلوگیری از ریپلای به پیام /cancel
    )
    return States.MAIN_MENU

async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پاسخ به پیام‌هایی که توسط هیچ کنترل‌کننده دیگری در ConversationHandler گرفته نشده‌اند."""
    user = update.effective_user
    current_state = context.user_data.get('state') # اگر حالت را دستی ذخیره کرده باشیم
    logger.warning(f"کاربر {user.id} پیام نامعتبر '{update.message.text}' در حالت {current_state if current_state else 'ناشناخته/خارج از مکالمه'} ارسال کرد.")
    await update.message.reply_text(
        "متوجه نشدم چه گفتید. لطفاً از گزینه‌های منو استفاده کنید یا اگر در مرحله خاصی هستید، ورودی مورد انتظار را ارسال نمایید.",
        reply_markup=MAIN_MENU_KEYBOARD # بازگشت به منوی اصلی به عنوان گزینه امن
    )

# --- بخش وب سرور Flask ---
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    """یک اندپوینت ساده برای اینکه Render تشخیص دهد سرویس فعال است و به پورت گوش می‌دهد."""
    logger.info("درخواست Health Check به اندپوینت '/' Flask دریافت شد.")
    return 'ربات تلگرام تافته فعال است و به پورت گوش می‌دهد!', 200

def run_flask_app():
    """Flask app را در پورتی که Render مشخص می‌کند اجرا می‌کند."""
    # Render پورت را از طریق متغیر محیطی PORT تنظیم می‌کند.
    port = int(os.environ.get('PORT', 8080)) # استفاده از 8080 به عنوان پیش‌فرض اگر PORT تنظیم نشده باشد (برای تست محلی)
    logger.info(f"ترد Flask: در حال تلاش برای شروع وب سرور روی هاست 0.0.0.0 و پورت {port}")
    try:
        # استفاده از werkzeug داخلی Flask برای اجرا. برای پروداکشن ساده مناسب است.
        # در محیط Render، خود Render مدیریت فرآیند را بر عهده دارد.
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
        logger.info(f"ترد Flask: وب سرور Flask روی پورت {port} متوقف شد.") # این پیام معمولا دیده نمی‌شود مگر اینکه سرور خاموش شود
    except Exception as e:
        logger.error(f"ترد Flask: خطایی در اجرای وب سرور Flask رخ داد: {e}", exc_info=True)


# --- مدیریت اجرای همزمان Flask و ربات ---
if __name__ == '__main__':
    logger.info("بلوک اصلی برنامه (__name__ == '__main__') شروع شد.")
    
    logger.info("در حال تنظیم و شروع ترد Flask...")
    flask_thread = threading.Thread(target=run_flask_app, name="FlaskThread")
    flask_thread.daemon = True # با بسته شدن برنامه اصلی (مثلا با Ctrl+C)، ترد Flask هم بسته شود
    flask_thread.start()
    logger.info("ترد Flask شروع به کار کرد.")

    logger.info("در حال ساخت اپلیکیشن ربات تلگرام...")
    telegram_application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # تعریف ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            States.MAIN_MENU: [
                MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات)$"), main_menu_handler),
                # برای مدیریت بازگشت از حالت‌های دیگر به منوی اصلی با دکمه
                MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start) # اگر کاربر از جای دیگری به این دکمه برسد
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
            CommandHandler("start", start), # برای ریست کردن مکالمه در هر زمان
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), # فال‌بک عمومی برای دکمه بازگشت
        ],
        persistent=False, # برای سادگی، حالت‌ها را در حافظه نگه می‌داریم (بین ری‌استارت‌ها ذخیره نمی‌شوند)
        name="main_conversation" # یک نام برای ConversationHandler (اختیاری)
    )

    telegram_application.add_handler(conv_handler)
    # یک کنترل‌کننده fallback برای پیام‌هایی که توسط هیچ کنترل‌کننده دیگری گرفته نمی‌شوند (خارج از ConversationHandler)
    telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    
    logger.info("ربات تلگرام در حال شروع polling (این یک عملیات بلاک کننده است)...")
    try:
        # run_polling شامل initialize(), updater.start_polling(), start(), و updater.idle() است
        telegram_application.run_polling(allowed_updates=Update.ALL_TYPES) # دریافت همه نوع آپدیت‌ها
        # این خط فقط زمانی اجرا می‌شود که run_polling به طور صحیح خاتمه یابد (مثلاً با سیگنال خارجی یا توقف دستی)
        logger.info("Polling ربات تلگرام متوقف شد.")
    except KeyboardInterrupt: # اگر با Ctrl+C متوقف شود
        logger.info("درخواست توقف (KeyboardInterrupt) دریافت شد. ربات در حال خاموش شدن...")
    except Exception as e:
        logger.error(f"خطایی در حین اجرای run_polling یا در زمان کار ربات رخ داد: {e}", exc_info=True)
    finally:
        logger.info("برنامه در حال بسته شدن است. ترد Flask نیز به دلیل daemon=True بسته خواهد شد.")
        # اگر نیاز به خاموش کردن دستی application باشد (معمولا run_polling خودش مدیریت می‌کند در خروج عادی)
        # if telegram_application.updater and telegram_application.updater.is_running:
        #     async def shutdown_bot():
        #         await telegram_application.updater.stop()
        #         await telegram_application.stop()
        #         await telegram_application.shutdown()
        #     asyncio.run(shutdown_bot()) # اجرای عملیات async خاموش شدن
        #     logger.info("عملیات خاموش کردن ربات انجام شد.")