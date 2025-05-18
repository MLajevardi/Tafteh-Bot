import logging
import httpx
import os
from enum import Enum
from dotenv import load_dotenv
import threading
from flask import Flask
import asyncio
import random

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
        logging.warning(f"فایل کلید Firebase در مسیر '{cred_path}' یافت نشد.")
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

logger.info("اسکریپت main.py شروع به کار کرد...")

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "openai/gpt-3.5-turbo")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg")
URL_TAFTEH_WEBSITE = "https://tafteh.ir/"

POINTS_FOR_JOINING_CLUB = 50
POINTS_FOR_PROFILE_COMPLETION = 20
POINTS_FOR_CLUB_TIP = 2

BADGE_CLUB_MEMBER = "عضو باشگاه تافته 🏅"
BADGE_PROFILE_COMPLETE = "پروفایل کامل 🧑‍🔬"
BADGE_HEALTH_EXPLORER = "کاشف سلامت 🧭" # تغییر ایموجی
CLUB_TIP_BADGE_THRESHOLD = 3

if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    logger.error("!!! بحرانی: توکن‌های ضروری ربات یا API در متغیرهای محیطی یافت نشد. برنامه خارج می‌شود.")
    exit(1)
else:
    logger.info("توکن ربات و کلید API با موفقیت بارگذاری شدند.")


class States(Enum):
    MAIN_MENU = 1
    AWAITING_AGE = 2
    AWAITING_GENDER = 3
    DOCTOR_CONVERSATION = 4
    AWAITING_CLUB_JOIN_CONFIRMATION = 5 # حالت جدید برای تایید عضویت در باشگاه

