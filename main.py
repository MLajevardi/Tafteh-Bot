import logging
import httpx
import os
from enum import Enum
from dotenv import load_dotenv
import threading
from flask import Flask
import asyncio
import random # برای انتخاب نکته سلامتی تصادفی

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore

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

load_dotenv()

db = None 
try:
    cred_path_render = os.getenv("FIREBASE_CREDENTIALS_PATH", "/etc/secrets/firebase-service-account-key.json")
    cred_path_local = "firebase-service-account-key.json" 
    cred_path = cred_path_render if os.path.exists(cred_path_render) else cred_path_local
    
    if not os.path.exists(cred_path):
        logging.warning(f"فایل کلید Firebase در مسیر '{cred_path}' یافت نشد. ربات بدون اتصال به دیتابیس اجرا خواهد شد.")
    else:
        cred = credentials.Certificate(cred_path)
        if not firebase_admin._apps: 
            firebase_admin.initialize_app(cred)
        db = firestore.client() 
        logging.info("Firebase Admin SDK با موفقیت مقداردهی اولیه شد و به Firestore متصل است.")
except Exception as e:
    logging.error(f"خطای بحرانی در مقداردهی اولیه Firebase Admin SDK: {e}", exc_info=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__) 

logger.info("اسکریپت main.py شروع به کار کرد. در حال بررسی متغیرهای محیطی...")

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "openai/gpt-3.5-turbo")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg")
URL_TAFTEH_WEBSITE = "https://tafteh.ir/"

# ثابت‌های امتیازات
POINTS_FOR_JOINING_CLUB = 50
POINTS_FOR_PROFILE_COMPLETION = 20

if not TELEGRAM_TOKEN:
    logger.error("!!! بحرانی: توکن تلگرام (BOT_TOKEN) در متغیرهای محیطی یافت نشد. برنامه خارج می‌شود.")
    exit(1)
# ... (بقیه بررسی‌های توکن و کلید API مانند قبل) ...
if not OPENROUTER_API_KEY:
    logger.error("!!! بحرانی: کلید API اوپن‌روتر (OPENROUTER_API_KEY) در متغیرهای محیطی یافت نشد. برنامه خارج می‌شود.")
    exit(1)
else:
    logger.info(f"کلید API اوپن‌روتر با موفقیت بارگذاری شد (بخشی از کلید: sk-...{OPENROUTER_API_KEY[-4:]}).")


class States(Enum):
    MAIN_MENU = 1
    AWAITING_AGE = 2
    AWAITING_GENDER = 3
    DOCTOR_CONVERSATION = 4

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"], ["⭐ عضویت/وضعیت باشگاه مشتریان"]],
    resize_keyboard=True
)

DOCTOR_CONVERSATION_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ سوال جدید از دکتر"],
        ["🔙 بازگشت به منوی اصلی"]
    ],
    resize_keyboard=True
)

GENDER_SELECTION_KEYBOARD = ReplyKeyboardMarkup(
    [["زن"], ["مرد"]],
    resize_keyboard=True,
    one_time_keyboard=True
)

# لیست نکات سلامتی برای اعضای باشگاه
HEALTH_TIPS_FOR_CLUB = [
    "نکته ۱: روزانه حداقل ۸ لیوان آب بنوشید تا بدنتان هیدراته بماند.",
    "نکته ۲: خواب کافی (۷-۸ ساعت) برای بازیابی انرژی و سلامت روان ضروری است.",
    "نکته ۳: حداقل ۳۰ دقیقه فعالیت بدنی متوسط در بیشتر روزهای هفته به حفظ سلامت قلب کمک می‌کند.",
    "نکته ۴: مصرف میوه‌ها و سبزیجات رنگارنگ، ویتامین‌ها و آنتی‌اکسیدان‌های لازم را به بدن شما می‌رساند.",
    "نکته ۵: برای کاهش استرس، تکنیک‌های آرام‌سازی مانند مدیتیشن یا تنفس عمیق را امتحان کنید."
]

