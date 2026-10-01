import os
import html
import sqlite3
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Owner")

DB_DIR = "/app/data"
DB_PATH = os.path.join(DB_DIR, "races.db")

TITLE, COUNTDOWN, WINNER_MODE, PUBLIC_RANK, SHOW_USERNAME, CONFIRM = range(6)

races = {}


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def init_db():
    os.makedirs(DB_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS races (
            race_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            countdown INTEGER NOT NULL,
            winner_show INTEGER NOT NULL,
            public_rank INTEGER NOT NULL,
            show_username INTEGER NOT NULL,

            destination TEXT NOT NULL,
            chat_id TEXT,
            chat_title TEXT,
            chat_type TEXT,

            message_id INTEGER,

            status TEXT NOT NULL,

            created_at_utc TEXT,
            start_at_utc TEXT,
            ended_at_utc TEXT,

            winner_user_id INTEGER,
            winner_username TEXT,
            winner_display_name TEXT,
            winner_time_utc TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            race_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,

            username TEXT,
            display_name TEXT,

            clicked_at_utc TEXT NOT NULL,
            timezone TEXT,

            rank INTEGER,

            UNIQUE(race_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def db_execute(query, params=(), fetchone=False, fetchall=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()
    cur.execute(query, params)

    result = None

    if fetchone:
        result = cur.fetchone()

    elif fetchall:
        result = cur.fetchall()

    conn.commit()
    conn.close()

    return result


# =========================================================
# TIME
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso(dt=None):
    if dt is None:
        dt = utc_now()

    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def parse_utc(value):
    if not value:
        return None

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def get_user_timezone(user_id):
    row = db_execute(
        "SELECT timezone FROM user_settings WHERE user_id = ?",
        (user_id,),
        fetchone=True,
    )

    if row:
        return row["timezone"]

    return "UTC"


def save_user_timezone(user_id, timezone_name):
    db_execute(
        """
        INSERT INTO user_settings(user_id, timezone)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET timezone = excluded.timezone
        """,
        (user_id, timezone_name),
    )


def format_local_time(utc_time, timezone_name):
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
        timezone_name = "UTC"

    local = utc_time.astimezone(tz)

    return (
        f"{local.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
        f" ({timezone_name})"
    )


# =========================================================
# TEXT HELPERS
# =========================================================

def h(value):
    return html.escape(str(value))


def owner_label():
    name = str(OWNER_USERNAME).strip()

    if name.startswith("@"):
        return h(name)

    return f"@{h(name)}"


def ordinal(position):
    if 10 <= position % 100 <= 20:
        suffix = "th"
    else:
        suffix = {
            1: "st",
            2: "nd",
            3: "rd",
        }.get(position % 10, "th")

    return f"{position}{suffix}"


# =========================================================
# BIG COUNTDOWN DIGITS
# =========================================================

DIGITS = {
    "0": [
        " ███ ",
        "██ ██",
        "██ ██",
        "██ ██",
        " ███ ",
    ],
    "1": [
        " ██  ",
        "███  ",
        " ██  ",
        " ██  ",
        "█████",
    ],
    "2": [
        " ███ ",
        "██ ██",
        "  ██ ",
        " ██  ",
        "█████",
    ],
    "3": [
        "████ ",
        "   ██",
        " ███ ",
        "   ██",
        "████ ",
    ],
    "4": [
        "██ ██",
        "██ ██",
        "█████",
        "   ██",
        "   ██",
    ],
    "5": [
        "█████",
        "██   ",
        "████ ",
        "   ██",
        "████ ",
    ],
    "6": [
        " ███ ",
        "██   ",
        "████ ",
        "██ ██",
        " ███ ",
    ],
    "7": [
        "█████",
        "   ██",
        "  ██ ",
        " ██  ",
        "██   ",
    ],
    "8": [
        " ███ ",
        "██ ██",
        " ███ ",
        "██ ██",
        " ███ ",
    ],
    "9": [
        " ███ ",
        "██ ██",
        " ████",
        "   ██",
        " ███ ",
    ],
}


def big_number(number):
    text = str(number)

    rows = [""] * 5

    for char in text:
        pattern = DIGITS.get(char, DIGITS["0"])

        for i in range(5):
            rows[i] += pattern[i] + "   "

    return "\n".join(rows)


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(user_id):
    return user_id == OWNER_ID


# =========================================================
# RACE DATABASE
# =========================================================

def create_race_db(race_id, race):
    db_execute(
        """
        INSERT INTO races(
            race_id,
            title,
            countdown,
            winner_show,
            public_rank,
            show_username,
            destination,
            status,
            created_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            race_id,
            race["title"],
            race["countdown"],
            int(race["winner_show"]),
            int(race["public_rank"]),
            int(race["show_username"]),
            race["destination"],
            "ready",
            utc_iso(),
        ),
    )


def update_race_db(race_id, **fields):
    if not fields:
        return

    keys = list(fields.keys())

    sql = (
        "UPDATE races SET "
        + ", ".join(f"{key} = ?" for key in keys)
        + " WHERE race_id = ?"
    )

    params = [fields[key] for key in keys]
    params.append(race_id)

    db_execute(sql, params)


def get_race_db(race_id):
    return db_execute(
        "SELECT * FROM races WHERE race_id = ?",
        (race_id,),
        fetchone=True,
    )


# =========================================================
# JOB CONTROL
# =========================================================

def remove_race_jobs(application, race_id):
    if not application.job_queue:
        return

    jobs = application.job_queue.get_jobs_by_name(race_id)

    for job in jobs:
        job.schedule_removal()


def schedule_race(application, race_id):
    race = races.get(race_id)

    if not race:
        return

    if not application.job_queue:
        logger.error("JobQueue is not available.")
        return

    remove_race_jobs(application, race_id)

    application.job_queue.run_repeating(
        countdown_job,
        interval=1,
        first=1,
        data={"race_id": race_id},
        name=race_id,
    )


# =========================================================
# NEW RACE
# =========================================================

async def newrace(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Only the owner can create races."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🏆 <b>FIRSTCLICK PRO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✨ <b>CREATE NEW RACE</b>\n\n"
        "Please send me the <b>Race Title</b>.\n\n"
        "Example:\n"
        "<code>Golden Hand Speed Challenge</code>",
        parse_mode=ParseMode.HTML,
    )

    return TITLE


async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):

    title = update.message.text.strip()

    if not title:
        await update.message.reply_text("Please enter a valid title.")
        return TITLE

    context.user_data["title"] = title

    await update.message.reply_text(
        f"✅ Title saved:\n\n"
        f"<b>{h(title)}</b>\n\n"
        f"⏱ Now send the <b>Countdown Seconds</b>.\n\n"
        f"Example: <code>10</code>",
        parse_mode=ParseMode.HTML,
    )

    return COUNTDOWN


async def receive_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        seconds = int(update.message.text.strip())

        if seconds < 3 or seconds > 300:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "❌ Please enter a number between 3 and 300."
        )
        return COUNTDOWN

    context.user_data["countdown"] = seconds

    keyboard = [
        [
            InlineKeyboardButton(
                "👑 Show Real Username",
                callback_data="winner_show",
            )
        ],
        [
            InlineKeyboardButton(
                "🕶 Anonymous",
                callback_data="winner_hide",
            )
        ],
    ]

    await update.message.reply_text(
        f"⏱ <b>Countdown:</b> {seconds} seconds\n\n"
        f"👑 <b>Winner Display Mode</b>\n\n"
        f"Choose how the winner should be displayed.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )

    return WINNER_MODE


async def winner_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    context.user_data["winner_show"] = (
        query.data == "winner_show"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🌟 Yes - Public Ranking",
                callback_data="public_yes",
            )
        ],
        [
            InlineKeyboardButton(
                "🔒 No - Private",
                callback_data="public_no",
            )
        ],
    ]

    await query.edit_message_text(
        "📊 <b>PUBLIC RANKING</b>\n\n"
        "Do you want the result to be shown publicly?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )

    return PUBLIC_RANK


async def public_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    context.user_data["public_rank"] = (
        query.data == "public_yes"
    )

    if context.user_data["public_rank"]:

        keyboard = [
            [
                InlineKeyboardButton(
                    "👤 Show Usernames",
                    callback_data="name_show",
                )
            ],
            [
                InlineKeyboardButton(
                    "🕶 Anonymous",
                    callback_data="name_hide",
                )
            ],
        ]

        await query.edit_message_text(
            "👤 <b>USERNAME DISPLAY</b>\n\n"
            "Show participant usernames in the public result?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )

        return SHOW_USERNAME

    context.user_data["show_username"] = False

    return await confirm_race(update, context)


async def show_username(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    context.user_data["show_username"] = (
        query.data == "name_show"
    )

    return await confirm_race(update, context)


async def confirm_race(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    title = context.user_data["title"]
    countdown = context.user_data["countdown"]

    winner_show = (
        "Show Real Username"
        if context.user_data["winner_show"]
        else "Anonymous"
    )

    public = (
        "Yes"
        if context.user_data["public_rank"]
        else "No"
    )

    show_name = (
        "Show Usernames"
        if context.user_data.get("show_username")
        else "Anonymous"
    )

    text = (
        "✅ <b>RACE CONFIGURATION</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏆 Title: <b>{h(title)}</b>\n"
        f"⏱ Countdown: <b>{countdown}s</b>\n"
        f"👑 Winner: <b>{winner_show}</b>\n"
        f"📊 Public Ranking: <b>{public}</b>\n"
        f"👤 Usernames: <b>{show_name}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📢 Send the <b>Channel or Group</b> destination.\n\n"
        "Examples:\n"
        "<code>@YourChannel</code>\n"
        "or\n"
        "<code>-1001234567890</code>"
    )

    if query:
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
        )

    return CONFIRM


# =========================================================
# RECEIVE DESTINATION
# =========================================================

async def receive_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    destination = update.message.text.strip()

    if not destination.startswith("@") and not destination.startswith("-100"):
        await update.message.reply_text(
            "❌ Invalid destination.\n\n"
            "Use:\n"
            "@YourChannel\n\n"
            "or a Telegram chat ID such as:\n"
            "-1001234567890"
        )
        return CONFIRM

    context.user_data["destination"] = destination

    race_id = f"FC-{int(utc_now().timestamp())}"

    race = {
        "title": context.user_data["title"],
        "countdown": context.user_data["countdown"],
        "winner_show": context.user_data["winner_show"],
        "public_rank": context.user_data["public_rank"],
        "show_username": context.user_data.get("show_username", False),
        "destination": destination,
        "status": "ready",
        "message_id": None,
        "clicks": {},
    }

    races[race_id] = race

    create_race_db(race_id, race)

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 PUBLISH RACE",
                callback_data=f"publish_{race_id}",
            )
        ]
    ]

    await update.message.reply_text(
        f"📢 <b>DESTINATION READY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 {h(destination)}\n\n"
        f"🆔 Race ID:\n"
        f"<code>{race_id}</code>\n\n"
        f"Tap below to publish.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )

    return ConversationHandler.END


# =========================================================
# PUBLISH
# =========================================================

async def publish_race(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if not is_owner(query.from_user.id):
        return

    race_id = query.data.replace("publish_", "", 1)
    race = races.get(race_id)

    if not race:
        await query.edit_message_text("❌ Race not found.")
        return

    destination = race["destination"]

    try:
        chat = await context.bot.get_chat(destination)

        chat_id = chat.id
        chat_title = chat.title or chat.username or str(chat.id)
        chat_type = chat.type

        start_time = utc_now()

        race["status"] = "countdown"
        race["start_at"] = start_time
        race["chat_id"] = chat_id
        race["chat_title"] = chat_title

        countdown = race["countdown"]

        text = (
            "🏆✨ <b>CUSTOM RACE</b> ✨🏆\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔥 <b>{h(race['title'])}</b>\n\n"
            "⚡ <b>GET READY</b>\n\n"
            f"<pre>{big_number(countdown)}</pre>\n\n"
            "🔒 <b>LOCKED</b>\n"
            "Race has not started yet.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👑 Owner: {owner_label()}"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔒 {countdown}",
                    callback_data=f"locked_{race_id}",
                )
            ]
        ]

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )

        race["message_id"] = msg.message_id

        update_race_db(
            race_id,
            status="countdown",
            chat_id=str(chat_id),
            chat_title=chat_title,
            chat_type=chat_type,
            message_id=msg.message_id,
            start_at_utc=utc_iso(start_time),
        )

        schedule_race(context.application, race_id)

        await query.edit_message_text(
            f"✅ <b>RACE PUBLISHED</b>\n\n"
            f"📍 {h(chat_title)}\n"
            f"🆔 <code>{race_id}</code>\n\n"
            f"⏱ Countdown: {countdown}s",
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:

        logger.exception("Publish race failed")

        await query.edit_message_text(
            f"❌ <b>Publish failed</b>\n\n"
            f"<code>{h(str(e))}</code>",
            parse_mode=ParseMode.HTML,
        )


# =========================================================
# COUNTDOWN
# =========================================================

async def countdown_job(context: ContextTypes.DEFAULT_TYPE):

    race_id = context.job.data["race_id"]

    race = races.get(race_id)

    if not race:
        context.job.schedule_removal()
        return

    start_time = race.get("start_at")

    if not start_time:
        context.job.schedule_removal()
        return

    remaining = int(
        (start_time - utc_now()).total_seconds()
    )

    if remaining > 0:

        text = (
            "🏆✨ <b>CUSTOM RACE</b> ✨🏆\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔥 <b>{h(race['title'])}</b>\n\n"
            "⚡ <b>GET READY</b>\n\n"
            f"<pre>{big_number(remaining)}</pre>\n\n"
            "🔒 <b>LOCKED</b>\n"
            "Too early to claim.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👑 Owner: {owner_label()}"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔒 {remaining}",
                    callback_data=f"locked_{race_id}",
                
