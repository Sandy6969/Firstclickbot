import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode

# ====================== 这里必须修改 ======================
BOT_TOKEN = "8944063576:AAH_M3kcKnYFBdEO6L4hHH5pZNvU1hJK1Qg"
OWNER_ID = 7810866246          # 这里改成你自己的数字ID
OWNER_USERNAME = "_chen86"  # 不带@
# ========================================================

TITLE, COUNTDOWN, WINNER_MODE, PUBLIC_RANK, SHOW_USERNAME, CONFIRM = range(6)
races = {}

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def get_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

async def newrace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Only the owner can create races.")
        return ConversationHandler.END
    await update.message.reply_text(
        "✨ *FirstClick Pro - Create New Race*\n━━━━━━━━━━━━━━━━━━━━\nPlease send me the *Race Title*:\n\nExample: Golden Hand Speed Challenge",
        parse_mode=ParseMode.MARKDOWN
    )
    return TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text(
        f"Title saved: *{context.user_data['title']}*\n\nNow please send the *Countdown seconds* (number only):\nExample: 10 / 15 / 30 / 60",
        parse_mode=ParseMode.MARKDOWN
    )
    return COUNTDOWN

async def receive_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        seconds = int(update.message.text.strip())
        if seconds < 3 or seconds > 300:
            await update.message.reply_text("Please enter a number between 3 and 300.")
            return COUNTDOWN
        context.user_data["countdown"] = seconds
    except:
        await update.message.reply_text("Please enter a valid number.")
        return COUNTDOWN
    keyboard = [
        [InlineKeyboardButton("Show Real Username", callback_data="winner_show")],
        [InlineKeyboardButton("Anonymous (Hide Username)", callback_data="winner_hide")]
    ]
    await update.message.reply_text(
        f"Countdown set to: *{seconds} seconds*\n\nNow choose *Winner Display Mode*:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    return WINNER_MODE

async def winner_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["winner_show"] = (query.data == "winner_show")
    keyboard = [
        [InlineKeyboardButton("Yes - Show Full Ranking", callback_data="public_yes")],
        [InlineKeyboardButton("No - Only Private Rank", callback_data="public_no")]
    ]
    await query.edit_message_text(
        "Do you want to show the *Full Public Ranking* after the race?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    return PUBLIC_RANK

async def public_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["public_rank"] = (query.data == "public_yes")
    if context.user_data["public_rank"]:
        keyboard = [
            [InlineKeyboardButton("Show Usernames", callback_data="name_show")],
            [InlineKeyboardButton("Anonymous Mode", callback_data="name_hide")]
        ]
        await query.edit_message_text(
            "In the public ranking, show real *Usernames*?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return SHOW_USERNAME
    else:
        context.user_data["show_username"] = False
        return await confirm_race(update, context)

async def show_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["show_username"] = (query.data == "name_show")
    return await confirm_race(update, context)

async def confirm_race(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    title = context.user_data["title"]
    countdown = context.user_data["countdown"]
    winner_show = "Show Real Username" if context.user_data["winner_show"] else "Anonymous"
    public = "Yes" if context.user_data["public_rank"] else "No"
    show_name = "Show Usernames" if context.user_data.get("show_username") else "Anonymous"
    text = (
        f"✅ *Race Created Successfully!*\n━━━━━━━━━━━━━━━━━━━━\n"
        f"Title: *{title}*\nCountdown: *{countdown} seconds*\n"
        f"Winner Display: *{winner_show}*\nPublic Ranking: *{public}*\n"
        f"Show Usernames: *{show_name}*\n\n"
        f"Now send me the *Channel username* (with @) where you want to publish:\nExample: @YourChannel"
    )
    if query:
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    return CONFIRM

async def receive_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel = update.message.text.strip()
    if not channel.startswith("@"):
        await update.message.reply_text("Please send channel username starting with @")
        return CONFIRM
    context.user_data["channel"] = channel
    race_id = f"FC-{int(datetime.now().timestamp())}"
    races[race_id] = {
        "title": context.user_data["title"],
        "countdown": context.user_data["countdown"],
        "winner_show": context.user_data["winner_show"],
        "public_rank": context.user_data["public_rank"],
        "show_username": context.user_data.get("show_username", False),
        "channel": channel,
        "status": "ready",
        "message_id": None,
        "clicks": {}
    }
    keyboard = [[InlineKeyboardButton("📢 Publish to Channel", callback_data=f"publish_{race_id}")]]
    await update.message.reply_text(
        f"Channel set: *{channel}*\n\nRace ID: `{race_id}`\n\nTap the button below to publish it now.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def publish_race(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    race_id = query.data.replace("publish_", "")
    race = races.get(race_id)
    if not race:
        await query.edit_message_text("Race not found.")
        return
    channel = race["channel"]
    title = race["title"]
    countdown = race["countdown"]
    text = (
        f"🏆 *CUSTOM RACE
