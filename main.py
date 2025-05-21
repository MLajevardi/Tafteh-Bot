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
# لاگ‌گیری اولیه برای Firebase
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
# ... (بقیه ثابت‌ها مانند قبل) ...
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "openai/gpt-3.5-turbo")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg")
URL_TAFTEH_WEBSITE = "https://tafteh.ir/"

POINTS_FOR_JOINING_CLUB = 50
POINTS_FOR_PROFILE_COMPLETION = 20 # برای سن و جنسیت
POINTS_FOR_NAME_COMPLETION = 15
# POINTS_FOR_CLUB_TIP حذف شد

BADGE_CLUB_MEMBER = "عضو باشگاه تافته 🏅"
BADGE_PROFILE_COMPLETE = "پروفایل پایه کامل 🧑‍🔬"
BADGE_FULL_PROFILE = "پروفایل طلایی ✨"
BADGE_HEALTH_EXPLORER = "کاشف سلامت 🧭"
CLUB_TIP_BADGE_THRESHOLD = 3


if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    logger.error("!!! بحرانی: توکن‌های ضروری ربات یا API یافت نشدند.")
    exit(1)
else:
    logger.info("توکن ربات و کلید API با موفقیت بارگذاری شدند.")


class States(Enum):
    MAIN_MENU = 1
    AWAITING_AGE = 2
    AWAITING_GENDER = 3
    DOCTOR_CONVERSATION = 4
    AWAITING_CLUB_JOIN_CONFIRMATION = 5
    PROFILE_VIEW = 6
    AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION = 7
    AWAITING_FIRST_NAME = 8
    AWAITING_LAST_NAME = 9

