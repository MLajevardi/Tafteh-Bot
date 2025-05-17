import logging
import httpx
import os
from enum import Enum
from dotenv import load_dotenv
import threading
from flask import Flask
import asyncio

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

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["👨‍⚕️ دکتر تافته", "📦 راهنمای محصولات"]],
    resize_keyboard=True
)

DOCTOR_CONVERSATION_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ سوال جدید از دکتر"],
        ["🔙 بازگشت به منوی اصلی"]
    ],
    resize_keyboard=True
)

# کیبورد جدید برای انتخاب جنسیت
GENDER_SELECTION_KEYBOARD = ReplyKeyboardMarkup(
    [["زن"], ["مرد"]],
    resize_keyboard=True,
    one_time_keyboard=True # پس از انتخاب، کیبورد حذف شود
)


async def ask_openrouter(system_prompt: str, chat_history: list) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    messages_payload = [{"role": "system", "content": system_prompt}] + chat_history

    body = {
        "model": OPENROUTER_MODEL_NAME,
        "messages": messages_payload
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

                if "?" in llm_response_content or "بیشتر توضیح دهید" in llm_response_content or "آیا علامت دیگری دارید" in llm_response_content:
                    logger.info("LLM یک سوال پرسیده یا درخواست اطلاعات بیشتر کرده.")
                else:
                    logger.info("LLM یک پاسخ یا توصیه ارائه داده.")
                return llm_response_content
            else:
                logger.error(f"ساختار پاسخ دریافت شده از OpenRouter نامعتبر یا فاقد محتوا است: {data}")
                return "❌ مشکلی در پردازش پاسخ از سرویس پزشک مجازی رخ داد. لطفاً دوباره تلاش کنید."
            
        except httpx.HTTPStatusError as e:
            logger.error(f"خطای HTTP از OpenRouter: {e.response.status_code} - {e.response.text}")
            return f"❌ مشکل در ارتباط با سرویس پزشک مجازی (کد خطا: {e.response.status_code}). لطفاً بعداً تلاش کنید."
        except httpx.RequestError as e:
            logger.error(f"خطای درخواست به OpenRouter (ممکن است مشکل شبکه باشد): {e}")
            return "❌ مشکل در برقراری ارتباط با سرویس پزشک مجازی. لطفاً از اتصال اینترنت خود مطمئن شوید و دوباره تلاش کنید."
        except Exception as e:
            logger.error(f"خطای پیش‌بینی نشده در تابع ask_openrouter: {e}", exc_info=True)
            return "❌ مشکلی پیش‌بینی نشده در دریافت پاسخ پیش آمده است. لطفاً دوباره تلاش کنید."

def _prepare_doctor_system_prompt(age: int, gender: str) -> str:
    """یک تابع کمکی برای ساخت پرامپت سیستمی دکتر تافته."""
    return (
        f"شما یک پزشک عمومی متخصص، دقیق، صبور و همدل به نام 'دکتر تافته' هستید. کاربری که با شما صحبت می‌کند {age} ساله و {gender} است. "
        "وظیفه شما این است که از طریق یک مکالمه چند مرحله‌ای با کاربر، به سوالات پزشکی او به زبان فارسی پاسخ دهید. "
        "وقتی کاربر سوال اولیه‌ای مطرح می‌کند، اگر برای ارائه یک پاسخ جامع و دقیق نیاز به اطلاعات بیشتری دارید، سوالات تکمیلی و شفاف‌کننده از کاربر بپرسید. "
        "**بسیار مهم: هنگام پرسیدن سوالات تکمیلی، حتماً و مطلقاً در هر نوبت فقط یک، و حداکثر دو سوال کوتاه، کلیدی و کاملاً مرتبط بپرسید. از پرسیدن لیست بلند سوالات یا سوالات متعدد در یک پیام جداً خودداری کنید.** "
        "در تفسیر علائم کاربر بسیار دقت کنید. به عنوان مثال، 'دل درد' معمولاً به درد ناحیه شکم اشاره دارد؛ سریعاً آن را به درد قلبی تعبیر نکنید مگر اینکه علائم بسیار واضح و مشخص دیگری نیز همراه آن باشند که قویا به نفع مشکلات قلبی باشد. سوال 'هوای شما چطور است؟' معمولا برای کاربر نامفهوم است؛ به جای آن در صورت لزوم از تنگی نفس یا مشکلات تنفسی سوال کنید. "
        "پس از چند نوبت پرسش و پاسخ، و **قبل از ارائه توصیه نهایی، حتماً از کاربر بپرسید آیا علامت دیگری دارد یا نکته دیگری هست که فکر می‌کند شما باید بدانید، یا آیا سوال دیگری در مورد همین مشکل فعلی خود دارد. تنها پس از اینکه کاربر تایید کرد اطلاعات بیشتری ندارد یا شما پس از سوال از او متوجه شدید که دیگر اطلاعات جدید و مرتبطی ارائه نمی‌دهد، توصیه‌های خود را ارائه دهید.** "
        "هدف شما جمع‌آوری اطلاعات کافی برای ارائه یک توصیه پزشکی عمومی جامع و مناسب با وضعیت کاربر است. "
        "اگر سوالی از شما پرسیده شد که به وضوح پزشکی نیست (مثلاً درباره آشپزی، تاریخ، ریاضی و غیره)، باید به صراحت، محترمانه و با استفاده از این عبارت دقیق پاسخ دهید: 'متاسفم، من یک ربات پزشک هستم و فقط می‌توانم به سوالات مرتبط با حوزه پزشکی پاسخ دهم. چطور می‌توانم در زمینه پزشکی به شما کمک کنم؟' به هیچ وجه سعی در پاسخ به سوالات غیرپزشکی نکنید."
        "در پاسخ‌های خود (چه سوالات تکمیلی و چه توصیه‌های نهایی)، مستقیماً به سراغ مطلب بروید و از هرگونه عبارت مقدماتی غیرضروری مانند 'بله'، 'خب'، 'البته'، 'حتما' یا مشابه آن استفاده نکنید."
        "مکالمه تا زمانی ادامه پیدا می‌کند که شما تشخیص دهید اطلاعات کافی برای یک توصیه جامع دارید یا کاربر بخواهد مکالمه را پایان دهد. "
        "به یاد داشته باشید شما یک پزشک عمومی هستید و نباید توصیه‌های بسیار تخصصی خارج از این حوزه ارائه دهید. در صورت نیاز، کاربر را به مراجعه حضوری به پزشک یا متخصص ارجاع دهید. "
        "همیشه محترمانه، دقیق و صبور باشید و از عبارات قابل فهم برای عموم استفاده کنید."
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    logger.info(f"کاربر {user.id} ({user.full_name if user.full_name else user.username}) /start را فراخوانی کرد یا به منوی اصلی بازگشت.")
    
    # حفظ سن و جنسیت در صورت وجود، و پاک کردن بقیه اطلاعات جلسه
    preserved_age = context.user_data.get("age")
    preserved_gender = context.user_data.get("gender")
    
    context.user_data.clear() # ابتدا همه چیز را پاک می‌کنیم
    
    if preserved_age and preserved_gender: # فقط اگر هر دو وجود داشتند، آن‌ها را بازیابی می‌کنیم
        context.user_data["age"] = preserved_age
        context.user_data["gender"] = preserved_gender
        logger.info(f"سن ({preserved_age}) و جنسیت ({preserved_gender}) کاربر {user.id} برای استفاده‌های بعدی حفظ شد.")
    else:
        logger.info(f"سن یا جنسیت برای کاربر {user.id} موجود نبود یا کامل نبود، بنابراین حفظ نشد.")

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
        age = context.user_data.get("age")
        gender = context.user_data.get("gender")
        if age and gender: # اگر سن و جنسیت از قبل موجود است
            logger.info(f"کاربر {user.id} قبلاً سن ({age}) و جنسیت ({gender}) را وارد کرده است. مستقیم به مکالمه با دکتر می‌رود.")
            # بازسازی پرامپت سیستمی و پاک کردن تاریخچه مکالمه قبلی
            system_prompt = _prepare_doctor_system_prompt(age, gender)
            context.user_data["system_prompt_for_doctor"] = system_prompt
            context.user_data["doctor_chat_history"] = []
            logger.info("پرامپت سیستمی دکتر بازسازی شد و تاریخچه مکالمه پاک شد.")
            
            await update.message.reply_text(
                f"مشخصات شما (سن: {age}، جنسیت: {gender}) از قبل در سیستم موجود است.\n"
                "اکنون می‌توانید سوال پزشکی خود را از دکتر تافته بپرسید. دکتر تافته ممکن است برای ارائه پاسخ بهتر، سوالات بیشتری از شما بپرسد.",
                reply_markup=DOCTOR_CONVERSATION_KEYBOARD
            )
            return States.DOCTOR_CONVERSATION
        else: # اگر سن و جنسیت موجود نیست، طبق روال قبل سوال شود
            logger.info(f"سن یا جنسیت برای کاربر {user.id} موجود نیست. درخواست سن.")
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
    else: # این حالت معمولا نباید رخ دهد چون Regex گزینه‌ها را پوشش می‌دهد
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
    # ارسال کیبورد انتخاب جنسیت
    await update.message.reply_text("متشکرم. حالا لطفاً جنسیت خود را انتخاب کنید:", reply_markup=GENDER_SELECTION_KEYBOARD)
    return States.AWAITING_GENDER

async def request_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    gender_input = update.message.text.strip() # باید "زن" یا "مرد" باشد
    user = update.effective_user
    
    # اعتبارسنجی دیگر لازم نیست چون Regex در ConversationHandler این کار را می‌کند
    # اما برای اطمینان بیشتر می‌توان آن را نگه داشت یا حذف کرد.
    # if gender_input not in ["زن", "مرد"]:
    #     await update.message.reply_text("لطفاً یکی از گزینه‌های «زن» یا «مرد» را انتخاب کنید.", reply_markup=GENDER_SELECTION_KEYBOARD)
    #     return States.AWAITING_GENDER

    context.user_data["gender"] = gender_input # gender_input یا "زن" است یا "مرد"
        
    age = context.user_data.get("age")
    gender = context.user_data.get("gender")
    logger.info(f"کاربر {user.id} جنسیت خود را '{gender}' انتخاب کرد. سن: {age}")

    system_prompt = _prepare_doctor_system_prompt(age, gender)
    context.user_data["system_prompt_for_doctor"] = system_prompt
    context.user_data["doctor_chat_history"] = []

    logger.info(f"پرامپت سیستمی برای دکتر تافته تنظیم شد. تاریخچه مکالمه پاک شد.")

    await update.message.reply_text(
        f"✅ مشخصات شما ثبت شد:\n"
        f"سن: {age} سال\n"
        f"جنسیت: {gender}\n\n"
        "اکنون می‌توانید سوال پزشکی خود را از دکتر تافته بپرسید. دکتر تافته ممکن است برای ارائه پاسخ بهتر، سوالات بیشتری از شما بپرسد.",
        reply_markup=DOCTOR_CONVERSATION_KEYBOARD
    )
    return States.DOCTOR_CONVERSATION

async def doctor_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    logger.info(f"--- DCH Entered --- User: {update.effective_user.id}, Text: '{update.message.text}', History items: {len(context.user_data.get('doctor_chat_history', []))}")
    
    user_question = update.message.text
    user = update.effective_user
    
    chat_history = context.user_data.get("doctor_chat_history", [])
    system_prompt = context.user_data.get("system_prompt_for_doctor")

    if not system_prompt: 
        logger.error(f"DCH: System prompt for user {user.id} not found! This should not happen if age/gender were collected. Returning to main menu.")
        await update.message.reply_text("مشکلی در بازیابی اطلاعات مکالمه پیش آمده. لطفاً از ابتدا شروع کنید.", reply_markup=MAIN_MENU_KEYBOARD)
        # به جای clear کامل، فقط اطلاعات مربوط به دکتر را پاک می‌کنیم تا سن و جنسیت باقی بماند برای دفعه بعد
        if "doctor_chat_history" in context.user_data: del context.user_data["doctor_chat_history"]
        if "system_prompt_for_doctor" in context.user_data: del context.user_data["system_prompt_for_doctor"]
        return await start(update, context) # بازگشت به منوی اصلی با حفظ احتمالی سن/جنسیت

    if user_question == "🔙 بازگشت به منوی اصلی":
        logger.info(f"DCH: User {user.id} selected 'بازگشت به منوی اصلی'. Delegating to start handler.")
        # تابع start مسئول پاکسازی انتخابی داده‌ها و نمایش منوی اصلی خواهد بود
        return await start(update, context) 
    elif user_question == "❓ سوال جدید از دکتر":
        logger.info(f"DCH: User {user.id} selected 'سوال جدید از دکتر'. Clearing chat history.")
        context.user_data["doctor_chat_history"] = [] 
        await update.message.reply_text("بسیار خب، تاریخچه مکالمه قبلی پاک شد. سوال پزشکی جدید خود را بپرسید:", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
        return States.DOCTOR_CONVERSATION

    logger.info(f"DCH: Processing conversational text from user {user.id}: '{user_question}'")
    
    chat_history.append({"role": "user", "content": user_question})
    
    await update.message.reply_text("⏳ دکتر تافته در حال بررسی پیام شماست، لطفاً کمی صبر کنید...")

    assistant_response = await ask_openrouter(system_prompt, chat_history)
    
    chat_history.append({"role": "assistant", "content": assistant_response})
    context.user_data["doctor_chat_history"] = chat_history

    await update.message.reply_text(assistant_response, parse_mode="Markdown", reply_markup=DOCTOR_CONVERSATION_KEYBOARD)
    return States.DOCTOR_CONVERSATION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    user = update.effective_user
    logger.info(f"User {user.id} called /cancel. Delegating to start handler for cleanup and main menu.")
    await update.message.reply_text("درخواست شما لغو شد. بازگشت به منوی اصلی...", reply_markup=ReplyKeyboardRemove())
    return await start(update, context) # واگذاری به تابع start برای پاکسازی انتخابی و نمایش منوی اصلی

async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.warning(f"--- GLOBAL FALLBACK Reached --- User: {user.id}, Text: '{update.message.text}', Current user_data: {context.user_data}")
    await update.message.reply_text(
        "متوجه نشدم چه گفتید. لطفاً از گزینه‌های منو استفاده کنید یا اگر در مرحله خاصی هستید، ورودی مورد انتظار را ارسال نمایید.",
        reply_markup=MAIN_MENU_KEYBOARD
    )

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
        logger.info(f"ترد Flask: وب سرور Flask روی پورت {port} متوقف شد.")
    except Exception as e:
        logger.error(f"ترد Flask: خطایی در اجرای وب سرور Flask رخ داد: {e}", exc_info=True)

if __name__ == '__main__':
    logger.info("بلوک اصلی برنامه (__name__ == '__main__') شروع شد.")
    
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
                MessageHandler(filters.Regex("^(👨‍⚕️ دکتر تافته|📦 راهنمای محصولات)$"), main_menu_handler),
                # کنترل‌کننده Regex برای "بازگشت به منوی اصلی" دیگر اینجا لازم نیست چون به start واگذار می‌شود
            ],
            States.AWAITING_AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, request_age_handler)
            ],
            States.AWAITING_GENDER: [
                # استفاده از Regex برای دریافت دقیق "زن" یا "مرد" از دکمه‌ها
                MessageHandler(filters.Regex("^(زن|مرد)$"), request_gender_handler),
                # افزودن یک کنترل کننده برای پیام‌های متنی دیگر در این حالت (اگر کاربر به جای دکمه تایپ کند)
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
            CommandHandler("start", start), # /start همیشه به منوی اصلی و پاکسازی (انتخابی) می‌برد
            # این فال بک عمومی برای "بازگشت به منوی اصلی" اگر از دکمه‌ای خارج از دکتر تافته استفاده شود
            MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), start), 
        ],
        persistent=False,
        name="main_conversation"
    )

    telegram_application.add_handler(conv_handler)
    telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))
    
    logger.info("ربات تلگرام در حال شروع polling (این یک عملیات بلاک کننده است)...")
    try:
        telegram_application.run_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Polling ربات تلگرام متوقف شد.")
    except KeyboardInterrupt:
        logger.info("درخواست توقف (KeyboardInterrupt) دریافت شد. ربات در حال خاموش شدن...")
    except Exception as e:
        logger.error(f"خطایی در حین اجرای run_polling یا در زمان کار ربات رخ داد: {e}", exc_info=True)
    finally:
        logger.info("برنامه در حال بسته شدن است. ترد Flask نیز به دلیل daemon=True بسته خواهد شد.")