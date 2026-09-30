import os
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.helpers import escape_markdown


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Owner")

TITLE, COUNTDOWN, WINNER_MODE, PUBLIC_RANK, SHOW_USERNAME, CONFIRM = range(6)

races = {}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def get_time_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def md(text) -> str:
    """
    Safely escape dynamic text for Telegram Markdown v1.
    This prevents characters such as _, *, [, ], etc.
    from breaking Telegram entity parsing.
    """
    return escape_markdown(str(text), version=1)


def get_place_text(position: int) -> str:
    """
    Correct ordinal suffix:
    1st
    2nd
    3rd
    4th...
    """
    if 10 <= position % 100 <= 20:
        suffix = "th"
    else:
        suffix = {
            1: "st",
            2: "nd",
            3: "rd",
        }.get(position % 10, "th")

    return f"{position}{suffix} Place"


# ============================================================
# /newrace
# ============================================================

async def newrace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Only the owner can create races."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "✨ *FirstClick Pro - Create New Race*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Please send me the *Race Title*:\n\n"
        "Example: Golden Hand Speed Challenge",
        parse_mode=ParseMode.MARKDOWN,
    )

    return TITLE


# ============================================================
# RECEIVE TITLE
# ============================================================

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()

    if not title:
        await update.message.reply_text(
            "Please enter a valid race title."
        )
        return TITLE

    context.user_data["title"] = title

    await update.message.reply_text(
        f"Title saved: *{md(title)}*\n\n"
        "Now please send the *Countdown seconds* (number only):\n"
        "Example: 10 / 15 / 30 / 60",
        parse_mode=ParseMode.MARKDOWN,
    )

    return COUNTDOWN


# ============================================================
# RECEIVE COUNTDOWN
# ============================================================

