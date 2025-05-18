import logging
import httpx
import os
from enum import Enum
from dotenv import load_dotenv
import threading
from flask import Flask
import asyncio
import random

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

# بارگذاری متغیرهای محیطی از فایل .env (برای اجرای محلی مفید است)
load_dotenv()

# مقداردهی اولیه Firebase Admin SDK در ابتدای برنامه
db = None 
try:
    cred_path_render = os.getenv("FIREBASE_CREDENTIALS_PATH", "/etc/secrets/firebase-service-account-key.json")
    cred_path_local = "firebase-service-account-key.json" 
    cred_path = cred_path_render if os.path.exists(cred_path_render) else cred_path_local
    
    if not os.path.exists(cred_path):
        # استفاده از logging استاندارد پایتون قبل از basicConfig سفارشی
        # این پیام ممکن است در Render نمایش داده نشود اگر basicConfig بعدا override کند
        # اما برای دیباگ اولیه می‌تواند مفید باشد.
        print(f"هشدار: فایل کلید Firebase در مسیر '{cred_path}' یافت نشد.")
        logging.warning(f"فایل کلید Firebase در مسیر '{cred_path}' یافت نشد. ربات بدون اتصال به دیتابیس اجرا خواهد شد.")
    else:
        cred = credentials.Certificate(cred_path)
        if not firebase_admin._apps: 
            firebase_admin.initialize_app(cred)
        db = firestore.client() 
        logging.info("Firebase Admin SDK با موفقیت مقداردهی اولیه شد و به Firestore متصل است.")
except Exception as e:
    print(f"خطای بحرانی در مقداردهی اولیه Firebase Admin SDK: {e}")
    logging.error(f"خطای بحرانی در مقداردهی اولیه Firebase Admin SDK: {e}", exc_info=True)


# تنظیمات لاگ‌گیری اصلی برنامه
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()] # اطمینان از خروجی به کنسول (stdout/stderr)
)
logger = logging.getLogger(__name__) 

logger.info("اسکریپت main.py شروع به کار کرد. در حال بررسی متغیرهای محیطی...")

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL_NAME", "openai/gpt-3.5-turbo")
WELCOME_IMAGE_URL = os.getenv("WELCOME_IMAGE_URL", "https://tafteh.ir/wp-content/uploads/2024/12/navar-nehdashti2-600x600.jpg")
URL_TAFTEH_WEBSITE = "https://tafteh.ir/"

POINTS_FOR_JOINING_CLUB = 50
POINTS_FOR_PROFILE_COMPLETION = 20 # برای سن و جنسیت
POINTS_FOR_NAME_COMPLETION = 15 
POINTS_FOR_CLUB_TIP = 2

BADGE_CLUB_MEMBER = "عضو باشگاه تافته 🏅"
BADGE_PROFILE_COMPLETE = "پروفایل پایه کامل 🧑‍🔬" 
BADGE_FULL_PROFILE = "پروفایل طلایی ✨" 
BADGE_HEALTH_EXPLORER = "کاشف سلامت 🧭"
CLUB_TIP_BADGE_THRESHOLD = 3

if not TELEGRAM_TOKEN:
    logger.error("!!! بحرانی: توکن تلگرام (BOT_TOKEN) در متغیرهای محیطی یافت نشد. برنامه خارج می‌شود.")
    exit(1)
else:
    logger.info(f"توکن تلگرام با موفقیت بارگذاری شد (بخشی از توکن: ...{TELEGRAM_TOKEN[-6:]}).")

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
    AWAITING_CLUB_JOIN_CONFIRMATION = 5
    PROFILE_VIEW = 6
    AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION = 7
    AWAITING_FIRST_NAME = 8 
    AWAITING_LAST_NAME = 9  

# --- تعریف کیبوردها ---
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
    [
        ["✏️ تکمیل/ویرایش نام"], 
        ["💔 لغو عضویت از باشگاه"],
        ["🔙 بازگشت به منوی اصلی"]
    ],
    resize_keyboard=True
)
CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD = ReplyKeyboardMarkup(
    [["✅ بله، عضویتم لغو شود"], ["❌ خیر، منصرف شدم"]],
    resize_keyboard=True, one_time_keyboard=True
)
NAME_INPUT_KEYBOARD = ReplyKeyboardMarkup(
    [["🔙 انصراف و بازگشت به پروفایل"]], resize_keyboard=True, one_time_keyboard=True
)