# --- تعریف کیبوردها (بدون تغییر نسبت به قبل) ---
DOCTOR_CONVERSATION_KEYBOARD = ReplyKeyboardMarkup(
    [["❓ سوال جدید از دکتر"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True
)
AGE_INPUT_KEYBOARD = ReplyKeyboardMarkup(
    [["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True, one_time_keyboard=True
)
GENDER_SELECTION_KEYBOARD = ReplyKeyboardMarkup(
    [["زن"], ["مرد"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True, one_time_keyboard=True
)
CLUB_JOIN_CONFIRMATION_KEYBOARD = ReplyKeyboardMarkup(
    [["✅ بله، عضو می‌شوم"], ["❌ خیر، فعلاً نه"]], resize_keyboard=True, one_time_keyboard=True
)
PROFILE_VIEW_KEYBOARD = ReplyKeyboardMarkup(
    [["✏️ تکمیل/ویرایش نام"], ["💔 لغو عضویت از باشگاه"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True
)
CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD = ReplyKeyboardMarkup(
    [["✅ بله، عضویتم لغو شود"], ["❌ خیر، منصرف شدم"]], resize_keyboard=True, one_time_keyboard=True
)
NAME_INPUT_KEYBOARD = ReplyKeyboardMarkup(
    [["🔙 انصراف و بازگشت به پروفایل"]], resize_keyboard=True, one_time_keyboard=True
)
# HEALTH_TIPS_FOR_CLUB بدون تغییر

# --- توابع دیتابیس (get_or_create_user_profile, update_user_profile_data, get_user_profile_data - بدون تغییر) ---
# --- توابع کمکی ربات (ask_openrouter, _prepare_doctor_system_prompt, notify_points_awarded, award_badge_if_not_already_awarded, get_dynamic_main_menu_keyboard - بدون تغییر) ---

# HEALTH_TIPS_FOR_CLUB مانند قبل
HEALTH_TIPS_FOR_CLUB = [
    "روزانه حداقل ۸ لیوان آب بنوشید تا بدنتان هیدراته بماند.",
    "خواب کافی (۷-۸ ساعت) برای بازیابی انرژی و سلامت روان ضروری است.",
    "حداقل ۳۰ دقیقه فعالیت بدنی متوسط در بیشتر روزهای هفته به حفظ سلامت قلب کمک می‌کند.",
    "مصرف میوه‌ها و سبزیجات رنگارنگ، ویتامین‌ها و آنتی‌اکسیدان‌های لازم را به بدن شما می‌رساند.",
    "برای کاهش استرس، تکنیک‌های آرام‌سازی مانند مدیتیشن یا تنفس عمیق را امتحان کنید."
]

def get_or_create_user_profile(user_id: str, username: str = None, first_name: str = None) -> dict:
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Profile for user {user_id} will be in-memory mock.")
        return {"user_id": user_id, "username": username, "first_name": first_name, "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [], "profile_completion_points_awarded": False, "club_tip_usage_count": 0, "club_join_date": None, "name_first_db": None, "name_last_db": None, "profile_name_completion_points_awarded": False}
    user_ref = db.collection('users').document(user_id)
    try:
        user_doc = user_ref.get()
    except Exception as e:
        logger.error(f"DB: خطا هنگام get() برای کاربر {user_id}: {e}", exc_info=True)
        return {"user_id": user_id, "username": username, "first_name": first_name, "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [], "profile_completion_points_awarded": False, "club_tip_usage_count": 0, "club_join_date": None, "name_first_db": None, "name_last_db": None, "profile_name_completion_points_awarded": False}
    default_fields = {'age': None, 'gender': None, 'is_club_member': False, 'points': 0, 'badges': [], 'profile_completion_points_awarded': False, 'club_tip_usage_count': 0, 'club_join_date': None, 'name_first_db': None, 'name_last_db': None, 'profile_name_completion_points_awarded': False}
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
                except Exception as e_update: logger.error(f"DB: خطا در آپدیت فیلدهای پیش فرض برای کاربر {user_id} هنگام خواندن: {e_update}")
        return user_data
    else:
        user_data = {'user_id': user_id, 'username': username, 'first_name': first_name, 'registration_date': firestore.SERVER_TIMESTAMP, 'last_interaction_date': firestore.SERVER_TIMESTAMP}
        for key, default_value in default_fields.items(): user_data[key] = default_value
        try: user_ref.set(user_data)
        except Exception as e_set: logger.error(f"DB: خطا در ایجاد پروفایل جدید برای کاربر {user_id}: {e_set}")
        return user_data

def update_user_profile_data(user_id: str, data_to_update: dict) -> None:
    if not db: return
    user_ref = db.collection('users').document(user_id)
    data_to_update['last_updated_date'] = firestore.SERVER_TIMESTAMP
    try:
        user_ref.update(data_to_update)
        logger.info(f"DB: پروفایل کاربر {user_id} با داده‌های {data_to_update} در Firestore به‌روز شد.")
    except Exception as e:
        logger.error(f"DB: خطا در به‌روزرسانی پروفایل کاربر {user_id} با داده‌های {data_to_update}: {e}", exc_info=True)

def get_user_profile_data(user_id: str) -> dict | None:
    if not db: return None
    user_ref = db.collection('users').document(user_id)
    try:
        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            defaults = {'is_club_member': False, 'points': 0, 'badges': [], 'profile_completion_points_awarded': False, 'club_tip_usage_count': 0, 'club_join_date': None, 'age': None, 'gender': None, 'name_first_db': None, 'name_last_db': None, 'profile_name_completion_points_awarded': False}
            for key, default_value in defaults.items():
                if key not in user_data: user_data[key] = default_value
            return user_data
    except Exception as e:
        logger.error(f"DB: خطا در خواندن پروفایل کاربر {user_id}: {e}", exc_info=True)
    return None

async def ask_openrouter(system_prompt: str, chat_history: list, model_override: str = None) -> str:
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    messages_payload = [{"role": "system", "content": system_prompt}] + chat_history
    current_model = model_override if model_override else OPENROUTER_MODEL_NAME
    body = {"model": current_model, "messages": messages_payload, "temperature": 0.5} # کاهش دما برای پاسخ‌های متمرکزتر
    logger.info(f"آماده‌سازی درخواست برای OpenRouter. مدل: {current_model}, تاریخچه: {len(chat_history)} پیام.")
    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            if data.get("choices") and data["choices"][0].get("message") and data["choices"][0]["message"].get("content"):
                llm_response_content = data["choices"][0]["message"]["content"].strip()
                logger.info(f"محتوای دقیق پاسخ LLM ({current_model}): '{llm_response_content}'")
                return llm_response_content
            else:
                logger.error(f"ساختار پاسخ OpenRouter ({current_model}) نامعتبر: {data}")
                return "❌ مشکلی در پردازش پاسخ از سرویس هوش مصنوعی رخ داد."
        except Exception as e:
            logger.error(f"خطا در ارتباط یا پردازش پاسخ OpenRouter ({current_model}): {e}", exc_info=True)
            return "❌ بروز خطا در ارتباط با سرویس هوش مصنوعی. لطفاً مجدداً تلاش نمایید."


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
    # ... (بدون تغییر) ...
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
    # ... (بدون تغییر) ...
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
    # ... (منطق پاک کردن کش در start انجام می‌شود، اینجا فقط می‌خوانیم) ...
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
            is_member = False # در صورت خطا، فرض بر عدم عضویت
    else: # اگر دیتابیس در دسترس نیست
        is_member = False
    
    context.user_data['is_club_member_cached'] = is_member # کش کردن برای استفاده در همین درخواست

    if is_member:
        keyboard_layout = [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"], ["👤 پروفایل و باشگاه"], ["📣 نکته سلامتی باشگاه"]]
    else:
        keyboard_layout = [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"], ["⭐ عضویت در باشگاه تافته"]]
    return ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    is_cancel_flow = context.user_data.pop('_is_cancel_flow', False) # خواندن و حذف فلگ
    message_prefix = "درخواست لغو شما انجام شد. " if is_cancel_flow else ""

    logger.info(f"کاربر {user_id_str} ({user.full_name or user.username}) /start یا بازگشت/لغو به منوی اصلی.")

    keys_to_clear_from_session = ["doctor_chat_history", "system_prompt_for_doctor", "age_temp", 
                                  "is_club_member_cached", "awaiting_field_to_edit", "temp_first_name",
                                  "club_join_flow_active"] # پاک کردن فلگ عضویت باشگاه
    for key in keys_to_clear_from_session:
        if key in context.user_data:
            del context.user_data[key]
    logger.info(f"اطلاعات جلسه (user_data) برای کاربر {user_id_str} پاکسازی شد.")

    if db:
        try:
            await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        except Exception as e:
            logger.error(f"خطا در get_or_create_user_profile (start) برای کاربر {user_id_str}: {e}", exc_info=True)

    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str) # منوی پویا پس از پاکسازی کش
    welcome_message_text = f"سلام {user.first_name or 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:"
    if message_prefix:
        welcome_message_text = message_prefix + "به منوی اصلی بازگشتید."

    effective_chat_id = update.effective_chat.id
    try:
        # فقط اگر دستور /start مستقیم زده شده و پیام عکس ندارد، عکس ارسال شود
        if update.message and update.message.text == "/start" and not (hasattr(update.message, 'photo') and update.message.photo):
            await context.bot.send_photo(chat_id=effective_chat_id, photo=WELCOME_IMAGE_URL, caption=welcome_message_text, reply_markup=dynamic_main_menu)
        else: # در غیر این صورت (بازگشت از منو، /cancel و ...) فقط متن بفرست
            await context.bot.send_message(chat_id=effective_chat_id, text=welcome_message_text, reply_markup=dynamic_main_menu)
    except Exception as e:
        logger.error(f"خطا در ارسال پیام خوش‌آمدگویی برای {user_id_str}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=effective_chat_id, text=welcome_message_text, reply_markup=dynamic_main_menu)
    return States.MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id = user.id if user else "Unknown"
    logger.info(f"User {user_id} called /cancel. Delegating to start handler.")
    context.user_data['_is_cancel_flow'] = True
    if update.effective_chat: # ارسال پیام فقط اگر چت معتبر باشد
        await context.bot.send_message(chat_id=update.effective_chat.id, text="درخواست شما لغو شد. بازگشت به منوی اصلی...", reply_markup=ReplyKeyboardRemove())
    return await start(update, context) # واگذاری به start برای مدیریت صحیح user_data و نمایش منو

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} در منوی اصلی گزینه '{text}' را انتخاب کرد.")
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)

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
            # اگر در جریان عضویت باشگاه نیستیم، این فلگ را False می‌کنیم
            context.user_data['club_join_flow_active'] = False 
            await update.message.reply_text(
                "برای استفاده از دکتر تافته، ابتدا باید سن و جنسیت خود را وارد کنید. لطفاً سن خود را وارد کنید:",
                reply_markup=AGE_INPUT_KEYBOARD
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
        
    elif text == "⭐ عضویت در باشگاه تافته": # برای کاربران غیرعضو
        logger.info(f"کاربر {user_id_str} گزینه 'عضویت در باشگاه تافته' را انتخاب کرد.")
        age, gender = None, None
        if db:
            user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
            if user_profile:
                age = user_profile.get("age")
                gender = user_profile.get("gender")
        
        if not (age and gender): # اگر سن یا جنسیت هنوز در دیتابیس ثبت نشده
            logger.info(f"کاربر {user_id_str} برای عضویت نیاز به تکمیل پروفایل (سن/جنسیت) دارد. هدایت به AWAITING_AGE.")
            context.user_data['club_join_flow_active'] = True # علامت‌گذاری برای جریان عضویت
            await update.message.reply_text(
                "برای عضویت در باشگاه، ابتدا باید سن خود را وارد کنید:",
                reply_markup=AGE_INPUT_KEYBOARD
            )
            return States.AWAITING_AGE
        else: # سن و جنسیت موجود است، پس سوال برای تایید عضویت
            await update.message.reply_text(
                "عضویت در باشگاه مشتریان تافته مزایای ویژه‌ای برای شما خواهد داشت! آیا مایل به عضویت هستید؟",
                reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD
            )
            return States.AWAITING_CLUB_JOIN_CONFIRMATION
        
    elif text == "👤 پروفایل و باشگاه": # برای کاربران عضو
        logger.info(f"کاربر {user_id_str} گزینه 'پروفایل و باشگاه' را انتخاب کرد.")
        return await my_profile_info_handler(update, context) # به نمایش پروفایل و گزینه‌های مدیریت می‌رود

    elif text == "📣 نکته سلامتی باشگاه": # برای کاربران عضو
        logger.info(f"کاربر {user_id_str} گزینه 'نکته سلامتی باشگاه' را انتخاب کرد.")
        return await health_tip_command_handler(update, context)

    else: # اگر متن دکمه با هیچکدام مطابقت نداشت (نباید اتفاق بیفتد با Regex فعلی)
        await update.message.reply_text("گزینه انتخاب شده معتبر نیست. لطفاً از منوی زیر انتخاب کنید:", reply_markup=dynamic_main_menu)
        return States.MAIN_MENU

async def request_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    if text == "🔙 بازگشت به منوی اصلی":
        logger.info(f"User {update.effective_user.id} returned to main menu from AWAITING_AGE.")
        if 'club_join_flow_active' in context.user_data: del context.user_data['club_join_flow_active']
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
        logger.info(f"User {update.effective_user.id} returned to main menu from AWAITING_GENDER.")
        if "age_temp" in context.user_data: del context.user_data["age_temp"]
        if 'club_join_flow_active' in context.user_data: del context.user_data['club_join_flow_active']
        return await start(update, context)
    gender_input = text.strip()
    user = update.effective_user
    user_id_str = str(user.id)
    age = context.user_data.pop("age_temp", None)
    if not age:
        logger.error(f"خطا: سن موقت برای کاربر {user_id_str} یافت نشد.")
        await update.message.reply_text("مشکلی در پردازش اطلاعات پیش آمد.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        return await start(update, context)
    gender = gender_input
    awarded_profile_points_and_badge = False
    if db:
        try:
            user_profile_before_update = await asyncio.to_thread(get_user_profile_data, user_id_str)
            update_payload = {"age": age, "gender": gender}
            if user_profile_before_update and not user_profile_before_update.get('profile_completion_points_awarded', False):
                if (user_profile_before_update.get("age") is None or user_profile_before_update.get("gender") is None) and age and gender:
                    update_payload["points"] = firestore.Increment(POINTS_FOR_PROFILE_COMPLETION)
                    update_payload["profile_completion_points_awarded"] = True
                    awarded_profile_points_and_badge = True
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            logger.info(f"سن ({age}) و جنسیت ({gender}) کاربر {user_id_str} در دیتابیس ذخیره/به‌روز شد.")
            if awarded_profile_points_and_badge:
                logger.info(f"کاربر {user_id_str} واجد شرایط دریافت امتیاز و نشان تکمیل پروفایل پایه است.")
        except Exception as e:
            logger.error(f"خطا در ذخیره سن/جنسیت یا اعطای امتیاز/نشان برای {user_id_str}: {e}", exc_info=True)

    # اگر کاربر در جریان عضویت در باشگاه بود، او را به مرحله تایید عضویت هدایت کن
    if context.user_data.pop('club_join_flow_active', False):
        logger.info(f"کاربر {user_id_str} پروفایل (سن/جنسیت) را در جریان عضویت باشگاه تکمیل کرد. هدایت به تایید عضویت.")
        # پیام تکمیل پروفایل و سپس پیام تایید عضویت
        profile_completion_message = f"✅ پروفایل پایه شما (سن: {age}، جنسیت: {gender}) تکمیل شد.\n"
        if awarded_profile_points_and_badge:
            # این پیام‌ها توسط توابع notify_points_awarded و award_badge_if_not_already_awarded ارسال می‌شوند
            pass 
        
        await update.message.reply_text(profile_completion_message + "اکنون برای عضویت در باشگاه مشتریان تایید نهایی را انجام دهید:",
                                        reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD)
        if awarded_profile_points_and_badge: # اطلاع رسانی پس از پیام اصلی
            await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_PROFILE_COMPLETION, "تکمیل پروفایل (سن و جنسیت)")
            await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_PROFILE_COMPLETE)
        return States.AWAITING_CLUB_JOIN_CONFIRMATION
    else: # در غیر این صورت، به مکالمه با دکتر برود
        context.user_data["age"] = age
        context.user_data["gender"] = gender
        system_prompt = _prepare_doctor_system_prompt(age, gender)
        context.user_data["system_prompt_for_doctor"] = system_prompt
        context.user_data["doctor_chat_history"] = []
        await update.message.reply_text(
            f"✅ مشخصات شما ثبت شد:\nسن: {age} سال\nجنسیت: {gender}\n\nاکنون می‌توانید سوال پزشکی خود را از دکتر تافته بپرسید.",
            reply_markup=DOCTOR_CONVERSATION_KEYBOARD
        )
        if awarded_profile_points_and_badge:
            await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_PROFILE_COMPLETION, "تکمیل پروفایل (سن و جنسیت)")
            await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_PROFILE_COMPLETE)
        return States.DOCTOR_CONVERSATION

async def handle_club_join_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    text = update.message.text
    logger.info(f"کاربر {user_id_str} به سوال عضویت در باشگاه پاسخ داد: '{text}'")

    if text == "✅ بله، عضو می‌شوم":
        if not db:
            await update.message.reply_text("سیستم باشگاه مشتریان موقتا در دسترس نیست.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            return await start(update, context)
        try:
            await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name) # اطمینان از وجود پروفایل
            await asyncio.to_thread(update_user_profile_data, user_id_str,
                                    {"is_club_member": True,
                                     "points": firestore.Increment(POINTS_FOR_JOINING_CLUB),
                                     "club_join_date": firestore.SERVER_TIMESTAMP})
            context.user_data['is_club_member_cached'] = True
            logger.info(f"کاربر {user_id_str} به باشگاه پیوست و {POINTS_FOR_JOINING_CLUB} امتیاز گرفت.")

            await update.message.reply_text(f"عضویت شما در باشگاه مشتریان تافته با موفقیت انجام شد! ✨")
            await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_JOINING_CLUB, "عضویت در باشگاه مشتریان")
            await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_CLUB_MEMBER)
            
            # ارسال پیام بازگشت به منو با کیبورد آپدیت شده
            await context.bot.send_message(chat_id=update.effective_chat.id, text="از همراهی شما سپاسگزاریم. به منوی اصلی بازگشتید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))

        except Exception as e:
            logger.error(f"خطا در عضویت باشگاه برای {user_id_str}: {e}", exc_info=True)
            await update.message.reply_text("مشکلی در عضویت شما پیش آمد.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            return await start(update, context)
            
    elif text == "❌ خیر، فعلاً نه":
        await update.message.reply_text("متوجه شدم. هر زمان تمایل داشتید، می‌توانید از طریق منوی اصلی اقدام کنید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
    else:
        await update.message.reply_text("لطفاً یکی از گزینه‌ها را انتخاب کنید.", reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD)
        return States.AWAITING_CLUB_JOIN_CONFIRMATION
    return States.MAIN_MENU # اطمینان از بازگشت به حالت صحیح برای دریافت پیام بعدی از منو

async def doctor_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    logger.info(f"--- DCH Entered --- User: {update.effective_user.id}, Text: '{update.message.text}', History items: {len(context.user_data.get('doctor_chat_history', []))}")
    user_question = update.message.text
    user = update.effective_user
    user_id_str = str(user.id)
    chat_history = context.user_data.get("doctor_chat_history", [])
    system_prompt = context.user_data.get("system_prompt_for_doctor")
    if not system_prompt:
        logger.warning(f"DCH: System prompt for user {user_id_str} not found! Attempting rebuild.")
        age_db, gender_db = None, None
        if db:
            try:
                profile_db = await asyncio.to_thread(get_user_profile_data, user_id_str)
                if profile_db:
                    age_db = profile_db.get("age")
                    gender_db = profile_db.get("gender")
            except Exception as e: logger.error(f"DCH: Error fetching profile for {user_id_str} to rebuild prompt: {e}")
        if age_db and gender_db:
            system_prompt = _prepare_doctor_system_prompt(age_db, gender_db)
            context.user_data["system_prompt_for_doctor"] = system_prompt
            context.user_data["age"], context.user_data["gender"] = age_db, gender_db
            logger.info(f"DCH: System prompt for user {user_id_str} rebuilt.")
        else:
            logger.error(f"DCH: Could not rebuild system prompt for {user_id_str}. Age/Gender missing. Redirecting to AWAITING_AGE.")
            await update.message.reply_text("برای ادامه، لطفاً ابتدا سن خود را وارد کنید:", reply_markup=AGE_INPUT_KEYBOARD)
            return States.AWAITING_AGE
    if user_question == "🔙 بازگشت به منوی اصلی":
        return await start(update, context)
    elif user_question == "❓ سوال جدید از دکتر":
        context.user_data["doctor_chat_history"] = []
        await update.message.reply_text("تاریخچه پاک شد. سوال جدید خود را بپرسید:", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
        return States.DOCTOR_CONVERSATION
    logger.info(f"DCH: Processing text from {user_id_str}: '{user_question}'")
    chat_history.append({"role": "user", "content": user_question})
    await update.message.reply_text("⏳ دکتر تافته در حال بررسی پیام شماست...")
    assistant_response = await ask_openrouter(system_prompt, chat_history)
    chat_history.append({"role": "assistant", "content": assistant_response})
    context.user_data["doctor_chat_history"] = chat_history
    await update.message.reply_text(assistant_response, parse_mode="Markdown", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
    return States.DOCTOR_CONVERSATION

async def my_profile_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست 'پروفایل و باشگاه' یا /myprofile را داد.")
    
    if not db:
        await update.message.reply_text("سیستم پروفایل موقتا در دسترس نیست.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        return States.MAIN_MENU

    try:
        user_profile = await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        points = user_profile.get('points', 0)
        badges = user_profile.get('badges', [])
        is_member = user_profile.get('is_club_member', False)
        age = user_profile.get('age', 'ثبت نشده')
        gender = user_profile.get('gender', 'ثبت نشده')
        name_first = user_profile.get('name_first_db') or user_profile.get('first_name') or 'ثبت نشده'
        name_last = user_profile.get('name_last_db', 'ثبت نشده')

        reply_message = f"👤 **پروفایل شما در باشگاه تافته** 👤\n\n"
        reply_message += f"نام شما: {name_first} {name_last}\n"
        reply_message += f"سن: {age}\n"
        reply_message += f"جنسیت: {gender}\n\n"
        if is_member:
            reply_message += " عضویت باشگاه: ✅ فعال\n"
            reply_message += f" امتیاز شما: {points} 🌟\n"
            if badges:
                reply_message += "\nنشان‌های شما:\n"
                for badge_item in badges: reply_message += f"  - {badge_item}\n"
            else: reply_message += "\nشما هنوز هیچ نشانی دریافت نکرده‌اید.\n"
            
            await update.message.reply_text(reply_message, parse_mode="Markdown", reply_markup=PROFILE_VIEW_KEYBOARD)
            return States.PROFILE_VIEW
        else: # اگر کاربر از طریق /myprofile آمده و عضو نیست یا دکمه منو اشتباه نمایش داده شده
            reply_message = "شما هنوز عضو باشگاه مشتریان تافته نیستید.\n"
            reply_message += "برای استفاده از امکانات پروفایل و باشگاه، لطفاً ابتدا از طریق منوی اصلی در باشگاه عضو شوید."
            await update.message.reply_text(reply_message, reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            return States.MAIN_MENU
    except Exception as e:
        logger.error(f"خطا در نمایش پروفایل برای {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text("مشکلی در نمایش پروفایل شما پیش آمد.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
    return States.MAIN_MENU

async def profile_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    text = update.message.text
    logger.info(f"کاربر {user_id_str} در حالت PROFILE_VIEW گزینه '{text}' را انتخاب کرد.")

    if text == "💔 لغو عضویت از باشگاه":
        await update.message.reply_text(
            "آیا مطمئن هستید که می‌خواهید عضویت خود را از باشگاه مشتریان لغو کنید؟\n"
            "⚠️ **اخطار:** با لغو عضویت، تمام امتیازات و نشان‌های کسب شده شما از بین خواهد رفت و اطلاعات پروفایل شما (سن، جنسیت، نام) نیز ریست خواهد شد.",
            reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD,
            parse_mode="Markdown"
        )
        return States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION
    elif text == "✏️ تکمیل/ویرایش نام":
        await update.message.reply_text("لطفاً نام کوچک خود را وارد کنید (یا برای انصراف، گزینه زیر را انتخاب کنید):", reply_markup=NAME_INPUT_KEYBOARD)
        return States.AWAITING_FIRST_NAME
    elif text == "🔙 بازگشت به منوی اصلی":
        return await start(update, context)
    else:
        await update.message.reply_text("لطفاً یکی از گزینه‌های پروفایل را انتخاب کنید.", reply_markup=PROFILE_VIEW_KEYBOARD)
        return States.PROFILE_VIEW

async def handle_cancel_membership_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    text = update.message.text
    logger.info(f"کاربر {user_id_str} به سوال لغو عضویت پاسخ داد: '{text}'")

    if text == "✅ بله، عضویتم لغو شود":
        if not db:
            await update.message.reply_text("سیستم باشگاه موقتا در دسترس نیست.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            return await start(update, context)
        try:
            update_payload = {
                "is_club_member": False, "points": 0, "badges": [],
                "club_join_date": None, "club_tip_usage_count": 0,
                "age": None, "gender": None, 
                "name_first_db": None, "name_last_db": None, 
                "profile_completion_points_awarded": False, 
                "profile_name_completion_points_awarded": False 
            }
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            context.user_data['is_club_member_cached'] = False # آپدیت کش
            logger.info(f"عضویت کاربر {user_id_str} لغو شد و اطلاعات پروفایل و باشگاه او ریست گردید.")
            await update.message.reply_text("عضویت شما از باشگاه مشتریان با موفقیت لغو شد. اطلاعات پروفایل و امتیازات شما ریست شد.")
        except Exception as e:
            logger.error(f"خطا در لغو عضویت کاربر {user_id_str}: {e}", exc_info=True)
            await update.message.reply_text("مشکلی در لغو عضویت شما پیش آمد.")
    elif text == "❌ خیر، منصرف شدم":
        await update.message.reply_text("خوشحالیم که همچنان عضو باشگاه مشتریان تافته باقی می‌مانید!")
        return await my_profile_info_handler(update, context) # بازگشت به نمایش پروفایل
    else:
        await update.message.reply_text("لطفاً یکی از گزینه‌ها را انتخاب کنید.", reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD)
        return States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION
    return await start(update, context) # در صورت لغو موفق یا خطا، به منوی اصلی برمی‌گردد

async def awaiting_first_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    text = update.message.text.strip()

    if text == "🔙 انصراف و بازگشت به پروفایل":
        logger.info(f"کاربر {user_id_str} از ورود نام انصراف داد.")
        return await my_profile_info_handler(update, context)

    if not text or len(text) < 2 or len(text) > 50:
        await update.message.reply_text("نام وارد شده معتبر نیست (باید بین ۲ تا ۵۰ حرف باشد). لطفاً نام صحیح خود را وارد کنید یا انصراف دهید.", reply_markup=NAME_INPUT_KEYBOARD)
        return States.AWAITING_FIRST_NAME
    
    context.user_data['temp_first_name'] = text
    logger.info(f"کاربر {user_id_str} نام کوچک موقت '{text}' را وارد کرد.")
    await update.message.reply_text("متشکرم. حالا لطفاً نام خانوادگی خود را وارد کنید (یا برای انصراف، گزینه زیر را انتخاب کنید):", reply_markup=NAME_INPUT_KEYBOARD)
    return States.AWAITING_LAST_NAME

async def awaiting_last_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    last_name_text = update.message.text.strip()

    if last_name_text == "🔙 انصراف و بازگشت به پروفایل":
        logger.info(f"کاربر {user_id_str} از ورود نام خانوادگی انصراف داد.")
        if 'temp_first_name' in context.user_data: del context.user_data['temp_first_name']
        return await my_profile_info_handler(update, context)

    if not last_name_text or len(last_name_text) < 2 or len(last_name_text) > 50:
        await update.message.reply_text("نام خانوادگی وارد شده معتبر نیست (باید بین ۲ تا ۵۰ حرف باشد). لطفاً نام خانوادگی صحیح خود را وارد کنید یا انصراف دهید.", reply_markup=NAME_INPUT_KEYBOARD)
        return States.AWAITING_LAST_NAME

    first_name = context.user_data.pop('temp_first_name', None)
    if not first_name:
        logger.error(f"خطا: نام کوچک موقت برای کاربر {user_id_str} یافت نشد.")
        await update.message.reply_text("مشکلی در پردازش اطلاعات پیش آمد، لطفاً از ابتدا پروفایل را ویرایش کنید.")
        return await my_profile_info_handler(update, context)

    awarded_name_completion_points_and_badge = False
    if db:
        try:
            user_profile_before_update = await asyncio.to_thread(get_user_profile_data, user_id_str)
            update_payload = {"name_first_db": first_name, "name_last_db": last_name_text}

            if user_profile_before_update and not user_profile_before_update.get('profile_name_completion_points_awarded', False):
                if first_name and last_name_text:
                    update_payload["points"] = firestore.Increment(POINTS_FOR_NAME_COMPLETION)
                    update_payload["profile_name_completion_points_awarded"] = True
                    awarded_name_completion_points_and_badge = True
            
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            logger.info(f"نام ({first_name} {last_name_text}) کاربر {user_id_str} در دیتابیس ذخیره شد.")

            await update.message.reply_text(f"نام شما به '{first_name} {last_name_text}' با موفقیت ثبت شد.")
            if awarded_name_completion_points_and_badge:
                await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_NAME_COMPLETION, "تکمیل نام و نام خانوادگی")
                await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_FULL_PROFILE)
        except Exception as e:
            logger.error(f"خطا در ذخیره نام/نام خانوادگی یا اعطای امتیاز/نشان برای {user_id_str}: {e}", exc_info=True)
            await update.message.reply_text("مشکلی در ذخیره نام شما پیش آمد.")
    else:
        await update.message.reply_text(f"نام شما به '{first_name} {last_name_text}' تنظیم شد (ذخیره‌سازی دیتابیس غیرفعال است).")
        
    return await my_profile_info_handler(update, context) # بازگشت به نمایش پروفایل با اطلاعات به‌روز شده

async def health_tip_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست 'نکته سلامتی باشگاه' کرد.")
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)

    if not db:
        await update.message.reply_text("سیستم باشگاه مشتریان موقتا در دسترس نیست.", reply_markup=dynamic_main_menu)
        return States.MAIN_MENU

    try:
        user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
        
        if user_profile and user_profile.get('is_club_member', False):
            tip_system_prompt = (
                "شما یک متخصص سلامت و تندرستی هستید. لطفاً یک نکته سلامتی کوتاه (حداکثر دو جمله)، مفید، علمی و کاربردی به زبان فارسی برای کاربر ارائه دهید. "
                "نکته باید عمومی باشد و برای طیف وسیعی از افراد مناسب باشد. از دادن توصیه پزشکی خاص یا تشخیص بیماری خودداری کنید. "
                "پاسخ شما باید فقط و فقط خود نکته باشد، بدون هیچ مقدمه یا موخره‌ای."
            )
            tip_user_message = "یک نکته سلامتی به من بگو." 
            health_tip_response = await ask_openrouter(tip_system_prompt, [{"role": "user", "content": tip_user_message}])

            if health_tip_response.startswith("❌"):
                 await update.message.reply_text("متاسفانه در حال حاضر امکان ارائه نکته سلامتی وجود ندارد. لطفاً بعداً تلاش کنید.", reply_markup=dynamic_main_menu)
                 return States.MAIN_MENU

            message_to_send = f"⚕️ **نکته سلامتی ویژه اعضای باشگاه تافته:**\n\n_{health_tip_response}_"
            await update.message.reply_text(message_to_send, parse_mode="Markdown", reply_markup=dynamic_main_menu)
            
            new_tip_usage_count = user_profile.get('club_tip_usage_count', 0) + 1
            update_payload = {"club_tip_usage_count": new_tip_usage_count}
            # اگر می‌خواهید برای مشاهده نکته همچنان امتیاز بدهید، اینجا اضافه کنید:
            # update_payload["points"] = firestore.Increment(POINTS_FOR_CLUB_TIP) 
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            logger.info(f"نکته سلامتی برای عضو باشگاه {user_id_str} ارسال شد. تعداد استفاده: {new_tip_usage_count}")
            
            if new_tip_usage_count >= CLUB_TIP_BADGE_THRESHOLD:
                await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_HEALTH_EXPLORER)
        else:
            await update.message.reply_text("این بخش مخصوص اعضای باشگاه مشتریان تافته است.", reply_markup=dynamic_main_menu)
            
    except Exception as e:
        logger.error(f"خطا در ارسال نکته سلامتی برای {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text("مشکلی در ارائه نکته سلامتی پیش آمد.", reply_markup=dynamic_main_menu)
    return States.MAIN_MENU

# --- تعریف تابع fallback_message ---
async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id_str = str(user.id) if user else "UnknownUser"
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)
    
    logger.warning(f"--- GLOBAL FALLBACK Reached --- User: {user_id_str}, Text: '{update.message.text if update.message else 'No message text'}', Current user_data: {context.user_data}")
    
    if update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="متاسفم، متوجه منظور شما نشدم یا در حال حاضر در مرحله مناسبی برای این درخواست نیستید. "
                 "لطفاً از گزینه‌های منوی زیر استفاده کنید. اگر مشکل ادامه داشت، می‌توانید با ارسال مجدد دستور /start، ربات را مجدداً راه‌اندازی کنید.",
            reply_markup=dynamic_main_menu
        )
    else:
        logger.error(f"Fallback_message: effective_chat is None for user {user_id_str}, cannot send reply.")

# --- Flask App & Main Execution ---
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check():
    return 'ربات تلگرام تافته فعال است!', 200

def run_flask_app():
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"ترد Flask: شروع وب سرور روی 0.0.0.0:{port}")
    try:
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"ترد Flask: خطا در اجرا: {e}", exc_info=True)

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
            States.AWAITING_AGE: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, request_age_handler)],
            States.AWAITING_GENDER: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.Regex("^(زن|مرد)$"), request_gender_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("لطفاً با دکمه انتخاب کنید یا بازگردید.",reply_markup=GENDER_SELECTION_KEYBOARD))],
            States.DOCTOR_CONVERSATION: [MessageHandler(filters.Regex("^(❓ سوال جدید از دکتر|🔙 بازگشت به منوی اصلی)$"), doctor_conversation_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, doctor_conversation_handler)],
            States.AWAITING_CLUB_JOIN_CONFIRMATION: [MessageHandler(filters.Regex("^(✅ بله، عضو می‌شوم|❌ خیر، فعلاً نه)$"), handle_club_join_confirmation), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("با دکمه‌ها پاسخ دهید.",reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD))],
            States.PROFILE_VIEW: [MessageHandler(filters.Regex("^(✏️ تکمیل/ویرایش نام|💔 لغو عضویت از باشگاه|🔙 بازگشت به منوی اصلی)$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("گزینه پروفایل را انتخاب کنید.",reply_markup=PROFILE_VIEW_KEYBOARD))],
            States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION: [MessageHandler(filters.Regex("^(✅ بله، عضویتم لغو شود|❌ خیر، منصرف شدم)$"), handle_cancel_membership_confirmation), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("با دکمه‌ها پاسخ دهید.",reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD))],
            States.AWAITING_FIRST_NAME: [MessageHandler(filters.Regex("^🔙 انصراف و بازگشت به پروفایل$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_first_name_handler)],
            States.AWAITING_LAST_NAME: [MessageHandler(filters.Regex("^🔙 انصراف و بازگشت به پروفایل$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_last_name_handler)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel), 
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start),
        ],
        persistent=False, 
        name="main_conversation",
        allow_reentry=True # این پارامتر مهم است
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