async def ask_openrouter(system_prompt: str, chat_history: list) -> str:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی)
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    messages_payload = [{"role": "system", "content": system_prompt}] + chat_history
    body = {
        "model": OPENROUTER_MODEL_NAME,
        "messages": messages_payload,
        "temperature": 0.6, 
    }
    logger.info(f"آماده‌سازی درخواست برای OpenRouter با مدل: {OPENROUTER_MODEL_NAME} و {len(chat_history)} پیام در تاریخچه.")
    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            logger.debug(f"ارسال درخواست به OpenRouter. Body: {body}")
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            logger.debug(f"پاسخ خام دریافت شده از OpenRouter: {data}")
            llm_response_content = ""
            if data.get("choices") and len(data["choices"]) > 0 and data["choices"][0].get("message") and data["choices"][0]["message"].get("content"):
                llm_response_content = data["choices"][0]["message"]["content"].strip()
                logger.info(f"محتوای دقیق پاسخ دریافت شده از LLM: '{llm_response_content}'")
                return llm_response_content
            else:
                logger.error(f"ساختار پاسخ دریافت شده از OpenRouter نامعتبر یا فاقد محتوا است: {data}")
                return "❌ مشکلی در پردازش پاسخ از سرویس پزشک مجازی رخ داد."
        except Exception as e: 
            logger.error(f"خطا در ارتباط یا پردازش پاسخ OpenRouter: {e}", exc_info=True)
            return "❌ بروز خطا در ارتباط با سرویس پزشک مجازی. لطفاً مجدداً تلاش نمایید."


def _prepare_doctor_system_prompt(age: int, gender: str) -> str:
    # ... (پرامپت سیستمی دکتر تافته بدون تغییر نسبت به نسخه کامل قبلی)
    return (
        f"شما یک پزشک عمومی متخصص، بسیار دقیق، با دانش به‌روز، صبور و همدل به نام 'دکتر تافته' هستید. کاربری که با شما صحبت می‌کند {age} ساله و {gender} است. "
        "وظیفه شما ارائه راهنمایی پزشکی اولیه از طریق یک مکالمه چند مرحله‌ای هدفمند به زبان فارسی روان، صحیح، علمی و قابل فهم برای عموم است. شما هرگز تشخیص قطعی نمی‌دهید و دارو تجویز نمی‌کنید، بلکه اطلاعات اولیه را جمع‌آوری کرده، توصیه‌های عمومی و ایمن ارائه می‌دهید و در صورت لزوم کاربر را به مراجعه به پزشک راهنمایی می‌کنید."
        "**لحن شما باید حرفه‌ای، محترمانه، علمی و همدلانه باشد. از به‌کار بردن عبارات بیش از حد احساسی، شعاری، یا جملات پایانی بسیار طولانی و غیرضروری مانند 'ایمان دارم بهبودی خوبی خواهید داشت' یا 'پشتیبانی همیشگی من در دسترس شماست' جداً خودداری کنید. پاسخ‌های خود را مختصر و مفید نگه دارید.**"
        "**روند مکالمه شما باید به صورت اجباری مراحل زیر را طی کند:** "
        "1.  **دریافت مشکل اولیه کاربر.** "
        "2.  **مرحله پرسشگری فعال و دقیق (بسیار کلیدی و الزامی):** به محض دریافت مشکل اولیه، **به هیچ وجه نباید اطلاعات عمومی یا توصیه فوری ارائه دهید.** شما موظف هستید ابتدا **حداقل یک یا دو سوال تکمیلی بسیار دقیق، کوتاه و کاملاً مرتبط** با همان مشکل مطرح شده از کاربر بپرسید تا جزئیات بیشتری کسب کنید. (مثال برای سردرد: 'سردردتان از کی شروع شده و دقیقاً کجای سرتان است؟ آیا حالت تهوع یا حساسیت به نور هم دارید؟'). "
        "3.  **ادامه پرسشگری هوشمندانه:** بر اساس پاسخ کاربر، در صورت نیاز، سوالات تکمیلی دیگری بپرسید (همچنان یک یا دو سوال کوتاه و مرتبط در هر نوبت). "
        "4.  **پرسش برای اطلاعات تکمیلی نهایی از کاربر (الزامی قبل از هرگونه توصیه):** پس از اینکه چند سوال کلیدی پرسیدید و قبل از ارائه هرگونه جمع‌بندی یا توصیه، **حتماً و الزاماً از کاربر این سوال را بپرسید: 'آیا نکته یا علامت دیگری در مورد این مشکل وجود دارد که بخواهید اضافه کنید یا سوال دیگری در این مورد دارید؟'** "
        "5.  **ارائه توصیه‌های عمومی و اولیه (پس از مرحله ۴):** تنها پس از پاسخ کاربر به سوال مرحله ۴ (و اگر اطلاعات جدید و مهمی ارائه نداد یا گفت سوال دیگری ندارد)، می‌توانید توصیه‌های عمومی و ایمن اولیه ارائه دهید. **از توصیه داروهای خاص یا تشخیص قطعی خودداری کنید.** در پایان توصیه‌ها، همیشه تاکید کنید که اگر علائم ادامه یافت یا شدیدتر شد، باید به پزشک مراجعه کنند. سپس مکالمه را با یک جمله کوتاه و حرفه‌ای مانند 'امیدوارم بهتر شوید. آیا سوال دیگری هست که بتوانم کمک کنم؟' به پایان برسانید یا منتظر پاسخ کاربر بمانید."
        "**سایر دستورالعمل‌های مهم:** "
        "   - از اصطلاحات صحیح و رایج پزشکی و عمومی در زبان فارسی استفاده کنید. "
        "   - اگر سوالی به وضوح پزشکی نبود، با این عبارت دقیق پاسخ دهید: 'متاسفم، من یک ربات پزشک هستم و فقط می‌توانم به سوالات مرتبط با حوزه پزشکی پاسخ دهم. چطور می‌توانم در زمینه پزشکی به شما کمک کنم؟' "
        "   - در تمامی پاسخ‌های خود، مستقیماً به سراغ مطلب بروید و از مقدمات غیرضروری استفاده نکنید. "
        "   - همیشه محترمانه و دقیق باشید."
    )

