import logging
import random
import re
from functools import wraps
from itertools import permutations, product

import certifi
import requests
from telegram import Update, InputFile
from telegram import Bot
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes, ConversationHandler, \
    CallbackContext
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
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
games_collection = db.get_collection('games')

bot = Bot(token=os.environ['BOT_TOKEN'])

s3_client = boto3.client(
    service_name="s3",
    endpoint_url=f"https://{os.environ['ACCOUNT_ID']}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ['ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['SECRET_ACCESS_KEY'],
    region_name="apac",
)


def restricted(func):
    """Decorator to restrict access to users who are in the specified group chat."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        try:
            chat_member = await context.bot.get_chat_member(chat_id=os.environ['CHAT_ID'], user_id=user_id)
            if chat_member.status in ['member', 'administrator', 'creator']:
                return await func(update, context, *args, **kwargs)
            else:
                await update.message.reply_text('Please join the MIG Channel to use this bot.')
        except Exception as e:
            await update.message.reply_text('Please join the MIG Channel to use this bot.')
    return wrapped


# Handlers
@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to MIG Bot! For verification, please enter your full name:")
    return NAME


async def name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    context.user_data['name'] = update.message.text
    await update.message.reply_text("Please enter your class as well (e.g. 101):")
    return CLASS


async def clas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = {
        'user_id': user.id,
        'name': context.user_data['name'],
        'class': update.message.text,
        'points': 0.0,
    }
    users_collection.insert_one(user_data)
    await update.message.reply_text("Your data has been saved.")
    return ConversationHandler.END


@restricted
async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})
    if user_data is None:
        await update.message.reply_text(
            "Please use the /start command to enter your name and class before using this command.")
        return

    if context.args:
        number = context.args[0].strip()
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
        await update.message.reply_text(
            f"Your answer {number} has been saved. Number of attempts: {len(user_attempts) + 1}")
    else:
        await update.message.reply_text("Please provide an answer after the command. Example: /answer 42")


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('game1_active', False):
        job = context.user_data.get('game1_end_job')
        if job:
            job.schedule_removal()
        context.user_data.clear()
    elif context.user_data.get('game2_active', False):
        job = context.user_data.get('game2_end_job')
        if job:
            job.schedule_removal()
        await update.message.reply_text("Game cancelled!")
        context.user_data.clear()


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
        prev_score = user['points']
        total_score = sum(10 if attempt == correct_answer else 0 for attempt in user_attempts)
        average_score = total_score / len(user_attempts)
        users_collection.update_one({'user_id': user_id}, {'$inc': {'points': average_score}})
        message = f"Your average score for problem {problem_number} is {round(average_score, 2)}, across all {len(user_attempts)}\
        submissions. Your total score is {round(prev_score + average_score, 2)}."
        await bot.send_message(chat_id=user_id, text=message)


async def announce_new_problem():
    chat_id = 7320259947  # os.environ['CHAT_ID']
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


# for math 24 game
def evaluate_expression(expr):
    try:
        return eval(expr)
    except ZeroDivisionError:
        return None


def generate_all_expressions(nums):
    operators = ['+', '-', '*', '/']
    perms = permutations(nums)
    expressions = set()

    for perm in perms:
        a, b, c, d = perm
        op_combos = product(operators, repeat=3)
        for ops in op_combos:
            op1, op2, op3 = ops
            expressions.add(f"({a}{op1}{b}){op2}({c}{op3}{d})")
            expressions.add(f"({a}{op1}({b}{op2}{c})){op3}{d}")
            expressions.add(f"(({a}{op1}{b}){op2}{c}){op3}{d}")
            expressions.add(f"{a}{op1}(({b}{op2}{c}){op3}{d})")
            expressions.add(f"{a}{op1}({b}{op2}({c}{op3}{d}))")

    return expressions


def find_solution(nums):
    all_expressions = generate_all_expressions(nums)
    for expr in all_expressions:
        if not evaluate_expression(expr) is None and round(evaluate_expression(expr), 5) == 24:
            return expr
    return "No Solution"


@restricted
async def math24_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})
    if user_data is None:
        await update.message.reply_text(
            "Please use the /start command to enter your name and class before using this command.")
        return
    await cancel(update, context)
    context.user_data['game1_active'] = True
    context.user_data['correct_count'] = 0
    context.user_data['game1_end_job'] = context.job_queue.run_once(game1_end_job, when=60,
                                                                    data=(update.message.chat_id, user.id, context))
    await update.message.reply_text(f"Use the following 4 numbers and the 4 operations (+, -, *, /) with brackets to "
                                    f"achieve the number 24! You may use the numbers in any order. If you think there "
                                    f"is no solution, answer -1.\nAnswer as many as you can in 1 minute!")
    await send_next_number(update, context)


def is_valid_user_expression(user_expr, nums):
    try:
        if not re.match(r'^[\d+\-*/()\s]+$', user_expr) or '//' in user_expr:
            return False
        # Check if the user's expression evaluates to 24
        if not evaluate_expression(user_expr) is None and round(evaluate_expression(user_expr), 5) != 24:
            return False

        # Check if the user's expression uses exactly the provided numbers
        used_numbers = [int(n) for n in user_expr if n.isdigit()]
        if sorted(used_numbers) != sorted(nums):
            return False

        return True
    except:
        return False


async def send_next_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'correct_count' not in context.user_data or not context.user_data.get('game1_active', False):
        return

    # Generate two random numbers and their sum
    number1 = random.randrange(1, 10)
    number2 = random.randrange(1, 10)
    number3 = random.randrange(1, 10)
    number4 = random.randrange(1, 10)
    context.user_data['math24_numbers'] = [number1, number2, number3, number4]

    await update.message.reply_text(f"{number1} {number2} {number3} {number4}")


async def math24_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_answer = update.message.text.strip()
    solution = find_solution(context.user_data['math24_numbers'])
    if is_valid_user_expression(user_answer, context.user_data['math24_numbers']):
        context.user_data['correct_count'] += 1
        await update.message.reply_text("Correct!")
    elif user_answer == "-1" and solution == "No Solution":
        context.user_data['correct_count'] += 1
        await update.message.reply_text("Correct!")
    elif solution == "No Solution":
        await update.message.reply_text("Wrong :( There is actually no solution!")
    else:
        await update.message.reply_text(f"Wrong :( A possible solution is {solution}.")
    await send_next_number(update, context)


async def game1_end_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, user_id, context = context.job.data
    correct_count = context.user_data.get('correct_count', 0)
    await context.bot.send_message(chat_id=chat_id, text=f"Game over! You got {correct_count} correct answers.")
    game_data = {
        'user_id': user_id,
        'correct_count': correct_count,
        'timestamp': datetime.now(),
        'game': '24'
    }
    games_collection.insert_one(game_data)
    # Reset user data
    context.user_data.clear()


# Game handlers
@restricted
async def sums_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cancel(update, context)
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})
    if user_data is None:
        await update.message.reply_text(
            "Please use the /start command to enter your name and class before using this command.")
        return
    context.user_data['correct_count'] = 0
    context.user_data['game2_active'] = True  # Flag to indicate the game is active

    # Set a job to end the game in exactly one minute
    context.user_data['game2_end_job'] = context.job_queue.run_once(game2_end_job, when=30,
                                                                    data=(update.message.chat_id, user.id, context))

    # Send the first sum
    await send_next_sum(update, context)


async def send_next_sum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'correct_count' not in context.user_data or not context.user_data.get('game2_active', False):
        return

    # Generate two random numbers and their sum
    num1 = random.randint(1, 10)
    num2 = random.randint(1, 10)
    current_sum = num1 + num2

    context.user_data['current_sum'] = current_sum
    await update.message.reply_text(f"{num1} + {num2} = ?")


async def sums_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'current_sum' not in context.user_data or not context.user_data.get('game2_active', False):
        return

    user = update.message.from_user
    user_answer = int(update.message.text.strip())

    if user_answer == context.user_data['current_sum']:
        context.user_data['correct_count'] += 1

    await send_next_sum(update, context)


async def game2_end_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, user_id, context = context.job.data
    correct_count = context.user_data.get('correct_count', 0)
    context.user_data.clear()

    game_data = {
        'user_id': user_id,
        'correct_count': correct_count,
        'timestamp': datetime.now(),
        'game': 'sums',
    }
    games_collection.insert_one(game_data)

    await context.bot.send_message(chat_id=chat_id, text=f"Game over! You got {correct_count} correct answers.")


@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'math24_numbers' in context.user_data and context.user_data.get('game1_active', False):
        await math24_answer(update, context)
    elif 'current_sum' in context.user_data and context.user_data.get('game2_active', False):
        await sums_answer(update, context)


async def announce():
    chat_id = 7320259947  # os.environ['CHAT_ID']
    # problem_number = problems_collection.find_one({'_id': 'current_problem'})['number']

    # if problem_number > 0:
    #     await notify_users()

    text_message = "Your scheduled announcement text here."
    # Download the image from Cloudflare R2
    # image_path = f"Problem {problem_number + 1}.jpg"
    # s3_client.download_file("mig-telegram", image_path, image_path)

    # if problem_number > 0:
    #     # Download the PDF from Cloudflare R2
    #     pdf_path = f"Problem {problem_number}.pdf"
    #     s3_client.download_file("mig-telegram", pdf_path, pdf_path)

    await bot.send_message(chat_id=chat_id, text=text_message)
    # if problem_number > 0:
    #     await bot.send_document(chat_id=chat_id, document=open(pdf_path, 'rb'))
    #     os.remove(pdf_path)
    # await bot.send_photo(chat_id=chat_id, photo=open(image_path, 'rb'))
    # os.remove(image_path)

# Main function
if __name__ == '__main__':
    application = ApplicationBuilder().token(os.environ['BOT_TOKEN']).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name)],
            CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, clas)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("answer", answer))
    application.add_handler(CommandHandler("game1", math24_start))
    application.add_handler(CommandHandler("game2", sums_start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduler for announcements
    scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Singapore'))
    scheduler.add_job(announce_new_problem, 'cron', day_of_week='sun', hour=20, minute=0)
    scheduler.add_job(announce, 'date', run_date=datetime(2024, 7, 18, 7, 41))
    scheduler.start()

    application.run_polling()