# --- تعریف کیبوردها ---
DOCTOR_CONVERSATION_KEYBOARD = ReplyKeyboardMarkup(
    [["❓ سوال جدید از دکتر"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True
)
AGE_INPUT_KEYBOARD = ReplyKeyboardMarkup( # کیبورد برای مرحله ورود سن
    [["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True, one_time_keyboard=True
)
GENDER_SELECTION_KEYBOARD = ReplyKeyboardMarkup( # کیبورد برای مرحله انتخاب جنسیت
    [["زن"], ["مرد"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True, one_time_keyboard=True
)
CLUB_JOIN_CONFIRMATION_KEYBOARD = ReplyKeyboardMarkup( # کیبورد برای تایید عضویت در باشگاه
    [["✅ بله، عضو می‌شوم"], ["❌ خیر، فعلاً نه"]], resize_keyboard=True, one_time_keyboard=True
)

# --- توابع کمکی دیتابیس و پرامپت (بدون تغییر زیاد نسبت به قبل) ---
async def ask_openrouter(system_prompt: str, chat_history: list) -> str:
    # ... (بدون تغییر)
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
    # ... (بدون تغییر - همان پرامپت بسیار دقیق قبلی)
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
    # ... (بدون تغییر) ...
    if not db: return
    try:
        user_profile_updated = await asyncio.to_thread(get_user_profile_data, user_id_str)
        total_points = user_profile_updated.get('points', 0) if user_profile_updated else points_awarded
        
        message = f"✨ شما {points_awarded} امتیاز برای '{reason}' دریافت کردید!\n"
        message += f"مجموع امتیاز شما اکنون: {total_points} است. 🌟"
        # از context.bot.send_message استفاده می‌کنیم چون update ممکن است مربوط به پیام قبلی باشد
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message)
        logger.info(f"به کاربر {user_id_str} برای '{reason}'، {points_awarded} امتیاز اطلاع داده شد. مجموع امتیاز: {total_points}")
    except Exception as e:
        logger.error(f"خطا در اطلاع‌رسانی امتیاز به کاربر {user_id_str}: {e}", exc_info=True)

async def award_badge_if_not_already_awarded(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id_str: str, badge_name: str):
    # ... (بدون تغییر) ...
    if not db: return
    try:
        user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
        if user_profile:
            current_badges = user_profile.get('badges', [])
            if badge_name not in current_badges:
                await asyncio.to_thread(update_user_profile_data, user_id_str, {'badges': firestore.ArrayUnion([badge_name])})
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🏆 تبریک! شما نشان '{badge_name}' را دریافت کردید!")
                logger.info(f"نشان '{badge_name}' به کاربر {user_id_str} اعطا شد.")
            else:
                logger.info(f"کاربر {user_id_str} از قبل نشان '{badge_name}' را داشته است.")
    except Exception as e:
        logger.error(f"خطا در اعطای نشان '{badge_name}' به کاربر {user_id_str}: {e}", exc_info=True)

# --- توابع دیتابیس (با اصلاح جزئی برای اطمینان از وجود همه فیلدها) ---
def get_or_create_user_profile(user_id: str, username: str = None, first_name: str = None) -> dict:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی - اطمینان از وجود همه فیلدها)
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Profile for user {user_id} will be in-memory mock.")
        return {"user_id": user_id, "username": username, "first_name": first_name, 
                "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [],
                "profile_completion_points_awarded": False, "club_tip_usage_count": 0, "club_join_date": None,
                "name_first_db": None, "name_last_db": None} # افزودن فیلدهای نام و نام خانوادگی

    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()

    default_fields = {
        'age': None, 'gender': None, 'is_club_member': False, 'points': 0, 'badges': [],
        'profile_completion_points_awarded': False, 'club_tip_usage_count': 0,
        'club_join_date': None, 'name_first_db': None, 'name_last_db': None
    }

    if user_doc.exists:
        logger.info(f"DB: پروفایل کاربر {user_id} از Firestore خوانده شد.")
        user_data = user_doc.to_dict()
        # اطمینان از وجود تمام فیلدهای پیش‌فرض جدید
        updated_in_read = False
        for key, default_value in default_fields.items():
            if key not in user_data:
                user_data[key] = default_value
                updated_in_read = True # اگر فیلدی اضافه شد، برای آپدیت احتمالی علامت‌گذاری می‌کنیم
        if updated_in_read: # اگر فیلد جدیدی اضافه شد، دیتابیس را آپدیت کن
             logger.info(f"DB: به‌روزرسانی پروفایل کاربر {user_id} با فیلدهای پیش‌فرض جدید در زمان خواندن.")
             # فقط فیلدهایی که وجود نداشتند و اضافه شدند را آپدیت نمی‌کنیم، بلکه کل user_data که حالا کامل است را set می‌کنیم
             # یا به صورت انتخابی آنهایی که اضافه شدند. برای سادگی فعلا فقط می‌خوانیم و در صورت نیاز در update جداگانه آپدیت می‌کنیم.
             pass # فعلا آپدیت نکنید تا منطق پیچیده نشود. در update_user_profile_data آپدیت می‌شوند.
        return user_data
    else:
        logger.info(f"DB: پروفایل جدیدی برای کاربر {user_id} در Firestore ایجاد می‌شود.")
        user_data = {
            'user_id': user_id,
            'username': username if username else None,
            'first_name': first_name if first_name else None, # نام از تلگرام
            'registration_date': firestore.SERVER_TIMESTAMP,
            'last_interaction_date': firestore.SERVER_TIMESTAMP
        }
        # افزودن فیلدهای پیش‌فرض
        for key, default_value in default_fields.items():
            user_data[key] = default_value
        
        user_ref.set(user_data)
        return user_data

def update_user_profile_data(user_id: str, data_to_update: dict) -> None:
    # ... (بدون تغییر) ...
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Cannot update profile for user {user_id}.")
        return

    user_ref = db.collection('users').document(user_id)
    data_to_update['last_updated_date'] = firestore.SERVER_TIMESTAMP
    user_ref.update(data_to_update) # از set با merge=True هم می‌توان استفاده کرد اگر می‌خواهید داکیومنت را بازنویسی کنید و فقط فیلدهای مشخص شده آپدیت شوند
    logger.info(f"DB: پروفایل کاربر {user_id} با داده‌های {data_to_update} در Firestore به‌روز شد.")

def get_user_profile_data(user_id: str) -> dict | None:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی - اطمینان از وجود همه فیلدها)
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Cannot get profile for user {user_id}.")
        return None
        
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        defaults = {
            'is_club_member': False, 'points': 0, 'badges': [],
            'profile_completion_points_awarded': False, 'club_tip_usage_count': 0,
            'club_join_date': None, 'age': None, 'gender': None,
            'name_first_db': None, 'name_last_db': None
        }
        for key, default_value in defaults.items():
            if key not in user_data:
                user_data[key] = default_value
        return user_data
    return None

# --- کنترل‌کننده‌های اصلی ---
async def get_dynamic_main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE, user_id_str: str) -> ReplyKeyboardMarkup:
    """بر اساس وضعیت عضویت کاربر، کیبورد منوی اصلی مناسب را برمی‌گرداند."""
    is_member = False
    # ابتدا از user_data (کش جلسه) چک کن، اگر نبود از دیتابیس
    if 'is_club_member_cached' in context.user_data:
        is_member = context.user_data['is_club_member_cached']
        logger.info(f"وضعیت عضویت کاربر {user_id_str} از کش user_data خوانده شد: {is_member}")
    elif db:
        try:
            user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
            if user_profile and user_profile.get('is_club_member', False):
                is_member = True
            context.user_data['is_club_member_cached'] = is_member # ذخیره در کش برای دسترسی سریع‌تر در همین جلسه
            logger.info(f"وضعیت عضویت کاربر {user_id_str} از دیتابیس خوانده و در کش ذخیره شد: {is_member}")
        except Exception as e:
            logger.error(f"خطا در خواندن وضعیت عضویت کاربر {user_id_str} برای منوی پویا: {e}")
    
    if is_member:
        keyboard_layout = [
            ["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"],
            ["👤 پروفایل من / باشگاه"], 
            ["📣 نکته سلامتی باشگاه"]
        ]
    else:
        keyboard_layout = [
            ["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"],
            ["⭐ عضویت در باشگاه مشتریان"]
        ]
    return ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id) 
    logger.info(f"کاربر {user_id_str} ({user.full_name if user.full_name else user.username}) /start یا بازگشت به منو.")
    
    # فقط اطلاعات مربوط به مکالمه دکتر را پاک می‌کنیم. سن و جنسیت در دیتابیس مدیریت می‌شوند.
    # وضعیت عضویت هم از دیتابیس خوانده می‌شود.
    if "doctor_chat_history" in context.user_data:
        del context.user_data["doctor_chat_history"]
    if "system_prompt_for_doctor" in context.user_data:
        del context.user_data["system_prompt_for_doctor"]
    if 'is_club_member_cached' in context.user_data: # پاک کردن کش وضعیت عضویت
        del context.user_data['is_club_member_cached']

    if db: 
        try:
            # اطمینان از وجود پروفایل کاربر در دیتابیس
            await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        except Exception as e:
            logger.error(f"خطا در get_or_create_user_profile برای کاربر {user_id_str} در تابع start: {e}", exc_info=True)

    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=WELCOME_IMAGE_URL,
            caption=f"سلام {user.first_name if user.first_name else 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=dynamic_main_menu # استفاده از منوی پویا
        )
    except Exception as e:
        logger.error(f"خطا در ارسال تصویر خوش‌آمدگویی برای کاربر {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text(
            f"سلام {user.first_name if user.first_name else 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=dynamic_main_menu # استفاده از منوی پویا
        )
    return States.MAIN_MENU

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} در منوی اصلی گزینه '{text}' را انتخاب کرد.")
    
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str) # برای استفاده در صورت نیاز

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
            logger.info(f"کاربر {user_id_str} سن ({age}) و جنسیت ({gender}) را از دیتابیس دارد.")
            system_prompt = _prepare_doctor_system_prompt(age, gender)
            context.user_data["system_prompt_for_doctor"] = system_prompt
            context.user_data["doctor_chat_history"] = []
            
            await update.message.reply_text(
                f"مشخصات شما (سن: {age}، جنسیت: {gender}) از قبل در سیستم موجود است.\n"
                "اکنون می‌توانید سوال پزشکی خود را از دکتر تافته بپرسید.",
                reply_markup=DOCTOR_CONVERSATION_KEYBOARD
            )
            return States.DOCTOR_CONVERSATION
        else: 
            logger.info(f"سن یا جنسیت برای کاربر {user_id_str} در دیتابیس موجود نیست. درخواست سن.")
            await update.message.reply_text(
                "بسیار خب. برای اینکه بتوانم بهتر به شما کمک کنم، لطفاً سن خود را وارد کنید:",
                reply_markup=AGE_INPUT_KEYBOARD # کیبورد با گزینه بازگشت
            )
            return States.AWAITING_AGE
            
    elif text == "📦 راهنمای محصولات":
        # ... (بدون تغییر) ...
        keyboard = [[InlineKeyboardButton("مشاهده وب‌سایت تافته", url=URL_TAFTEH_WEBSITE)]]
        reply_markup_inline = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "برای مشاهده محصولات و وب‌سایت تافته، روی دکمه زیر کلیک کنید:",
            reply_markup=reply_markup_inline
        )
        return States.MAIN_MENU # در منوی اصلی باقی می‌ماند
        
    elif text == "⭐ عضویت در باشگاه مشتریان": # گزینه برای کاربران غیرعضو
        logger.info(f"کاربر {user_id_str} گزینه 'عضویت در باشگاه مشتریان' را انتخاب کرد.")
        await update.message.reply_text(
            "عضویت در باشگاه مشتریان تافته مزایای ویژه‌ای برای شما خواهد داشت! آیا مایل به عضویت هستید؟",
            reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD
        )
        return States.AWAITING_CLUB_JOIN_CONFIRMATION
        
    elif text == "👤 پروفایل من / باشگاه": # گزینه برای کاربران عضو
        logger.info(f"کاربر {user_id_str} گزینه 'پروفایل من / باشگاه' را انتخاب کرد.")
        return await my_profile_info_handler(update, context) # به نمایش پروفایل می‌رود و سپس به منوی اصلی بازمی‌گردد

    elif text == "📣 نکته سلامتی باشگاه": # گزینه برای کاربران عضو
        logger.info(f"کاربر {user_id_str} گزینه 'نکته سلامتی باشگاه' را انتخاب کرد.")
        return await health_tip_command_handler(update, context) # نمایش نکته و بازگشت به منوی اصلی

    else: 
        await update.message.reply_text(
            "گزینه انتخاب شده معتبر نیست. لطفاً یکی از گزینه‌های منو را انتخاب کنید.",
            reply_markup=dynamic_main_menu
        )
        return States.MAIN_MENU