HEALTH_TIPS_FOR_CLUB = [
    "روزانه حداقل ۸ لیوان آب بنوشید تا بدنتان هیدراته بماند.",
    "خواب کافی (۷-۸ ساعت) برای بازیابی انرژی و سلامت روان ضروری است.",
    "حداقل ۳۰ دقیقه فعالیت بدنی متوسط در بیشتر روزهای هفته به حفظ سلامت قلب کمک می‌کند.",
    "مصرف میوه‌ها و سبزیجات رنگارنگ، ویتامین‌ها و آنتی‌اکسیدان‌های لازم را به بدن شما می‌رساند.",
    "برای کاهش استرس، تکنیک‌های آرام‌سازی مانند مدیتیشن یا تنفس عمیق را امتحان کنید."
]

async def ask_openrouter(system_prompt: str, chat_history: list) -> str:
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

async def notify_points_awarded(bot: Application.bot, chat_id: int, user_id_str: str, points_awarded: int, reason: str):
    if not db: return 
    try:
        user_profile_updated = await asyncio.to_thread(get_user_profile_data, user_id_str)
        total_points = user_profile_updated.get('points', 0) if user_profile_updated else points_awarded
        
        message = f"✨ شما {points_awarded} امتیاز برای '{reason}' دریافت کردید!\n"
        message += f"مجموع امتیاز شما اکنون: {total_points} است. 🌟"
        await bot.send_message(chat_id=chat_id, text=message) 
        logger.info(f"به کاربر {user_id_str} برای '{reason}'، {points_awarded} امتیاز اطلاع داده شد. مجموع امتیاز: {total_points}")
    except Exception as e:
        logger.error(f"خطا در اطلاع‌رسانی امتیاز به کاربر {user_id_str}: {e}", exc_info=True)

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
            else:
                logger.info(f"کاربر {user_id_str} از قبل نشان '{badge_name}' را داشته است.")
    except Exception as e:
        logger.error(f"خطا در اعطای نشان '{badge_name}' به کاربر {user_id_str}: {e}", exc_info=True)

def get_or_create_user_profile(user_id: str, username: str = None, first_name: str = None) -> dict:
    if not db:
        logger.warning(f"DB: Firestore client (db) is None. Profile for user {user_id} will be in-memory mock.")
        return {"user_id": user_id, "username": username, "first_name": first_name, 
                "age": None, "gender": None, "is_club_member": False, "points": 0, "badges": [],
                "profile_completion_points_awarded": False, "club_tip_usage_count": 0, "club_join_date": None,
                "name_first_db": None, "name_last_db": None, "profile_name_completion_points_awarded": False}

    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()

    default_fields = {
        'age': None, 'gender': None, 'is_club_member': False, 'points': 0, 'badges': [],
        'profile_completion_points_awarded': False, 'club_tip_usage_count': 0,
        'club_join_date': None, 'name_first_db': None, 'name_last_db': None,
        'profile_name_completion_points_awarded': False
    }

    if user_doc.exists:
        user_data = user_doc.to_dict()
        needs_update_in_db = False
        for key, default_value in default_fields.items():
            if key not in user_data:
                user_data[key] = default_value
                needs_update_in_db = True 
        if needs_update_in_db:
             logger.info(f"DB: به‌روزرسانی پروفایل کاربر {user_id} با فیلدهای پیش‌فرض جدید در زمان خواندن.")
             update_payload = {k:v for k,v in default_fields.items() if k not in user_doc.to_dict()}
             if update_payload: 
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

def update_user_profile_data(user_id: str, data_to_update: dict) -> None:
    if not db: return
    user_ref = db.collection('users').document(user_id)
    data_to_update['last_updated_date'] = firestore.SERVER_TIMESTAMP
    user_ref.update(data_to_update) 
    logger.info(f"DB: پروفایل کاربر {user_id} با داده‌های {data_to_update} در Firestore به‌روز شد.")

def get_user_profile_data(user_id: str) -> dict | None:
    if not db: return None
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    if user_doc.exists:
        user_data = user_doc.to_dict()
        defaults = { 
            'is_club_member': False, 'points': 0, 'badges': [],
            'profile_completion_points_awarded': False, 'club_tip_usage_count': 0,
            'club_join_date': None, 'age': None, 'gender': None,
            'name_first_db': None, 'name_last_db': None, 'profile_name_completion_points_awarded': False
        }
        for key, default_value in defaults.items():
            if key not in user_data:
                user_data[key] = default_value
        return user_data
    return None