async def notify_points_awarded(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id_str: str, points_awarded: int, reason: str):
    """به کاربر اطلاع می‌دهد که امتیازی دریافت کرده و مجموع امتیازاتش را نمایش می‌دهد."""
    if not db: return # اگر دیتابیس در دسترس نیست، اطلاع‌رسانی نکن

    try:
        user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
        total_points = user_profile.get('points', 0) if user_profile else points_awarded # اگر پروفایل نبود، امتیاز فعلی را همان امتیاز دریافتی در نظر بگیر
        
        # اگر امتیاز در نتیجه Increment بوده، پروفایل را دوباره بخوانیم تا مقدار دقیق را داشته باشیم
        # این کار برای اطمینان از نمایش امتیاز صحیح است، چون Increment یک عملیات اتمی در سرور است.
        if points_awarded > 0 : # فقط اگر امتیازی واقعا اضافه شده باشد
             user_profile_updated = await asyncio.to_thread(get_user_profile_data, user_id_str)
             if user_profile_updated: total_points = user_profile_updated.get('points', 0)

        message = f"✨ شما {points_awarded} امتیاز برای '{reason}' دریافت کردید!\n"
        message += f"مجموع امتیاز شما اکنون: {total_points} است. 🌟"
        await update.message.reply_text(message)
        logger.info(f"به کاربر {user_id_str} برای '{reason}'، {points_awarded} امتیاز اطلاع داده شد. مجموع امتیاز: {total_points}")
    except Exception as e:
        logger.error(f"خطا در اطلاع‌رسانی امتیاز به کاربر {user_id_str}: {e}", exc_info=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی)
    user = update.effective_user
    user_id_str = str(user.id) 
    logger.info(f"کاربر {user_id_str} ({user.full_name if user.full_name else user.username}) /start یا بازگشت به منو.")
    
    if "doctor_chat_history" in context.user_data:
        del context.user_data["doctor_chat_history"]
        logger.info(f"تاریخچه مکالمه دکتر برای کاربر {user_id_str} پاک شد (در صورت وجود).")
    if "system_prompt_for_doctor" in context.user_data:
        del context.user_data["system_prompt_for_doctor"]
        logger.info(f"پرامپت سیستمی دکتر برای کاربر {user_id_str} پاک شد (در صورت وجود).")
    
    if db: 
        try:
            await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        except Exception as e:
            logger.error(f"خطا در get_or_create_user_profile برای کاربر {user_id_str} در تابع start: {e}", exc_info=True)

    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=WELCOME_IMAGE_URL,
            caption=f"سلام {user.first_name if user.first_name else 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    except Exception as e:
        logger.error(f"خطا در ارسال تصویر خوش‌آمدگویی برای کاربر {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text(
            f"سلام {user.first_name if user.first_name else 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=MAIN_MENU_KEYBOARD
        )
    return States.MAIN_MENU


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی، فقط مطمئن شوید دکمه باشگاه مشتریان به درستی به club_status_or_join_handler می‌رود)
    text = update.message.text
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} در منوی اصلی گزینه '{text}' را انتخاب کرد.")

    if text == "👨‍⚕️ دکتر تافته":
        age, gender = None, None
        if db: 
            try:
                user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
                if user_profile: 
                    age = user_profile.get("age")
                    gender = user_profile.get("gender")
            except Exception as e:
                logger.error(f"خطا در خواندن پروفایل کاربر {user_id_str} از دیتابیس: {e}", exc_info=True)
        
        if age and gender: 
            logger.info(f"کاربر {user_id_str} سن ({age}) و جنسیت ({gender}) را از دیتابیس دارد. مستقیم به مکالمه با دکتر می‌رود.")
            system_prompt = _prepare_doctor_system_prompt(age, gender)
            context.user_data["system_prompt_for_doctor"] = system_prompt
            context.user_data["doctor_chat_history"] = []
            logger.info("پرامپت سیستمی دکتر بازسازی شد و تاریخچه مکالمه پاک شد.")
            
            await update.message.reply_text(
                f"مشخصات شما (سن: {age}، جنسیت: {gender}) از قبل در سیستم موجود است.\n"
                "اکنون می‌توانید سوال پزشکی خود را از دکتر تافته بپرسید.",
                reply_markup=DOCTOR_CONVERSATION_KEYBOARD
            )
            return States.DOCTOR_CONVERSATION
        else: 
            logger.info(f"سن یا جنسیت برای کاربر {user_id_str} در دیتابیس موجود نیست یا کامل نیست. درخواست سن.")
            if "age" in context.user_data: del context.user_data["age"] 
            if "gender" in context.user_data: del context.user_data["gender"]
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
    elif text == "⭐ عضویت/وضعیت باشگاه مشتریان": 
        return await club_status_or_join_handler(update, context)
    else: 
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های موجود در منو را انتخاب کنید.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return States.MAIN_MENU