async def request_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    if text == "🔙 بازگشت به منوی اصلی":
        return await start(update, context)

    age_text = text
    user = update.effective_user
    user_id_str = str(user.id)

    if not age_text.isdigit() or not (1 <= int(age_text) <= 120):
        await update.message.reply_text("❗️ لطفاً یک سن معتبر (عدد بین ۱ تا ۱۲۰) وارد کنید یا بازگردید.", reply_markup=AGE_INPUT_KEYBOARD)
        return States.AWAITING_AGE 

    context.user_data["age_temp"] = int(age_text) 
    logger.info(f"کاربر {user_id_str} سن موقت خود را {age_text} وارد کرد.")
    await update.message.reply_text("متشکرم. حالا لطفاً جنسیت خود را انتخاب کنید یا بازگردید:", reply_markup=GENDER_SELECTION_KEYBOARD)
    return States.AWAITING_GENDER

async def request_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    if text == "🔙 بازگشت به منوی اصلی":
        # اگر از اینجا به منوی اصلی بازگردد، سن موقت را پاک می‌کنیم
        if "age_temp" in context.user_data: del context.user_data["age_temp"]
        return await start(update, context)

    gender_input = text.strip() 
    user = update.effective_user
    user_id_str = str(user.id)
    
    age = context.user_data.pop("age_temp", None) 
    if not age:
        logger.error(f"خطا: سن موقت برای کاربر {user_id_str} یافت نشد. بازگشت به منوی اصلی.")
        await update.message.reply_text("مشکلی در پردازش اطلاعات پیش آمد. لطفاً دوباره از ابتدا شروع کنید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        return await start(update, context) 

    gender = gender_input 
    
    awarded_profile_points_and_badge = False
    if db: 
        try:
            user_profile_before_update = await asyncio.to_thread(get_user_profile_data, user_id_str)
            update_payload = {"age": age, "gender": gender}
            
            if user_profile_before_update and not user_profile_before_update.get('profile_completion_points_awarded', False):
                if (user_profile_before_update.get("age") is None or user_profile_before_update.get("gender") is None): # اگر قبلا ناقص بوده
                    update_payload["points"] = firestore.Increment(POINTS_FOR_PROFILE_COMPLETION)
                    update_payload["profile_completion_points_awarded"] = True
                    awarded_profile_points_and_badge = True
            
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            logger.info(f"سن ({age}) و جنسیت ({gender}) کاربر {user_id_str} در دیتابیس ذخیره/به‌روز شد.")

            if awarded_profile_points_and_badge:
                # ارسال پیام امتیاز و نشان پس از پیام اصلی برای جلوگیری از دو ReplyKeyboard همزمان
                # بنابراین، اینجا فقط لاگ می‌کنیم و در انتها یکجا ارسال می‌کنیم
                logger.info(f"کاربر {user_id_str} واجد شرایط دریافت امتیاز و نشان تکمیل پروفایل است.")

        except Exception as e:
            logger.error(f"خطا در ذخیره سن/جنسیت یا اعطای امتیاز/نشان پروفایل برای کاربر {user_id_str} در دیتابیس: {e}", exc_info=True)

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
    # پس از ارسال پیام اصلی، امتیاز و نشان را اطلاع‌رسانی کن (اگر واجد شرایط بود)
    if awarded_profile_points_and_badge:
        await notify_points_awarded(update, context, user_id_str, POINTS_FOR_PROFILE_COMPLETION, "تکمیل پروفایل (سن و جنسیت)")
        await award_badge_if_not_already_awarded(update, context, user_id_str, BADGE_PROFILE_COMPLETE)
        
    return States.DOCTOR_CONVERSATION

# --- کنترل کننده جدید برای تایید عضویت در باشگاه ---
async def handle_club_join_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    text = update.message.text
    logger.info(f"کاربر {user_id_str} به سوال عضویت در باشگاه پاسخ داد: '{text}'")
    
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str) # برای بازگشت به منو

    if text == "✅ بله، عضو می‌شوم":
        if not db:
            await update.message.reply_text("متاسفانه در حال حاضر امکان اتصال به سیستم باشگاه مشتریان وجود ندارد. لطفاً بعداً تلاش کنید.", reply_markup=dynamic_main_menu)
            return States.MAIN_MENU
        try:
            user_profile = await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
            if user_profile and user_profile.get('is_club_member', False):
                await update.message.reply_text("شما از قبل عضو باشگاه مشتریان تافته هستید! 🎉", reply_markup=dynamic_main_menu)
            else:
                await asyncio.to_thread(update_user_profile_data, user_id_str, 
                                        {"is_club_member": True, 
                                         "points": firestore.Increment(POINTS_FOR_JOINING_CLUB),
                                         "club_join_date": firestore.SERVER_TIMESTAMP})
                logger.info(f"کاربر {user_id_str} با موفقیت به باشگاه مشتریان پیوست و {POINTS_FOR_JOINING_CLUB} امتیاز دریافت کرد.")
                
                # پیام عضویت و امتیاز را جداگانه ارسال می‌کنیم
                await update.message.reply_text(
                    f"عضویت شما در باشگاه مشتریان تافته با موفقیت انجام شد! ✨\n"
                    "از این پس از مزایای ویژه اعضا بهره‌مند خواهید شد.",
                    reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str) # منوی به‌روز شده را بفرست
                )
                await notify_points_awarded(update, context, user_id_str, POINTS_FOR_JOINING_CLUB, "عضویت در باشگاه مشتریان")
                await award_badge_if_not_already_awarded(update, context, user_id_str, BADGE_CLUB_MEMBER)
        except Exception as e:
            logger.error(f"خطا در پردازش عضویت باشگاه برای کاربر {user_id_str}: {e}", exc_info=True)
            await update.message.reply_text("متاسفانه مشکلی در فرآیند عضویت شما پیش آمد.", reply_markup=dynamic_main_menu)
        
    elif text == "❌ خیر، فعلاً نه":
        await update.message.reply_text("متوجه شدم. هر زمان تمایل داشتید، می‌توانید از طریق منوی اصلی اقدام به عضویت کنید.", reply_markup=dynamic_main_menu)
    else: # اگر ورودی غیر از دکمه‌ها بود (نباید اتفاق بیفتد با فیلتر Regex)
        await update.message.reply_text("لطفاً یکی از گزینه‌های 'بله' یا 'خیر' را انتخاب کنید.", reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD)
        return States.AWAITING_CLUB_JOIN_CONFIRMATION # در همین حالت بماند

    return States.MAIN_MENU