async def get_dynamic_main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE, user_id_str: str) -> ReplyKeyboardMarkup:
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
    
    if is_member:
        keyboard_layout = [
            ["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"],
            ["👤 پروفایل و باشگاه"], 
            ["📣 نکته سلامتی باشگاه"]
        ]
    else:
        keyboard_layout = [
            ["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"],
            ["⭐ عضویت در باشگاه تافته"]
        ]
    return ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    user_id_str = str(user.id) 
    message_prefix = "درخواست لغو شما انجام شد. " if context.user_data.get('_is_cancel_flow', False) else ""
    if context.user_data.get('_is_cancel_flow', False): del context.user_data['_is_cancel_flow']

    logger.info(f"کاربر {user_id_str} ({user.full_name or user.username}) /start یا بازگشت/لغو به منوی اصلی.")
    
    keys_to_clear_from_session = [
        "doctor_chat_history", "system_prompt_for_doctor", 
        "age_temp", "is_club_member_cached", 
        "awaiting_field_to_edit" 
    ]
    for key in keys_to_clear_from_session:
        if key in context.user_data:
            del context.user_data[key]
    
    if db: 
        try:
            await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        except Exception as e:
            logger.error(f"خطا در get_or_create_user_profile (start) برای کاربر {user_id_str}: {e}", exc_info=True)

    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)
    welcome_message_text = f"سلام {user.first_name or 'کاربر'}! 👋\nمن ربات تافته هستم. لطفاً یکی از گزینه‌ها را انتخاب کنید:"
    if message_prefix:
        welcome_message_text = message_prefix + "به منوی اصلی بازگشتید."
    
    effective_chat_id = update.effective_chat.id
    if update.message and update.message.photo : 
         await context.bot.send_message(chat_id=effective_chat_id, text=welcome_message_text, reply_markup=dynamic_main_menu)
    elif update.message:
        await update.message.reply_text(welcome_message_text, reply_markup=dynamic_main_menu)
    else: 
         await context.bot.send_message(chat_id=effective_chat_id, text=welcome_message_text, reply_markup=dynamic_main_menu)
    return States.MAIN_MENU

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
        
    elif text == "⭐ عضویت در باشگاه تافته": 
        logger.info(f"کاربر {user_id_str} گزینه 'عضویت در باشگاه تافته' را انتخاب کرد.")
        age, gender = None, None
        if db:
            user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
            if user_profile:
                age = user_profile.get("age")
                gender = user_profile.get("gender")
        
        if not (age and gender):
            await update.message.reply_text(
                "برای عضویت در باشگاه، ابتدا باید پروفایل خود را با وارد کردن سن و جنسیت تکمیل کنید.\n"
                "می‌توانید این کار را با انتخاب گزینه '👨‍⚕️ دکتر تافته' از منوی اصلی انجام دهید و پس از آن مجدداً برای عضویت تلاش نمایید.",
                reply_markup=dynamic_main_menu
            )
            return States.MAIN_MENU
        else:
            await update.message.reply_text(
                "عضویت در باشگاه مشتریان تافته مزایای ویژه‌ای برای شما خواهد داشت! آیا مایل به عضویت هستید؟",
                reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD
            )
            return States.AWAITING_CLUB_JOIN_CONFIRMATION
        
    elif text == "👤 پروفایل و باشگاه": 
        logger.info(f"کاربر {user_id_str} گزینه 'پروفایل و باشگاه' را انتخاب کرد.")
        return await my_profile_info_handler(update, context) 

    elif text == "📣 نکته سلامتی باشگاه": 
        logger.info(f"کاربر {user_id_str} گزینه 'نکته سلامتی باشگاه' را انتخاب کرد.")
        return await health_tip_command_handler(update, context)

    else: 
        await update.message.reply_text("گزینه انتخاب شده معتبر نیست.", reply_markup=dynamic_main_menu)
        return States.MAIN_MENU

