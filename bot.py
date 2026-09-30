import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from telegram.constants import ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Owner")

TITLE, COUNTDOWN, WINNER_MODE, PUBLIC_RANK, SHOW_USERNAME, CONFIRM = range(6)
races = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
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
        "✨ *FirstClick Pro - Create New Race*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Please send me the *Race Title*:\n\n"
        "Example: Golden Hand Speed Challenge",
        parse_mode=ParseMode.MARKDOWN
    )
    return TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text(
        f"Title saved: *{context.user_data['title']}*\n\n"
        "Now please send the *Countdown seconds* (number only):\n"
        "Example: 10 / 15 / 30 / 60",
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
    except Exception:
        await update.message.reply_text("Please enter a valid number.")
        return COUNTDOWN
    keyboard = [
        [InlineKeyboardButton("Show Real Username", callback_data="winner_show")],
        [InlineKeyboardButton("Anonymous (Hide Username)", callback_data="winner_hide")]
    ]
    await update.message.reply_text(
        f"Countdown set to: *{seconds} seconds*\n\n"
        "Now choose *Winner Display Mode*:",
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
        f"✅ *Race Created Successfully!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Title: *{title}*\n"
        f"Countdown: *{countdown} seconds*\n"
        f"Winner Display: *{winner_show}*\n"
        f"Public Ranking: *{public}*\n"
        f"Show Usernames: *{show_name}*\n\n"
        f"Now send me the *Channel username* (with @) where you want to publish:\n"
        f"Example: @YourChannel"
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
        f"Channel set: *{channel}*\n\n"
        f"Race ID: `{race_id}`\n\n"
        "Tap the button below to publish it now.",
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
        "🏆 *CUSTOM RACE* 🏆\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Title: *{title}*\n\n"
        f"⏳ Countdown: `{countdown:02d}`\n\n"
        "Get ready... Button will appear when countdown ends.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Owned by @{OWNER_USERNAME}"
    )
    msg = await context.bot.send_message(
        chat_id=channel,
        text=text,
        parse_mode=ParseMode.MARKDOWN
    )
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
            "remaining": countdown
        },
        name=race_id
    )
    await query.edit_message_text(f"✅ Published to {channel}!\nCountdown started.")

async def countdown_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    race_id = data["race_id"]
    remaining = data["remaining"] - 1
    data["remaining"] = remaining
    race = races.get(race_id)
    if not race:
        job.schedule_removal()
        return
    title = race["title"]
    channel = data["chat_id"]
    message_id = data["message_id"]
    if remaining > 0:
        text = (
            "🏆 *CUSTOM RACE* 🏆\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Title: *{title}*\n\n"
            f"⏳ Countdown: `{remaining:02d}`\n\n"
            "Get ready... Button will appear when countdown ends.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Owned by @{OWNER_USERNAME}"
        )
        try:
            await context.bot.edit_message_text(
                chat_id=channel,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(e)
    else:
        text = (
            "🏆 *CUSTOM RACE* 🏆\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Title: *{title}*\n\n"
            "⚡ *THE RACE IS LIVE!*\n"
            "First click wins. Time recorded to the millisecond.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Owned by @{OWNER_USERNAME}"
        )
        keyboard = [[InlineKeyboardButton("⚡ CLICK NOW", callback_data=f"click_{race_id}")]]
        try:
            await context.bot.edit_message_text(
                chat_id=channel,
                message_id=message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(e)
        race["status"] = "live"
        job.schedule_removal()

async def handle_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    race_id = query.data.replace("click_", "")
    race = races.get(race_id)
    if not race or race["status"] != "live":
        await query.answer("This race is not active.", show_alert=True)
        return
    user = query.from_user
    user_id = user.id
    username = user.username or user.full_name
    if user_id in race["clicks"]:
        await query.answer("You already clicked!", show_alert=True)
        return
    click_time = get_time_str()
    race["clicks"][user_id] = {
        "username": username,
        "time": click_time,
        "timestamp": datetime.now().timestamp()
    }
    sorted_clicks = sorted(race["clicks"].items(), key=lambda x: x[1]["timestamp"])
    result_text = (
        "✨ *RACE RESULTS* ✨\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Title: *{race['title']}*\n\n"
    )
    for i, (uid, data) in enumerate(sorted_clicks[:3], 1):
        medal = ["🥇", "🥈", "🥉"][i-1]
        if i == 1:
            name = data["username"] if race["winner_show"] else "Anonymous Winner"
        else:
            name = data["username"] if race["show_username"] else f"Player #{i}"
        result_text += f"{medal} *{i}st Place*\n{name}\nTime: `{data['time']}`\n\n"
    if race["public_rank"]:
        result_text += "━━━━━━━━━━━━━━━━━━━━\n"
        if race["winner_show"]:
            winner_name = sorted_clicks[0][1]["username"]
            result_text += f"👑 Congratulations to @{winner_name}!\nYou claimed the first click with legendary speed.\n"
        else:
            result_text += "👑 Congratulations to the Anonymous Winner!\n"
    else:
        result_text += (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "The full ranking is private.\n"
            "Participants can check their own rank using /myrank\n"
        )
    result_text += f"\nOwned by @{OWNER_USERNAME}"
    try:
        await context.bot.edit_message_text(
            chat_id=race["channel"],
            message_id=race["message_id"],
            text=result_text,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(e)
    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ Your click has been recorded!\nTime: `{click_time}`\nUse /myrank to check your position later.",
        parse_mode=ParseMode.MARKDOWN
    )

async def myrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    found = False
    for race_id, race in races.items():
        if user_id in race["clicks"]:
            sorted_clicks = sorted(race["clicks"].items(), key=lambda x: x[1]["timestamp"])
            for i, (uid, data) in enumerate(sorted_clicks, 1):
                if uid == user_id:
                    await update.message.reply_text(
                        "📊 *Your Personal Rank*\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"Race: *{race['title']}*\n\n"
                        f"Your Position: *{i}th Place*\n"
                        f"Your Time: `{data['time']}`\n\n"
                        "Only you can see this information.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    found = True
                    break
    if not found:
        await update.message.reply_text("You haven't participated in any race yet.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update.effective_user.id):
        await update.message.reply_text(
            "✨ *FirstClick Pro*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Welcome, Owner!\n\n"
            "Commands:\n"
            "/newrace - Create a new race\n"
            "/myrank - Check your rank\n"
            "/cancel - Cancel current operation",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "✨ *FirstClick Pro*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "This is a premium click race bot.\n"
            "Join races in the channel and use /myrank to check your position.\n\n"
            f"Owned by @{OWNER_USERNAME}"
        )

def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN not set")
        return
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newrace", newrace)],
        states={
            TITLE: [
                MessageHandler(filters.TEXT & \~filters.COMMAND, receive_title)
            ],
            COUNTDOWN: [
                MessageHandler(filters.TEXT & \~filters.COMMAND, receive_countdown)
            ],
            WINNER_MODE: [
                CallbackQueryHandler(winner_mode)
            ],
            PUBLIC_RANK: [
                CallbackQueryHandler(public_rank)
            ],
            SHOW_USERNAME: [
                CallbackQueryHandler(show_username)
            ],
            CONFIRM: [
                MessageHandler(filters.TEXT & \~filters.COMMAND, receive_channel)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(publish_race, pattern="^publish_"))
    app.add_handler(CallbackQueryHandler(handle_click, pattern="^click_"))
    app.add_handler(CommandHandler("myrank", myrank))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
