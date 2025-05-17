import logging
import httpx
import os
from enum import Enum
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton # اضافه شدن Inline موارد
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# بارگذاری متغیرهای محیطی از فایل .env
load_dotenv()

# تنظیمات لاگ‌گیری
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# توکن‌ها و تنظیمات از متغیرهای محیطی
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "openai/gpt-3.5-turbo")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg")
URL_TAFTEH_WEBSITE = "https://tafteh.ir/" # آدرس وب‌سایت اصلی تافته

# بررسی وجود توکن‌های ضروری
if not TELEGRAM_TOKEN:
    logger.error("توکن تلگرام (BOT_TOKEN) در متغیرهای محیطی یافت نشد.")
    exit()
if not OPENROUTER_API_KEY:
    logger.error("کلید API اوپن‌روتر (OPENROUTER_API_KEY) در متغیرهای محیطی یافت نشد.")
    exit()

# تعریف حالت‌های مکالمه
class States(Enum):
    MAIN_MENU = 1
    AWAITING_AGE = 2
    AWAITING_GENDER = 3
    DOCTOR_CONVERSATION = 4
    # States.PRODUCT_GUIDE حذف شد

# منوها
MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"]],
    resize_keyboard=True
    # one_time_keyboard=True حذف شد تا منو پایدارتر باشد
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
    system_message_content = (
        "شما یک پزشک عمومی متخصص هستید. لطفاً به تمام سوالات پزشکی کاربران به زبان فارسی، دقیق، علمی، محترمانه و ساده پاسخ دهید. "
        "از دادن توصیه‌های غیرپزشکی خودداری کنید. اگر سوالی کاملا غیرپزشکی بود، به کاربر اطلاع دهید که فقط به سوالات پزشکی پاسخ می‌دهید."
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
        # ایجاد دکمه شیشه‌ای (Inline Button) برای لینک وب‌سایت
        keyboard = [[InlineKeyboardButton("مشاهده وب‌سایت تافته", url=URL_TAFTEH_WEBSITE)]]
        reply_markup_inline = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "برای مشاهده محصولات و وب‌سایت تافته، روی دکمه زیر کلیک کنید:",
            reply_markup=reply_markup_inline
        )
        # کاربر در منوی اصلی باقی می‌ماند و منوی اصلی همچنان باید فعال باشد
        # (چون one_time_keyboard=True از MAIN_MENU_KEYBOARD حذف شد)
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

# product_guide_handler حذف شد چون دیگر نیازی به آن نیست

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

def main() -> None:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            States.MAIN_MENU: [
                MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات)$"), main_menu_handler),
                MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start) # برای اطمینان
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
            # States.PRODUCT_GUIDE حذف شد
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start),
        ],
    )

    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))

    logger.info("ربات در حال اجرا است...")
    application.run_polling()

if __name__ == '__main__':
    main()