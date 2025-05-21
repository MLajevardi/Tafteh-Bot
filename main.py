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
_initial_fb_logger = logging.getLogger("FIREBASE_INIT_LOGGER")
_initial_fb_logger.setLevel(logging.INFO)
_fb_handler = logging.StreamHandler()
_fb_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
_fb_handler.setFormatter(_fb_formatter)
if not _initial_fb_logger.hasHandlers():
    _initial_fb_logger.addHandler(_fb_handler)
try:
    cred_path_render = os.getenv("FIREBASE_CREDENTIALS_PATH", "/etc/secrets/firebase-service-account-key.json")
    cred_path_local = "firebase-service-account-key.json"
    cred_path = cred_path_render if os.path.exists(cred_path_render) else cred_path_local
    if not os.path.exists(cred_path):
        _initial_fb_logger.warning(f"فایل کلید Firebase در مسیر '{cred_path}' یافت نشد.")
    else:
        cred = credentials.Certificate(cred_path)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        _initial_fb_logger.info("Firebase Admin SDK با موفقیت مقداردهی اولیه شد.")
except Exception as e:
    _initial_fb_logger.error(f"خطای بحرانی در مقداردهی اولیه Firebase: {e}", exc_info=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
    force=True
)
logger = logging.getLogger(__name__)
logger.info("اسکریپت main.py شروع به کار کرد...")

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "openai/gpt-3.5-turbo")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg")
URL_TAFTEH_WEBSITE = "https://tafteh.ir/"

POINTS_FOR_JOINING_CLUB = 50
# POINTS_FOR_PROFILE_COMPLETION (سن و جنسیت) حذف شد
POINTS_FOR_FULL_PROFILE_COMPLETION = 30 # امتیاز جدید برای تکمیل نام، نام خانوادگی، سن و جنسیت
# POINTS_FOR_CLUB_TIP حذف شد

BADGE_CLUB_MEMBER = "عضو باشگاه تافته 🏅"
# BADGE_PROFILE_COMPLETE (سن و جنسیت) حذف شد
BADGE_FULL_PROFILE = "پروفایل کامل طلایی ✨" # برای نام، نام خانوادگی، سن و جنسیت
BADGE_HEALTH_EXPLORER = "کاشف سلامت 🧭"
CLUB_TIP_BADGE_THRESHOLD = 3

if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    logger.error("!!! بحرانی: توکن‌های ضروری ربات یا API یافت نشدند.")
    exit(1)
else:
    logger.info("توکن ربات و کلید API با موفقیت بارگذاری شدند.")

class States(Enum):
    MAIN_MENU = 1
    AWAITING_PROFILE_FIRST_NAME = 10 # شروع جریان تکمیل پروفایل کامل
    AWAITING_PROFILE_LAST_NAME = 11
    AWAITING_PROFILE_AGE = 12
    AWAITING_PROFILE_GENDER = 13
    AWAITING_DOCTOR_AGE = 2 # قبلا AWAITING_AGE بود، برای تفکیک تغییر نام دادیم
    AWAITING_DOCTOR_GENDER = 3 # قبلا AWAITING_GENDER بود
    DOCTOR_CONVERSATION = 4
    AWAITING_CLUB_JOIN_CONFIRMATION = 5
    PROFILE_VIEW = 6
    AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION = 7
    AWAITING_EDIT_FIRST_NAME = 8 # قبلا AWAITING_FIRST_NAME بود
    AWAITING_EDIT_LAST_NAME = 9  # قبلا AWAITING_LAST_NAME بود