async def request_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    text = update.message.text
    if text == "🔙 بازگشت به منوی اصلی":
        logger.info(f"User {update.effective_user.id} returned to main menu from AWAITING_AGE.")
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
            return await start(update, context) # بازگشت به منوی اصلی
        try:
            await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
            await asyncio.to_thread(update_user_profile_data, user_id_str, 
                                    {"is_club_member": True, 
                                     "points": firestore.Increment(POINTS_FOR_JOINING_CLUB),
                                     "club_join_date": firestore.SERVER_TIMESTAMP})
            context.user_data['is_club_member_cached'] = True 
            logger.info(f"کاربر {user_id_str} به باشگاه پیوست و {POINTS_FOR_JOINING_CLUB} امتیاز گرفت.")
            
            await update.message.reply_text(f"عضویت شما در باشگاه مشتریان تافته با موفقیت انجام شد! ✨")
            await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_JOINING_CLUB, "عضویت در باشگاه مشتریان")
            await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_CLUB_MEMBER)
        except Exception as e:
            logger.error(f"خطا در عضویت باشگاه برای {user_id_str}: {e}", exc_info=True)
            await update.message.reply_text("مشکلی در عضویت شما پیش آمد.")
    elif text == "❌ خیر، فعلاً نه":
        await update.message.reply_text("متوجه شدم. هر زمان تمایل داشتید، می‌توانید از طریق منوی اصلی اقدام کنید.")
    else: 
        await update.message.reply_text("لطفاً یکی از گزینه‌ها را انتخاب کنید.", reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD)
        return States.AWAITING_CLUB_JOIN_CONFIRMATION 
    return await start(update, context) # بازگشت به منوی اصلی با منوی به‌روز شده

