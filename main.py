import os
import json
import secrets
import threading
import asyncio
import time
from threading import Event
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from collections import defaultdict
from flask import Flask
from telegram.constants import ParseMode
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes
)

# Initialize Flask
keep_running = Event()
server = Flask(__name__)

@server.route("/")
def home():
    return "🟢 Bot is ONLINE", 200

@server.route("/health")
def health():
    return "OK", 200

# Initialize message limiter
class MessageLimiter:
    def __init__(self):
        self.user_messages = defaultdict(list)

    def check_limit(self, user_id, max_messages=3, period_seconds=60):
        now = datetime.now()
        self.user_messages[user_id] = [t for t in self.user_messages[user_id] if (now - t).seconds < period_seconds]
        if len(self.user_messages[user_id]) < max_messages:
            self.user_messages[user_id].append(now)
            return True
        return False

message_limiter = MessageLimiter()

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = "@ShahriarSakib"
ADMIN_USERNAME_2 = "@mohammadtajid03"
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "5512534898"))
ADMIN_CHAT_ID_2 = int(os.getenv("ADMIN_CHAT_ID_2", "5319025828")) # For old members

# Google Sheets Configuration
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1r_zR236RAp-Pf1GduE--M89BM-I8wYlqOqMWj6ldiRI")
SHEET_RANGES = {
    "new_member": ["Sheet1!A:Q", "Sheet2!A:D"],
    "old_member": ["Sheet3!A:D"]
}
FORM_URL = os.getenv("FORM_URL", "https://forms.gle/YOUR_GOOGLE_FORM_LINK")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# --- Google Sheets Credentials ---
def get_google_credentials():
    service_account_info = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if service_account_info:
        try:
            service_account_dict = json.loads(service_account_info)
            return service_account.Credentials.from_service_account_info(service_account_dict, scopes=SCOPES)
        except json.JSONDecodeError:
            print("Error: Invalid JSON in GOOGLE_SERVICE_ACCOUNT_JSON")
            return None
    elif os.path.exists("service_account.json"):
        return service_account.Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
    else:
        print("Error: No Google service account credentials found")
        return None

creds = get_google_credentials()

# --- User Data Storage ---
user_states = {}
user_messages = {}

# --- Keyboards / Menus ---
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("📌 Rules", callback_data="rules")],
        [InlineKeyboardButton("📝 Admission Form", callback_data="form")],
        [InlineKeyboardButton("🆔 KYC Check", callback_data="kyc_check_start")],
        [InlineKeyboardButton("💳 Payment Info", callback_data="payment_info_start")],
        [InlineKeyboardButton("📞 Contact Us", callback_data="contact_admin")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_member_type_menu(action):
    keyboard = [
        [InlineKeyboardButton("New Member", callback_data=f"{action}_new")],
        [InlineKeyboardButton("Old Member", callback_data=f"{action}_old")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Core Bot Functions ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = f"Hello {update.effective_user.first_name}!\n\nWelcome to *Odvut Info Bot*. Please choose an option below:"
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())

async def show_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rules_text = """
📜 *VERIFICATION REQUIREMENTS*

✅ *MUST HAVE*
1. A public Telegram username (@yourname)
2. Clear profile photo (not default)
3. Facebook link in bio (must be 2+ years old)
4. Matching name & photo across all platforms

🚫 *PROHIBITED*
1. No blank/private profiles
2. No recently created accounts
3. No mismatched information
4. No VPN/proxy connections

⚠️ *NOTE*
- Fake profiles will be banned permanently
- All info must match your government ID
"""
    await query.edit_message_text(rules_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Admission Form", callback_data="form")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back")]
    ]))

async def show_form(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    form_text = f"📝 *Admission Form*\n\nPlease fill out the form carefully with accurate information.\nAll fields are required for verification.\n\n[Click here to access the form]({FORM_URL})"
    await query.edit_message_text(form_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I\\'ve Submitted", callback_data="back")],
        [InlineKeyboardButton("🔙 Back", callback_data="back")]
    ]))

async def kyc_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please select your member type:", reply_markup=get_member_type_menu("kyc_check"))

async def payment_info_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please select your member type:", reply_markup=get_member_type_menu("payment_info"))

def check_kyc_status(username, member_type):
    if not creds:
        return {"verified": None, "reason": "Google Sheets not configured"}
    
    sheet_ranges_to_check = SHEET_RANGES.get(member_type, [])
    if not sheet_ranges_to_check:
        return {"verified": False, "reason": "Invalid member type"}

    try:
        service = build("sheets", "v4", credentials=creds)
        for sheet_range in sheet_ranges_to_check:
            sheet = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=sheet_range).execute()
            values = sheet.get("values", [])
            for row in values:
                if len(row) > 0 and username.lower().lstrip("@") == row[0].lower().lstrip("@"):
                    status = row[1].strip().upper() if len(row) > 1 and row[1].strip() else ""
                    reason = row[2] if len(row) > 2 and row[2].strip() else "No reason provided"
                    if not status:
                        return {"verified": None, "reason": "Under review"}
                    return {"verified": status == "VERIFIED", "reason": reason}
        return {"verified": False, "reason": "Not found in database"}
    except Exception as e:
        print(f"Sheet error: {e}")
        return {"verified": None, "reason": "Error accessing database"}