# --- تعریف کیبوردها ---
DOCTOR_CONVERSATION_KEYBOARD = ReplyKeyboardMarkup(
    [["❓ سوال جدید از دکتر"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True
)
# کیبورد برای تمام مراحل ورود اطلاعات پروفایل (نام، نام خانوادگی، سن، جنسیت)
PROFILE_INPUT_BACK_KEYBOARD = ReplyKeyboardMarkup(
    [["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True, one_time_keyboard=True
)
# کیبورد برای مرحله انتخاب جنسیت در تکمیل پروفایل
PROFILE_GENDER_SELECTION_KEYBOARD = ReplyKeyboardMarkup(
    [["زن"], ["مرد"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True, one_time_keyboard=True
)
CLUB_JOIN_CONFIRMATION_KEYBOARD = ReplyKeyboardMarkup(
    [["✅ بله، عضو می‌شوم"], ["❌ خیر، فعلاً نه"]], resize_keyboard=True, one_time_keyboard=True
)
PROFILE_VIEW_KEYBOARD = ReplyKeyboardMarkup(
    [["✏️ ویرایش نام"], ["💔 لغو عضویت از باشگاه"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True # "تکمیل نام" به "ویرایش نام" تغییر کرد
)
CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD = ReplyKeyboardMarkup(
    [["✅ بله، عضویتم لغو شود"], ["❌ خیر، منصرف شدم"]], resize_keyboard=True, one_time_keyboard=True
)
NAME_EDIT_BACK_KEYBOARD = ReplyKeyboardMarkup( # کیبورد برای انصراف از ویرایش نام
    [["🔙 انصراف و بازگشت به پروفایل"]], resize_keyboard=True, one_time_keyboard=True
)

HEALTH_TIPS_FOR_CLUB = [
    "روزانه حداقل ۸ لیوان آب بنوشید تا بدنتان هیدراته بماند.",
    "خواب کافی (۷-۸ ساعت) برای بازیابی انرژی و سلامت روان ضروری است.",
    "حداقل ۳۰ دقیقه فعالیت بدنی متوسط در بیشتر روزهای هفته به حفظ سلامت قلب کمک می‌کند."
]

# --- توابع دیتابیس ---
def get_or_create_user_profile(user_id: str, username: str = None, first_name: str = None) -> dict:
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Profile for user {user_id} will be in-memory mock.")
        return {"user_id": user_id, "username": username, "first_name": first_name, "age": None, "gender": None, 
                "is_club_member": False, "points": 0, "badges": [], "club_tip_usage_count": 0, 
                "club_join_date": None, "name_first_db": None, "name_last_db": None, 
                "full_profile_completion_points_awarded": False} # نام فیلد امتیاز تغییر کرد

    user_ref = db.collection('users').document(user_id)
    try: user_doc = user_ref.get()
    except Exception as e:
        logger.error(f"DB: خطا هنگام get() برای کاربر {user_id}: {e}", exc_info=True)
        return {"user_id": user_id, "username": username, "first_name": first_name, "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [], "club_tip_usage_count": 0, "club_join_date": None, "name_first_db": None, "name_last_db": None, "full_profile_completion_points_awarded": False}

    default_fields = {'age': None, 'gender': None, 'is_club_member': False, 'points': 0, 'badges': [], 
                      'club_tip_usage_count': 0, 'club_join_date': None, 'name_first_db': None, 
                      'name_last_db': None, 'full_profile_completion_points_awarded': False}

    if user_doc.exists:
        user_data = user_doc.to_dict()
        needs_update_in_db = False
        for key, default_value in default_fields.items():
            if key not in user_data:
                user_data[key] = default_value
                needs_update_in_db = True
        if needs_update_in_db:
             update_payload = {k:v for k,v in default_fields.items() if k not in user_doc.to_dict()}
             if update_payload:
                try: user_ref.update(update_payload)
                except Exception as e_update: logger.error(f"DB: خطا در آپدیت فیلدهای پیش فرض برای کاربر {user_id}: {e_update}")
        return user_data
    else:
        user_data = {'user_id': user_id, 'username': username, 'first_name': first_name, 
                     'registration_date': firestore.SERVER_TIMESTAMP, 'last_interaction_date': firestore.SERVER_TIMESTAMP}
        for key, default_value in default_fields.items(): user_data[key] = default_value
        try: user_ref.set(user_data)
        except Exception as e_set: logger.error(f"DB: خطا در ایجاد پروفایل جدید برای کاربر {user_id}: {e_set}")
        return user_data

def update_user_profile_data(user_id: str, data_to_update: dict) -> None:
    # ... (بدون تغییر)
    if not db: return
    user_ref = db.collection('users').document(user_id)
    data_to_update['last_updated_date'] = firestore.SERVER_TIMESTAMP
    try:
        user_ref.update(data_to_update)
        logger.info(f"DB: پروفایل کاربر {user_id} با داده‌های {data_to_update} در Firestore به‌روز شد.")
    except Exception as e:
        logger.error(f"DB: خطا در به‌روزرسانی پروفایل کاربر {user_id} با داده‌های {data_to_update}: {e}", exc_info=True)


def get_user_profile_data(user_id: str) -> dict | None:
    # ... (با فیلد full_profile_completion_points_awarded)
    if not db: return None
    user_ref = db.collection('users').document(user_id)
    try:
        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            defaults = {'is_club_member': False, 'points': 0, 'badges': [], 'club_tip_usage_count': 0, 
                        'club_join_date': None, 'age': None, 'gender': None, 'name_first_db': None, 
                        'name_last_db': None, 'full_profile_completion_points_awarded': False}
            for key, default_value in defaults.items():
                if key not in user_data: user_data[key] = default_value
            return user_data
    except Exception as e:
        logger.error(f"DB: خطا در خواندن پروفایل کاربر {user_id}: {e}", exc_info=True)
    return None


# --- توابع کمکی ربات ---
async def ask_openrouter(system_prompt: str, chat_history: list, model_override: str = None) -> str:
    # ... (بدون تغییر)
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    messages_payload = [{"role": "system", "content": system_prompt}] + chat_history
    current_model = model_override if model_override else OPENROUTER_MODEL_NAME
    body = {"model": current_model, "messages": messages_payload, "temperature": 0.5}
    logger.info(f"آماده‌سازی درخواست OpenRouter. مدل: {current_model}, تاریخچه: {len(chat_history)}.")
    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            if data.get("choices") and data["choices"][0].get("message") and data["choices"][0]["message"].get("content"):
                llm_response_content = data["choices"][0]["message"]["content"].strip()
                logger.info(f"پاسخ LLM ({current_model}): '{llm_response_content}'")
                return llm_response_content
            logger.error(f"ساختار پاسخ OpenRouter ({current_model}) نامعتبر: {data}")
            return "❌ مشکلی در پردازش پاسخ از سرویس هوش مصنوعی رخ داد."
        except Exception as e:
            logger.error(f"خطا در ارتباط OpenRouter ({current_model}): {e}", exc_info=True)
            return "❌ بروز خطا در ارتباط با سرویس هوش مصنوعی."

def _prepare_doctor_system_prompt(age: int, gender: str) -> str:
    # پرامپت اصلاح شده و کوتاه‌تر برای دکتر تافته
    return (
        f"شما 'دکتر تافته'، یک پزشک عمومی متخصص، دقیق و با حوصله هستید. کاربری که با شما صحبت می‌کند {age} ساله و {gender} است. "
        "وظیفه اصلی شما پاسخگویی دقیق و علمی به سوالات پزشکی کاربر به زبان فارسی ساده و قابل فهم است. "
        "اگر سوال کاربر برای ارائه پاسخ کامل و ایمن، نیاز به اطلاعات بیشتری داشت، **فقط یک یا دو سوال کوتاه، کلیدی و کاملاً مرتبط بپرسید** تا جزئیات لازم را کسب کنید. از پرسیدن سوالات غیرضروری یا لیست بلند سوالات خودداری کنید. "
        "پس از دریافت اطلاعات کافی، راهنمایی پزشکی عمومی و اولیه ارائه دهید. **هرگز تشخیص قطعی ندهید یا دارو تجویز نکنید.** "
        "همیشه تاکید کنید که برای تشخیص قطعی و درمان تخصصی، باید به پزشک مراجعه کنند، خصوصاً اگر علائم شدید یا طولانی‌مدت هستند. "
        "اگر سوالی کاملاً غیرپزشکی بود (مانند آشپزی، تاریخ و ...)، با احترام و با این عبارت پاسخ دهید: 'متاسفم، من یک ربات پزشک هستم و فقط می‌توانم به سوالات مرتبط با حوزه پزشکی پاسخ دهم. چطور می‌توانم در زمینه پزشکی به شما کمک کنم؟' "
        "در تمامی پاسخ‌های خود، مستقیماً به سراغ اصل مطلب بروید و از مقدمات غیرضروری (مانند 'بله'، 'خب') پرهیز کنید. "
        "لحن شما باید حرفه‌ای، همدلانه و محترمانه باشد، اما از عبارات بیش از حد احساسی یا شعاری خودداری کنید."
    )

async def notify_points_awarded(bot: Application.bot, chat_id: int, user_id_str: str, points_awarded: int, reason: str):
    # ... (بدون تغییر)
    if not db: return
    try:
        await asyncio.to_thread(get_or_create_user_profile, user_id_str) 
        user_profile_updated = await asyncio.to_thread(get_user_profile_data, user_id_str)
        total_points = user_profile_updated.get('points', 0) if user_profile_updated else points_awarded
        message = f"✨ شما {points_awarded} امتیاز برای '{reason}' دریافت کردید!\nمجموع امتیاز شما اکنون: {total_points} است. 🌟"
        await bot.send_message(chat_id=chat_id, text=message)
        logger.info(f"اطلاع‌رسانی امتیاز به {user_id_str} برای '{reason}'. امتیاز: {points_awarded}, مجموع: {total_points}")
    except Exception as e:
        logger.error(f"خطا در اطلاع‌رسانی امتیاز به {user_id_str}: {e}", exc_info=True)

async def award_badge_if_not_already_awarded(bot: Application.bot, chat_id: int, user_id_str: str, badge_name: str):
    # ... (بدون تغییر)
    if not db: return
    try:
        user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
        if user_profile:
            current_badges = user_profile.get('badges', [])
            if badge_name not in current_badges:
                await asyncio.to_thread(update_user_profile_data, user_id_str, {'badges': firestore.ArrayUnion([badge_name])})
                await bot.send_message(chat_id=chat_id, text=f"🏆 تبریک! شما نشان '{badge_name}' را دریافت کردید!")
                logger.info(f"نشان '{badge_name}' به کاربر {user_id_str} اعطا شد.")
    except Exception as e:
        logger.error(f"خطا در اعطای نشان '{badge_name}' به {user_id_str}: {e}", exc_info=True)


async def get_dynamic_main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE, user_id_str: str) -> ReplyKeyboardMarkup:
    # ... (بدون تغییر)
    is_member = False
    if 'is_club_member_cached' in context.user_data:
        is_member = context.user_data['is_club_member_cached']
    elif db:
        try:
            user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
            is_member = user_profile.get('is_club_member', False) if user_profile else False
            context.user_data['is_club_member_cached'] = is_member
        except Exception as e:
            logger.error(f"خطا در خواندن وضعیت عضویت کاربر {user_id_str} (get_dynamic_main_menu): {e}")
            is_member = False 
    else: context.user_data['is_club_member_cached'] = False
    if is_member:
        keyboard_layout = [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"], ["👤 پروفایل و باشگاه"], ["📣 نکته سلامتی باشگاه"]]
    else:
        keyboard_layout = [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"], ["⭐ عضویت در باشگاه تافته"]]
    return ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True)

# --- کنترل‌کننده‌های اصلی ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (منطق پاکسازی user_data و ارسال منوی پویا بدون تغییر) ...
    user = update.effective_user
    user_id_str = str(user.id) 
    message_prefix = "درخواست لغو شما انجام شد. " if context.user_data.get('_is_cancel_flow', False) else ""
    if context.user_data.get('_is_cancel_flow', False): del context.user_data['_is_cancel_flow']
    logger.info(f"کاربر {user_id_str} ({user.full_name or user.username}) /start یا بازگشت/لغو به منوی اصلی.")
    keys_to_clear_from_session = ["doctor_chat_history", "system_prompt_for_doctor", "age_temp", "is_club_member_cached", "awaiting_field_to_edit", "temp_first_name", "profile_completion_flow_active", "club_join_after_profile_flow"]
    for key in keys_to_clear_from_session:
        if key in context.user_data: del context.user_data[key]
    logger.info(f"اطلاعات جلسه (user_data) برای کاربر {user_id_str} پاکسازی شد.")
    if db: 
        try: await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        except Exception as e: logger.error(f"خطا در get_or_create_user_profile (start) برای کاربر {user_id_str}: {e}", exc_info=True)
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)
    welcome_message_text = f"سلام {user.first_name or 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:"
    if message_prefix: welcome_message_text = message_prefix + "به منوی اصلی بازگشتید."
    effective_chat_id = update.effective_chat.id
    try:
        is_direct_start_command = update.message and update.message.text == "/start"
        is_photo_present_in_message = hasattr(update.message, 'photo') and update.message.photo is not None
        if is_direct_start_command and not is_photo_present_in_message : 
            await context.bot.send_photo(chat_id=effective_chat_id, photo=WELCOME_IMAGE_URL, caption=welcome_message_text, reply_markup=dynamic_main_menu)
        else: 
            await context.bot.send_message(chat_id=effective_chat_id, text=welcome_message_text, reply_markup=dynamic_main_menu)
    except Exception as e:
        logger.error(f"خطا در ارسال پیام خوش‌آمدگویی برای {user_id_str}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=effective_chat_id, text=welcome_message_text, reply_markup=dynamic_main_menu)
    return States.MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر) ...
    user = update.effective_user
    user_id = user.id if user else "Unknown"
    logger.info(f"User {user_id} called /cancel. Delegating to start handler.")
    context.user_data['_is_cancel_flow'] = True
    if update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="درخواست شما لغو شد. بازگشت به منوی اصلی...", reply_markup=ReplyKeyboardRemove())
    return await start(update, context)


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} در منوی اصلی گزینه '{text}' را انتخاب کرد.")
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)

    if text == "👨‍⚕️ دکتر تافته":
        context.user_data['profile_completion_flow_active'] = True # برای دکتر هم نیاز به پروفایل پایه داریم
        context.user_data['club_join_after_profile_flow'] = False # از مسیر دکتر آمده، نه عضویت باشگاه
        
        age, gender, name_first, name_last = None, None, None, None
        if db:
            try:
                user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
                if user_profile:
                    age = user_profile.get("age")
                    gender = user_profile.get("gender")
                    name_first = user_profile.get("name_first_db")
                    name_last = user_profile.get("name_last_db")
            except Exception as e:
                logger.error(f"خطا در خواندن پروفایل کاربر {user_id_str} (دکتر تافته): {e}", exc_info=True)

        if age and gender and name_first and name_last: # اگر پروفایل کامل است
            logger.info(f"کاربر {user_id_str} پروفایل کامل دارد. مستقیم به مکالمه با دکتر.")
            system_prompt = _prepare_doctor_system_prompt(age, gender)
            context.user_data["system_prompt_for_doctor"] = system_prompt
            context.user_data["doctor_chat_history"] = []
            await update.message.reply_text(
                f"مشخصات شما (نام: {name_first} {name_last}, سن: {age}، جنسیت: {gender}) از قبل موجود است.\n"
                "اکنون می‌توانید سوال پزشکی خود را از دکتر تافته بپرسید.",
                reply_markup=DOCTOR_CONVERSATION_KEYBOARD
            )
            return States.DOCTOR_CONVERSATION
        else: # اگر پروفایل کامل نیست، به ترتیب سوال شود
            logger.info(f"پروفایل کاربر {user_id_str} کامل نیست. شروع فرآیند تکمیل پروفایل.")
            await update.message.reply_text("برای استفاده بهینه از خدمات، لطفاً ابتدا پروفایل خود را تکمیل کنید.\nلطفاً نام کوچک خود را وارد کنید:",
                                            reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
            return States.AWAITING_PROFILE_FIRST_NAME
            
    elif text == "📦 راهنمای محصولات":
        # ... (بدون تغییر) ...
        keyboard = [[InlineKeyboardButton("مشاهده وب‌سایت تافته", url=URL_TAFTEH_WEBSITE)]]
        reply_markup_inline = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("برای مشاهده محصولات و وب‌سایت تافته...", reply_markup=reply_markup_inline)
        return States.MAIN_MENU
        
    elif text == "⭐ عضویت در باشگاه تافته": # برای کاربران غیرعضو
        logger.info(f"کاربر {user_id_str} گزینه 'عضویت در باشگاه تافته' را انتخاب کرد.")
        age, gender, name_first, name_last = None, None, None, None
        if db:
            user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
            if user_profile:
                age = user_profile.get("age")
                gender = user_profile.get("gender")
                name_first = user_profile.get("name_first_db")
                name_last = user_profile.get("name_last_db")
        
        if not (age and gender and name_first and name_last): # اگر پروفایل کامل نیست
            logger.info(f"کاربر {user_id_str} برای عضویت نیاز به تکمیل پروفایل کامل دارد.")
            context.user_data['profile_completion_flow_active'] = True
            context.user_data['club_join_after_profile_flow'] = True # پس از تکمیل پروفایل، به عضویت باشگاه برو
            await update.message.reply_text(
                "برای عضویت در باشگاه، ابتدا باید پروفایل خود را تکمیل کنید.\n"
                "لطفاً نام کوچک خود را وارد کنید:",
                reply_markup=PROFILE_INPUT_BACK_KEYBOARD
            )
            return States.AWAITING_PROFILE_FIRST_NAME
        else: # پروفایل کامل است، پس سوال برای تایید عضویت
            await update.message.reply_text(
                "عضویت در باشگاه مشتریان تافته مزایای ویژه‌ای برای شما خواهد داشت! آیا مایل به عضویت هستید؟",
                reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD
            )
            return States.AWAITING_CLUB_JOIN_CONFIRMATION
        
    elif text == "👤 پروفایل و باشگاه": 
        return await my_profile_info_handler(update, context)
    elif text == "📣 نکته سلامتی باشگاه": 
        return await health_tip_command_handler(update, context)
    else: 
        await update.message.reply_text("گزینه انتخاب شده معتبر نیست.", reply_markup=dynamic_main_menu)
        return States.MAIN_MENU

# --- کنترل‌کننده‌های جدید برای جریان تکمیل پروفایل کامل ---
async def awaiting_profile_first_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text.strip()
    user = update.effective_user
    user_id_str = str(user.id)

    if text == "🔙 بازگشت به منوی اصلی":
        logger.info(f"کاربر {user_id_str} از تکمیل پروفایل (نام کوچک) انصراف داد و به منوی اصلی بازگشت.")
        if 'profile_completion_flow_active' in context.user_data: del context.user_data['profile_completion_flow_active']
        if 'club_join_after_profile_flow' in context.user_data: del context.user_data['club_join_after_profile_flow']
        return await start(update, context)

    if not text or len(text) < 2 or len(text) > 50:
        await update.message.reply_text("نام وارد شده معتبر نیست. لطفاً نام صحیح خود را وارد کنید یا بازگردید.", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
        return States.AWAITING_PROFILE_FIRST_NAME
    
    context.user_data['temp_profile_first_name'] = text
    logger.info(f"کاربر {user_id_str} نام کوچک موقت '{text}' را برای پروفایل وارد کرد.")
    await update.message.reply_text("متشکرم. حالا لطفاً نام خانوادگی خود را وارد کنید (یا بازگردید):", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
    return States.AWAITING_PROFILE_LAST_NAME

async def awaiting_profile_last_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text.strip()
    user = update.effective_user
    user_id_str = str(user.id)

    if text == "🔙 بازگشت به منوی اصلی":
        logger.info(f"کاربر {user_id_str} از تکمیل پروفایل (نام خانوادگی) انصراف داد و به منوی اصلی بازگشت.")
        if 'temp_profile_first_name' in context.user_data: del context.user_data['temp_profile_first_name']
        if 'profile_completion_flow_active' in context.user_data: del context.user_data['profile_completion_flow_active']
        if 'club_join_after_profile_flow' in context.user_data: del context.user_data['club_join_after_profile_flow']
        return await start(update, context)

    if not text or len(text) < 2 or len(text) > 50:
        await update.message.reply_text("نام خانوادگی وارد شده معتبر نیست. لطفاً نام خانوادگی صحیح خود را وارد کنید یا بازگردید.", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
        return States.AWAITING_PROFILE_LAST_NAME

    context.user_data['temp_profile_last_name'] = text
    logger.info(f"کاربر {user_id_str} نام خانوادگی موقت '{text}' را برای پروفایل وارد کرد.")
    await update.message.reply_text("عالی! حالا لطفاً سن خود را به عدد وارد کنید (یا بازگردید):", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
    return States.AWAITING_PROFILE_AGE

async def awaiting_profile_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text.strip()
    if text == "🔙 بازگشت به منوی اصلی":
        logger.info(f"User {update.effective_user.id} returned to main menu from AWAITING_PROFILE_AGE.")
        for key in ['temp_profile_first_name', 'temp_profile_last_name', 'profile_completion_flow_active', 'club_join_after_profile_flow']:
            if key in context.user_data: del context.user_data[key]
        return await start(update, context)
    
    age_text = text
    user = update.effective_user
    user_id_str = str(user.id)
    if not age_text.isdigit() or not (1 <= int(age_text) <= 120):
        await update.message.reply_text("❗️ لطفاً یک سن معتبر (عدد بین ۱ تا ۱۲۰) وارد کنید یا بازگردید.", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
        return States.AWAITING_PROFILE_AGE
    
    context.user_data["temp_profile_age"] = int(age_text)
    logger.info(f"کاربر {user_id_str} سن موقت خود را {age_text} برای پروفایل وارد کرد.")
    await update.message.reply_text("بسیار خوب. در نهایت، لطفاً جنسیت خود را انتخاب کنید (یا بازگردید):", reply_markup=PROFILE_GENDER_SELECTION_KEYBOARD)
    return States.AWAITING_PROFILE_GENDER

async def awaiting_profile_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text.strip()
    if text == "🔙 بازگشت به منوی اصلی":
        logger.info(f"User {update.effective_user.id} returned to main menu from AWAITING_PROFILE_GENDER.")
        for key in ['temp_profile_first_name', 'temp_profile_last_name', 'temp_profile_age', 'profile_completion_flow_active', 'club_join_after_profile_flow']:
            if key in context.user_data: del context.user_data[key]
        return await start(update, context)

    gender_input = text
    user = update.effective_user
    user_id_str = str(user.id)

    first_name = context.user_data.pop('temp_profile_first_name', None)
    last_name = context.user_data.pop('temp_profile_last_name', None)
    age = context.user_data.pop('temp_profile_age', None)
    
    if not (first_name and last_name and age):
        logger.error(f"خطا: اطلاعات ناقص پروفایل برای کاربر {user_id_str} در مرحله نهایی تکمیل.")
        await update.message.reply_text("مشکلی در گردآوری اطلاعات پروفایل شما پیش آمد. لطفاً از ابتدا تلاش کنید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        return await start(update, context)
    
    gender = gender_input
    awarded_full_profile_points_badge = False
    if db:
        try:
            user_profile_before_update = await asyncio.to_thread(get_user_profile_data, user_id_str)
            update_payload = {"name_first_db": first_name, "name_last_db": last_name, "age": age, "gender": gender}
            
            if user_profile_before_update and not user_profile_before_update.get('profile_name_completion_points_awarded', False): # اگر قبلا امتیاز تکمیل نام را نگرفته
                update_payload["points"] = firestore.Increment(POINTS_FOR_NAME_COMPLETION) # اینجا POINTS_FOR_FULL_PROFILE_COMPLETION
                update_payload["profile_name_completion_points_awarded"] = True # این پرچم برای نام و نام خانوادگی
                awarded_full_profile_points_badge = True
            
            # بررسی و اعطای امتیاز برای اولین تکمیل سن و جنسیت (اگر قبلا داده نشده)
            if user_profile_before_update and not user_profile_before_update.get('profile_completion_points_awarded', False):
                if (user_profile_before_update.get("age") is None or user_profile_before_update.get("gender") is None):
                    if "points" not in update_payload: update_payload["points"] = firestore.Increment(POINTS_FOR_PROFILE_COMPLETION)
                    else: update_payload["points"] = firestore.Increment(POINTS_FOR_PROFILE_COMPLETION + POINTS_FOR_NAME_COMPLETION if awarded_full_profile_points_and_badge else POINTS_FOR_PROFILE_COMPLETION) # تجمیع امتیاز
                    update_payload["profile_completion_points_awarded"] = True
                    # نشان پروفایل پایه هم اینجا داده می‌شود اگر قبلا داده نشده
                    # awarded_full_profile_points_badge به این معنی است که هر دو نوع تکمیل انجام شده


            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            logger.info(f"پروفایل کامل کاربر {user_id_str} (نام: {first_name} {last_name}, سن: {age}, جنسیت: {gender}) در دیتابیس ذخیره شد.")
            
            if awarded_full_profile_points_badge: # اگر امتیاز تکمیل نام داده شد
                 logger.info(f"کاربر {user_id_str} واجد شرایط دریافت امتیاز و نشان تکمیل پروفایل کامل است.")
            elif awarded_profile_points_and_badge: # اگر فقط امتیاز تکمیل سن/جنسیت داده شد
                 logger.info(f"کاربر {user_id_str} واجد شرایط دریافت امتیاز و نشان تکمیل پروفایل پایه است.")


        except Exception as e:
            logger.error(f"خطا در ذخیره پروفایل کامل یا اعطای امتیاز/نشان برای {user_id_str}: {e}", exc_info=True)

    # ذخیره در user_data برای استفاده در همین جلسه اگر لازم شد (مثلا برای دکتر تافته)
    context.user_data["age"] = age 
    context.user_data["gender"] = gender
    context.user_data["name_first_db"] = first_name
    context.user_data["name_last_db"] = last_name


    await update.message.reply_text(
        f"✅ پروفایل شما با موفقیت تکمیل شد:\n"
        f"نام: {first_name} {last_name}\n"
        f"سن: {age}\n"
        f"جنسیت: {gender}",
        reply_markup=ReplyKeyboardRemove() # حذف کیبورد قبلی
    )

    if awarded_full_profile_points_badge: # اگر امتیاز تکمیل نام داده شد، نشان کامل هم داده می‌شود
        await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_NAME_COMPLETION + (POINTS_FOR_PROFILE_COMPLETION if not user_profile_before_update.get('profile_completion_points_awarded') else 0), "تکمیل کامل پروفایل")
        await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_FULL_PROFILE)
        if not user_profile_before_update.get('profile_completion_points_awarded'): # اگر نشان پروفایل پایه هم نگرفته بود
             await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_PROFILE_COMPLETE)
    elif awarded_profile_points_and_badge: # اگر فقط امتیاز تکمیل سن/جنسیت داده شد
        await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_PROFILE_COMPLETION, "تکمیل پروفایل (سن و جنسیت)")
        await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_PROFILE_COMPLETE)


    if context.user_data.pop('club_join_after_profile_flow', False):
        logger.info(f"کاربر {user_id_str} پروفایل را تکمیل کرد، هدایت به تایید عضویت باشگاه.")
        await update.message.reply_text(
            "اکنون که پروفایل شما کامل شد، برای عضویت در باشگاه مشتریان تایید نهایی را انجام دهید:",
            reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD
        )
        return States.AWAITING_CLUB_JOIN_CONFIRMATION
    else: # اگر از مسیر دیگری آمده بود (مثلا دکتر تافته یا ویرایش پروفایل)
        # await update.message.reply_text("به منوی اصلی بازگشتید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        return await start(update, context) # بازگشت به منوی اصلی با منوی صحیح


# --- کنترل‌کننده‌های دیگر (handle_club_join_confirmation, doctor_conversation_handler, my_profile_info_handler, profile_view_handler, handle_cancel_membership_confirmation, awaiting_first_name_handler (برای ویرایش), awaiting_last_name_handler (برای ویرایش), health_tip_command_handler, fallback_message, Flask) مانند قبل با اصلاحات جزئی در جریان بازگشت و پیام‌ها ---
# ... (کد این توابع که قبلاً ارسال شده و نیاز به بازبینی و اصلاحات جزئی در جریان بازگشت به منو یا پروفایل دارند) ...

# --- کنترل‌کننده‌های ویرایش نام (قبلا awaiting_first_name_handler و awaiting_last_name_handler بودند، حالا فقط برای ویرایش استفاده می‌شوند)
async def edit_first_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # این همان awaiting_first_name_handler قبلی است، فقط برای ویرایش نام
    user = update.effective_user
    user_id_str = str(user.id)
    text = update.message.text.strip()

    if text == "🔙 انصراف و بازگشت به پروفایل":
        logger.info(f"کاربر {user_id_str} از ویرایش نام انصراف داد.")
        return await my_profile_info_handler(update, context) 

    if not text or len(text) < 2 or len(text) > 50: 
        await update.message.reply_text("نام وارد شده معتبر نیست. لطفاً نام صحیح خود را وارد کنید یا انصراف دهید.", reply_markup=NAME_INPUT_KEYBOARD)
        return States.AWAITING_EDIT_FIRST_NAME # بازگشت به همین حالت
    
    context.user_data['temp_edit_first_name'] = text
    logger.info(f"کاربر {user_id_str} نام کوچک موقت برای ویرایش '{text}' را وارد کرد.")
    await update.message.reply_text("متشکرم. حالا لطفاً نام خانوادگی جدید خود را وارد کنید (یا برای انصراف، گزینه زیر را انتخاب کنید):", reply_markup=NAME_INPUT_KEYBOARD)
    return States.AWAITING_EDIT_LAST_NAME

async def edit_last_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # این همان awaiting_last_name_handler قبلی است، فقط برای ویرایش نام
    user = update.effective_user
    user_id_str = str(user.id)
    last_name_text = update.message.text.strip()

    if last_name_text == "🔙 انصراف و بازگشت به پروفایل":
        logger.info(f"کاربر {user_id_str} از ویرایش نام خانوادگی انصراف داد.")
        if 'temp_edit_first_name' in context.user_data: del context.user_data['temp_edit_first_name']
        return await my_profile_info_handler(update, context)

    if not last_name_text or len(last_name_text) < 2 or len(last_name_text) > 50: 
        await update.message.reply_text("نام خانوادگی وارد شده معتبر نیست. لطفاً نام خانوادگی صحیح خود را وارد کنید یا انصراف دهید.", reply_markup=NAME_INPUT_KEYBOARD)
        return States.AWAITING_EDIT_LAST_NAME # بازگشت به همین حالت

    first_name = context.user_data.pop('temp_edit_first_name', None)
    if not first_name:
        logger.error(f"خطا: نام کوچک موقت برای ویرایش برای کاربر {user_id_str} یافت نشد.")
        await update.message.reply_text("مشکلی در پردازش اطلاعات پیش آمد، لطفاً از ابتدا پروفایل را ویرایش کنید.")
        return await my_profile_info_handler(update, context) 

    # برای ویرایش نام، امتیاز و نشان جدیدی در نظر نمی‌گیریم مگر اینکه بخواهید
    if db:
        try:
            update_payload = {"name_first_db": first_name, "name_last_db": last_name_text}
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            logger.info(f"نام کاربر {user_id_str} به ({first_name} {last_name_text}) در دیتابیس به‌روز شد.")
            await update.message.reply_text(f"نام شما به '{first_name} {last_name_text}' با موفقیت به‌روز شد.")
        except Exception as e:
            logger.error(f"خطا در به‌روزرسانی نام/نام خانوادگی برای {user_id_str}: {e}", exc_info=True)
            await update.message.reply_text("مشکلی در به‌روزرسانی نام شما پیش آمد.")
    else:
        await update.message.reply_text(f"نام شما به '{first_name} {last_name_text}' تنظیم شد (ذخیره‌سازی دیتابیس غیرفعال است).")
        
    return await my_profile_info_handler(update, context) # بازگشت به نمایش پروفایل با اطلاعات به‌روز شده


# --- بلوک اصلی برنامه ---
if __name__ == '__main__':
    logger.info("بلوک اصلی برنامه آغاز شد.")
    if db is None:
        logger.warning("*"*65 + "\n* دیتابیس Firestore مقداردهی اولیه نشده! ربات با قابلیت محدود اجرا می‌شود. *\n" + "*"*65)

    flask_thread = threading.Thread(target=run_flask_app, name="FlaskThread", daemon=True)
    flask_thread.start()
    logger.info("ترد Flask شروع به کار کرد.")

    telegram_application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            States.MAIN_MENU: [MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات|⭐ عضویت در باشگاه تافته|👤 پروفایل و باشگاه|📣 نکته سلامتی باشگاه)$"), main_menu_handler)],
            
            # جریان تکمیل پروفایل اولیه (اگر از "دکتر تافته" یا "عضویت در باشگاه" آمده و پروفایل ناقص است)
            States.AWAITING_PROFILE_FIRST_NAME: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_profile_first_name_handler)],
            States.AWAITING_PROFILE_LAST_NAME: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_profile_last_name_handler)],
            States.AWAITING_PROFILE_AGE: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_profile_age_handler)],
            States.AWAITING_PROFILE_GENDER: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.Regex("^(زن|مرد)$"), awaiting_profile_gender_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("لطفاً با دکمه انتخاب کنید یا بازگردید.",reply_markup=PROFILE_GENDER_SELECTION_KEYBOARD))],
            
            # جریان ورود سن و جنسیت فقط برای دکتر تافته (اگر پروفایل کامل نیست اما نخواهیم نام را بپرسیم)
            States.AWAITING_DOCTOR_AGE: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, request_age_handler)], # نام تابع request_age_handler تغییر کرده بود
            States.AWAITING_DOCTOR_GENDER: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.Regex("^(زن|مرد)$"), request_gender_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("لطفاً با دکمه انتخاب کنید یا بازگردید.",reply_markup=GENDER_SELECTION_KEYBOARD))],
            
            States.DOCTOR_CONVERSATION: [MessageHandler(filters.Regex("^(❓ سوال جدید از دکتر|🔙 بازگشت به منوی اصلی)$"), doctor_conversation_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, doctor_conversation_handler)],
            States.AWAITING_CLUB_JOIN_CONFIRMATION: [MessageHandler(filters.Regex("^(✅ بله، عضو می‌شوم|❌ خیر، فعلاً نه)$"), handle_club_join_confirmation), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("با دکمه‌ها پاسخ دهید.",reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD))],
            
            States.PROFILE_VIEW: [MessageHandler(filters.Regex("^(✏️ تکمیل/ویرایش نام|💔 لغو عضویت از باشگاه|🔙 بازگشت به منوی اصلی)$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("گزینه پروفایل را انتخاب کنید.",reply_markup=PROFILE_VIEW_KEYBOARD))],
            States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION: [MessageHandler(filters.Regex("^(✅ بله، عضویتم لغو شود|❌ خیر، منصرف شدم)$"), handle_cancel_membership_confirmation), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("با دکمه‌ها پاسخ دهید.",reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD))],
            
            # جریان ویرایش نام از داخل پروفایل
            States.AWAITING_EDIT_FIRST_NAME: [MessageHandler(filters.Regex("^🔙 انصراف و بازگشت به پروفایل$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, edit_first_name_handler)],
            States.AWAITING_EDIT_LAST_NAME: [MessageHandler(filters.Regex("^🔙 انصراف و بازگشت به پروفایل$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, edit_last_name_handler)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel), 
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), # فال بک عمومی
        ],
        persistent=False, name="main_conversation", allow_reentry=True
    )
    
    telegram_application.add_handler(CommandHandler("myprofile", my_profile_info_handler))
    telegram_application.add_handler(CommandHandler("clubtip", health_tip_command_handler))
    telegram_application.add_handler(conv_handler)
    telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    
    logger.info("ربات تلگرام در حال شروع polling...")
    try:
        telegram_application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"خطای مرگبار در run_polling: {e}", exc_info=True)
    finally:
        logger.info("برنامه در حال خاتمه است.")