async def doctor_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر) ...
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
            logger.error(f"DCH: Could not rebuild system prompt for user {user_id_str}. Age/Gender missing.")
            await update.message.reply_text("مشکلی در بازیابی اطلاعات شما پیش آمده. لطفاً از ابتدا با انتخاب 'دکتر تافته' شروع کنید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
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
    await update.message.reply_text(assistant_response, parse_mode="Markdown", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
    return States.DOCTOR_CONVERSATION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر) ...
    user = update.effective_user
    logger.info(f"User {user.id} called /cancel. Delegating to start handler for cleanup and main menu.")
    await update.message.reply_text("درخواست شما لغو شد. بازگشت به منوی اصلی...", reply_markup=ReplyKeyboardRemove())
    return await start(update, context) 

async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (بدون تغییر) ...
    user = update.effective_user
    logger.warning(f"--- GLOBAL FALLBACK Reached --- User: {user.id}, Text: '{update.message.text}', Current user_data: {context.user_data}")
    await update.message.reply_text(
        "متوجه نشدم چه گفتید. لطفاً از گزینه‌های منو استفاده کنید یا اگر در مرحله خاصی هستید، ورودی مورد انتظار را ارسال نمایید.",
        reply_markup= await get_dynamic_main_menu_keyboard(context, str(user.id)) # ارسال منوی پویا
    )


async def health_tip_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States: # بازگشت حالت برای سازگاری با main_menu_handler
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست نکته سلامتی باشگاه کرد.")

    if not db:
        await update.message.reply_text("متاسفانه در حال حاضر امکان دسترسی به سیستم باشگاه مشتریان وجود ندارد.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        return States.MAIN_MENU

    try:
        user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
        
        if user_profile and user_profile.get('is_club_member', False):
            tip = random.choice(HEALTH_TIPS_FOR_CLUB) 
            new_tip_usage_count = user_profile.get('club_tip_usage_count', 0) + 1
            update_payload = {"points": firestore.Increment(POINTS_FOR_CLUB_TIP), "club_tip_usage_count": new_tip_usage_count}
            
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            
            message_to_send = f"⚕️ **نکته سلامتی ویژه اعضای باشگاه تافته:**\n\n_{tip}_"
            await update.message.reply_text(message_to_send, parse_mode="Markdown", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            await notify_points_awarded(update, context, user_id_str, POINTS_FOR_CLUB_TIP, "مطالعه نکته سلامتی باشگاه")
            
            if new_tip_usage_count >= CLUB_TIP_BADGE_THRESHOLD:
                await award_badge_if_not_already_awarded(update, context, user_id_str, BADGE_HEALTH_EXPLORER)
        else:
            await update.message.reply_text("این بخش مخصوص اعضای باشگاه مشتریان تافته است. برای عضویت، لطفاً گزینه '⭐ عضویت در باشگاه مشتریان' را از منوی اصلی انتخاب کنید.", 
                                            reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            
    except Exception as e:
        logger.error(f"خطا در ارسال نکته سلامتی برای کاربر {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text("متاسفانه مشکلی در ارائه نکته سلامتی پیش آمد.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
    return States.MAIN_MENU


async def my_profile_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر نسبت به نسخه کامل قبلی، فقط reply_markup را پویا می‌کنیم) ...
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست اطلاعات پروفایل (نشان‌ها و امتیازات) را داد.")
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)

    if not db:
        await update.message.reply_text("متاسفانه در حال حاضر امکان دسترسی به اطلاعات پروفایل شما وجود ندارد.", reply_markup=dynamic_main_menu)
        return States.MAIN_MENU

    try:
        user_profile = await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        
        points = user_profile.get('points', 0)
        badges = user_profile.get('badges', [])
        is_member = user_profile.get('is_club_member', False)
        age = user_profile.get('age', 'ثبت نشده')
        gender = user_profile.get('gender', 'ثبت نشده')
        # نام و نام خانوادگی در فاز بعدی اضافه می‌شود
        name_first = user_profile.get('name_first_db', user_profile.get('first_name', 'ثبت نشده')) # استفاده از نام تلگرام اگر نام دیتابیس خالی است
        name_last = user_profile.get('name_last_db', 'ثبت نشده')


        reply_message = f"👤 **پروفایل شما در ربات تافته** 👤\n\n"
        reply_message += f"نام: {name_first} {name_last}\n"
        reply_message += f"سن: {age}\n"
        reply_message += f"جنسیت: {gender}\n\n"
        if is_member:
            reply_message += " عضویت باشگاه: ✅ فعال\n"
        else:
            reply_message += " عضویت باشگاه: ❌ غیرفعال (از منو عضو شوید)\n"
        
        reply_message += f" امتیاز شما: {points} 🌟\n"
        
        if badges:
            reply_message += "\nنشان‌های شما:\n"
            for badge in badges:
                reply_message += f"  - {badge}\n"
        else:
            reply_message += "\nشما هنوز هیچ نشانی دریافت نکرده‌اید."
            
        # در فاز بعدی، اینجا دکمه‌هایی برای "ویرایش نام" و "تکمیل پروفایل" اضافه خواهد شد.
        await update.message.reply_text(reply_message, parse_mode="Markdown", reply_markup=dynamic_main_menu)

    except Exception as e:
        logger.error(f"خطا در نمایش اطلاعات پروفایل برای کاربر {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text("متاسفانه مشکلی در نمایش اطلاعات پروفایل شما پیش آمد.", reply_markup=dynamic_main_menu)
    
    return States.MAIN_MENU


# توابع دیتابیس بدون تغییر زیاد نسبت به قبل، فقط اطمینان از فیلدهای جدید در get_or_create
# ... (get_or_create_user_profile, update_user_profile_data, get_user_profile_data - با فیلدهای name_first_db, name_last_db) ...
def get_or_create_user_profile(user_id: str, username: str = None, first_name: str = None) -> dict:
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Profile for user {user_id} will be in-memory mock.")
        return {"user_id": user_id, "username": username, "first_name": first_name, 
                "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [],
                "profile_completion_points_awarded": False, "club_tip_usage_count": 0, "club_join_date": None,
                "name_first_db": None, "name_last_db": None} # افزودن فیلدهای نام و نام خانوادگی

    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()

    default_fields = {
        'age': None, 'gender': None, 'is_club_member': False, 'points': 0, 'badges': [],
        'profile_completion_points_awarded': False, 'club_tip_usage_count': 0,
        'club_join_date': None, 'name_first_db': None, 'name_last_db': None
    }

    if user_doc.exists:
        logger.info(f"DB: پروفایل کاربر {user_id} از Firestore خوانده شد.")
        user_data = user_doc.to_dict()
        # اطمینان از وجود تمام فیلدهای پیش‌فرض جدید
        needs_update = False
        for key, default_value in default_fields.items():
            if key not in user_data:
                user_data[key] = default_value
                needs_update = True 
        
        if needs_update: # اگر فیلد جدیدی به ساختار پیش‌فرض اضافه شده باشد، پروفایل کاربر را در دیتابیس آپدیت کن
             logger.info(f"DB: به‌روزرسانی پروفایل کاربر {user_id} با فیلدهای پیش‌فرض جدید در زمان خواندن.")
             # فقط فیلدهایی که وجود نداشتند را اضافه می‌کنیم
             update_payload = {k:v for k,v in default_fields.items() if k not in user_doc.to_dict()}
             if update_payload: # فقط اگر چیزی برای آپدیت وجود داشت
                user_ref.update(update_payload)
        return user_data
    else:
        logger.info(f"DB: پروفایل جدیدی برای کاربر {user_id} در Firestore ایجاد می‌شود.")
        user_data = {
            'user_id': user_id,
            'username': username if username else None,
            'first_name': first_name if first_name else None, 
            'registration_date': firestore.SERVER_TIMESTAMP,
            'last_interaction_date': firestore.SERVER_TIMESTAMP
        }
        for key, default_value in default_fields.items():
            user_data[key] = default_value
        
        user_ref.set(user_data)
        return user_data

def get_user_profile_data(user_id: str) -> dict | None:
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Cannot get profile for user {user_id}.")
        return None
        
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        defaults = { # اطمینان از وجود همه کلیدها هنگام خواندن
            'is_club_member': False, 'points': 0, 'badges': [],
            'profile_completion_points_awarded': False, 'club_tip_usage_count': 0,
            'club_join_date': None, 'age': None, 'gender': None,
            'name_first_db': None, 'name_last_db': None
        }
        for key, default_value in defaults.items():
            if key not in user_data:
                user_data[key] = default_value
        return user_data
    return None


# --- Flask App & Main Execution ---
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check():
    logger.info("درخواست Health Check به اندپوینت '/' Flask دریافت شد.")
    return 'ربات تلگرام تافته فعال است و به پورت گوش می‌دهد!', 200

def run_flask_app():
    # ... (بدون تغییر) ...
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"ترد Flask: در حال تلاش برای شروع وب سرور روی هاست 0.0.0.0 و پورت {port}")
    try:
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
        logger.info(f"ترد Flask: وب سرور Flask روی پورت {port} متوقف شد.")
    except Exception as e:
        logger.error(f"ترد Flask: خطایی در اجرای وب سرور Flask رخ داد: {e}", exc_info=True)


if __name__ == '__main__':
    logger.info("بلوک اصلی برنامه (__name__ == '__main__') شروع شد.")
    
    if db is None:
        logger.warning("*"*65)
        logger.warning("* دیتابیس Firestore مقداردهی اولیه نشده است!                     *")
        logger.warning("* ربات با قابلیت‌های محدود (بدون ذخیره دائمی اطلاعات) اجرا می‌شود. *")
        logger.warning("* لطفاً تنظیمات Firebase و فایل کلید را بررسی کنید.                *")
        logger.warning("*"*65)

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
                MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات|⭐ عضویت در باشگاه مشتریان|👤 پروفایل من / باشگاه|📣 نکته سلامتی باشگاه)$"), main_menu_handler),
            ],
            States.AWAITING_AGE: [
                MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), # گزینه بازگشت
                MessageHandler(filters.TEXT & ~filters.COMMAND, request_age_handler)
            ],
            States.AWAITING_GENDER: [
                MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), # گزینه بازگشت
                MessageHandler(filters.Regex("^(زن|مرد)$"), request_gender_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, 
                               lambda update, context: update.message.reply_text("لطفاً یکی از گزینه‌های «زن» یا «مرد» را با دکمه انتخاب کنید یا بازگردید.", reply_markup=GENDER_SELECTION_KEYBOARD))
            ],
            States.DOCTOR_CONVERSATION: [
                MessageHandler(filters.Regex("^(❓ سوال جدید از دکتر|🔙 بازگشت به منوی اصلی)$"), doctor_conversation_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, doctor_conversation_handler)
            ],
            States.AWAITING_CLUB_JOIN_CONFIRMATION: [ # حالت جدید برای تایید عضویت
                MessageHandler(filters.Regex("^(✅ بله، عضو می‌شوم|❌ خیر، فعلاً نه)$"), handle_club_join_confirmation),
                MessageHandler(filters.TEXT & ~filters.COMMAND, 
                               lambda update, context: update.message.reply_text("لطفاً با استفاده از دکمه‌ها پاسخ دهید.", reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD))
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel), # cancel همیشه به start (با حفظ پروفایل) می‌رود
            CommandHandler("start", start), 
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), # فال‌بک عمومی برای دکمه بازگشت
        ],
        persistent=False,
        name="main_conversation"
    )

    # حذف فرمان /joinclub چون از طریق منو انجام می‌شود
    # telegram_application.add_handler(CommandHandler("joinclub", join_club_command_handler)) 
    # حذف فرمان /clubstatus چون با /myprofile و دکمه منو جایگزین شده
    # telegram_application.add_handler(CommandHandler("clubstatus", my_profile_info_handler)) 
    telegram_application.add_handler(CommandHandler("myprofile", my_profile_info_handler)) 
    # فرمان /clubtip باقی می‌ماند اما از منو هم قابل دسترسی است
    telegram_application.add_handler(CommandHandler("clubtip", health_tip_command_handler)) 
    
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