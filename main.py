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
_initial_fb_logger = logging.getLogger("FIREBASE_INIT_LOGGER_V4") 
_initial_fb_logger.propagate = False 
_initial_fb_logger.setLevel(logging.INFO)
if not _initial_fb_logger.hasHandlers():
    _fb_handler = logging.StreamHandler()
    _fb_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    _fb_handler.setFormatter(_fb_formatter)
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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO, handlers=[logging.StreamHandler()], force=True
)
logger = logging.getLogger(__name__)
logger.info("اسکریپت main.py شروع به کار کرد...")

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "openai/gpt-3.5-turbo")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg")
URL_TAFTEH_WEBSITE = "https://tafteh.ir/"

POINTS_FOR_JOINING_CLUB = 50
POINTS_FOR_FULL_PROFILE_COMPLETION = 35 # امتیاز برای تکمیل نام، نام خانوادگی، سن و جنسیت
# POINTS_FOR_CLUB_TIP حذف شد

BADGE_CLUB_MEMBER = "عضو باشگاه تافته 🏅"
BADGE_FULL_PROFILE = "پروفایل طلایی ✨" 
BADGE_HEALTH_EXPLORER = "کاشف سلامت 🧭" 
CLUB_TIP_BADGE_THRESHOLD = 3 

if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    logger.error("!!! بحرانی: توکن‌های ضروری ربات یا API یافت نشدند.")
    exit(1)
else:
    logger.info(f"توکن ربات (...{TELEGRAM_TOKEN[-6:]}) و کلید API (...{OPENROUTER_API_KEY[-4:]}) بارگذاری شدند.")

class States(Enum):
    MAIN_MENU = 1
    AWAITING_PROFILE_FIRST_NAME = 10 
    AWAITING_PROFILE_LAST_NAME = 11
    AWAITING_PROFILE_AGE = 12
    AWAITING_PROFILE_GENDER = 13
    DOCTOR_CONVERSATION = 4
    AWAITING_CLUB_JOIN_CONFIRMATION = 5
    PROFILE_VIEW = 6
    AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION = 7
    AWAITING_EDIT_FIRST_NAME = 8 
    AWAITING_EDIT_LAST_NAME = 9

