import os
import sqlite3
import calendar
import asyncio
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

TELEGRAM_BOT_TOKEN = "8842009526:AAFrxI9BErcQ6uM8RBeLrmTJ0iT5OfRc6QY"

# Conversation States (তারিখের পর কমেন্ট নেওয়ার স্টেট)
SELECTING_TAG, ENTERING_TITLE, SELECTING_DATE, ENTERING_COMMENT = range(4)

# ----------------- ডেটাবেস সেটআপ ও অটো-মাইগ্রেশন -----------------
def init_db():
    conn = sqlite3.connect("notices.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            thread_id INTEGER,
            tag TEXT,
            title TEXT,
            comment TEXT,
            due_date TEXT,
            weekday TEXT,
            reminded INTEGER DEFAULT 0
        )
    """)
    # পুরনো ডেটাবেসের কলাম আপডেট
    cursor.execute("PRAGMA table_info(notices)")
    columns = [col[1] for col in cursor.fetchall()]
    if "weekday" not in columns:
        cursor.execute("ALTER TABLE notices ADD COLUMN weekday TEXT")
    if "comment" not in columns:
        cursor.execute("ALTER TABLE notices ADD COLUMN comment TEXT")
        
    conn.commit()
    conn.close()

init_db()

# মেসেজ ডিলিট করার হেল্পার ফাংশন
async def clean_temp_messages(chat_id: int, msg_ids: list, bot):
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass

# ----------------- ইনলাইন ক্যালেন্ডার জেনারেটর -----------------
def create_calendar(year=None, month=None):
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    keyboard = []
    month_name = calendar.month_name[month]
    
    keyboard.append([
        InlineKeyboardButton("◀️", callback_data=f"CAL_PREV_{year}_{month}"),
        InlineKeyboardButton(f"{month_name} {year}", callback_data="CAL_IGNORE"),
        InlineKeyboardButton("▶️", callback_data=f"CAL_NEXT_{year}_{month}")
    ])

    week_days = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    keyboard.append([InlineKeyboardButton(day, callback_data="CAL_IGNORE") for day in week_days])

    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="CAL_IGNORE"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"CAL_DAY_{year}_{month}_{day}"))
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)

# ----------------- /start হ্যান্ডলার (৫ সেকেন্ডে অটো ডিলিট) -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id
    thread_id = update.message.message_thread_id if update.message else None
    
    bot_msg = await update.message.reply_text(
        "👋 হ্যালো! আমি আপনার স্টাডি নোটিস বট।\nনতুন নোটিস যোগ করতে `/add` কমান্ড দিন।", 
        parse_mode="Markdown",
        message_thread_id=thread_id
    )
    
    # ৫ সেকেন্ড পর ইউজার ও বটের মেসেজ নিজে থেকেই ডিলিট হয়ে যাবে
    await asyncio.sleep(5)
    await clean_temp_messages(chat_id, [user_msg_id, bot_msg.message_id], context.bot)

# ----------------- /add কনভারসেশন -----------------
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp_msgs"] = [update.message.message_id]
    
    keyboard = [
        [
            InlineKeyboardButton("📝 Exam", callback_data="exam"),
            InlineKeyboardButton("📚 Assignment", callback_data="assignment"),
        ],
        [
            InlineKeyboardButton("📊 Presentation", callback_data="presentation"),
            InlineKeyboardButton("📌 Other", callback_data="other"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    thread_id = update.message.message_thread_id if update.message else None
    bot_msg = await update.message.reply_text(
        "কোন ধরনের নোটিস যোগ করতে চান? অপশন বেছে নিন:",
        reply_markup=reply_markup,
        message_thread_id=thread_id
    )
    context.user_data["temp_msgs"].append(bot_msg.message_id)
    return SELECTING_TAG

async def tag_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_tag = query.data
    context.user_data["tag"] = selected_tag
    
    await query.edit_message_text(
        text=f"ট্যাগ: **{selected_tag.capitalize()}**\n\nএখন বিষয় বা টাস্কের নাম লিখে পাঠান (যেমন: CSE 112):",
        parse_mode="Markdown"
    )
    return ENTERING_TITLE

async def title_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp_msgs"].append(update.message.message_id)
    context.user_data["title"] = update.message.text
    
    cal_markup = create_calendar()
    thread_id = update.message.message_thread_id if update.message else None
    
    bot_msg = await update.message.reply_text(
        "📅 **ক্যালেন্ডার থেকে ডেডলাইনের তারিখ সিলেক্ট করুন:**",
        reply_markup=cal_markup,
        parse_mode="Markdown",
        message_thread_id=thread_id
    )
    context.user_data["temp_msgs"].append(bot_msg.message_id)
    return SELECTING_DATE

async def calendar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "CAL_IGNORE":
        await query.answer()
        return SELECTING_DATE

    if data.startswith("CAL_PREV_"):
        await query.answer()
        _, _, year, month = data.split("_")
        year, month = int(year), int(month)
        month -= 1
        if month < 1:
            month = 12
            year -= 1
        await query.edit_message_reply_markup(reply_markup=create_calendar(year, month))
        return SELECTING_DATE

    if data.startswith("CAL_NEXT_"):
        await query.answer()
        _, _, year, month = data.split("_")
        year, month = int(year), int(month)
        month += 1
        if month > 12:
            month = 1
            year += 1
        await query.edit_message_reply_markup(reply_markup=create_calendar(year, month))
        return SELECTING_DATE

    if data.startswith("CAL_DAY_"):
        _, _, year, month, day = data.split("_")
        selected_date = date(int(year), int(month), int(day))

        if selected_date < date.today():
            await query.answer("❌ অতীতের কোনো তারিখ সিলেক্ট করা যাবে না!", show_alert=True)
            return SELECTING_DATE

        await query.answer()
        
        # তারিখের তথ্য সংরক্ষণ
        context.user_data["date_str"] = selected_date.strftime("%Y-%m-%d")
        context.user_data["weekday_str"] = selected_date.strftime("%A")
        context.user_data["days_left"] = (selected_date - date.today()).days

        # তারিখ সিলেকশনের পর কমেন্ট চাওয়া
        await query.edit_message_text(
            f"📅 তারিখ: **{context.user_data['date_str']} ({context.user_data['weekday_str']})**\n\n"
            f"📝 কোনো নির্দিষ্ট **নোট বা কমেন্ট** যোগ করতে চান?\n*(না থাকলে `/skip` লিখুন)*",
            parse_mode="Markdown"
        )
        return ENTERING_COMMENT

async def comment_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp_msgs"].append(update.message.message_id)
    user_input = update.message.text.strip()
    
    if user_input.lower() == "/skip":
        comment = None
    else:
        comment = user_input

    tag = context.user_data.get("tag", "Other")
    title = context.user_data.get("title", "No Title")
    date_str = context.user_data.get("date_str")
    weekday_str = context.user_data.get("weekday_str")
    days_left = context.user_data.get("days_left", 0)
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id if update.message else None

    # ডেটাবেসে সেভ করা
    conn = sqlite3.connect("notices.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO notices (chat_id, thread_id, tag, title, comment, due_date, weekday) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, thread_id, tag, title, comment, date_str, weekday_str)
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # ইন্টারমিডিয়েট সব আগের মেসেজ মুছে ফেলা
    temp_msgs = context.user_data.get("temp_msgs", [])
    await clean_temp_messages(chat_id, temp_msgs, context.bot)

    # ফাইনাল নোটিস ফরম্যাট
    day_count_text = "আজকেই ডেডলাইন!" if days_left == 0 else f"{days_left} দিন বাকি"
    
    final_msg = (
        f"📌 **{tag.capitalize()} Notice** `[ID: {task_id}]`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 **বিষয়:** {title}\n"
        f"📅 **তারিখ:** {date_str} ({weekday_str})\n"
        f"⏳ **সময়:** {day_count_text}\n"
    )
    if comment:
        final_msg += f"💬 **নোট:** \"_{comment}_\"\n"
        
    final_msg += f"━━━━━━━━━━━━━━━━━━━━\n🔔 *ডেডলাইনের ১ দিন আগে স্বয়ংক্রিয় রিমাইন্ডার পাবেন।*"

    # ফাইনাল নোটিস পোস্ট করা
    kwargs = {
        "chat_id": chat_id,
        "text": final_msg,
        "parse_mode": "Markdown"
    }
    if thread_id:
        kwargs["message_thread_id"] = thread_id
        
    await context.bot.send_message(**kwargs)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    temp_msgs = context.user_data.get("temp_msgs", [])
    if update.message:
        temp_msgs.append(update.message.message_id)
    await clean_temp_messages(chat_id, temp_msgs, context.bot)
    
    thread_id = update.message.message_thread_id if update.message else None
    await update.message.reply_text("বাতিল করা হয়েছে।", message_thread_id=thread_id)
    return ConversationHandler.END

# ----------------- ফিল্টার ও তালিকা কমান্ড -----------------
def fetch_notices(chat_id: int, tag: str = None):
    conn = sqlite3.connect("notices.db")
    cursor = conn.cursor()
    today_str = date.today().strftime("%Y-%m-%d")
    
    if tag:
        cursor.execute(
            "SELECT id, tag, title, comment, due_date, weekday FROM notices WHERE chat_id = ? AND tag = ? AND due_date >= ? ORDER BY due_date ASC",
            (chat_id, tag, today_str)
        )
    else:
        cursor.execute(
            "SELECT id, tag, title, comment, due_date, weekday FROM notices WHERE chat_id = ? AND due_date >= ? ORDER BY due_date ASC",
            (chat_id, today_str)
        )
    rows = cursor.fetchall()
    conn.close()
    return rows

async def show_list_by_tag(update: Update, tag: str, tag_name: str):
    chat_id = update.effective_chat.id
    items = fetch_notices(chat_id, tag)
    thread_id = update.message.message_thread_id if update.message else None
    
    if not items:
        await update.message.reply_text(f"বর্তমানে কোনো পেন্ডিং **{tag_name}** নেই!", parse_mode="Markdown", message_thread_id=thread_id)
        return

    msg = f"📋 **পেন্ডিং {tag_name} তালিকা:**\n\n"
    for item_id, item_tag, title, comment, due_date, weekday in items:
        due_dt = datetime.strptime(due_date, "%Y-%m-%d").date()
        days_left = (due_dt - date.today()).days
        day_text = "আজকেই ডেডলাইন!" if days_left == 0 else f"{days_left} দিন বাকি"
        
        msg += f"• `[ID: {item_id}]` **{title}**\n"
        msg += f"  📅 তারিখ: {due_date} ({weekday})\n"
        msg += f"  ⏳ বাকি: {day_text}\n"
        if comment:
            msg += f"  💬 নোট: \"_{comment}_\"\n"
        msg += "\n"

    await update.message.reply_text(msg, parse_mode="Markdown", message_thread_id=thread_id)

async def list_exam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_list_by_tag(update, "exam", "Exam")

async def list_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_list_by_tag(update, "assignment", "Assignment")

async def list_presentation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_list_by_tag(update, "presentation", "Presentation")

async def list_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_list_by_tag(update, None, "সকল নোটিস")

async def done_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id if update.message else None
    if not context.args:
        await update.message.reply_text("ব্যবহারের নিয়ম: `/done <ID>` (যেমন: `/done 1`)", parse_mode="Markdown", message_thread_id=thread_id)
        return
    
    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("সঠিক আইডি নম্বর দিন!", message_thread_id=thread_id)
        return

    conn = sqlite3.connect("notices.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notices WHERE id = ? AND chat_id = ?", (task_id, update.effective_chat.id))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted:
        await update.message.reply_text(f"✅ ID `{task_id}` সফলভাবে তালিকা থেকে মুছে ফেলা হয়েছে!", parse_mode="Markdown", message_thread_id=thread_id)
    else:
        await update.message.reply_text("এই আইডির কোনো নোটিস পাওয়া যায়নি।", message_thread_id=thread_id)

# ----------------- রিমাইন্ডার শিডিউলার -----------------
async def daily_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    conn = sqlite3.connect("notices.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, chat_id, thread_id, tag, title, comment, weekday FROM notices WHERE due_date = ? AND reminded = 0",
        (tomorrow,)
    )
    upcoming_tasks = cursor.fetchall()
    
    for task_id, chat_id, thread_id, tag, title, comment, weekday in upcoming_tasks:
        alert_msg = (
            f"🚨 **রিমাইন্ডার অ্যালার্ট (আগামীকাল ডেডলাইন!)** 🚨\n\n"
            f"📌 **ধরন:** {tag.capitalize()}\n"
            f"📖 **বিষয়:** {title}\n"
            f"📅 **তারিখ:** {tomorrow} ({weekday})\n"
        )
        if comment:
            alert_msg += f"💬 **নোট:** \"_{comment}_\"\n"
            
        alert_msg += f"\nসবাই প্রস্তুতি সম্পন্ন করে রাখুন!"
        
        try:
            kwargs = {
                "chat_id": chat_id,
                "text": alert_msg,
                "parse_mode": "Markdown"
            }
            if thread_id:
                kwargs["message_thread_id"] = thread_id
                
            await context.bot.send_message(**kwargs)
            cursor.execute("UPDATE notices SET reminded = 1 WHERE id = ?", (task_id,))
            conn.commit()
        except Exception as e:
            print(f"মেসেজ পাঠাতে সমস্যা: {e}")
            
    conn.close()

# ----------------- মেইন ফাংশন -----------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            SELECTING_TAG: [CallbackQueryHandler(tag_selected)],
            ENTERING_TITLE: [MessageHandler(filters.TEXT & (~filters.COMMAND), title_entered)],
            SELECTING_DATE: [CallbackQueryHandler(calendar_handler)],
            ENTERING_COMMENT: [MessageHandler(filters.TEXT & (~filters.COMMAND) | filters.Regex(r"^/skip$"), comment_entered)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("exam", list_exam))
    app.add_handler(CommandHandler("assignment", list_assignment))
    app.add_handler(CommandHandler("presentation", list_presentation))
    app.add_handler(CommandHandler("all", list_all))
    app.add_handler(CommandHandler("done", done_task))

    if app.job_queue:
        app.job_queue.run_repeating(daily_reminder_job, interval=3600, first=10)

    print("নোটস ও ক্যালেন্ডার বট সফলভাবে চালু হয়েছে...")
    app.run_polling()