import asyncio
import json
import os
import random
import logging
import traceback
from telebot.async_telebot import AsyncTeleBot
from telebot.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# -------------------------------
# تنظیمات اولیه
# -------------------------------
API_TOKEN = os.getenv("TOKEN")  # جایگزین کن
bot = AsyncTeleBot(API_TOKEN)

DATA_FILE = "soaps.json"
BUILD_TIME = 60
FACTORY_LIFETIME = 100
data_lock = asyncio.Lock()

# -------------------------------
# Logging خطاها
# -------------------------------
logging.basicConfig(
    filename="bot_errors.log",
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# -------------------------------
# Safe async execute
# -------------------------------
async def safe_execute(coro, *args, **kwargs):
    try:
        await coro(*args, **kwargs)
    except Exception as e:
        logging.error("Exception in %s: %s\n%s", coro.__name__, str(e), traceback.format_exc())
        message = kwargs.get("message")
        if message:
            await bot.reply_to(message, "⚠️ یه خطای غیرمنتظره رخ داد، بعداً دوباره امتحان کن!")

# -------------------------------
# مدیریت داده‌ها
# -------------------------------
async def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

async def save_data(data):
    async with data_lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

# -------------------------------
# کلاس کارخانه
# -------------------------------
class Factory:
    def __init__(self, info=None):
        info = info or {}
        self.building = info.get("building", False)
        self.start_time = info.get("start_time", 0)
        self.soaps_ready = info.get("soaps_ready", 0)
        self.total_built = info.get("total_built", 0)

    async def produce_soaps(self, user):
        if not self.building:
            return
        now = asyncio.get_event_loop().time()
        elapsed = now - self.start_time
        cycles = int(elapsed // BUILD_TIME)
        if cycles <= 0:
            return

        possible_cycles = min(cycles, user.ash, user.fat)
        if possible_cycles <= 0:
            return

        user.ash -= possible_cycles
        user.fat -= possible_cycles
        self.soaps_ready += possible_cycles
        self.total_built += possible_cycles

        if self.total_built >= FACTORY_LIFETIME:
            self.building = False
            self.start_time = 0
            await bot.send_message(user.chat_id, "🏭 کارخانه شما پس از ساخت 100 صابون خراب شد! باید دوباره بسازید.")

        self.start_time += cycles * BUILD_TIME
        await user.save_to_data()

# -------------------------------
# کلاس کاربر
# -------------------------------
class User:
    def __init__(self, chat_id, telegram_user, data):
        self.chat_id = chat_id
        self.user_id = telegram_user.id
        self.first_name = telegram_user.first_name
        self.data = data
        self.furnaces = 0
        self.gas = 0
        self.last = 0
        self.fat = 0
        self.ash = 0
        self.soap = 0
        self.factory = Factory()
        self.load_from_data()

    def load_from_data(self):
        chat_data = self.data.get(str(self.chat_id), {})
        user_data = chat_data.get(str(self.user_id), None)
        if user_data:
            self.furnaces = user_data.get("furnaces", 0)
            self.gas = user_data.get("gas", 0)
            self.last = user_data.get("last", 0)
            self.fat = user_data.get("fat", 0)
            self.ash = user_data.get("ash", 0)
            self.soap = user_data.get("soap", 0)
            self.factory = Factory(user_data.get("factory"))

    async def save_to_data(self):
        chat_dict = self.data.setdefault(str(self.chat_id), {})
        chat_dict[str(self.user_id)] = {
            "furnaces": self.furnaces,
            "gas": self.gas,
            "last": self.last,
            "fat": self.fat,
            "ash": self.ash,
            "soap": self.soap,
            "factory": self.factory.__dict__
        }
        await save_data(self.data)

    async def check_factory(self):
        await self.factory.produce_soaps(self)
        await self.save_to_data()

# -------------------------------
# وضعیت و اینلاین دکمه‌ها
# -------------------------------
async def send_status(user: User, message: Message):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🧼 جمع‌آوری صابون", callback_data="collect_soap"),
        InlineKeyboardButton("🏆 لیدربورد", callback_data="leaderboard")
    )
    text = (
        f"📊 *وضعیت شما*\n\n"
        f"💪 چربی: {user.fat}\n"
        f"🔥 خاکستر: {user.ash}\n"
        f"🧼 صابون: {user.soap}\n"
        f"🏭 کارخانه فعال: {'✅' if user.factory.building else '❌'}\n"
        f"🧼 صابون آماده برای جمع‌آوری: {user.factory.soaps_ready}\n"
        f"📦 کل صابون تولید شده توسط کارخانه: {user.factory.total_built}"
    )
    await bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")

# -------------------------------
# پردازش پیام‌ها
# -------------------------------
async def process_message(message: Message):
    text = message.text.strip().lower()
    chat_id = message.chat.id
    data = await load_data()
    user = User(chat_id, message.from_user, data)
    await user.check_factory()

    # ---------- شعار ----------
    trigger_texts = ["مرگ بر اسرائیل", "مرگ بر یهود", "درود بر هیتلر",
                     "هایل هیتلر", "卐", "درود بر کوروش", "مرگ بر بات ضحاک"]
    if text in trigger_texts:
        now = asyncio.get_event_loop().time()
        if now - user.last >= 100:
            fat = random.randint(1,3)
            user.fat += fat
            user.last = now
            await user.save_to_data()
            await bot.reply_to(message, f"{fat} چربی به حسابت اضافه شد!\nکل چربی: {user.fat}")
        return

    # ---------- خرید کوره ----------
    elif text == "خرید کوره":
        if user.fat >= 10:
            user.fat -= 10
            user.furnaces = 1
            user.gas = 3
            await user.save_to_data()
            await bot.reply_to(message, f"🔥 کوره خریدی! ۳ بار قابل استفاده است.\nچربی باقی‌مانده: {user.fat}")
        else:
            await bot.reply_to(message, f"🚫 چربی کافی نداری! نیاز به ۱۰ واحد داری.\nچربی فعلی: {user.fat}")
        return

    # ---------- خرید گاز ----------
    elif text == "خرید گاز":
        if user.furnaces <= 0:
            await bot.reply_to(message, "❌ تو کوره نداری! اول یه کوره بخر.")
            return
        if user.fat < 3:
            await bot.reply_to(message, "🚫 چربی کافی نداری! نیاز به ۳ واحد داری.")
            return
        user.fat -= 3
        user.gas = 3
        await user.save_to_data()
        await bot.reply_to(message, "⛽ گاز کوره پر شد! ۳ واحد چربی کم شد.")
        return

    # ---------- ساخت کارخانه ----------
    elif text == "ساخت کارخانه":
        if user.factory.building:
            await bot.reply_to(message, "🏭 کارخانه در حال حاضر فعال است!")
        else:
            user.factory.building = True
            user.factory.start_time = asyncio.get_event_loop().time()
            user.factory.soaps_ready = 0
            user.factory.total_built = 0
            await user.save_to_data()
            await bot.reply_to(message, "🏭 کارخانه ساخته شد و شروع به تولید صابون می‌کند (هر صابون ۱ دقیقه).")
        return

    # ---------- جمع‌آوری صابون ----------
    elif text in ["جمع آوری صابون", "جمع‌آوری صابون"]:
        ready = user.factory.soaps_ready
        if ready <= 0:
            await bot.reply_to(message, "🧼 هنوز صابونی آماده نشده است.")
        else:
            user.soap += ready
            user.factory.soaps_ready = 0
            await user.save_to_data()
            await bot.reply_to(message, f"🧼 {ready} صابون جمع‌آوری شد! کل صابون: {user.soap}")
        return

    # ---------- برو تو کوره ----------
    elif text == "برو تو کوره":
        if not message.reply_to_message:
            await bot.reply_to(message, "❌ برای 'برو تو کوره' باید به کسی ریپلای کنی!")
            return
        if user.furnaces <= 0:
            await bot.reply_to(message, "🔥 تو کوره‌ای نداری! اول یه کوره بخر.")
            return
        if user.gas <= 0:
            await bot.reply_to(message, "⛔ گاز کوره تموم شده! با 'خرید گاز' پرش کن.")
            return

        target = User(chat_id, message.reply_to_message.from_user, data)
        if target.user_id == user.user_id:
            await bot.reply_to(message, "😂 نمی‌تونی خودتو بندازی تو کوره!")
            return

        ash = random.randint(3, 10)
        user.ash += ash
        user.gas -= 1
        target.soap = max(0, target.soap - 2)
        await user.save_to_data()
        await target.save_to_data()
        await bot.reply_to(
            message,
            f"🔥 {target.first_name} رفت تو کوره!\n"
            f"{target.first_name} {ash} خاکستر تولید شد 💨\n"
            f"گاز کوره باقی‌مانده: {user.gas}"
        )
        return

    # ---------- وضعیت ----------
    elif text == "وضعیت":
        await send_status(user, message)
        return

    # ---------- لیدربورد ----------
    elif text == "لیدربورد":
        chat_users = data.get(str(chat_id), {})
        if not chat_users:
            await bot.reply_to(message, "🏆 هیچ کاربری در گپ موجود نیست.")
            return
        sorted_users = sorted(chat_users.items(), key=lambda x: x[1].get("soap",0), reverse=True)
        ranking_text = "🏆 رنکینگ کاربران بر اساس صابون:\n"
        for i, (uid, udata) in enumerate(sorted_users[:10], 1):
            ranking_text += f"{i}. {udata.get('soap',0)} صابون\n"
        await bot.reply_to(message, ranking_text)
        return

# -------------------------------
# پردازش Callback اینلاین
# -------------------------------
async def process_callback(call: CallbackQuery):
    data = await load_data()
    user = User(call.message.chat.id, call.from_user, data)

    if call.data == "collect_soap":
        ready = user.factory.soaps_ready
        if ready <= 0:
            await bot.answer_callback_query(call.id, "🧼 هنوز صابونی آماده نشده است.")
        else:
            user.soap += ready
            user.factory.soaps_ready = 0
            await user.save_to_data()
            await bot.answer_callback_query(call.id, f"🧼 {ready} صابون جمع‌آوری شد! کل صابون: {user.soap}")

    elif call.data == "leaderboard":
        chat_users = data.get(str(call.message.chat.id), {})
        sorted_users = sorted(chat_users.items(), key=lambda x: x[1].get("soap",0), reverse=True)
        ranking_text = "🏆 رنکینگ کاربران بر اساس صابون:\n"
        for i, (uid, udata) in enumerate(sorted_users[:10], 1):
            ranking_text += f"{i}. {udata.get('soap',0)} صابون\n"
        await bot.answer_callback_query(call.id)
        await bot.send_message(call.message.chat.id, ranking_text)

# -------------------------------
# هندلر start/help
# -------------------------------
@bot.message_handler(commands=["start","help"])
async def send_welcome(message: Message):
    await safe_execute(bot.reply_to, message,
        "سلام! دستورات:\n"
        "1. شعار => چربی می‌گیری\n"
        "2. خرید کوره => کوره بخرید\n"
        "3. ریپلای 'برو تو کوره' => خاکستر می‌گیرید\n"
        "4. خرید گاز => گاز برای کوره بخرید\n"
        "5. وضعیت => وضعیت خود را ببینید (با دکمه‌ها)\n"
        "6. لیدربورد => لیدربورد را ببینید (با دکمه‌ها)"
    )

# -------------------------------
# هندلر پیام‌ها
# -------------------------------
@bot.message_handler(func=lambda m: True, content_types=['text'])
async def reply_text(message: Message):
    await safe_execute(process_message, message=message)

# -------------------------------
# هندلر callback اینلاین
# -------------------------------
@bot.callback_query_handler(func=lambda call: True)
async def callback_handler(call):
    await safe_execute(process_callback, call=call)

# -------------------------------
# اجرا
# -------------------------------
async def main():
    print("Bot is running...")
    await bot.infinity_polling()

if __name__ == "__main__":
    asyncio.run(main())