async def receive_countdown(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        seconds = int(update.message.text.strip())

        if seconds < 3 or seconds > 300:
            await update.message.reply_text(
                "Please enter a number between 3 and 300."
            )
            return COUNTDOWN

        context.user_data["countdown"] = seconds

    except Exception:
        await update.message.reply_text(
            "Please enter a valid number."
        )
        return COUNTDOWN

    keyboard = [
        [
            InlineKeyboardButton(
                "Show Real Username",
                callback_data="winner_show"
            )
        ],
        [
            InlineKeyboardButton(
                "Anonymous (Hide Username)",
                callback_data="winner_hide"
            )
        ],
    ]

    await update.message.reply_text(
        f"Countdown set to: *{seconds} seconds*\n\n"
        "Now choose *Winner Display Mode*:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )

    return WINNER_MODE


# ============================================================
# WINNER MODE
# ============================================================

async def winner_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    context.user_data["winner_show"] = (
        query.data == "winner_show"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "Yes - Show Full Ranking",
                callback_data="public_yes"
            )
        ],
        [
            InlineKeyboardButton(
                "No - Only Private Rank",
                callback_data="public_no"
            )
        ],
    ]

    await query.edit_message_text(
        "Do you want to show the *Full Public Ranking* after the race?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )

    return PUBLIC_RANK


# ============================================================
# PUBLIC RANK
# ============================================================

async def public_rank(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    context.user_data["public_rank"] = (
        query.data == "public_yes"
    )

    if context.user_data["public_rank"]:

        keyboard = [
            [
                InlineKeyboardButton(
                    "Show Usernames",
                    callback_data="name_show"
                )
            ],
            [
                InlineKeyboardButton(
                    "Anonymous Mode",
                    callback_data="name_hide"
                )
            ],
        ]

        await query.edit_message_text(
            "In the public ranking, show real *Usernames*?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )

        return SHOW_USERNAME

    else:
        context.user_data["show_username"] = False
        return await confirm_race(update, context)


# ============================================================
# SHOW USERNAME
# ============================================================

async def show_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    context.user_data["show_username"] = (
        query.data == "name_show"
    )

    return await confirm_race(update, context)


# ============================================================
# CONFIRM RACE
# ============================================================

async def confirm_race(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
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
        "✅ *Race Created Successfully!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Title: *{md(title)}*\n"
        f"Countdown: *{countdown} seconds*\n"
        f"Winner Display: *{md(winner_show)}*\n"
        f"Public Ranking: *{md(public)}*\n"
        f"Show Usernames: *{md(show_name)}*\n\n"
        "Now send me the *Channel username* "
        "(with @) where you want to publish:\n"
        "Example: @YourChannel"
    )

    if query:
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN
        )

    return CONFIRM


# ============================================================
# RECEIVE CHANNEL
# ============================================================

async def receive_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    channel = update.message.text.strip()

    if not channel.startswith("@"):
        await update.message.reply_text(
            "Please send channel username starting with @"
        )
        return CONFIRM

    context.user_data["channel"] = channel

    race_id = f"FC-{int(datetime.now().timestamp())}"

    races[race_id] = {
        "title": context.user_data["title"],
        "countdown": context.user_data["countdown"],
        "winner_show": context.user_data["winner_show"],
        "public_rank": context.user_data["public_rank"],
        "show_username": context.user_data.get(
            "show_username",
            False
        ),
        "channel": channel,
        "status": "ready",
        "message_id": None,
        "clicks": {},
    }

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Publish to Channel",
                callback_data=f"publish_{race_id}"
            )
        ]
    ]

    await update.message.reply_text(
        f"Channel set: *{md(channel)}*\n\n"
        f"Race ID: `{md(race_id)}`\n\n"
        "Tap the button below to publish it now.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )

    return ConversationHandler.END


# ============================================================
# PUBLISH RACE
# ============================================================

async def publish_race(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if not is_owner(query.from_user.id):
        return

    race_id = query.data.replace("publish_", "")

    race = races.get(race_id)

    if not race:
        await query.edit_message_text(
            "Race not found."
        )
        return

    channel = race["channel"]
    title = race["title"]
    countdown = race["countdown"]

    text = (
        "🏆 *CUSTOM RACE* 🏆\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Title: *{md(title)}*\n\n"
        f"⏳ Countdown: `{countdown:02d}`\n\n"
        "Get ready... Button will appear when countdown ends.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Owned by @{md(OWNER_USERNAME)}"
    )

    try:
        msg = await context.bot.send_message(
            chat_id=channel,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )

    except Exception as e:
        logger.exception(
            "Failed to publish race %s: %s",
            race_id,
            e
        )

        await query.edit_message_text(
            "❌ Failed to publish the race.\n\n"
            "Please check the channel username and bot permissions."
        )

        return

    race["message_id"] = msg.message_id
    race["status"] = "countdown"

    context.job_queue.run_repeating(
        countdown_job,
        interval=1,
        first=1,
        data={
            "race_id": race_id,
            "chat_id": channel,
            "message_id": msg.message_id,
            "remaining": countdown,
        },
        name=race_id,
    )

    await query.edit_message_text(
        f"✅ Published to {md(channel)}!\n"
        "Countdown started.",
        parse_mode=ParseMode.MARKDOWN
    )


# ============================================================
# COUNTDOWN JOB
# ============================================================

async def countdown_job(
    context: ContextTypes.DEFAULT_TYPE
):
    job = context.job
    data = job.data

    remaining = data["remaining"] - 1
    data["remaining"] = remaining

    race_id = data["race_id"]
    race = races.get(race_id)

    if not race:
        job.schedule_removal()
        return

    title = race["title"]
    channel = data["chat_id"]
    message_id = data["message_id"]

    # --------------------------------------------------------
    # COUNTDOWN
    # --------------------------------------------------------

    if remaining > 0:

        text = (
            "🏆 *CUSTOM RACE* 🏆\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Title: *{md(title)}*\n\n"
            f"⏳ Countdown: `{remaining:02d}`\n\n"
            "Get ready... Button will appear when countdown ends.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Owned by @{md(OWNER_USERNAME)}"
        )

        try:
            await context.bot.edit_message_text(
                chat_id=channel,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )

        except Exception as e:
            logger.exception(
                "Countdown edit error for %s: %s",
                race_id,
                e
            )

    # --------------------------------------------------------
    # RACE LIVE
    # --------------------------------------------------------

    else:

        text = (
            "🏆 *CUSTOM RACE* 🏆\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Title: *{md(title)}*\n\n"
            "⚡ *THE RACE IS LIVE!*\n"
            "First click wins. Time recorded to the millisecond.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Owned by @{md(OWNER_USERNAME)}"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "⚡ CLICK NOW",
                    callback_data=f"click_{race_id}"
                )
            ]
        ]

        try:
            await context.bot.edit_message_text(
                chat_id=channel,
                message_id=message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN,
            )

        except Exception as e:
            logger.exception(
                "Failed to activate race %s: %s",
                race_id,
                e
            )

        race["status"] = "live"
        job.schedule_removal()


