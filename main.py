import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOOKS = [
    "1. كتاب العادات الذرية",
    "2. كتاب الأب الغني والأب الفقير",
    "3. كتاب فن اللامبالاة",
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا بيك بمكتبة البوت 📚\nاكتب /books حتى تشوف الكتب")

async def books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\n".join(BOOKS))

if __name__ == "__main__":
    token = os.environ.get("BOT_TOKEN")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("books", books))
    app.run_polling()
