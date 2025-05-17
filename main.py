# main.py - نسخه موقت برای دیباگ کردن اولیه
import os
import sys

# این print ها باید در لاگ Render ظاهر شوند اگر اسکریپت اصلاً اجرا شود
print("DEBUG: اجرای فایل main.py (نسخه دیباگ) شروع شد.", flush=True)
print(f"DEBUG: نسخه پایتون: {sys.version}", flush=True)
print("DEBUG: در حال تلاش برای خواندن متغیرهای محیطی...", flush=True)

bot_token_render = os.getenv("BOT_TOKEN")
openrouter_key_render = os.getenv("OPENROUTER_API_KEY")
render_port = os.getenv("PORT") # رندر این متغیر را برای پورت تنظیم می‌کند

print(f"DEBUG: مقدار BOT_TOKEN خوانده شده: '{bot_token_render}'", flush=True)
print(f"DEBUG: مقدار OPENROUTER_API_KEY خوانده شده: '{openrouter_key_render}'", flush=True)
print(f"DEBUG: مقدار PORT خوانده شده از محیط: '{render_port}'", flush=True)

if not bot_token_render:
    print("!!! DEBUG بحرانی: BOT_TOKEN یافت نشد یا خالی است.", flush=True)

if not openrouter_key_render:
    print("!!! DEBUG بحرانی: OPENROUTER_API_KEY یافت نشد یا خالی است.", flush=True)

if not render_port:
    print("!!! DEBUG هشدار: متغیر محیطی PORT توسط Render تنظیم نشده است (برای Web Service لازم است).", flush=True)

print("DEBUG: بررسی اولیه متغیرهای محیطی تمام شد.", flush=True)
print("DEBUG: پایان اسکریپت دیباگ main.py.", flush=True)

# فعلاً از اجرای بقیه کد ربات جلوگیری می‌کنیم تا مشکل اولیه را پیدا کنیم
# اگر این print ها در لاگ Render ظاهر شوند، یعنی مشکل در بخش‌های بعدی کد اصلی شماست.
# اگر حتی این print ها هم ظاهر نشوند، مشکل بسیار پایه‌ای‌تر است.
exit(0) # خروج کنترل شده پس از چاپ پیام‌های دیباگ

# ------------------------------------------------------------------
# بقیه کد اصلی ربات شما برای این تست کامنت یا حذف می‌شود
# import logging
# from enum import Enum
# ... (و الی آخر)
# ------------------------------------------------------------------