# ============================================================
# HANDLE CLICK
# ============================================================

async def handle_click(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    race_id = query.data.replace("click_", "")

    race = races.get(race_id)

    if not race or race["status"] != "live":
        await query.answer(
            "This race is not active.",
            show_alert=True
        )
        return

    user = query.from_user
    user_id = user.id

    # Keep username separately.
    # If no username exists, use full name.
    username = user.username
    display_name = username or user.full_name

    if user_id in race["clicks"]:
        await query.answer(
            "You already clicked!",
            show_alert=True
        )
        return

    click_time = get_time_str()

    race["clicks"][user_id] = {
        "username": username,
        "display_name": display_name,
        "time": click_time,
        "timestamp": datetime.now().timestamp(),
    }

    sorted_clicks = sorted(
        race["clicks"].items(),
        key=lambda x: x[1]["timestamp"]
    )

    result_text = (
        "✨ *RACE RESULTS* ✨\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Title: *{md(race['title'])}*\n\n"
    )

    # --------------------------------------------------------
    # TOP 3
    # --------------------------------------------------------

    for i, (uid, data) in enumerate(
        sorted_clicks[:3],
        1
    ):

        medal = ["🥇", "🥈", "🥉"][i - 1]

        if i == 1:
            if race["winner_show"]:
                name = data["display_name"]
            else:
                name = "Anonymous Winner"

        else:
            if race["show_username"]:
                name = data["display_name"]
            else:
                name = f"Player #{i}"

        place = get_place_text(i)

        result_text += (
            f"{medal} *{md(place)}*\n"
            f"{md(name)}\n"
            f"Time: `{md(data['time'])}`\n\n"
        )

    # --------------------------------------------------------
    # PUBLIC RANKING
    # --------------------------------------------------------

    if race["public_rank"]:

        result_text += (
            "━━━━━━━━━━━━━━━━━━━━\n"
        )

        if race["winner_show"]:

            winner_data = sorted_clicks[0][1]

            winner_username = winner_data.get(
                "username"
            )

            winner_display_name = winner_data.get(
                "display_name",
                "Winner"
            )

            if winner_username:
                result_text += (
                    f"👑 Congratulations to "
                    f"@{md(winner_username)}!\n"
                )
            else:
                result_text += (
                    f"👑 Congratulations to "
                    f"*{md(winner_display_name)}*!\n"
                )

            result_text += (
                "You claimed the first click "
                "with legendary speed.\n"
            )

        else:

            result_text += (
                "👑 Congratulations to the "
                "Anonymous Winner!\n"
            )

    # --------------------------------------------------------
    # PRIVATE RANKING
    # --------------------------------------------------------

    else:

        result_text += (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "The full ranking is private.\n"
            "Participants can check their own rank "
            "using /myrank\n"
        )

    result_text += (
        f"\nOwned by @{md(OWNER_USERNAME)}"
    )

    # --------------------------------------------------------
    # UPDATE CHANNEL MESSAGE
    # --------------------------------------------------------

    try:

        await context.bot.edit_message_text(
            chat_id=race["channel"],
            message_id=race["message_id"],
            text=result_text,
            parse_mode=ParseMode.MARKDOWN,
        )

    except Exception as e:

        logger.exception(
            "Failed to update race result %s: %s",
            race_id,
            e
        )

    # --------------------------------------------------------
    # PRIVATE MESSAGE TO CLICKER
    # --------------------------------------------------------

    try:

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ Your click has been recorded!\n"
                f"Time: `{md(click_time)}`\n"
                "Use /myrank to check your position later."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    except Exception as e:

        # This can happen when the user has never started
        # a private chat with the bot.
        logger.warning(
            "Could not send private result to user %s: %s",
            user_id,
            e
        )


# ============================================================
# /myrank
# ============================================================

async def myrank(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id
    found = False

    for race_id, race in races.items():

        if user_id in race["clicks"]:

            sorted_clicks = sorted(
                race["clicks"].items(),
                key=lambda x: x[1]["timestamp"]
            )
