import logging
import certifi
import requests
from telegram import Update, InputFile
from telegram import Bot
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes, ConversationHandler
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz
import boto3

# MongoDB connection URI
uri = f"mongodb+srv://dingchenghao283:{os.environ['DB_PASSWORD']}@telegram-2024.ehpqyci.mongodb.net/?appName=Telegram-2024"

# Create a new client and connect to the server
client = MongoClient(uri, server_api=ServerApi('1'), tlsCAFile=certifi.where())

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
db = client.get_database('main')
users_collection = db.get_collection('users')
problems_collection = db.get_collection('problems')

bot = Bot(token=os.environ['BOT_TOKEN'])

s3_client = boto3.client(
    service_name ="s3",
    endpoint_url = f"https://{os.environ['ACCOUNT_ID']}.r2.cloudflarestorage.com",
    aws_access_key_id = os.environ['ACCESS_KEY_ID'],
    aws_secret_access_key = os.environ['SECRET_ACCESS_KEY'],
    region_name="apac",
)


# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to MIG Bot! To begin, enter your full name:")
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
        'class': update.message.text,
        'score': 0.0,
    }
    users_collection.insert_one(user_data)
    await update.message.reply_text("Your data has been saved.")
    return ConversationHandler.END


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    print("/answer chat id", update.effective_chat.id)
    user_data = users_collection.find_one({'user_id': user.id})
    if user_data is None:
        await update.message.reply_text(
            "Please use the /start command to enter your name and class before using this command.")
        return

    if context.args:
        number = context.args[0]
        current_problem = problems_collection.find_one({'_id': 'current_problem'})
        problem_number = current_problem['number']
        if f"answer{problem_number}" in user_data:
            user_attempts = user_data[f"answer{problem_number}"]
        else:
            user_attempts = []

        if len(user_attempts) >= 10:
            await update.message.reply_text("You have already used 10 attempts.")
            return

        users_collection.update_one(
            {'user_id': user.id},
            {'$push': {f"answer{problem_number}": number}},
            upsert=True
        )
        await update.message.reply_text(f"Your answer {number} has been saved. Number of attempts: {len(user_attempts)+1}")
    else:
        await update.message.reply_text("Please provide an answer after the command. Example: /answer 42")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled and data saved if available.")
    return ConversationHandler.END


async def notify_users():
    problem_number = problems_collection.find_one({'_id': 'current_problem'})['number']

    # Get the correct answer for the previous problem
    correct_answer = problems_collection.find_one({'problem': problem_number})['answer']

    if correct_answer is None:
        return  # No correct answer for the previous problem

    # Notify each user who submitted an answer
    users = users_collection.find({f'answer{problem_number}': {'$exists': True}})
    for user in users:
        user_id = user['user_id']
        user_attempts = user.get(f'answer{problem_number}')
        prev_score = user['score']
        total_score = sum(10 if attempt == correct_answer else 0 for attempt in user_attempts)
        average_score = total_score / len(user_attempts)
        users_collection.update_one({'user_id': user_id}, {'$inc': {'score': average_score}})
        message = f"Your average score for problem {problem_number} is {round(average_score, 2)}, across all {len(user_attempts)}\
        submissions. Your total score is {round(prev_score+average_score, 2)}."
        await bot.send_message(chat_id=user_id, text=message)

async def announce_new_problem():
    chat_id = 7320259947 # os.environ['CHAT_ID']
    problem_number = problems_collection.find_one({'_id': 'current_problem'})['number']

    if problem_number > 0:
        await notify_users()

    text_message = "Your scheduled announcement text here."
    # Download the image from Cloudflare R2
    image_path = f"Problem {problem_number + 1}.jpg"
    # print("img path:", f"Problem {problem_number + 1}.jpg", ";", image_path)
    s3_client.download_file("mig-telegram", image_path, image_path)

    if problem_number > 0:
        # Download the PDF from Cloudflare R2
        pdf_path = f"Problem {problem_number}.pdf"
        s3_client.download_file("mig-telegram", pdf_path, pdf_path)

    await bot.send_message(chat_id=chat_id, text=text_message)
    if problem_number > 0:
        await bot.send_document(chat_id=chat_id, document=open(pdf_path, 'rb'))
        os.remove(pdf_path)
    await bot.send_photo(chat_id=chat_id, photo=open(image_path, 'rb'))
    os.remove(image_path)

    problems_collection.update_one({'_id': 'current_problem'}, {'$inc': {'number': 1}})

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

    # Scheduler for announcements
    scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Singapore'))
    scheduler.add_job(announce_new_problem, 'cron', day_of_week='fri', hour=20, minute=0)
    scheduler.start()

    application.run_polling()