# --- تعریف کیبوردها ---
DOCTOR_CONVERSATION_KEYBOARD = ReplyKeyboardMarkup([["❓ سوال جدید از دکتر"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True)
PROFILE_INPUT_BACK_KEYBOARD = ReplyKeyboardMarkup([["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True, one_time_keyboard=True)
PROFILE_GENDER_SELECTION_KEYBOARD = ReplyKeyboardMarkup([["زن"], ["مرد"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True, one_time_keyboard=True)
CLUB_JOIN_CONFIRMATION_KEYBOARD = ReplyKeyboardMarkup([["✅ بله، عضو می‌شوم"], ["❌ خیر، فعلاً نه"]], resize_keyboard=True, one_time_keyboard=True)
PROFILE_VIEW_KEYBOARD = ReplyKeyboardMarkup([["✏️ ویرایش نام"], ["💔 لغو عضویت از باشگاه"], ["🔙 بازگشت به منوی اصلی"]], resize_keyboard=True)
CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD = ReplyKeyboardMarkup([["✅ بله، عضویتم لغو شود"], ["❌ خیر، منصرف شدم"]], resize_keyboard=True, one_time_keyboard=True)
NAME_EDIT_BACK_KEYBOARD = ReplyKeyboardMarkup([["🔙 انصراف و بازگشت به پروفایل"]], resize_keyboard=True, one_time_keyboard=True)

HEALTH_TIPS_FOR_CLUB = [ "روزانه حداقل ۸ لیوان آب بنوشید.", "خواب کافی (۷-۸ ساعت) ضروری است.", "حداقل ۳۰ دقیقه فعالیت بدنی روزانه داشته باشید." ]

# --- توابع دیتابیس ---
def get_or_create_user_profile(user_id: str, username: str = None, first_name: str = None) -> dict:
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Profile for user {user_id} will be in-memory mock.")
        return {"user_id": user_id, "username": username, "first_name": first_name, "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [], "club_tip_usage_count": 0, "club_join_date": None, "name_first_db": None, "name_last_db": None, "full_profile_completion_points_awarded": False}

    user_ref = db.collection('users').document(user_id)
    try: user_doc = user_ref.get()
    except Exception as e:
        logger.error(f"DB: خطا هنگام get() برای کاربر {user_id}: {e}", exc_info=True)
        return {"user_id": user_id, "username": username, "first_name": first_name, "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [], "club_tip_usage_count": 0, "club_join_date": None, "name_first_db": None, "name_last_db": None, "full_profile_completion_points_awarded": False}

    default_fields = {'age': None, 'gender': None, 'is_club_member': False, 'points': 0, 'badges': [], 'club_tip_usage_count': 0, 'club_join_date': None, 'name_first_db': None, 'name_last_db': None, 'full_profile_completion_points_awarded': False}
    if user_doc.exists:
        user_data = user_doc.to_dict()
        needs_update_in_db = False
        for key, val in default_fields.items():
            if key not in user_data: user_data[key] = val; needs_update_in_db = True
        if needs_update_in_db:
             update_payload = {k:v for k,v in default_fields.items() if k not in user_doc.to_dict()}
             if update_payload:
                try: user_ref.update(update_payload)
                except Exception as e_upd: logger.error(f"DB: خطا آپدیت فیلد پیش‌فرض برای کاربر {user_id}: {e_upd}")
        return user_data
    else:
        user_data = {'user_id': user_id, 'username': username, 'first_name': first_name, 'registration_date': firestore.SERVER_TIMESTAMP, 'last_interaction_date': firestore.SERVER_TIMESTAMP}
        for key, val in default_fields.items(): user_data[key] = val
        try: user_ref.set(user_data)
        except Exception as e_set: logger.error(f"DB: خطا ایجاد پروفایل جدید برای کاربر {user_id}: {e_set}")
        return user_data

def update_user_profile_data(user_id: str, data_to_update: dict) -> None:
    if not db: return
    user_ref = db.collection('users').document(user_id)
    data_to_update['last_updated_date'] = firestore.SERVER_TIMESTAMP
    try:
        user_ref.update(data_to_update)
        logger.info(f"DB: پروفایل {user_id} با {data_to_update} آپدیت شد.")
    except Exception as e:
        logger.error(f"DB: خطا آپدیت پروفایل {user_id} با {data_to_update}: {e}", exc_info=True)

def get_user_profile_data(user_id: str) -> dict | None:
    if not db: return None
    user_ref = db.collection('users').document(user_id)
    try:
        user_doc = user_ref.get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            defaults = {'is_club_member': False, 'points': 0, 'badges': [], 'club_tip_usage_count': 0, 'club_join_date': None, 'age': None, 'gender': None, 'name_first_db': None, 'name_last_db': None, 'full_profile_completion_points_awarded': False}
            for key, val in defaults.items():
                if key not in user_data: user_data[key] = val
            return user_data
    except Exception as e:
        logger.error(f"DB: خطا خواندن پروفایل {user_id}: {e}", exc_info=True)
    return None

# --- توابع کمکی ربات ---
async def ask_openrouter(system_prompt: str, chat_history: list, model_override: str = None) -> str:
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
    is_member = False
    if 'is_club_member_cached' in context.user_data: del context.user_data['is_club_member_cached']
    if db:
        try:
            user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
            is_member = user_profile.get('is_club_member', False) if user_profile else False
            context.user_data['is_club_member_cached'] = is_member
        except Exception as e: logger.error(f"خطا خواندن وضعیت عضویت {user_id_str}: {e}"); is_member = False
    else: context.user_data['is_club_member_cached'] = False
    if is_member:
        keyboard_layout = [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"], ["👤 پروفایل و باشگاه"], ["📣 نکته سلامتی باشگاه"]]
    else:
        keyboard_layout = [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"], ["⭐ عضویت در باشگاه تافته"]]
    return ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True)

# --- کنترل‌کننده‌های اصلی ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id_str = str(user.id) 
    message_prefix = "درخواست لغو شما انجام شد. " if context.user_data.get('_is_cancel_flow', False) else ""
    if context.user_data.get('_is_cancel_flow', False): del context.user_data['_is_cancel_flow']
    logger.info(f"کاربر {user_id_str} ({user.full_name or user.username}) /start یا بازگشت/لغو به منوی اصلی.")
    keys_to_clear = ["doctor_chat_history", "system_prompt_for_doctor", "age_temp", "is_club_member_cached", 
                     "awaiting_field_to_edit", "temp_first_name", "profile_completion_flow_active", 
                     "club_join_after_profile_flow", "temp_profile_first_name", "temp_profile_last_name", "temp_profile_age"]
    for key in keys_to_clear:
        if key in context.user_data: del context.user_data[key]
    logger.info(f"اطلاعات جلسه برای {user_id_str} پاکسازی شد.")
    if db: 
        try: await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        except Exception as e: logger.error(f"خطا get_or_create_user_profile (start) برای {user_id_str}: {e}", exc_info=True)
    
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)
    welcome_text = f"سلام {user.first_name or 'کاربر'}! 👋\nربات تافته آماده خدمت‌رسانی است:"
    if message_prefix: welcome_text = message_prefix + "به منوی اصلی بازگشتید."
    
    effective_chat_id = update.effective_chat.id
    try:
        is_direct_start = update.message and update.message.text == "/start"
        is_photo_present = hasattr(update.message, 'photo') and update.message.photo
        if is_direct_start and not is_photo_present : 
            await context.bot.send_photo(chat_id=effective_chat_id, photo=WELCOME_IMAGE_URL, caption=welcome_text, reply_markup=dynamic_main_menu)
        else: 
            await context.bot.send_message(chat_id=effective_chat_id, text=welcome_text, reply_markup=dynamic_main_menu)
    except Exception as e:
        logger.error(f"خطا ارسال خوش‌آمدگویی برای {user_id_str}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=effective_chat_id, text=welcome_text, reply_markup=dynamic_main_menu)
    return States.MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id = user.id if user else "Unknown"
    logger.info(f"User {user_id} called /cancel. Delegating to start handler.")
    context.user_data['_is_cancel_flow'] = True
    if update.effective_chat: await context.bot.send_message(chat_id=update.effective_chat.id, text="درخواست شما لغو شد.", reply_markup=ReplyKeyboardRemove())
    return await start(update, context)

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} در منوی اصلی گزینه '{text}' را انتخاب کرد.")
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)

    if text == "👨‍⚕️ دکتر تافته":
        context.user_data['profile_completion_flow_active'] = True 
        context.user_data['club_join_after_profile_flow'] = False 
        
        age, gender, name_first, name_last = None, None, None, None
        if db:
            user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
            if user_profile: age, gender, name_first, name_last = user_profile.get("age"), user_profile.get("gender"), user_profile.get("name_first_db"), user_profile.get("name_last_db")
        
        if age and gender and name_first and name_last: 
            system_prompt = _prepare_doctor_system_prompt(age, gender)
            context.user_data["system_prompt_for_doctor"] = system_prompt
            context.user_data["doctor_chat_history"] = []
            await update.message.reply_text(f"مشخصات شما (نام: {name_first} {name_last}, سن: {age}، جنسیت: {gender}) موجود است.\nسوال پزشکی خود را بپرسید.", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
            return States.DOCTOR_CONVERSATION
        else: 
            await update.message.reply_text("برای استفاده از دکتر تافته، پروفایل خود را تکمیل کنید.\nنام کوچک:", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
            return States.AWAITING_PROFILE_FIRST_NAME
            
    elif text == "📦 راهنمای محصولات":
        keyboard = [[InlineKeyboardButton("مشاهده وب‌سایت تافته", url=URL_TAFTEH_WEBSITE)]]
        await update.message.reply_text("برای مشاهده محصولات:", reply_markup=InlineKeyboardMarkup(keyboard))
        return States.MAIN_MENU
        
    elif text == "⭐ عضویت در باشگاه تافته": 
        age, gender, name_first, name_last = None, None, None, None
        if db:
            user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
            if user_profile: age, gender, name_first, name_last = user_profile.get("age"), user_profile.get("gender"), user_profile.get("name_first_db"), user_profile.get("name_last_db")
        
        if not (age and gender and name_first and name_last): 
            context.user_data['profile_completion_flow_active'] = True
            context.user_data['club_join_after_profile_flow'] = True 
            await update.message.reply_text("برای عضویت، ابتدا پروفایل خود را تکمیل کنید (نام، ن.خانوادگی، سن، جنسیت).\nنام کوچک:", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
            return States.AWAITING_PROFILE_FIRST_NAME
        else: 
            await update.message.reply_text("پروفایل شما کامل است. آیا مایل به عضویت در باشگاه هستید؟", reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD)
            return States.AWAITING_CLUB_JOIN_CONFIRMATION
        
    elif text == "👤 پروفایل و باشگاه": 
        return await my_profile_info_handler(update, context)
    elif text == "📣 نکته سلامتی باشگاه": 
        return await health_tip_command_handler(update, context)
    else: 
        await update.message.reply_text("گزینه نامعتبر.", reply_markup=dynamic_main_menu)
        return States.MAIN_MENU

# --- کنترل‌کننده‌های جریان تکمیل پروفایل کامل ---
async def awaiting_profile_first_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text.strip(); user_id_str = str(update.effective_user.id)
    if text == "🔙 بازگشت به منوی اصلی":
        for key in ['profile_completion_flow_active', 'club_join_after_profile_flow']: context.user_data.pop(key, None)
        return await start(update, context)
    if not text or len(text) < 2 or len(text) > 50:
        await update.message.reply_text("نام معتبر نیست. دوباره وارد کنید یا بازگردید.", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
        return States.AWAITING_PROFILE_FIRST_NAME
    context.user_data['temp_profile_first_name'] = text
    await update.message.reply_text("نام خانوادگی (یا بازگردید):", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
    return States.AWAITING_PROFILE_LAST_NAME

async def awaiting_profile_last_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text.strip(); user_id_str = str(update.effective_user.id)
    if text == "🔙 بازگشت به منوی اصلی":
        for key in ['temp_profile_first_name', 'profile_completion_flow_active', 'club_join_after_profile_flow']: context.user_data.pop(key, None)
        return await start(update, context)
    if not text or len(text) < 2 or len(text) > 50:
        await update.message.reply_text("نام خانوادگی معتبر نیست. دوباره وارد کنید یا بازگردید.", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
        return States.AWAITING_PROFILE_LAST_NAME
    context.user_data['temp_profile_last_name'] = text
    await update.message.reply_text("سن خود را به عدد وارد کنید (یا بازگردید):", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
    return States.AWAITING_PROFILE_AGE

async def awaiting_profile_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text.strip(); user_id_str = str(update.effective_user.id)
    if text == "🔙 بازگشت به منوی اصلی":
        for key in ['temp_profile_first_name', 'temp_profile_last_name', 'profile_completion_flow_active', 'club_join_after_profile_flow']: context.user_data.pop(key, None)
        return await start(update, context)
    if not text.isdigit() or not (1 <= int(text) <= 120):
        await update.message.reply_text("سن معتبر (۱-۱۲۰) وارد کنید یا بازگردید.", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
        return States.AWAITING_PROFILE_AGE
    context.user_data["temp_profile_age"] = int(text)
    await update.message.reply_text("جنسیت خود را انتخاب کنید (یا بازگردید):", reply_markup=PROFILE_GENDER_SELECTION_KEYBOARD)
    return States.AWAITING_PROFILE_GENDER

async def awaiting_profile_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text.strip(); user = update.effective_user; user_id_str = str(user.id)
    if text == "🔙 بازگشت به منوی اصلی":
        for key in ['temp_profile_first_name', 'temp_profile_last_name', 'temp_profile_age', 'profile_completion_flow_active', 'club_join_after_profile_flow']: context.user_data.pop(key, None)
        return await start(update, context)

    gender_input = text
    first_name = context.user_data.pop('temp_profile_first_name', None)
    last_name = context.user_data.pop('temp_profile_last_name', None)
    age = context.user_data.pop('temp_profile_age', None)
    
    if not (first_name and last_name and age and gender_input in ["زن", "مرد"]):
        logger.error(f"خطا: اطلاعات ناقص پروفایل برای {user_id_str} در مرحله نهایی.")
        await update.message.reply_text("مشکلی پیش آمد، از ابتدا تلاش کنید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        for key_to_del in ['profile_completion_flow_active', 'club_join_after_profile_flow']: context.user_data.pop(key_to_del, None)
        return await start(update, context)
    
    gender = gender_input
    awarded_full_profile_badge_and_points = False
    if db:
        try:
            user_profile_before = await asyncio.to_thread(get_user_profile_data, user_id_str)
            update_payload = {"name_first_db": first_name, "name_last_db": last_name, "age": age, "gender": gender}
            if user_profile_before and not user_profile_before.get('full_profile_completion_points_awarded', False):
                update_payload["points"] = firestore.Increment(POINTS_FOR_FULL_PROFILE_COMPLETION)
                update_payload["full_profile_completion_points_awarded"] = True
                awarded_full_profile_badge_and_points = True
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            logger.info(f"پروفایل کامل {user_id_str} در DB ذخیره شد.")
            if awarded_full_profile_badge_and_points: logger.info(f"کاربر {user_id_str} واجد شرایط امتیاز و نشان پروفایل کامل است.")
        except Exception as e: logger.error(f"خطا ذخیره پروفایل کامل برای {user_id_str}: {e}", exc_info=True)

    await update.message.reply_text(f"✅ پروفایل شما تکمیل شد:\nنام: {first_name} {last_name}\nسن: {age}\nجنسیت: {gender}", reply_markup=ReplyKeyboardRemove())
    if awarded_full_profile_badge_and_points:
        await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_FULL_PROFILE_COMPLETION, "تکمیل پروفایل (نام، سن و جنسیت)")
        await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_FULL_PROFILE)

    if context.user_data.pop('club_join_after_profile_flow', False):
        logger.info(f"کاربر {user_id_str} پروفایل را تکمیل کرد، هدایت به تایید عضویت باشگاه.")
        await update.message.reply_text("برای عضویت در باشگاه مشتریان تایید نهایی را انجام دهید:", reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD)
        return States.AWAITING_CLUB_JOIN_CONFIRMATION
    elif context.user_data.pop('profile_completion_flow_active', False): # اگر از مسیر دکتر تافته آمده بود
        system_prompt = _prepare_doctor_system_prompt(age, gender)
        context.user_data["system_prompt_for_doctor"] = system_prompt
        context.user_data["doctor_chat_history"] = []
        await update.message.reply_text("اکنون سوال پزشکی خود را از دکتر تافته بپرسید.", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
        return States.DOCTOR_CONVERSATION
    return await start(update, context)

async def handle_club_join_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id_str = str(user.id); text = update.message.text
    logger.info(f"کاربر {user_id_str} به عضویت باشگاه پاسخ داد: '{text}'")
    if text == "✅ بله، عضو می‌شوم":
        if not db:
            await update.message.reply_text("سیستم باشگاه در دسترس نیست.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            return await start(update, context)
        try:
            await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
            await asyncio.to_thread(update_user_profile_data, user_id_str, {"is_club_member": True, "points": firestore.Increment(POINTS_FOR_JOINING_CLUB), "club_join_date": firestore.SERVER_TIMESTAMP})
            context.user_data['is_club_member_cached'] = True
            await update.message.reply_text(f"عضویت شما در باشگاه تافته انجام شد! ✨", reply_markup=ReplyKeyboardRemove())
            await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_JOINING_CLUB, "عضویت در باشگاه مشتریان")
            await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_CLUB_MEMBER)
            await context.bot.send_message(chat_id=update.effective_chat.id, text="به منوی اصلی بازگشتید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            return States.MAIN_MENU
        except Exception as e: logger.error(f"خطا در عضویت باشگاه برای {user_id_str}: {e}", exc_info=True)
    elif text == "❌ خیر، فعلاً نه":
        await update.message.reply_text("متوجه شدم.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
    else:
        await update.message.reply_text("گزینه نامعتبر.", reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD)
        return States.AWAITING_CLUB_JOIN_CONFIRMATION
    return await start(update, context)

async def doctor_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    logger.info(f"--- DCH Entered --- User: {update.effective_user.id}, Text: '{update.message.text}', History: {len(context.user_data.get('doctor_chat_history', []))}")
    user_question = update.message.text; user = update.effective_user; user_id_str = str(user.id)
    chat_history = context.user_data.get("doctor_chat_history", [])
    system_prompt = context.user_data.get("system_prompt_for_doctor")
    if not system_prompt:
        logger.warning(f"DCH: پرامپت دکتر برای {user_id_str} یافت نشد! بازسازی...")
        age_db, gender_db, name_f, name_l = None, None, None, None
        if db:
            profile_db = await asyncio.to_thread(get_user_profile_data, user_id_str)
            if profile_db: age_db, gender_db, name_f, name_l = profile_db.get("age"), profile_db.get("gender"), profile_db.get("name_first_db"), profile_db.get("name_last_db")
        if age_db and gender_db and name_f and name_l:
            system_prompt = _prepare_doctor_system_prompt(age_db, gender_db)
            context.user_data["system_prompt_for_doctor"] = system_prompt
            logger.info(f"DCH: پرامپت دکتر برای {user_id_str} بازسازی شد.")
        else:
            logger.error(f"DCH: عدم امکان بازسازی پرامپت دکتر برای {user_id_str}. پروفایل ناقص. هدایت به تکمیل پروفایل.")
            context.user_data['profile_completion_flow_active'] = True; context.user_data['club_join_after_profile_flow'] = False
            await update.message.reply_text("برای استفاده از دکتر، پروفایل خود را کامل کنید.\nنام کوچک:", reply_markup=PROFILE_INPUT_BACK_KEYBOARD)
            return States.AWAITING_PROFILE_FIRST_NAME
    if user_question == "🔙 بازگشت به منوی اصلی": return await start(update, context)
    elif user_question == "❓ سوال جدید از دکتر":
        context.user_data["doctor_chat_history"] = []
        await update.message.reply_text("تاریخچه پاک شد. سوال جدید:", reply_markup=DOCTOR_CONVERSATION_KEYBOARD); return States.DOCTOR_CONVERSATION
    chat_history.append({"role": "user", "content": user_question})
    await update.message.reply_text("⏳ دکتر تافته در حال بررسی...")
    assistant_response = await ask_openrouter(system_prompt, chat_history)
    chat_history.append({"role": "assistant", "content": assistant_response})
    await update.message.reply_text(assistant_response, parse_mode="Markdown", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
    return States.DOCTOR_CONVERSATION

async def my_profile_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} /myprofile یا دکمه پروفایل.")
    if not db:
        await update.message.reply_text("سیستم پروفایل در دسترس نیست.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        return States.MAIN_MENU
    try:
        profile = await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        msg = f"👤 **پروفایل شما** 👤\n\nنام: {profile.get('name_first_db') or profile.get('first_name') or ''} {profile.get('name_last_db','')}\nسن: {profile.get('age') or 'ثبت نشده'}\nجنسیت: {profile.get('gender') or 'ثبت نشده'}\n\n"
        if profile.get('is_club_member'):
            msg += f"عضویت باشگاه: ✅ فعال\nامتیاز: {profile.get('points',0)} 🌟\n"
            badges = profile.get('badges', [])
            if badges: msg += "نشان‌ها:\n" + "".join([f"  - {b}\n" for b in badges])
            else: msg += "هنوز نشانی ندارید.\n"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=PROFILE_VIEW_KEYBOARD)
            return States.PROFILE_VIEW
        else:
            await update.message.reply_text("شما عضو باشگاه نیستید. برای مشاهده پروفایل، ابتدا از منو عضو شوید.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            return States.MAIN_MENU
    except Exception as e: logger.error(f"خطا نمایش پروفایل {user_id_str}: {e}", exc_info=True)
    return States.MAIN_MENU

async def profile_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id_str = str(user.id); text = update.message.text
    logger.info(f"کاربر {user_id_str} در PROFILE_VIEW گزینه '{text}'.")
    if text == "💔 لغو عضویت از باشگاه":
        await update.message.reply_text("مطمئن هستید؟\n⚠️ **اخطار:** امتیازات، نشان‌ها و اطلاعات پروفایل (سن، جنسیت، نام) شما ریست خواهد شد.", reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD, parse_mode="Markdown")
        return States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION
    elif text == "✏️ تکمیل/ویرایش نام":
        await update.message.reply_text("نام کوچک خود را وارد کنید:", reply_markup=NAME_EDIT_BACK_KEYBOARD)
        return States.AWAITING_EDIT_FIRST_NAME
    elif text == "🔙 بازگشت به منوی اصلی": return await start(update, context)
    await update.message.reply_text("گزینه نامعتبر.", reply_markup=PROFILE_VIEW_KEYBOARD)
    return States.PROFILE_VIEW

async def handle_cancel_membership_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id_str = str(user.id); text = update.message.text
    logger.info(f"کاربر {user_id_str} به لغو عضویت پاسخ داد: '{text}'.")
    if text == "✅ بله، عضویتم لغو شود":
        if not db:
            await update.message.reply_text("سیستم باشگاه در دسترس نیست.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            return await start(update, context)
        try:
            payload = {"is_club_member": False, "points": 0, "badges": [], "club_join_date": None, "club_tip_usage_count": 0, "age": None, "gender": None, "name_first_db": None, "name_last_db": None, "profile_completion_points_awarded": False, "full_profile_completion_points_awarded": False}
            await asyncio.to_thread(update_user_profile_data, user_id_str, payload)
            context.user_data['is_club_member_cached'] = False
            await update.message.reply_text("عضویت شما لغو و اطلاعات پروفایل ریست شد.")
        except Exception as e: logger.error(f"خطا لغو عضویت {user_id_str}: {e}", exc_info=True)
    elif text == "❌ خیر، منصرف شدم":
        await update.message.reply_text("خوشحالیم که در باشگاه باقی می‌مانید!")
        return await my_profile_info_handler(update, context)
    else:
        await update.message.reply_text("گزینه نامعتبر.", reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD)
        return States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION
    return await start(update, context)

async def edit_first_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id_str = str(user.id); text = update.message.text.strip()
    if text == "🔙 انصراف و بازگشت به پروفایل": return await my_profile_info_handler(update, context)
    if not text or len(text) < 2 or len(text) > 50:
        await update.message.reply_text("نام معتبر نیست.", reply_markup=NAME_EDIT_BACK_KEYBOARD); return States.AWAITING_EDIT_FIRST_NAME
    context.user_data['temp_edit_first_name'] = text
    await update.message.reply_text("نام خانوادگی جدید:", reply_markup=NAME_EDIT_BACK_KEYBOARD)
    return States.AWAITING_EDIT_LAST_NAME

async def edit_last_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id_str = str(user.id); last_name_text = update.message.text.strip()
    if last_name_text == "🔙 انصراف و بازگشت به پروفایل":
        if 'temp_edit_first_name' in context.user_data: del context.user_data['temp_edit_first_name']
        return await my_profile_info_handler(update, context)
    if not last_name_text or len(last_name_text) < 2 or len(last_name_text) > 50:
        await update.message.reply_text("نام خانوادگی معتبر نیست.", reply_markup=NAME_EDIT_BACK_KEYBOARD); return States.AWAITING_EDIT_LAST_NAME
    first_name = context.user_data.pop('temp_edit_first_name', None)
    if not first_name: return await my_profile_info_handler(update, context)
    
    awarded_full_profile_badge_on_edit = False
    if db:
        try:
            user_profile_before = await asyncio.to_thread(get_user_profile_data, user_id_str)
            update_payload = {"name_first_db": first_name, "name_last_db": last_name_text}
            if user_profile_before and \
               not user_profile_before.get('full_profile_completion_points_awarded', False) and \
               first_name and last_name_text and \
               user_profile_before.get('age') and user_profile_before.get('gender'):
                update_payload["points"] = firestore.Increment(POINTS_FOR_FULL_PROFILE_COMPLETION)
                update_payload["full_profile_completion_points_awarded"] = True
                awarded_full_profile_badge_on_edit = True
            
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            await update.message.reply_text(f"نام شما به '{first_name} {last_name_text}' به‌روز شد.")
            if awarded_full_profile_badge_on_edit:
                await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_FULL_PROFILE_COMPLETION, "تکمیل پروفایل (نام، سن و جنسیت)")
                await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_FULL_PROFILE)
        except Exception as e: logger.error(f"خطا در ذخیره نام برای {user_id_str}: {e}", exc_info=True)
    else: await update.message.reply_text(f"نام شما '{first_name} {last_name_text}' تنظیم شد (DB غیرفعال).")
    return await my_profile_info_handler(update, context)

async def health_tip_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user; user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست نکته سلامتی.")
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)
    if not db:
        await update.message.reply_text("سیستم باشگاه در دسترس نیست.", reply_markup=dynamic_main_menu); return States.MAIN_MENU
    try:
        user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
        if user_profile and user_profile.get('is_club_member'):
            tip_system_prompt = ("شما یک متخصص سلامت هستید. یک نکته سلامتی کوتاه (۱-۲ جمله)، مفید، علمی و کاربردی به فارسی ارائه دهید. "
                                 "نکته عمومی باشد. پاسخ فقط خود نکته باشد، بدون مقدمه.")
            health_tip = await ask_openrouter(tip_system_prompt, [{"role": "user", "content": "یک نکته سلامتی"}])
            if health_tip.startswith("❌"):
                 await update.message.reply_text("قادر به ارائه نکته نیستم.", reply_markup=dynamic_main_menu); return States.MAIN_MENU
            await update.message.reply_text(f"⚕️ **نکته سلامتی اعضا:**\n\n_{health_tip}_", parse_mode="Markdown", reply_markup=dynamic_main_menu)
            new_tip_usage_count = user_profile.get('club_tip_usage_count', 0) + 1
            await asyncio.to_thread(update_user_profile_data, user_id_str, {"club_tip_usage_count": new_tip_usage_count})
            if new_tip_usage_count >= CLUB_TIP_BADGE_THRESHOLD:
                await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_HEALTH_EXPLORER)
        else: await update.message.reply_text("این بخش مخصوص اعضای باشگاه است.", reply_markup=dynamic_main_menu)
    except Exception as e: logger.error(f"خطا ارسال نکته سلامتی برای {user_id_str}: {e}", exc_info=True)
    return States.MAIN_MENU

async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; user_id_str = str(user.id) if user else "UnknownUser"
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)
    logger.warning(f"--- GLOBAL FALLBACK --- User: {user_id_str}, Text: '{update.message.text if update.message else 'N/A'}', Data: {context.user_data}")
    if update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="متاسفم، متوجه نشدم. لطفاً از منو انتخاب کنید یا /start را بزنید.", reply_markup=dynamic_main_menu)
    else: logger.error(f"Fallback: no effective_chat for {user_id_str}")

# --- Flask App & Main Execution ---
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check(): return 'ربات تلگرام تافته فعال است!', 200

def run_flask_app():
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"ترد Flask: شروع وب سرور روی 0.0.0.0:{port}")
    try: flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e: logger.error(f"ترد Flask: خطا در اجرا: {e}", exc_info=True)

if __name__ == '__main__':
    logger.info("بلوک اصلی برنامه آغاز شد.")
    if db is None: logger.warning("*"*65 + "\n* دیتابیس Firestore مقداردهی اولیه نشده! ربات با قابلیت محدود اجرا می‌شود. *\n" + "*"*65)

    flask_thread = threading.Thread(target=run_flask_app, name="FlaskThread", daemon=True)
    flask_thread.start()
    logger.info("ترد Flask شروع به کار کرد.")

    telegram_application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            States.MAIN_MENU: [MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات|⭐ عضویت در باشگاه تافته|👤 پروفایل و باشگاه|📣 نکته سلامتی باشگاه)$"), main_menu_handler)],
            States.AWAITING_PROFILE_FIRST_NAME: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_profile_first_name_handler)],
            States.AWAITING_PROFILE_LAST_NAME: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_profile_last_name_handler)],
            States.AWAITING_PROFILE_AGE: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_profile_age_handler)],
            States.AWAITING_PROFILE_GENDER: [MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), MessageHandler(filters.Regex("^(زن|مرد)$"), awaiting_profile_gender_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("لطفاً با دکمه انتخاب کنید یا بازگردید.",reply_markup=PROFILE_GENDER_SELECTION_KEYBOARD))],
            States.DOCTOR_CONVERSATION: [MessageHandler(filters.Regex("^(❓ سوال جدید از دکتر|🔙 بازگشت به منوی اصلی)$"), doctor_conversation_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, doctor_conversation_handler)],
            States.AWAITING_CLUB_JOIN_CONFIRMATION: [MessageHandler(filters.Regex("^(✅ بله، عضو می‌شوم|❌ خیر، فعلاً نه)$"), handle_club_join_confirmation), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("با دکمه‌ها پاسخ دهید.",reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD))],
            States.PROFILE_VIEW: [MessageHandler(filters.Regex("^(✏️ تکمیل/ویرایش نام|💔 لغو عضویت از باشگاه|🔙 بازگشت به منوی اصلی)$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("گزینه پروفایل را انتخاب کنید.",reply_markup=PROFILE_VIEW_KEYBOARD))],
            States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION: [MessageHandler(filters.Regex("^(✅ بله، عضویتم لغو شود|❌ خیر، منصرف شدم)$"), handle_cancel_membership_confirmation), MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("با دکمه‌ها پاسخ دهید.",reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD))],
            States.AWAITING_EDIT_FIRST_NAME: [MessageHandler(filters.Regex("^🔙 انصراف و بازگشت به پروفایل$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, edit_first_name_handler)],
            States.AWAITING_EDIT_LAST_NAME: [MessageHandler(filters.Regex("^🔙 انصراف و بازگشت به پروفایل$"), profile_view_handler), MessageHandler(filters.TEXT & ~filters.COMMAND, edit_last_name_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start), MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start)],
        persistent=False, name="main_conversation", allow_reentry=True # allow_reentry اضافه شده
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