async def request_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی)
    age_text = update.message.text
    user = update.effective_user
    user_id_str = str(user.id)

    if not age_text.isdigit() or not (1 <= int(age_text) <= 120):
        await update.message.reply_text("❗️ لطفاً یک سن معتبر (عدد بین ۱ تا ۱۲۰) وارد کنید.")
        return States.AWAITING_AGE 

    context.user_data["age_temp"] = int(age_text) 
    logger.info(f"کاربر {user_id_str} سن موقت خود را {age_text} وارد کرد.")
    await update.message.reply_text("متشکرم. حالا لطفاً جنسیت خود را انتخاب کنید:", reply_markup=GENDER_SELECTION_KEYBOARD)
    return States.AWAITING_GENDER


async def request_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    gender_input = update.message.text.strip() 
    user = update.effective_user
    user_id_str = str(user.id)
    
    age = context.user_data.pop("age_temp", None) 
    if not age:
        logger.error(f"خطا: سن موقت برای کاربر {user_id_str} یافت نشد. بازگشت به منوی اصلی.")
        await update.message.reply_text("مشکلی در پردازش اطلاعات پیش آمد. لطفاً دوباره تلاش کنید.", reply_markup=MAIN_MENU_KEYBOARD)
        return await start(update, context) 

    gender = gender_input 
    
    profile_updated = False
    if db: 
        try:
            # بررسی اینکه آیا سن و جنسیت قبلا در دیتابیس وجود داشته یا خیر
            user_profile_before_update = await asyncio.to_thread(get_user_profile_data, user_id_str)
            
            await asyncio.to_thread(update_user_profile_data, user_id_str, {"age": age, "gender": gender})
            logger.info(f"سن ({age}) و جنسیت ({gender}) کاربر {user_id_str} در دیتابیس ذخیره/به‌روز شد.")
            profile_updated = True

            # اعطای امتیاز برای تکمیل پروفایل (فقط یک بار)
            if user_profile_before_update and not user_profile_before_update.get('profile_completion_points_awarded', False):
                # اگر فیلدهای سن یا جنسیت قبلا None بوده‌اند و حالا مقدار گرفته‌اند
                if user_profile_before_update.get("age") is None or user_profile_before_update.get("gender") is None :
                    await asyncio.to_thread(update_user_profile_data, user_id_str, 
                                            {"points": firestore.Increment(POINTS_FOR_PROFILE_COMPLETION),
                                             "profile_completion_points_awarded": True})
                    logger.info(f"به کاربر {user_id_str} تعداد {POINTS_FOR_PROFILE_COMPLETION} امتیاز برای تکمیل پروفایل (سن و جنسیت) داده شد.")
                    # اطلاع رسانی به کاربر در اینجا یا پس از پیام اصلی
                    # برای جلوگیری از دو پیام پشت سر هم، فعلا اینجا اطلاع رسانی نمی‌کنیم و در join_club_command_handler انجام می‌شود
                    # یا یک تابع جدا برای اطلاع رسانی امتیاز بسازیم.
                    await notify_points_awarded(update, context, user_id_str, POINTS_FOR_PROFILE_COMPLETION, "تکمیل پروفایل (سن و جنسیت)")


        except Exception as e:
            logger.error(f"خطا در ذخیره سن/جنسیت یا اعطای امتیاز پروفایل برای کاربر {user_id_str} در دیتابیس: {e}", exc_info=True)

    context.user_data["age"] = age 
    context.user_data["gender"] = gender
    logger.info(f"کاربر {user_id_str} جنسیت خود را '{gender}' انتخاب کرد. سن: {age}")

    system_prompt = _prepare_doctor_system_prompt(age, gender)
    context.user_data["system_prompt_for_doctor"] = system_prompt
    context.user_data["doctor_chat_history"] = []

    logger.info(f"پرامپت سیستمی برای دکتر تافته تنظیم شد. تاریخچه مکالمه پاک شد.")

    await update.message.reply_text(
        f"✅ مشخصات شما ثبت شد:\n"
        f"سن: {age} سال\n"
        f"جنسیت: {gender}\n\n"
        "اکنون می‌توانید سوال پزشکی خود را از دکتر تافته بپرسید.",
        reply_markup=DOCTOR_CONVERSATION_KEYBOARD
    )
    return States.DOCTOR_CONVERSATION


