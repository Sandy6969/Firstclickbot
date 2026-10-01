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
                                )
            ]
        ]

        try:
            await context.bot.edit_message_text(
                chat_id=race["chat_id"],
                message_id=race["message_id"],
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )

        except Exception as e:
            logger.warning(
                "Countdown edit failed: %s",
                e,
            )

        return

    # =====================================================
    # RACE START
    # =====================================================

    race["status"] = "live"

    update_race_db(
        race_id,
        status="live",
    )

    text = (
        "🚨🔥 <b>RACE IS LIVE!</b> 🔥🚨\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏆 <b>{h(race['title'])}</b>\n\n"
        "⚡ <b>FIRST CLICK WINS</b>\n\n"
        "The fastest participant gets the crown.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>GO! GO! GO!</b> 🔥"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🏆 CLAIM NOW 🏆",
                callback_data=f"click_{race_id}",
            )
        ]
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=race["chat_id"],
            message_id=race["message_id"],
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.exception(
            "Race start edit failed"
        )

    context.job.schedule_removal()


# =========================================================
# LOCKED BUTTON
# =========================================================

async def locked_click(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer(
        "🔒 Race hasn't started yet. Please wait!",
        show_alert=True,
    )


# =========================================================
# CLAIM / WINNER
# =========================================================

async def handle_click(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    race_id = query.data.replace("click_", "", 1)

    race = races.get(race_id)

    if not race:
        await query.answer(
            "Race no longer exists.",
            show_alert=True,
        )
        return

    if race["status"] != "live":

        await query.answer(
            "This race is not active.",
            show_alert=True,
        )

        return

    user = query.from_user
    user_id = user.id

    # =====================================================
    # FIRST CLICK WINS
    # =====================================================

    click_time_utc = utc_now()

    username = user.username
    display_name = user.full_name or "Unknown User"

    # Prevent duplicate
    if user_id in race["clicks"]:

        await query.answer(
            "You already clicked!",
            show_alert=True,
        )

        return

    race["clicks"][user_id] = {
        "username": username,
        "display_name": display_name,
        "time_utc": click_time_utc,
    }

    race["status"] = "finished"

    timezone_name = get_user_timezone(user_id)

    local_time = format_local_time(
        click_time_utc,
        timezone_name,
    )

    username_display = (
        f"@{h(username)}"
        if username
        else h(display_name)
    )

    winner_display = (
        username_display
        if race["winner_show"]
        else "Anonymous Winner"
    )

    # =====================================================
    # SAVE WINNER
    # =====================================================

    update_race_db(
        race_id,
        status="finished",
        ended_at_utc=utc_iso(click_time_utc),
        winner_user_id=user_id,
        winner_username=username,
        winner_display_name=display_name,
        winner_time_utc=utc_iso(click_time_utc),
    )

    db_execute(
        """
        INSERT OR IGNORE INTO participants(
            race_id,
            user_id,
            username,
            display_name,
            clicked_at_utc,
            timezone,
            rank
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            race_id,
            user_id,
            username,
            display_name,
            utc_iso(click_time_utc),
            timezone_name,
            1,
        ),
    )

    # =====================================================
    # RESULT MESSAGE
    # =====================================================

    result_text = (
        "🏆✨🏆✨🏆✨🏆✨🏆\n\n"
        "👑 <b>WINNER</b> 👑\n\n"
        "🥇\n\n"
        f"<b>{winner_display}</b>\n\n"
        "⚡ <b>1ST PLACE</b> ⚡\n\n"
        f"🕐 UTC:\n"
        f"<code>{h(utc_iso(click_time_utc))}</code>\n\n"
        f"🌍 Local Time:\n"
        f"<code>{h(local_time)}</code>\n\n"
        "🔥 <b>LEGENDARY SPEED!</b> 🔥\n\n"
        "🏆✨🏆✨🏆✨🏆✨🏆"
    )

    try:
        await context.bot.edit_message_text(
            chat_id=race["chat_id"],
            message_id=race["message_id"],
            text=result_text,
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.exception(
            "Winner message edit failed"
        )

    await query.answer(
        "🏆 YOU WON!",
        show_alert=True,
    )

    # =====================================================
    # PRIVATE MESSAGE
    # =====================================================

    try:

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🏆✨ <b>CONGRATULATIONS!</b> ✨🏆\n\n"
                "👑 <b>YOU ARE THE WINNER!</b>\n\n"
                f"🏆 Race:\n"
                f"<b>{h(race['title'])}</b>\n\n"
                f"🕐 UTC:\n"
                f"<code>{h(utc_iso(click_time_utc))}</code>\n\n"
                f"🌍 Your Local Time:\n"
                f"<code>{h(local_time)}</code>\n\n"
                "🥇 <b>1ST PLACE</b>"
            ),
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:

        logger.warning(
            "Could not send private winner message: %s",
            e,
        )


# =========================================================
# MY RANK
# =========================================================

async def myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    rows = db_execute(
        """
        SELECT
            p.*,
            r.title,
            r.status
        FROM participants p
        JOIN races r
            ON p.race_id = r.race_id
        WHERE p.user_id = ?
        ORDER BY p.id DESC
        LIMIT 10
        """,
        (user_id,),
        fetchall=True,
    )

    if not rows:

        await update.message.reply_text(
            "📊 <b>YOUR RACE HISTORY</b>\n\n"
            "You haven't participated in any race yet.",
            parse_mode=ParseMode.HTML,
        )

        return

    text = (
        "📊 <b>YOUR RACE HISTORY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    timezone_name = get_user_timezone(user_id)

    for row in rows:

        clicked = parse_utc(
            row["clicked_at_utc"]
        )

        local_time = format_local_time(
            clicked,
            timezone_name,
        )

        text += (
            f"🏆 <b>{h(row['title'])}</b>\n"
            f"🥇 Position: <b>{ordinal(row['rank'])}</b>\n"
            f"🕐 {h(local_time)}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# TIMEZONE
# =========================================================

TIMEZONE_OPTIONS = {
    "tz_jakarta": "Asia/Jakarta",
    "tz_singapore": "Asia/Singapore",
    "tz_kuala": "Asia/Kuala_Lumpur",
    "tz_tokyo": "Asia/Tokyo",
    "tz_shanghai": "Asia/Shanghai",
    "tz_newyork": "America/New_York",
    "tz_losangeles": "America/Los_Angeles",
    "tz_london": "Europe/London",
}


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if context.args:

        timezone_name = context.args[0]

        try:
            ZoneInfo(timezone_name)

        except ZoneInfoNotFoundError:

            await update.message.reply_text(
                "❌ Invalid timezone.\n\n"
                "Example:\n"
                "<code>/timezone Asia/Jakarta</code>",
                parse_mode=ParseMode.HTML,
            )

            return

        save_user_timezone(
            update.effective_user.id,
            timezone_name,
        )

        await update.message.reply_text(
            f"✅ <b>Timezone Saved</b>\n\n"
            f"🌍 {h(timezone_name)}",
            parse_mode=ParseMode.HTML,
        )

        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🇮🇩 Jakarta",
                callback_data="tz_jakarta",
            ),
            InlineKeyboardButton(
                "🇸🇬 Singapore",
                callback_data="tz_singapore",
            ),
        ],
        [
            InlineKeyboardButton(
                "🇲🇾 Kuala Lumpur",
                callback_data="tz_kuala",
            ),
            InlineKeyboardButton(
                "🇯🇵 Tokyo",
                callback_data="tz_tokyo",
            ),
        ],
        [
            InlineKeyboardButton(
                "🇨🇳 Shanghai",
                callback_data="tz_shanghai",
            ),
        ],
        [
            InlineKeyboardButton(
                "🇺🇸 New York",
                callback_data="tz_newyork",
            ),
            InlineKeyboardButton(
                "🇺🇸 Los Angeles",
                callback_data="tz_losangeles",
            ),
        ],
        [
            InlineKeyboardButton(
                "🇬🇧 London",
                callback_data="tz_london",
            ),
        ],
    ]

    current = get_user_timezone(
        update.effective_user.id
    )

    await update.message.reply_text(
        "🌍 <b>YOUR TIMEZONE</b>\n\n"
        f"Current:\n"
        f"<code>{h(current)}</code>\n\n"
        "Choose your local timezone:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    timezone_name = TIMEZONE_OPTIONS.get(query.data)

    if not timezone_name:
        await query.answer("Invalid timezone.")
        return

    save_user_timezone(
        query.from_user.id,
        timezone_name,
    )

    await query.answer(
        "Timezone saved!",
        show_alert=True,
    )

    await query.edit_message_text(
        "🌍 <b>TIMEZONE SAVED</b>\n\n"
        f"Your timezone:\n"
        f"<code>{h(timezone_name)}</code>",
        parse_mode=ParseMode.HTML,
    )


#=========================================================
# ADMIN PANEL
# =========================================================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Owner only.")
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🟢 ACTIVE RACES",
                callback_data="admin_active",
            )
        ],
        [
            InlineKeyboardButton(
                "📜 RACE HISTORY",
                callback_data="admin_history",
            )
        ],
    ]

    await update.message.reply_text(
        "👑 <b>ADMIN PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Manage your FirstClick races:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def admin_active(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if not is_owner(query.from_user.id):
        await query.answer("Owner only.", show_alert=True)
        return

    rows = db_execute(
        """
        SELECT *
        FROM races
        WHERE status IN ('countdown', 'live')
        ORDER BY created_at_utc DESC
        """,
        fetchall=True,
    )

    if not rows:

        await query.edit_message_text(
            "🟢 <b>ACTIVE RACES</b>\n\n"
            "No active races.",
            parse_mode=ParseMode.HTML,
        )

        return

    text = (
        "🟢 <b>ACTIVE RACES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    keyboard = []

    for row in rows:

        text += (
            f"🏆 <b>{h(row['title'])}</b>\n"
            f"📍 {h(row['chat_title'] or row['destination'])}\n"
            f"⚡ Status: <b>{h(row['status'])}</b>\n"
            f"🆔 <code>{h(row['race_id'])}</code>\n\n"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🗑 Delete {row['race_id']}",
                    callback_data=f"delete_confirm_{row['race_id']}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data="admin_active",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def admin_history(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if not is_owner(query.from_user.id):
        await query.answer("Owner only.", show_alert=True)
        return

    rows = db_execute(
        """
        SELECT *
        FROM races
        ORDER BY created_at_utc DESC
        LIMIT 15
        """,
        fetchall=True,
    )

    if not rows:

        await query.edit_message_text(
            "📜 <b>RACE HISTORY</b>\n\n"
            "No races recorded.",
            parse_mode=ParseMode.HTML,
        )

        return

    text = (
        "📜 <b>RACE HISTORY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for row in rows:

        winner = "—"

        if row["winner_username"]:
            winner = f"@{row['winner_username']}"

        elif row["winner_display_name"]:
            winner = row["winner_display_name"]

        text += (
            f"🏆 <b>{h(row['title'])}</b>\n"
            f"📍 {h(row['chat_title'] or row['destination'])}\n"
            f"📌 Status: <b>{h(row['status'])}</b>\n"
            f"👑 Winner: <b>{h(winner)}</b>\n"
            f"🆔 <code>{h(row['race_id'])}</code>\n\n"
        )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# DELETE RACE
# =========================================================

async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if not is_owner(query.from_user.id):
        await query.answer("Owner only.", show_alert=True)
        return

    race_id = query.data.replace(
        "delete_confirm_",
        "",
        1,
    )

    row = get_race_db(race_id)

    if not row:
        await query.answer(
            "Race not found.",
            show_alert=True,
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "❌ CANCEL",
                callback_data="admin_active",
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 YES, DELETE",
                callback_data=f"delete_yes_{race_id}",
            )
        ],
    ]

    await query.edit_message_text(
        "⚠️ <b>DELETE ACTIVE RACE?</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏆 <b>{h(row['title'])}</b>\n"
        f"📍 {h(row['chat_title'] or row['destination'])}\n"
        f"⚡ Status: <b>{h(row['status'])}</b>\n\n"
        "The race will be stopped.\n"
        "The history record will remain.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if not is_owner(query.from_user.id):
        await query.answer("Owner only.", show_alert=True)
        return

    race_id = query.data.replace(
        "delete_yes_",
        "",
        1,
    )

    row = get_race_db(race_id)

    if not row:
        await query.answer(
            "Race not found.",
            show_alert=True,
        )
        return

    remove_race_jobs(
        context.application,
        race_id,
    )

    race = races.get(race_id)

    if race:

        try:

            await context.bot.edit_message_text(
                chat_id=race["chat_id"],
                message_id=race["message_id"],
                text=(
                    "🗑 <b>RACE CANCELLED</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🏆 {h(race['title'])}\n\n"
                    "This race has been cancelled by the administrator."
                ),
                parse_mode=ParseMode.HTML,
            )

        except Exception as e:

            logger.warning(
                "Could not edit deleted race: %s",
                e,
            )

        races.pop(race_id, None)

    update_race_db(
        race_id,
        status="deleted",
        ended_at_utc=utc_iso(),
    )

    await query.answer(
        "Race deleted.",
        show_alert=True,
    )

    await admin_active(
        update,
        context,
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "❌ Operation cancelled."
    )

    return ConversationHandler.END


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if is_owner(update.effective_user.id):

        await update.message.reply_text(
            "👑 <b>FIRSTCLICK PRO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Welcome, Owner!\n\n"
            "🏆 <b>Commands</b>\n\n"
            "/newrace — Create race\n"
            "/admin — Admin panel\n"
            "/myrank — Your race history\n"
            "/timezone — Set your timezone\n"
            "/cancel — Cancel current setup",
            parse_mode=ParseMode.HTML,
        )

    else:

        await update.message.reply_text(
            "🏆 <b>FIRSTCLICK PRO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Join races through the race channel.\n\n"
            "/myrank — Check your race history\n"
            "/timezone — Set your local timezone\n\n"
            f"👑 Owner: {owner_label()}",
            parse_mode=ParseMode.HTML,
        )


# =========================================================
# RESTORE ACTIVE RACES AFTER RAILWAY RESTART
# =========================================================

async def post_init(application: Application):

    init_db()

    rows = db_execute(
        """
        SELECT *
        FROM races
        WHERE status IN ('countdown', 'live')
        """,
        fetchall=True,
    )

    for row in rows:

        race = {
            "title": row["title"],
            "countdown": row["countdown"],
            "winner_show": bool(row["winner_show"]),
            "public_rank": bool(row["public_rank"]),
            "show_username": bool(row["show_username"]),
            "destination": row["destination"],
            "status": row["status"],
            "message_id": row["message_id"],
            "chat_id": row["chat_id"],
            "chat_title": row["chat_title"],
            "clicks": {},
        }

        if row["start_at_utc"]:
            race["start_at"] = parse_utc(
                row["start_at_utc"]
            )

        races[row["race_id"]] = race

        if row["status"] == "countdown":

            schedule_race(
                application,
                row["race_id"],
            )

    logger.info(
        "Restored %s active race(s).",
        len(rows),
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        print("Error: BOT_TOKEN not set")
        return

    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    text_filter = filters.TEXT & filters.Regex(
        r"^(?!/)"
    )

    conv_handler = ConversationHandler(

        entry_points=[
            CommandHandler(
                "newrace",
                newrace,
            )
        ],

        states={

            TITLE: [
                MessageHandler(
                    text_filter,
                    receive_title,
                )
            ],

            COUNTDOWN: [
                MessageHandler(
                    text_filter,
                    receive_countdown,
                )
            ],

            WINNER_MODE: [
                CallbackQueryHandler(
                    winner_mode
                )
            ],

            PUBLIC_RANK: [
                CallbackQueryHandler(
                    public_rank
                )
            ],

            SHOW_USERNAME: [
                CallbackQueryHandler(
                    show_username
                )
            ],

            CONFIRM: [
                MessageHandler(
                    text_filter,
                    receive_channel,
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel,
            )
        ],
    )

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(conv_handler)

    app.add_handler(
        CallbackQueryHandler(
            publish_race,
            pattern=r"^publish_",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            locked_click,
            pattern=r"^locked_",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            handle_click,
            pattern=r"^click_",
        )
    )

    app.add_handler(
        CommandHandler(
            "myrank",
            myrank,
        )
    )

    app.add_handler(
        CommandHandler(
            "timezone",
            timezone_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "admin",
            admin,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            timezone_callback,
            pattern=r"^tz_",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_active,
            pattern=r"^admin_active$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_history,
            pattern=r"^admin_history$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_confirm,
            pattern=r"^delete_confirm_",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            delete_yes,
            pattern=r"^delete_yes_",
        )
    )

    print("Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