async def kyc_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    member_type = "new_member" if "_new" in query.data else "old_member"
    user = query.from_user
    username = user.username or f"user_{user.id}"
    status = check_kyc_status(username, member_type)
    
    if status["verified"] is None:
        new_message = "⏳ *KYC Status*\n\nYour verification is under review.\nPlease check back later."
        new_buttons = [
            [InlineKeyboardButton("🔄 Refresh", callback_data=query.data)],
            [InlineKeyboardButton("🔙 Back", callback_data="back")]
        ]
    elif not status["verified"]:
        new_message = f"🔍 *KYC Status for* @{username}\n\n• Status: Not Verified\n• Reason: {status['reason']}\n\nPlease complete verification again"
        new_buttons = [
            [InlineKeyboardButton("📝 Submit Verification", url=FORM_URL)],
            [InlineKeyboardButton("🔄 Refresh Status", callback_data=query.data)],
            [InlineKeyboardButton("🔙 Back", callback_data="back")]
        ]
    else:
        new_message = f"✅ *KYC Verified*\n\nCongratulations @{username}!\nYour account has been successfully verified."
        new_buttons = [
            [InlineKeyboardButton("💳 Proceed to Payment", callback_data=f"payment_{member_type}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back")]
        ]

    try:
        await query.edit_message_text(
            text=new_message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(new_buttons)
        )
    except Exception as e:
        print(f"KYC check error: {e}")
        await query.answer("⚠️ Could not update status")

async def show_payment_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Determine member type from callback data
    member_type = "new_member" if "_new" in query.data else "old_member"
    
    # Select appropriate admin contact
    admin_contact = ADMIN_USERNAME if member_type == "new_member" else ADMIN_USERNAME_2
    
    payment_text = (
        "💳 *Payment Instructions*\n\n"
        "1. Complete your KYC verification first\n"
        "2. Payment methods available:\n"
        "   - Cryptocurrency (USDT)\n"
        "   - Binance\n"
        "   - Mexc\n"
        "3. Contact admin for payment details\n\n"
        f"Admin: {admin_contact}"
    )
    
    await query.edit_message_text(
        text=payment_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="back")]
        ])
    )

async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        # Extract full member type (new_member or old_member)
        member_type = query.data.split('_', 1)[1]  # Splits on first underscore only
        admin_chat_id = ADMIN_CHAT_ID if member_type == "new_member" else ADMIN_CHAT_ID_2
        
        user = query.from_user
        secret_code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
        
        # Store code in user data
        context.user_data['payment_code'] = secret_code
        
        # User message
        await query.edit_message_text(
             text=f"✅ *Payment Verification*\n\n🔐 Your code: `{secret_code}`\n\nSend this to {ADMIN_USERNAME if member_type == 'new_member' else ADMIN_USERNAME_2}",
             parse_mode=ParseMode.MARKDOWN_V2,
             reply_markup=InlineKeyboardMarkup([
                 [InlineKeyboardButton("🔙 Back", callback_data=f"kyc_check_{member_type.split('_')[0]}")],
                 [InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{(ADMIN_USERNAME if member_type == 'new_member' else ADMIN_USERNAME_2)[1:]}")]
             ])
        )
        
        # Admin message
        await context.bot.send_message(
            chat_id=admin_chat_id,
            text=f"🆕 Payment Request from @{user.username}\n🔢 Code: `{secret_code}`\n🆔 User ID: {user.id}\n👤 Type: {member_type.replace('_', ' ')}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
    except Exception as e:
        print(f"Payment error: {e}")
        await query.edit_message_text(
            "⚠️ Payment processing failed. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data=query.data)]
            ])
        )

# --- Admin & Helper Functions ---
async def contact_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_states[query.from_user.id] = "AWAITING_MESSAGE"
    await query.edit_message_text("✉️ Please type your message for admin (max 500 characters):", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_message")]
    ]))

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_states or user_states[user_id] != "AWAITING_MESSAGE":
        return
    if not message_limiter.check_limit(user_id):
        await update.message.reply_text("⏳ Please wait before sending another message")
        return
    message = update.message.text[:500]
    user_messages[user_id] = message
    admin_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Reply", callback_data=f"reply_{user_id}")]])
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📨 New message from @{update.message.from_user.username} (ID: {user_id}):\n\n{message}", reply_markup=admin_keyboard)
        await update.message.reply_text("✅ Your message has been sent to admin!", reply_markup=get_main_menu())
    except Exception as e:
        await update.message.reply_text("⚠️ Failed to send message. Please try later.")
    del user_states[user_id]