async def doctor_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # --- حذف بخش اعطای امتیاز برای هر سوال از دکتر ---
    # ... (بقیه کد تابع doctor_conversation_handler بدون تغییر نسبت به نسخه کامل قبلی) ...
    logger.info(f"--- DCH Entered --- User: {update.effective_user.id}, Text: '{update.message.text}', History items: {len(context.user_data.get('doctor_chat_history', []))}")
    
    user_question = update.message.text
    user = update.effective_user
    user_id_str = str(user.id)
    
    chat_history = context.user_data.get("doctor_chat_history", [])
    system_prompt = context.user_data.get("system_prompt_for_doctor")

    if not system_prompt: 
        logger.warning(f"DCH: System prompt for user {user_id_str} not found in user_data! Attempting to rebuild.")
        age_db, gender_db = None, None
        if db:
            try:
                profile_db = await asyncio.to_thread(get_user_profile_data, user_id_str)
                if profile_db:
                    age_db = profile_db.get("age")
                    gender_db = profile_db.get("gender")
            except Exception as e:
                logger.error(f"DCH: Error fetching profile for {user_id_str} to rebuild prompt: {e}")

        if age_db and gender_db:
            system_prompt = _prepare_doctor_system_prompt(age_db, gender_db)
            context.user_data["system_prompt_for_doctor"] = system_prompt
            context.user_data["age"] = age_db 
            context.user_data["gender"] = gender_db
            logger.info(f"DCH: System prompt for user {user_id_str} rebuilt from DB data.")
        else:
            logger.error(f"DCH: Could not rebuild system prompt for user {user_id_str}. Age/Gender missing. Returning to main menu.")
            await update.message.reply_text("مشکلی در بازیابی اطلاعات شما پیش آمده. لطفاً از ابتدا با انتخاب 'دکتر تافته' شروع کنید.", reply_markup=MAIN_MENU_KEYBOARD)
            if "doctor_chat_history" in context.user_data: del context.user_data["doctor_chat_history"]
            if "system_prompt_for_doctor" in context.user_data: del context.user_data["system_prompt_for_doctor"]
            return await start(update, context) 

    if user_question == "🔙 بازگشت به منوی اصلی":
        logger.info(f"DCH: User {user_id_str} selected 'بازگشت به منوی اصلی'. Delegating to start handler.")
        return await start(update, context) 
    elif user_question == "❓ سوال جدید از دکتر":
        logger.info(f"DCH: User {user_id_str} selected 'سوال جدید از دکتر'. Clearing chat history.")
        context.user_data["doctor_chat_history"] = [] 
        await update.message.reply_text("بسیار خب، تاریخچه مکالمه قبلی پاک شد. سوال پزشکی جدید خود را بپرسید:", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
        return States.DOCTOR_CONVERSATION

    logger.info(f"DCH: Processing conversational text from user {user_id_str}: '{user_question}'")
    
    chat_history.append({"role": "user", "content": user_question})
    
    await update.message.reply_text("⏳ دکتر تافته در حال بررسی پیام شماست، لطفاً کمی صبر کنید...")

    assistant_response = await ask_openrouter(system_prompt, chat_history)
    
    chat_history.append({"role": "assistant", "content": assistant_response})
    context.user_data["doctor_chat_history"] = chat_history

    # اعطای امتیاز برای هر سوال از دکتر حذف شد.

    await update.message.reply_text(assistant_response, parse_mode="Markdown", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
    return States.DOCTOR_CONVERSATION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی)
    user = update.effective_user
    logger.info(f"User {user.id} called /cancel. Delegating to start handler for cleanup and main menu.")
    await update.message.reply_text("درخواست شما لغو شد. بازگشت به منوی اصلی...", reply_markup=ReplyKeyboardRemove())
    return await start(update, context) 

async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی)
    user = update.effective_user
    logger.warning(f"--- GLOBAL FALLBACK Reached --- User: {user.id}, Text: '{update.message.text}', Current user_data: {context.user_data}")
    await update.message.reply_text(
        "متوجه نشدم چه گفتید. لطفاً از گزینه‌های منو استفاده کنید یا اگر در مرحله خاصی هستید، ورودی مورد انتظار را ارسال نمایید.",
        reply_markup=MAIN_MENU_KEYBOARD
    )