async def doctor_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    # ... (بدون تغییر) ...
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
            logger.error(f"DCH: Could not rebuild system prompt for {user_id_str}. Returning to main.")
            await update.message.reply_text("مشکلی در بازیابی اطلاعات شما پیش آمده.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
            if "doctor_chat_history" in context.user_data: del context.user_data["doctor_chat_history"]
            if "system_prompt_for_doctor" in context.user_data: del context.user_data["system_prompt_for_doctor"]
            return await start(update, context) 
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
    logger.info(f"کاربر {user_id_str} درخواست 'پروفایل و باشگاه' را داد.")
    
    if not db:
        await update.message.reply_text("سیستم پروفایل موقتا در دسترس نیست.", reply_markup=await get_dynamic_main_menu_keyboard(context, user_id_str))
        return States.MAIN_MENU

    try:
        user_profile = await asyncio.to_thread(get_or_create_user_profile, user_id_str, user.username, user.first_name)
        points = user_profile.get('points', 0)
        badges = user_profile.get('badges', [])
        is_member = user_profile.get('is_club_member', False) # باید True باشد تا به اینجا برسد
        age = user_profile.get('age', 'ثبت نشده')
        gender = user_profile.get('gender', 'ثبت نشده')
        name_first = user_profile.get('name_first_db') or user_profile.get('first_name') or 'ثبت نشده'
        name_last = user_profile.get('name_last_db', 'ثبت نشده')

        reply_message = f"👤 **پروفایل شما در باشگاه تافته** 👤\n\n"
        reply_message += f"نام: {name_first} {name_last}\n"
        reply_message += f"سن: {age}\n"
        reply_message += f"جنسیت: {gender}\n"
        reply_message += " عضویت باشگاه: ✅ فعال\n"
        reply_message += f" امتیاز شما: {points} 🌟\n"
        
        if badges:
            reply_message += "\nنشان‌های شما:\n"
            for badge in badges: reply_message += f"  - {badge}\n"
        else: reply_message += "\nشما هنوز هیچ نشانی دریافت نکرده‌اید.\n"
            
        await update.message.reply_text(reply_message, parse_mode="Markdown", reply_markup=PROFILE_VIEW_KEYBOARD)
        return States.PROFILE_VIEW 
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
            "⚠️ **اخطار:** با لغو عضویت، تمام امتیازات و نشان‌های کسب شده شما از بین خواهد رفت.",
            reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD,
            parse_mode="Markdown"
        )
        return States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION
    elif text == "✏️ تکمیل/ویرایش نام":
        await update.message.reply_text("لطفاً نام کوچک خود را وارد کنید (یا برای انصراف و بازگشت به پروفایل، گزینه زیر را انتخاب کنید):", reply_markup=NAME_INPUT_KEYBOARD)
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
            await asyncio.to_thread(update_user_profile_data, user_id_str, 
                                    {"is_club_member": False, "points": 0, "badges": [], 
                                     "club_join_date": None, "club_tip_usage_count": 0 })
            context.user_data['is_club_member_cached'] = False 
            logger.info(f"عضویت کاربر {user_id_str} لغو شد و امتیازات/نشان‌هایش پاک گردید.")
            await update.message.reply_text("عضویت شما از باشگاه مشتریان با موفقیت لغو شد. امتیازات و نشان‌های شما نیز حذف گردید.")
        except Exception as e:
            logger.error(f"خطا در لغو عضویت کاربر {user_id_str}: {e}", exc_info=True)
            await update.message.reply_text("مشکلی در لغو عضویت شما پیش آمد.")
    elif text == "❌ خیر، منصرف شدم":
        await update.message.reply_text("خوشحالیم که همچنان عضو باشگاه مشتریان تافته باقی می‌مانید!")
    else: # ورودی نامعتبر
        await update.message.reply_text("لطفاً یکی از گزینه‌ها را انتخاب کنید.", reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD)
        return States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION
    return await start(update, context) # بازگشت به منوی اصلی با منوی به‌روز شده


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
        return await my_profile_info_handler(update, context) # بازگشت به نمایش پروفایل

    awarded_name_completion_points_and_badge = False
    if db:
        try:
            user_profile_before_update = await asyncio.to_thread(get_user_profile_data, user_id_str)
            update_payload = {"name_first_db": first_name, "name_last_db": last_name_text}

            if user_profile_before_update and not user_profile_before_update.get('profile_name_completion_points_awarded', False):
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
    # ... (بدون تغییر نسبت به نسخه کامل قبلی، فقط reply_markup را پویا می‌کنیم) ...
    user = update.effective_user
    user_id_str = str(user.id)
    logger.info(f"کاربر {user_id_str} درخواست نکته سلامتی باشگاه کرد.")
    dynamic_main_menu = await get_dynamic_main_menu_keyboard(context, user_id_str)

    if not db:
        await update.message.reply_text("سیستم باشگاه مشتریان موقتا در دسترس نیست.", reply_markup=dynamic_main_menu)
        return States.MAIN_MENU

    try:
        user_profile = await asyncio.to_thread(get_user_profile_data, user_id_str)
        
        if user_profile and user_profile.get('is_club_member', False):
            tip = random.choice(HEALTH_TIPS_FOR_CLUB) 
            new_tip_usage_count = user_profile.get('club_tip_usage_count', 0) + 1
            update_payload = {"points": firestore.Increment(POINTS_FOR_CLUB_TIP), "club_tip_usage_count": new_tip_usage_count}
            
            await asyncio.to_thread(update_user_profile_data, user_id_str, update_payload)
            
            message_to_send = f"⚕️ **نکته سلامتی ویژه اعضای باشگاه تافته:**\n\n_{tip}_"
            await update.message.reply_text(message_to_send, parse_mode="Markdown", reply_markup=dynamic_main_menu) 
            await notify_points_awarded(update.get_bot(), update.effective_chat.id, user_id_str, POINTS_FOR_CLUB_TIP, "مطالعه نکته سلامتی باشگاه")
            
            if new_tip_usage_count >= CLUB_TIP_BADGE_THRESHOLD:
                await award_badge_if_not_already_awarded(update.get_bot(), update.effective_chat.id, user_id_str, BADGE_HEALTH_EXPLORER)
        else:
            await update.message.reply_text("این بخش مخصوص اعضای باشگاه مشتریان تافته است.", reply_markup=dynamic_main_menu)
            
    except Exception as e:
        logger.error(f"خطا در ارسال نکته سلامتی برای {user_id_str}: {e}", exc_info=True)
        await update.message.reply_text("مشکلی در ارائه نکته سلامتی پیش آمد.", reply_markup=dynamic_main_menu)
    return States.MAIN_MENU

# --- Flask App & Main Execution ---
# ... (Flask app و run_flask_app بدون تغییر) ...
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check():
    logger.info("درخواست Health Check به اندپوینت '/' Flask دریافت شد.")
    return 'ربات تلگرام تافته فعال است و به پورت گوش می‌دهد!', 200

def run_flask_app():
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"ترد Flask: در حال تلاش برای شروع وب سرور روی هاست 0.0.0.0 و پورت {port}")
    try:
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"ترد Flask: خطایی در اجرای وب سرور Flask رخ داد: {e}", exc_info=True)


