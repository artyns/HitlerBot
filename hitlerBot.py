import asyncio
import json
import os
import random
import logging
import traceback
import time
from threading import Thread
from telebot.async_telebot import AsyncTeleBot
from telebot.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from flask import Flask


# =========================
# CONFIG
# =========================

API_TOKEN = os.getenv("TOKEN")

if not API_TOKEN:
    raise RuntimeError("TOKEN environment variable is not set.")

bot = AsyncTeleBot(API_TOKEN)

app = Flask(__name__)

DATA_FILE = "soaps.json"

BUILD_TIME = 60
FACTORY_LIFETIME = 100

FAT_COOLDOWN = 60
FAT_MIN = 2
FAT_MAX = 5

data_lock = asyncio.Lock()


# =========================
# FLASK
# =========================

@app.route("/ping")
def ping():
    return "pong"


# =========================
# DATA
# =========================

def load_data_sync():
    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logging.exception("Failed to load data")
        return {}


async def load_data():
    async with data_lock:
        return load_data_sync()


async def save_data(data):
    async with data_lock:
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    data,
                    f,
                    ensure_ascii=False,
                    indent=2
                )
        except Exception:
            logging.exception("Failed to save data")


# =========================
# FACTORY
# =========================

class Factory:

    def __init__(self, info=None):
        info = info or {}

        # Backward compatibility
        self.exists = info.get(
            "exists",
            info.get("building", False)
        )

        self.active = info.get(
            "active",
            info.get("building", False)
        )

        self.start_time = info.get("start_time", 0)

        self.soaps_ready = info.get(
            "soaps_ready",
            0
        )

        self.total_built = info.get(
            "total_built",
            0
        )

        # New recipe
        self.fat_per_soap = info.get(
            "fat_per_soap",
            1
        )

        self.ash_per_soap = info.get(
            "ash_per_soap",
            1
        )

    async def produce_soaps(self, user):

        if not self.exists:
            return

        if not self.active:
            return

        now = time.time()

        if self.start_time <= 0:
            self.start_time = now
            return

        elapsed = now - self.start_time

        cycles = int(elapsed // BUILD_TIME)

        if cycles <= 0:
            return

        remaining_capacity = (
            FACTORY_LIFETIME - self.total_built
        )

        if remaining_capacity <= 0:
            self.active = False
            self.start_time = 0
            return

        cycles = min(
            cycles,
            remaining_capacity
        )

        # How many soaps can actually be produced
        max_by_fat = (
            user.fat // self.fat_per_soap
            if self.fat_per_soap > 0
            else cycles
        )

        max_by_ash = (
            user.ash // self.ash_per_soap
            if self.ash_per_soap > 0
            else cycles
        )

        possible_cycles = min(
            cycles,
            max_by_fat,
            max_by_ash
        )

        if possible_cycles <= 0:
            return

        user.fat -= (
            possible_cycles * self.fat_per_soap
        )

        user.ash -= (
            possible_cycles * self.ash_per_soap
        )

        self.soaps_ready += possible_cycles
        self.total_built += possible_cycles

        # Advance only by actually produced cycles
        self.start_time += (
            possible_cycles * BUILD_TIME
        )

        if self.total_built >= FACTORY_LIFETIME:
            self.active = False
            self.start_time = 0

            try:
                await bot.send_message(
                    user.chat_id,
                    "🏭 کارخانه شما پس از تولید "
                    f"{FACTORY_LIFETIME} صابون خراب شد! "
                    "برای تولید بیشتر باید کارخانه جدید بسازید."
                )
            except Exception:
                pass

        await user.save_to_data()


# =========================
# USER
# =========================

class User:

    def __init__(
        self,
        chat_id,
        telegram_user,
        data
    ):
        self.chat_id = chat_id
        self.user_id = telegram_user.id

        self.first_name = (
            telegram_user.first_name
            or "Unknown"
        )

        self.username = (
            telegram_user.username
        )

        users = data.setdefault(
            str(chat_id),
            {}
        )

        info = users.setdefault(
            str(self.user_id),
            {}
        )

        self.furnaces = info.get(
            "furnaces",
            0
        )

        self.gas = info.get(
            "gas",
            0
        )

        self.last = info.get(
            "last",
            0
        )

        self.fat = info.get(
            "fat",
            0
        )

        self.ash = info.get(
            "ash",
            0
        )

        self.soap = info.get(
            "soap",
            0
        )

        self.total_soap_earned = info.get(
            "total_soap_earned",
            self.soap
        )

        self.factory = Factory(
            info.get("factory", {})
        )

        # Keep profile information updated
        self.first_name = (
            info.get(
                "first_name",
                self.first_name
            )
        )

        self.username = info.get(
            "username",
            self.username
        )

        self.data = data

    async def save_to_data(self):

        chat = self.data.setdefault(
            str(self.chat_id),
            {}
        )

        chat[str(self.user_id)] = {
            "first_name": self.first_name,
            "username": self.username,

            "furnaces": self.furnaces,
            "gas": self.gas,
            "last": self.last,

            "fat": self.fat,
            "ash": self.ash,
            "soap": self.soap,

            "total_soap_earned":
                self.total_soap_earned,

            "factory": {
                "exists":
                    self.factory.exists,

                "active":
                    self.factory.active,

                "building":
                    self.factory.active,

                "start_time":
                    self.factory.start_time,

                "soaps_ready":
                    self.factory.soaps_ready,

                "total_built":
                    self.factory.total_built,

                "fat_per_soap":
                    self.factory.fat_per_soap,

                "ash_per_soap":
                    self.factory.ash_per_soap,
            }
        }

        await save_data(self.data)


# =========================
# HELPERS
# =========================

def get_display_name(user):

    if user.username:
        return f"@{user.username}"

    return f"{user.first_name} (ID: {user.user_id})"


async def get_user(message, data=None):

    if data is None:
        data = await load_data()

    return User(
        message.chat.id,
        message.from_user,
        data
    )


async def get_target_user(
    message,
    data
):

    if not message.reply_to_message:
        return None

    target_tg = (
        message.reply_to_message
        .from_user
    )

    return User(
        message.chat.id,
        target_tg,
        data
    )


# =========================
# STATUS
# =========================

async def send_status(
    user: User,
    message: Message
):

    await user.factory.produce_soaps(
        user
    )

    markup = InlineKeyboardMarkup(
        row_width=2
    )

    owner_id = user.user_id

    markup.add(
        InlineKeyboardButton(
            "🧼 جمع‌آوری صابون",
            callback_data=f"collect:{owner_id}"
        ),
        InlineKeyboardButton(
            "🏆 لیدربورد",
            callback_data=f"leaderboard:{owner_id}"
        ),
        InlineKeyboardButton(
            (
                "⏹ خاموش کردن کارخانه"
                if user.factory.active
                else "▶️ روشن کردن کارخانه"
            ),
            callback_data=f"toggle:{owner_id}"
        )
    )

    factory_state = (
        "🟢 روشن"
        if user.factory.active
        else "🔴 خاموش"
    )

    if not user.factory.exists:
        factory_state = "❌ ساخته نشده"

    text = (
        "📊 *وضعیت شما*\n\n"

        f"👤 {get_display_name(user)}\n"
        f"🆔 ID: `{user.user_id}`\n\n"

        f"💪 چربی: `{user.fat}`\n"
        f"🔥 خاکستر: `{user.ash}`\n"
        f"🧼 صابون: `{user.soap}`\n"
        f"🏆 کل صابون کسب‌شده: "
        f"`{user.total_soap_earned}`\n\n"

        f"🏭 وضعیت کارخانه: {factory_state}\n"
        f"🧈 مصرف چربی هر صابون: "
        f"`{user.factory.fat_per_soap}`\n"
        f"🔥 مصرف خاکستر هر صابون: "
        f"`{user.factory.ash_per_soap}`\n"
        f"🧼 صابون آماده: "
        f"`{user.factory.soaps_ready}`\n"
        f"📦 کل تولید کارخانه: "
        f"`{user.factory.total_built}`"
    )

    await bot.reply_to(
        message,
        text,
        reply_markup=markup,
        parse_mode="Markdown"
    )


# =========================
# START
# =========================

@bot.message_handler(
    commands=["start"]
)
async def start(message):

    text = (
        "سلام 👋\n\n"
        "به بازی کارخانه صابون خوش اومدی.\n\n"
        "برای دیدن وضعیتت:\n"
        "`/وضعیت`\n\n"
        "برای دیدن راهنما:\n"
        "`/help`"
    )

    await bot.reply_to(
        message,
        text,
        parse_mode="Markdown"
    )


# =========================
# HELP
# =========================

@bot.message_handler(
    commands=["help", "Help"]
)
async def help_command(message):

    text = (
        "📖 *راهنمای بازی*\n\n"

        "📊 `/وضعیت`\n"
        "نمایش وضعیت، منابع و کارخانه.\n\n"

        "🏭 `ساخت کارخانه 2 3`\n"
        "ساخت کارخانه‌ای که برای هر صابون "
        "۲ چربی و ۳ خاکستر مصرف می‌کند.\n\n"

        "⏹ `خاموش کردن کارخانه`\n"
        "تولید کارخانه را متوقف می‌کند.\n\n"

        "▶️ `روشن کردن کارخانه`\n"
        "کارخانه را دوباره فعال می‌کند.\n\n"

        "🏆 `لیدربورد`\n"
        "نمایش رتبه‌بندی کاربران.\n\n"

        "🧼 `جمع‌آوری صابون`\n"
        "صابون‌های تولیدشده را به موجودی شما "
        "اضافه می‌کند.\n\n"

        "⚙️ `ارتقای کوره`\n"
        "با مصرف ۱۰ صابون، ۱ گاز دریافت می‌کنی.\n\n"

        "🔥 `برو تو کوره`\n"
        "اگر به پیام یک کاربر ریپلای کنی، "
        "می‌توانی او را وارد کوره کنی.\n\n"

        "💡 چربی به صورت خودکار در طول زمان "
        "دریافت می‌شود."
    )

    await bot.reply_to(
        message,
        text,
        parse_mode="Markdown"
    )


# =========================
# STATUS
# =========================

@bot.message_handler(
    func=lambda m:
        m.text and m.text.strip() == "/وضعیت"
)
async def status_command(message):

    data = await load_data()

    user = User(
        message.chat.id,
        message.from_user,
        data
    )

    now = time.time()

    # Fat generation
    if now - user.last >= FAT_COOLDOWN:

        periods = int(
            (now - user.last)
            // FAT_COOLDOWN
        )

        if periods <= 0:
            periods = 1

        gained = 0

        for _ in range(periods):
            gained += random.randint(
                FAT_MIN,
                FAT_MAX
            )

        user.fat += gained
        user.last = now

    await user.factory.produce_soaps(
        user
    )

    await send_status(
        user,
        message
    )


# =========================
# BUILD FACTORY
# =========================

@bot.message_handler(
    func=lambda m:
        m.text
        and m.text.startswith(
            "ساخت کارخانه"
        )
)
async def build_factory(message):

    data = await load_data()

    user = User(
        message.chat.id,
        message.from_user,
        data
    )

    parts = message.text.split()

    if len(parts) != 3:

        await bot.reply_to(
            message,
            "❌ فرمت درست:\n\n"
            "`ساخت کارخانه 2 3`\n\n"
            "عدد اول = مصرف چربی\n"
            "عدد دوم = مصرف خاکستر",
            parse_mode="Markdown"
        )

        return

    try:

        fat_cost = int(parts[2])
        ash_cost = int(parts[1])

        # Actually the intended syntax is:
        # ساخت کارخانه FAT ASH
        fat_cost = int(parts[1])
        ash_cost = int(parts[2])

    except ValueError:

        await bot.reply_to(
            message,
            "❌ مقدار مصرف باید عدد باشد."
        )

        return

    if fat_cost <= 0 or ash_cost <= 0:

        await bot.reply_to(
            message,
            "❌ مصرف هر دو منبع باید بیشتر از صفر باشد."
        )

        return

    if user.factory.exists:

        await bot.reply_to(
            message,
            "❌ شما از قبل یک کارخانه دارید."
        )

        return

    user.factory = Factory({
        "exists": True,
        "active": True,
        "start_time": time.time(),
        "soaps_ready": 0,
        "total_built": 0,
        "fat_per_soap": fat_cost,
        "ash_per_soap": ash_cost
    })

    await user.save_to_data()

    await bot.reply_to(
        message,
        "🏭 کارخانه با موفقیت ساخته شد!\n\n"
        f"💪 مصرف چربی: `{fat_cost}`\n"
        f"🔥 مصرف خاکستر: `{ash_cost}`\n"
        f"⏱ هر صابون: `{BUILD_TIME}` ثانیه\n"
        f"📦 ظرفیت عمر کارخانه: "
        f"`{FACTORY_LIFETIME}` صابون",
        parse_mode="Markdown"
    )


# =========================
# TURN FACTORY OFF
# =========================

@bot.message_handler(
    func=lambda m:
        m.text
        and m.text.strip()
        == "خاموش کردن کارخانه"
)
async def turn_factory_off(message):

    data = await load_data()

    user = User(
        message.chat.id,
        message.from_user,
        data
    )

    await user.factory.produce_soaps(
        user
    )

    if not user.factory.exists:

        await bot.reply_to(
            message,
            "❌ شما کارخانه‌ای ندارید."
        )

        return

    if not user.factory.active:

        await bot.reply_to(
            message,
            "ℹ️ کارخانه همین الان خاموش است."
        )

        return

    user.factory.active = False
    user.factory.start_time = 0

    await user.save_to_data()

    await bot.reply_to(
        message,
        "⏹ کارخانه خاموش شد."
    )


# =========================
# TURN FACTORY ON
# =========================

@bot.message_handler(
    func=lambda m:
        m.text
        and m.text.strip()
        == "روشن کردن کارخانه"
)
async def turn_factory_on(message):

    data = await load_data()

    user = User(
        message.chat.id,
        message.from_user,
        data
    )

    if not user.factory.exists:

        await bot.reply_to(
            message,
            "❌ شما کارخانه‌ای ندارید."
        )

        return

    if user.factory.total_built >= FACTORY_LIFETIME:

        await bot.reply_to(
            message,
            "❌ این کارخانه خراب شده و ظرفیتش تمام شده."
        )

        return

    if user.factory.active:

        await bot.reply_to(
            message,
            "ℹ️ کارخانه همین الان روشن است."
        )

        return

    user.factory.active = True
    user.factory.start_time = time.time()

    await user.save_to_data()

    await bot.reply_to(
        message,
        "▶️ کارخانه روشن شد."
    )


# =========================
# COLLECT SOAP TEXT
# =========================

@bot.message_handler(
    func=lambda m:
        m.text
        and m.text.strip()
        == "جمع‌آوری صابون"
)
async def collect_soap_text(message):

    data = await load_data()

    user = User(
        message.chat.id,
        message.from_user,
        data
    )

    await user.factory.produce_soaps(
        user
    )

    amount = user.factory.soaps_ready

    if amount <= 0:

        await bot.reply_to(
            message,
            "🧼 فعلاً صابونی برای جمع‌آوری وجود ندارد."
        )

        return

    user.soap += amount
    user.factory.soaps_ready = 0

    await user.save_to_data()

    await bot.reply_to(
        message,
        f"🧼 تعداد `{amount}` صابون جمع‌آوری شد!",
        parse_mode="Markdown"
    )


# =========================
# LEADERBOARD
# =========================

async def show_leaderboard(
    chat_id,
    reply_message=None
):

    data = await load_data()

    chat_users = data.get(
        str(chat_id),
        {}
    )

    if not chat_users:

        text = "🏆 هنوز کسی در لیدربورد نیست."

        if reply_message:
            await bot.reply_to(
                reply_message,
                text
            )
        else:
            await bot.send_message(
                chat_id,
                text
            )

        return

    sorted_users = sorted(
        chat_users.items(),
        key=lambda x:
            x[1].get(
                "total_soap_earned",
                x[1].get("soap", 0)
            ),
        reverse=True
    )

    ranking_text = (
        "🏆 *لیدربورد صابون*\n\n"
    )

    for i, (uid, udata) in enumerate(
        sorted_users[:10],
        1
    ):

        username = udata.get(
            "username"
        )

        first_name = udata.get(
            "first_name",
            "Unknown"
        )

        if username:
            name = f"@{username}"
        else:
            name = first_name

        total = udata.get(
            "total_soap_earned",
            udata.get("soap", 0)
        )

        ranking_text += (
            f"{i}. {name}\n"
            f"   🆔 `{uid}`\n"
            f"   🧼 `{total}` صابون\n\n"
        )

    if reply_message:

        await bot.reply_to(
            reply_message,
            ranking_text,
            parse_mode="Markdown"
        )

    else:

        await bot.send_message(
            chat_id,
            ranking_text,
            parse_mode="Markdown"
        )


@bot.message_handler(
    func=lambda m:
        m.text
        and m.text.strip()
        == "لیدربورد"
)
async def leaderboard_command(message):

    await show_leaderboard(
        message.chat.id,
        message
    )


# =========================
# FURNACE
# =========================

@bot.message_handler(
    func=lambda m:
        m.text
        and m.text.strip()
        == "برو تو کوره"
)
async def furnace(message):

    if not message.reply_to_message:

        await bot.reply_to(
            message,
            "❌ باید روی پیام شخص موردنظر ریپلای کنی."
        )

        return

    target_tg = (
        message.reply_to_message
        .from_user
    )

    # Prevent bots from entering the furnace
    if getattr(
        target_tg,
        "is_bot",
        False
    ):

        await bot.reply_to(
            message,
            "🤖 بات‌ها قابل انداختن داخل کوره نیستند."
        )

        return

    data = await load_data()

    user = User(
        message.chat.id,
        message.from_user,
        data
    )

    target = User(
        message.chat.id,
        target_tg,
        data
    )

    if target.user_id == user.user_id:

        await bot.reply_to(
            message,
            "😂 نمی‌تونی خودتو بندازی تو کوره!"
        )

        return

    # Reward
    target_fat = random.randint(
        1,
        3
    )

    user.ash += target_fat

    await user.save_to_data()
    await target.save_to_data()

    await bot.reply_to(
        message,
        f"🔥 {get_display_name(target)} "
        "به کوره فرستاده شد!\n"
        f"🔥 شما `{target_fat}` خاکستر دریافت کردی.",
        parse_mode="Markdown"
    )


# =========================
# UPGRADE FURNACE
# =========================

@bot.message_handler(
    func=lambda m:
        m.text
        and m.text.strip()
        == "ارتقای کوره"
)
async def upgrade_furnace(message):

    data = await load_data()

    user = User(
        message.chat.id,
        message.from_user,
        data
    )

    COST = 10

    if user.soap < COST:

        await bot.reply_to(
            message,
            f"❌ برای ارتقای کوره "
            f"`{COST}` صابون لازم داری.\n"
            f"موجودی فعلی: `{user.soap}`",
            parse_mode="Markdown"
        )

        return

    user.soap -= COST
    user.furnaces += 1
    user.gas += 1

    await user.save_to_data()

    await bot.reply_to(
        message,
        "⚙️ کوره ارتقا پیدا کرد!\n\n"
        "➕ ۱ کوره\n"
        "➕ ۱ گاز\n"
        f"🧼 هزینه: `{COST}` صابون",
        parse_mode="Markdown"
    )


# =========================
# CALLBACKS
# =========================

@bot.callback_query_handler(
    func=lambda call: True
)
async def process_callback(
    call: CallbackQuery
):

    try:

        parts = call.data.split(":")

        if len(parts) != 2:

            await bot.answer_callback_query(
                call.id,
                "❌ دکمه نامعتبر است.",
                show_alert=True
            )

            return

        action = parts[0]

        try:
            owner_id = int(parts[1])
        except ValueError:

            await bot.answer_callback_query(
                call.id,
                "❌ مالک نامعتبر است.",
                show_alert=True
            )

            return

        # IMPORTANT:
        # Only the person who opened the status
        # can use its buttons.
        if call.from_user.id != owner_id:

            await bot.answer_callback_query(
                call.id,
                "❌ این دکمه برای شما نیست.",
                show_alert=True
            )

            return

        data = await load_data()

        user = User(
            call.message.chat.id,
            call.from_user,
            data
        )

        if action == "collect":

            await user.factory.produce_soaps(
                user
            )

            amount = (
                user.factory.soaps_ready
            )

            if amount <= 0:

                await bot.answer_callback_query(
                    call.id,
                    "🧼 هنوز صابونی آماده نیست."
                )

                return

            user.soap += amount
            user.total_soap_earned += amount

            user.factory.soaps_ready = 0

            await user.save_to_data()

            await bot.answer_callback_query(
                call.id,
                f"🧼 {amount} صابون جمع شد!"
            )

            await send_status(
                user,
                call.message
            )

        elif action == "leaderboard":

            await bot.answer_callback_query(
                call.id
            )

            await show_leaderboard(
                call.message.chat.id,
                call.message
            )

        elif action == "toggle":

            if not user.factory.exists:

                await bot.answer_callback_query(
                    call.id,
                    "❌ کارخانه‌ای ندارید.",
                    show_alert=True
                )

                return

            await user.factory.produce_soaps(
                user
            )

            if user.factory.active:

                user.factory.active = False
                user.factory.start_time = 0

                message = (
                    "⏹ کارخانه خاموش شد."
                )

            else:

                if (
                    user.factory.total_built
                    >= FACTORY_LIFETIME
                ):

                    await bot.answer_callback_query(
                        call.id,
                        "❌ ظرفیت کارخانه تمام شده.",
                        show_alert=True
                    )

                    return

                user.factory.active = True
                user.factory.start_time = (
                    time.time()
                )

                message = (
                    "▶️ کارخانه روشن شد."
                )

            await user.save_to_data()

            await bot.answer_callback_query(
                call.id,
                message
            )

            await send_status(
                user,
                call.message
            )

        else:

            await bot.answer_callback_query(
                call.id,
                "❌ عملیات ناشناخته.",
                show_alert=True
            )

    except Exception:

        logging.exception(
            "Callback error"
        )

        try:
            await bot.answer_callback_query(
                call.id,
                "❌ یک خطا رخ داد.",
                show_alert=True
            )
        except Exception:
            pass


# =========================
# GLOBAL ERROR HANDLER
# =========================

async def safe_polling():

    while True:

        try:

            await bot.polling(
                non_stop=True,
                skip_pending=True
            )

        except Exception:

            logging.exception(
                "Polling crashed"
            )

            await asyncio.sleep(5)


# =========================
# RUN
# =========================

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(message)s"
        )
    )

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    # Flask runs separately
    def run_flask():
        app.run(
            host="0.0.0.0",
            port=port
        )

    Thread(
        target=run_flask,
        daemon=True
    ).start()

    asyncio.run(
        safe_polling()
    )