async def club_status_or_join_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی)
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} گزینه 'عضویت/وضعیت باشگاه مشتریان' را انتخاب کرد.")

    if not db:
        await update.message.reply_text("متاسفانه در حال حاضر امکان دسترسی به سیستم باشگاه مشتریان وجود ندارد.", reply_markup=MAIN_MENU_KEYBOARD)
        return States.MAIN_MENU

    try:
        user_profile = await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        
        if user_profile.get('is_club_member', False):
            points = user_profile.get('points', 0)
            await update.message.reply_text(f"شما عضو باشگاه مشتریان تافته هستید! 🏅\nامتیاز فعلی شما: {points} امتیاز.", reply_markup=MAIN_MENU_KEYBOARD)
        else:
            await update.message.reply_text(
                "شما هنوز عضو باشگاه مشتریان تافته نیستید.\n"
                "برای عضویت و بهره‌مندی از مزایا و امتیازات ویژه، لطفاً دستور /joinclub را ارسال کنید.",
                reply_markup=MAIN_MENU_KEYBOARD
            )
    except Exception as e:
        logger.error(f"خطا در پردازش وضعیت/عضویت باشگاه برای کاربر {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text("متاسفانه مشکلی در بررسی وضعیت عضویت شما پیش آمد.", reply_markup=MAIN_MENU_KEYBOARD)
    
    return States.MAIN_MENU


async def join_club_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست عضویت در باشگاه مشتریان را با /joinclub داد.")

    if not db:
        await update.message.reply_text("متاسفانه در حال حاضر امکان اتصال به سیستم باشگاه مشتریان وجود ندارد. لطفاً بعداً تلاش کنید.")
        return

    try:
        user_profile = await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        
        if user_profile and user_profile.get('is_club_member', False):
            await update.message.reply_text("شما از قبل عضو باشگاه مشتریان تافته هستید! 🎉", reply_markup=MAIN_MENU_KEYBOARD)
        else:
            await asyncio.to_thread(update_user_profile_data, user_id_str, 
                                    {"is_club_member": True, 
                                     "points": firestore.Increment(POINTS_FOR_JOINING_CLUB),
                                     "club_join_date": firestore.SERVER_TIMESTAMP}) # تاریخ عضویت هم اضافه شد
            logger.info(f"کاربر {user_id_str} با موفقیت به باشگاه مشتریان پیوست و {POINTS_FOR_JOINING_CLUB} امتیاز دریافت کرد.")
            # اطلاع رسانی امتیاز
            await notify_points_awarded(update, context, user_id_str, POINTS_FOR_JOINING_CLUB, "عضویت در باشگاه مشتریان")
            
            await update.message.reply_text( # این پیام پس از پیام امتیاز ارسال می‌شود
                f"عضویت شما در باشگاه مشتریان تافته با موفقیت انجام شد! ✨\n"
                "از این پس از مزایای ویژه اعضا بهره‌مند خواهید شد.",
                reply_markup=MAIN_MENU_KEYBOARD 
            )
    except Exception as e:
        logger.error(f"خطا در پردازش عضویت باشگاه برای کاربر {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text("متاسفانه مشکلی در فرآیند عضویت شما پیش آمد. لطفاً بعداً دوباره تلاش کنید.")

async def club_status_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: 
    # ... (بدون تغییر نسبت به نسخه کامل قبلی)
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست وضعیت عضویت در باشگاه را با /clubstatus داد.")

    if not db:
        await update.message.reply_text("متاسفانه در حال حاضر امکان بررسی وضعیت عضویت وجود ندارد.")
        return

    try:
        user_profile = await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name) 
        
        if user_profile.get('is_club_member', False):
            points = user_profile.get('points', 0)
            await update.message.reply_text(f"شما عضو باشگاه مشتریان تافته هستید. 🏅\nامتیاز فعلی شما: {points} امتیاز.")
        else: 
            await update.message.reply_text("شما هنوز عضو باشگاه مشتریان تافته نیستید. برای عضویت و بهره‌مندی از مزایا، دستور /joinclub را ارسال کنید یا از دکمه منوی اصلی استفاده نمایید.")
            
    except Exception as e:
        logger.error(f"خطا در بررسی وضعیت عضویت باشگاه برای کاربر {user_id_str} با فرمان /clubstatus: {e}", exc_info=True)
        await update.message.reply_text("متاسفانه مشکلی در بررسی وضعیت عضویت شما پیش آمد.")

# --- تابع جدید برای نکته سلامتی اعضای باشگاه ---
async def health_tip_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست نکته سلامتی باشگاه را با /clubtip داد.")

    if not db:
        await update.message.reply_text("متاسفانه در حال حاضر امکان دسترسی به سیستم باشگاه مشتریان وجود ندارد.")
        return

    try:
        user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
        
        if user_profile and user_profile.get('is_club_member', False):
            tip = random.choice(HEALTH_TIPS_FOR_CLUB) # انتخاب یک نکته تصادفی
            points_for_tip = 2 # امتیاز کم برای خواندن نکته
            await asyncio.to_thread(update_user_profile_data, user_id_str, {"points": firestore.Increment(points_for_tip)})
            
            message_to_send = f"⚕️ **نکته سلامتی ویژه اعضای باشگاه تافته:**\n\n_{tip}_\n\n"
            message_to_send += f"شما +{points_for_tip} امتیاز برای مشاهده این نکته دریافت کردید!"
            await update.message.reply_text(message_to_send, parse_mode="Markdown")
            logger.info(f"نکته سلامتی برای عضو باشگاه {user_id_str} ارسال شد و {points_for_tip} امتیاز دریافت کرد.")
        else:
            await update.message.reply_text("این بخش مخصوص اعضای باشگاه مشتریان تافته است. برای عضویت و استفاده از این قابلیت، لطفاً دستور /joinclub را ارسال کنید.")
            
    except Exception as e:
        logger.error(f"خطا در ارسال نکته سلامتی برای کاربر {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text("متاسفانه مشکلی در ارائه نکته سلامتی پیش آمد.")


def get_or_create_user_profile(user_id: str, username: str = None, first_name: str = None) -> dict:
    # ... (به‌روز شده برای شامل کردن profile_completion_points_awarded)
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Cannot access profile for user {user_id}.")
        return {"user_id": user_id, "username": username, "first_name": first_name, 
                "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [],
                "profile_completion_points_awarded": False}

    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()

    if user_doc.exists:
        logger.info(f"DB: پروفایل کاربر {user_id} از Firestore خوانده شد.")
        user_data = user_doc.to_dict()
        # اطمینان از وجود فیلدهای پیش‌فرض جدید
        if 'is_club_member' not in user_data: user_data['is_club_member'] = False
        if 'points' not in user_data: user_data['points'] = 0
        if 'badges' not in user_data: user_data['badges'] = []
        if 'profile_completion_points_awarded' not in user_data: user_data['profile_completion_points_awarded'] = False
        if 'club_join_date' not in user_data: user_data['club_join_date'] = None # برای سازگاری با داده‌های قدیمی
        return user_data
    else:
        logger.info(f"DB: پروفایل جدیدی برای کاربر {user_id} در Firestore ایجاد می‌شود.")
        user_data = {
            'user_id': user_id,
            'username': username if username else None,
            'first_name': first_name if first_name else None,
            'registration_date': firestore.SERVER_TIMESTAMP,
            'age': None, 
            'gender': None, 
            'is_club_member': False,
            'points': 0,
            'badges': [],
            'last_interaction_date': firestore.SERVER_TIMESTAMP,
            'profile_completion_points_awarded': False, # فیلد جدید
            'club_join_date': None
        }
        user_ref.set(user_data)
        return user_data

def update_user_profile_data(user_id: str, data_to_update: dict) -> None:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی)
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Cannot update profile for user {user_id}.")
        return

    user_ref = db.collection('users').document(user_id)
    data_to_update['last_updated_date'] = firestore.SERVER_TIMESTAMP
    user_ref.update(data_to_update)
    logger.info(f"DB: پروفایل کاربر {user_id} با داده‌های {data_to_update} در Firestore به‌روز شد.")


def get_user_profile_data(user_id: str) -> dict | None:
    # ... (به‌روز شده برای شامل کردن profile_completion_points_awarded)
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Cannot get profile for user {user_id}.")
        return None
        
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        if 'is_club_member' not in user_data: user_data['is_club_member'] = False
        if 'points' not in user_data: user_data['points'] = 0
        if 'badges' not in user_data: user_data['badges'] = []
        if 'profile_completion_points_awarded' not in user_data: user_data['profile_completion_points_awarded'] = False
        if 'club_join_date' not in user_data: user_data['club_join_date'] = None
        return user_data
    return None


flask_app = Flask(__name__)
# ... (Flask app و run_flask_app بدون تغییر نسبت به نسخه کامل قبلی) ...
@flask_app.route('/')
def health_check():
    logger.info("درخواست Health Check به اندپوینت '/' Flask دریافت شد.")
    return 'ربات تلگرام تافته فعال است و به پورت گوش می‌دهد!', 200

def run_flask_app():
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"ترد Flask: در حال تلاش برای شروع وب سرور روی هاست 0.0.0.0 و پورت {port}")
    try:
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
        logger.info(f"ترد Flask: وب سرور Flask روی پورت {port} متوقف شد.")
    except Exception as e:
        logger.error(f"ترد Flask: خطایی در اجرای وب سرور Flask رخ داد: {e}", exc_info=True)