async def admin_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in [ADMIN_CHAT_ID, ADMIN_CHAT_ID_2]:
        await query.edit_message_text("🚫 Admin only feature")
        return
    try:
        user_id = int(query.data.split("_")[1])
        context.user_data["replying_to"] = user_id
        original_text = query.message.text
        await query.edit_message_text(f"{original_text}\n\n✍️ You are now replying to this user.\nType your message below:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reply")]
        ]))
    except Exception as e:
        await query.edit_message_text(f"Error: {str(e)}")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in [ADMIN_CHAT_ID, ADMIN_CHAT_ID_2]:
        return
    if "replying_to" not in context.user_data:
        await update.message.reply_text("⚠️ No user selected to reply to. Use the reply button from a user\\'s message.")
        return
    user_id = context.user_data["replying_to"]
    reply_text = f"💬 Admin Reply:\n\n{update.message.text}"
    try:
        await context.bot.send_message(chat_id=user_id, text=reply_text, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(f"✅ Reply sent to user {user_id}", reply_to_message_id=update.message.message_id)
        log_message = f"🔷 Admin Reply Log\n\n👤 User ID: {user_id}\n🕒 Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n📝 Message: {update.message.text}"
        await context.bot.send_message(update.message.chat_id, log_message)
    except Exception as e:
        error_msg = f"⚠️ Failed to send reply to {user_id}: {str(e)}"
        print(error_msg)
        await update.message.reply_text(error_msg)

async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not message_limiter.check_limit(user_id):
        await update.message.reply_text("⏳ Please wait 1 minute before sending another message", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back")]]))
        return
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📩 From @{update.message.from_user.username} (ID: {user_id}):\n\n{update.message.text}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 Reply", callback_data=f"reply_{user_id}")]]))
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID_2, text=f"📩 From @{update.message.from_user.username} (ID: {user_id}):\n\n{update.message.text}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 Reply", callback_data=f"reply_{user_id}")]]))
        await update.message.reply_text("✅ Message sent to admin!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back")]]))
    except Exception as e:
        print(f"Forwarding error: {e}")
        await update.message.reply_text("⚠️ Failed to send message. Please try again.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back")], [InlineKeyboardButton("🔄 Try Again", callback_data="contact_admin")]]))

async def cancel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await query.edit_message_text("❌ Message cancelled", reply_markup=get_main_menu())

async def cancel_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if "replying_to" in context.user_data:
        user_id = context.user_data.pop("replying_to")
        await query.edit_message_text(f"❌ Reply to user {user_id} cancelled", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 Reply Anyway", callback_data=f"reply_{user_id}")]]))
    else:
        await query.edit_message_text("No active reply to cancel")

async def return_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text="👋 Welcome back! Select an option:", reply_markup=get_main_menu())
    except Exception as e:
        print(f"⚠️ Menu edit failed, sending new message: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="👋 Welcome back! Select an option:", reply_markup=get_main_menu())

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    help_text = "❓ *Help Center*\n\nFor any assistance, please contact our admin team.\n\n" + f"Admin: {ADMIN_USERNAME}\n" + "We\\'re available to help you."
    await query.edit_message_text(help_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Contact Admin", callback_data="contact_admin")],
        [InlineKeyboardButton("🔙 Back", callback_data="back")]
    ]))

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Error: {context.error}")
    try:
        if update.callback_query:
            await update.callback_query.answer("⚠️ Error occurred")
            await return_to_menu(update, context)
    except: pass

# --- Application Setup ---
async def run_telegram_bot():
    print("🤖 Starting Telegram bot...")
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Command & Callback Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(show_rules, pattern="^rules$"))
    app.add_handler(CallbackQueryHandler(show_form, pattern="^form$"))
    app.add_handler(CallbackQueryHandler(kyc_check_start, pattern="^kyc_check_start$"))
    app.add_handler(CallbackQueryHandler(payment_info_start, pattern="^payment_info_start$"))
    app.add_handler(CallbackQueryHandler(kyc_check, pattern="^kyc_check_(new|old)$"))
    app.add_handler(CallbackQueryHandler(show_payment_info, pattern="^payment_info_(new|old)$"))
    app.add_handler(CallbackQueryHandler(handle_payment, pattern="^payment_(new|old)$"))
    app.add_handler(CallbackQueryHandler(handle_payment, pattern="^payment_(new_member|old_member)$"))
    app.add_handler(CallbackQueryHandler(contact_admin, pattern="^contact_admin$"))
    app.add_handler(CallbackQueryHandler(show_help, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(cancel_message, pattern="^cancel_message$"))
    app.add_handler(CallbackQueryHandler(cancel_reply, pattern="^cancel_reply$"))
    app.add_handler(CallbackQueryHandler(return_to_menu, pattern="^back$"))
    app.add_handler(CallbackQueryHandler(admin_reply_button, pattern="^reply_"))

    # Message Handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_CHAT_ID), handle_admin_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_CHAT_ID_2), handle_admin_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_user_message))

    app.add_error_handler(error_handler)
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    try:
        while keep_running.is_set():
            await asyncio.sleep(1)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

def run_flask_server():
    port = int(os.environ.get("PORT", 5000))
    print(f"🌐 Starting Flask server on port {port}...")
    server.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def run_bot_in_thread():
    asyncio.run(run_telegram_bot())

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ Error: BOT_TOKEN environment variable is required")
        exit(1)
    
    keep_running.set()
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print("🚀 Starting both Flask server and Telegram bot...")
    run_bot_in_thread()
