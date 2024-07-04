import logging
from telegram import Update
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes, ConversationHandler
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os

# MongoDB connection URI
uri = f"mongodb+srv://ding:{os.environ['DB_PASSWORD']}@helloworld.2revect.mongodb.net?appName=HelloWorld"

# Create a new client and connect to the server
client = MongoClient(uri, server_api=ServerApi('1'))

# Send a ping to confirm a successful connection
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Define conversation states
NAME, CLASS = range(2)

# Database
db = client.get_database('test')
users_collection = db.get_collection('users')


# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to MIG Bot! Enter your full name:")
    return NAME


async def name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    context.user_data['name'] = update.message.text
    await update.message.reply_text("Enter your class (e.g. 101):")
    return CLASS


async def clas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = {
        'user_id': user.id,
        'name': context.user_data['name'],
        'class': update.message.text
    }
    users_collection.insert_one(user_data)
    await update.message.reply_text("Your data has been saved.")
    return ConversationHandler.END


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})

    if user_data is None:
        await update.message.reply_text(
            "Please use the /start command to enter your name and class before using this command.")
        return

    if context.args:
        number = context.args[0]
        users_collection.update_one({'user_id': user.id}, {'$set': {'answer': number}})
        await update.message.reply_text(f"Your answer {number} has been saved.")
    else:
        await update.message.reply_text("Please provide an answer after the command. Example: /answer 42")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


# Main function
if __name__ == '__main__':
    application = ApplicationBuilder().token(os.environ['BOT_TOKEN']).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name)],
            CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, clas)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("answer", answer))

    application.run_polling()