if __name__ == '__main__':
    logger.info("بلوک اصلی برنامه (__name__ == '__main__') شروع شد.")
    
    if db is None:
        logger.warning("*"*65 + "\n* دیتابیس Firestore مقداردهی اولیه نشده است!                     *\n" +
                       "* ربات با قابلیت‌های محدود (بدون ذخیره دائمی اطلاعات) اجرا می‌شود. *\n" +
                       "* لطفاً تنظیمات Firebase و فایل کلید را بررسی کنید.                *\n" + "*"*65)

    logger.info("در حال تنظیم و شروع ترد Flask...")
    flask_thread = threading.Thread(target=run_flask_app, name="FlaskThread", daemon=True)
    flask_thread.start()
    logger.info("ترد Flask شروع به کار کرد.")

    logger.info("در حال ساخت اپلیکیشن ربات تلگرام...")
    telegram_application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            States.MAIN_MENU: [
                MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات|⭐ عضویت در باشگاه تافته|👤 پروفایل و باشگاه|📣 نکته سلامتی باشگاه)$"), main_menu_handler),
            ],
            States.AWAITING_AGE: [
                MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, request_age_handler)
            ],
            States.AWAITING_GENDER: [
                MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start),
                MessageHandler(filters.Regex("^(زن|مرد)$"), request_gender_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, 
                               lambda update, context: update.message.reply_text("لطفاً یکی از گزینه‌های «زن» یا «مرد» را با دکمه انتخاب کنید یا بازگردید.", reply_markup=GENDER_SELECTION_KEYBOARD))
            ],
            States.DOCTOR_CONVERSATION: [
                MessageHandler(filters.Regex("^(❓ سوال جدید از دکتر|🔙 بازگشت به منوی اصلی)$"), doctor_conversation_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, doctor_conversation_handler)
            ],
            States.AWAITING_CLUB_JOIN_CONFIRMATION: [ 
                MessageHandler(filters.Regex("^(✅ بله، عضو می‌شوم|❌ خیر، فعلاً نه)$"), handle_club_join_confirmation),
                MessageHandler(filters.TEXT & ~filters.COMMAND, 
                               lambda update, context: update.message.reply_text("لطفاً با استفاده از دکمه‌ها پاسخ دهید.", reply_markup=CLUB_JOIN_CONFIRMATION_KEYBOARD))
            ],
            States.PROFILE_VIEW: [ 
                MessageHandler(filters.Regex("^(✏️ تکمیل/ویرایش نام|💔 لغو عضویت از باشگاه|🔙 بازگشت به منوی اصلی)$"), profile_view_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, 
                               lambda update, context: update.message.reply_text("لطفاً یکی از گزینه‌های پروفایل را انتخاب کنید.", reply_markup=PROFILE_VIEW_KEYBOARD))
            ],
            States.AWAITING_CANCEL_MEMBERSHIP_CONFIRMATION: [ 
                MessageHandler(filters.Regex("^(✅ بله، عضویتم لغو شود|❌ خیر، منصرف شدم)$"), handle_cancel_membership_confirmation),
                 MessageHandler(filters.TEXT & ~filters.COMMAND, 
                               lambda update, context: update.message.reply_text("لطفاً با استفاده از دکمه‌ها پاسخ دهید.", reply_markup=CANCEL_MEMBERSHIP_CONFIRMATION_KEYBOARD))
            ],
            States.AWAITING_FIRST_NAME: [
                MessageHandler(filters.Regex("^🔙 انصراف و بازگشت به پروفایل$"), profile_view_handler), 
                MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_first_name_handler)
            ],
            States.AWAITING_LAST_NAME: [
                MessageHandler(filters.Regex("^🔙 انصراف و بازگشت به پروفایل$"), profile_view_handler), 
                MessageHandler(filters.TEXT & ~filters.COMMAND, awaiting_last_name_handler)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel), 
            CommandHandler("start", start), 
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), 
        ],
        persistent=False, name="main_conversation"
    )
    
    telegram_application.add_handler(CommandHandler("myprofile", my_profile_info_handler)) 
    telegram_application.add_handler(CommandHandler("clubtip", health_tip_command_handler)) 
    telegram_application.add_handler(conv_handler)
    telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    
    logger.info("ربات تلگرام در حال شروع polling...")
    try:
        telegram_application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"خطای مرگبار در run_polling که باعث توقف برنامه شد: {e}", exc_info=True)
    finally:
        logger.info("برنامه در حال خاتمه است.")