if __name__ == '__main__':
    # ... (بلوک اصلی بدون تغییر نسبت به نسخه کامل قبلی، فقط مطمئن شوید CommandHandler جدید اضافه شده)
    logger.info("بلوک اصلی برنامه (__name__ == '__main__') شروع شد.")
    
    if db is None:
        logger.warning("*****************************************************************")
        logger.warning("* دیتابیس Firestore مقداردهی اولیه نشده است!                     *")
        logger.warning("* ربات با قابلیت‌های محدود (بدون ذخیره دائمی اطلاعات) اجرا می‌شود. *")
        logger.warning("* لطفاً تنظیمات Firebase و فایل کلید را بررسی کنید.                *")
        logger.warning("*****************************************************************")

    logger.info("در حال تنظیم و شروع ترد Flask...")
    flask_thread = threading.Thread(target=run_flask_app, name="FlaskThread")
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("ترد Flask شروع به کار کرد.")

    logger.info("در حال ساخت اپلیکیشن ربات تلگرام...")
    telegram_application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            States.MAIN_MENU: [
                MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات|⭐ عضویت/وضعیت باشگاه مشتریان)$"), main_menu_handler),
            ],
            States.AWAITING_AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, request_age_handler)
            ],
            States.AWAITING_GENDER: [
                MessageHandler(filters.Regex("^(زن|مرد)$"), request_gender_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, 
                               lambda update, context: update.message.reply_text("لطفاً یکی از گزینه‌های «زن» یا «مرد» را با دکمه انتخاب کنید.", reply_markup=GENDER_SELECTION_KEYBOARD))
            ],
            States.DOCTOR_CONVERSATION: [
                MessageHandler(filters.Regex("^(❓ سوال جدید از دکتر|🔙 بازگشت به منوی اصلی)$"), doctor_conversation_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, doctor_conversation_handler)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start), 
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), 
        ],
        persistent=False,
        name="main_conversation"
    )

    telegram_application.add_handler(CommandHandler("joinclub", join_club_command_handler))
    telegram_application.add_handler(CommandHandler("clubstatus", club_status_command_handler)) 
    telegram_application.add_handler(CommandHandler("clubtip", health_tip_command_handler)) # افزودن کنترل کننده فرمان نکته سلامتی
    telegram_application.add_handler(conv_handler)
    telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    
    logger.info("ربات تلگرام در حال شروع polling...")
    try:
        telegram_application.run_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Polling ربات تلگرام متوقف شد.")
    except KeyboardInterrupt:
        logger.info("درخواست توقف (KeyboardInterrupt) دریافت شد. ربات در حال خاموش شدن...")
    except Exception as e:
        logger.error(f"خطایی در حین اجرای run_polling یا در زمان کار ربات رخ داد: {e}", exc_info=True)
    finally:
        logger.info("برنامه در حال بسته شدن است.")