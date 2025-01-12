import logging
import math
import random
import re
import time
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

def restricted_admin(func):
    """Decorator to restrict access to administrators."""

    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        try:
            chat_member = await context.bot.get_chat_member(chat_id=os.environ['CHAT_ID'], user_id=user_id)
            if chat_member.status in ['administrator', 'creator']:
                return await func(update, context, *args, **kwargs)

    return wrapped

# Handlers
@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})
    if user_data is not None:
        await update.message.reply_text("You have already registered your name and class.")
        return ConversationHandler.END
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
        'month_points': 0,
        'pubs_answers': [None for i in range(10)],
        'training_answer': None
    }
    users_collection.insert_one(user_data)
    await update.message.reply_text("Done! You may use /help to view all available commands and get started.")
    return ConversationHandler.END

@restricted
async def answer_pubs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})

    if context.args and len(args) == 2:
        qn_number = int(context.args[0].strip())
        answer = int(context.args[1].strip())
        user_data = users_collection.find_one({'user_id': user.id})
        answers = user_data['pubs_answers']
        answers[qn_number - 1] = answer
        users_collection.update_one({'user_id': user.id}, {'$set': {'pubs_answers': answers}})

        await update.message.reply_text(
            f"Your answer {answer} for question {qn_number} has been saved. If you would like to change your answer, call the /answerpubs command again.")
    else:
        await update.message.reply_text("Please provide a question number and an answer after the command. Example: /answerpubs 1 42")

@restricted
async def answer_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})

    if context.args and len(args) == 1:
        answer = int(context.args[0].strip())
        users_collection.update_one({'user_id': user.id}, {'$set': {'training_answer': answer}})

        await update.message.reply_text(
            f"Your answer {answer} has been saved. If you would like to change your answer, call the /answertraining command again.")
    else:
        await update.message.reply_text("Please provide an answer after the command. Example: /answertraining 42")


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, sendConfirmation=True):
    if context.user_data.get('game_active', False):
        job = context.user_data.get('game_end_job')
        if job:
            job.schedule_removal()
        if sendConfirmation:
            await update.message.reply_text("Game cancelled!")
        context.user_data.clear()


# Questions code
async def notify_users_pubs():
    problem_number = problems_collection.find_one({'_id': 'current_problem'})['number']

    # Get the correct answer for the previous problem
    correct_answer = str(problems_collection.find_one({'problem': problem_number})['answer']).split()
    problem_points = 10

    if correct_answer is None:
        return  # No correct answer for the previous problem

    # Notify each user who submitted an answer
    users = users_collection.find({'pubs_answers': {'$ne': [None for i in range(10)]}})
    for user in users:
        user_id = user['user_id']
        answers = user['pubs_answers']
        for i in range(10):
            if answers[i] == correct_answer[i]:
                users_collection.update_one({'user_id': user_id}, {'$inc': {'points': problem_points}})

            users_collection.update_one({'user_id': user_id}, {'$set': {'pubs_answers': [None for i in range(10)]}})

        message = f"The previous MIG Pubs Question Set is over. Your new score is {user['points']}."
        await bot.send_message(chat_id=user_id, text=message)

@restricted_admin
async def announce_new_pubs_problem():
    chat_id = os.environ['CHAT_ID']
    problem_number = problems_collection.find_one({'_id': 'current_problem'})['number']

    if problem_number > 0:
        await notify_users_pubs()

    text_message = f'''The new MIG Pubs problem set is out! See the image for the questions!'''
    text_message += f'''The answers for the previous MIG Pubs Question Set (if any) are in the PDF below.'''

    # Download the image from Cloudflare R2
    image_path = f"Problem {problem_number + 1}.jpg"
    # print("img path:", f"Problem {problem_number + 1}.jpg", ";", image_path)
    # s3_client.download_file("mig-telegram", image_path, image_path)

    if problem_number > 0:
        # Download the PDF from Cloudflare R2
        pdf_path = f"Problem {problem_number}.pdf"
        s3_client.download_file("mig-telegram", pdf_path, pdf_path)

    await bot.send_message(chat_id=chat_id, text=text_message, parse_mode='markdown')
    if problem_number > 0:
        await bot.send_document(chat_id=chat_id, document=open(pdf_path, 'rb'))
        os.remove(pdf_path)

    # await bot.send_photo(chat_id=chat_id, photo=open(image_path, 'rb'))
    # os.remove(image_path)

    problems_collection.update_one({'_id': 'current_problem'}, {'$inc': {'number': 1}})

# Questions code
async def notify_users_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    problem_number = problems_collection.find_one({'_id': 'current_problem'})['number']

    # Get the correct answer for the previous problem
    correct_answer = problems_collection.find_one({'problem': problem_number})['answer']
    problem_points = int(context.args[0].strip())

    if correct_answer is None:
        return  # No correct answer for the previous problem

    # Notify each user who submitted an answer
    users = users_collection.find({'training_answer': {'$ne': None}})
    for user in users:
        user_id = user['user_id']
        answer = user['training_answer']
        if answer == correct_answer:
            users_collection.update_one({'user_id': user_id}, {'$inc': {'points': problem_points}})

        users_collection.update_one({'user_id': user_id}, {'$set': {'training_answer': None}})

        message = f"The previous MIG Training Question is over. Your new score is {user['points']}."
        await bot.send_message(chat_id=user_id, text=message)

@restricted_admin
async def announce_new_training_problem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = os.environ['CHAT_ID']
    problem_number = problems_collection.find_one({'_id': 'current_problem'})['number']

    if problem_number > 0:
        if !context.args:
            await update.message.reply_text("Please also input the number of points awarded for the previous question set. ie, /announcetraining 70")
            return

        await notify_users_training(update, context)

    text_message = f'''The new MIG Training Question is out! See the image for the question!'''
    text_message += f'''The answer for the previous MIG Training Question (if any) is in the PDF below.'''

    # Download the image from Cloudflare R2
    image_path = f"Problem {problem_number + 1}.jpg"
    # print("img path:", f"Problem {problem_number + 1}.jpg", ";", image_path)
    # s3_client.download_file("mig-telegram", image_path, image_path)

    if problem_number > 0:
        # Download the PDF from Cloudflare R2
        pdf_path = f"Problem {problem_number}.pdf"
        s3_client.download_file("mig-telegram", pdf_path, pdf_path)

    await bot.send_message(chat_id=chat_id, text=text_message, parse_mode='markdown')
    if problem_number > 0:
        await bot.send_document(chat_id=chat_id, document=open(pdf_path, 'rb'))
        os.remove(pdf_path)

    # await bot.send_photo(chat_id=chat_id, photo=open(image_path, 'rb'))
    # os.remove(image_path)

    problems_collection.update_one({'_id': 'current_problem'}, {'$inc': {'number': 1}})

# End of questions code

# Game code (use 24 as a filler)
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
    return "-1"


async def send_next_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'correct_count' not in context.user_data or not context.user_data.get('game_active', False):
        return

    # Generate two random numbers and their sum
    number1 = random.randrange(1, 10)
    number2 = random.randrange(1, 10)
    number3 = random.randrange(1, 10)
    number4 = random.randrange(1, 10)
    if random.random() < 0.8:
        while find_solution([number1, number2, number3, number4]) == "-1":
            number1 = random.randrange(1, 10)
            number2 = random.randrange(1, 10)
            number3 = random.randrange(1, 10)
            number4 = random.randrange(1, 10)
    context.user_data['game_numbers'] = [number1, number2, number3, number4]

    await update.message.reply_text(f"{number1} {number2} {number3} {number4}")


def is_valid_user_expression(user_expr, nums):
    try:
        if not re.match(r'^[\d+\-*/()\s]+$', user_expr) or '**' in user_expr or '//' in user_expr:
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


async def game_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_answer = update.message.text.strip()
    solution = find_solution(context.user_data['game_numbers'])

    if user_answer == solution or is_valid_user_expression(user_answer, context.user_data['game_numbers']):
        context.user_data['correct_count'] += 1
        await update.message.reply_text("Correct!")

    elif solution == "-1":
        await update.message.reply_text("Wrong :(")
        await update.message.reply_text("There is actually no solution.")

    else:
        await update.message.reply_text("Wrong :(")
        await update.message.reply_text(f"A possible solution is {solution}.")

    await send_next_number(update, context)


async def game_end_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, user_id, context = context.job.data
    correct_count = context.user_data.get('correct_count', 0)
    user_data = users_collection.find_one({'user_id': user_id})
    high_score = max(user_data['month_points'], correct_count)
    await context.bot.send_message(chat_id=chat_id,
                                   text=f"Game over! You got {correct_count} correct answers. Your high score: {high_score}")
    game_data = {
        'user_id': user_id,
        'correct_count': correct_count,
        'timestamp': datetime.now(),
    }
    games_collection.insert_one(game_data)
    users_collection.update_one({'user_id': user_id}, {'$set': {'month_points': high_score}})
    # Reset user data
    context.user_data.clear()


@restricted
async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})
    if user_data is None:
        await update.message.reply_text(
            "Please use the /start command to enter your name and class before using this command.")
        return
    await cancel(update, context, False)
    context.user_data['game_active'] = True
    context.user_data['correct_count'] = 0
    context.user_data['game_end_job'] = context.job_queue.run_once(game_end_job, when=60,
                                                                   data=(update.message.chat_id, user.id, context))
    await update.message.reply_text(f"Use the following 4 numbers and the 4 operations (+, -, *, /) with brackets to "
                                    f"achieve the number 24! You may use the numbers in any order. If you think there "
                                    f"is no solution, answer -1.\nAnswer as many as you can in 1 minute!")
    await send_next_number(update, context)


async def game_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Probably should improve this code to do away with code duplication
    # Print month leaderboard
    leaderboard_text = "*Month Leaderboard*\n\n"
    top_users = list(users_collection.sort([("month_points", -1)]))
    if not top_users:
        leaderboard_text += "\nNo users have points yet this month.\n\n"
    else:
        leaderboard_text += "Top scorers this month:\n"

    current_rank = 1
    last_score = None
    display_count = 0
    rank = 1

    for i, user in enumerate(top_users):
        user_name = user['name']
        user_points = user["month_points"]

        if last_score is None or user_points != last_score:
            rank = current_rank

        if display_count < 5 or (user_points == last_score):
            leaderboard_text += f"{rank}. {user_name} - {user_points} points\n"
            display_count += 1
        else:
            break
        last_score = user_points
        current_rank += 1
    leaderboard_text += "\n"

    # Print semester leaderboard
    leaderboard_text = "*Semester Leaderboard (excludes the ongoing monthly game)*\n\n"
    top_users = list(users_collection.sort([("points", -1)]))
    if not top_users:
        leaderboard_text += "\nNo users have points yet.\n\n"
    else:
        leaderboard_text += "Top scorers:\n"

    current_rank = 1
    last_score = None
    display_count = 0
    rank = 1

    for i, user in enumerate(top_users):
        user_name = user['name']
        user_points = user["points"]

        if last_score is None or user_points != last_score:
            rank = current_rank

        if display_count < 5 or (user_points == last_score):
            leaderboard_text += f"{rank}. {user_name} - {user_points} points\n"
            display_count += 1
        else:
            break
        last_score = user_points
        current_rank += 1
    leaderboard_text += "\n"

    await update.message.reply_text(leaderboard_text, parse_mode='markdown')

@restricted_admin
async def end_ongoing_game():
    chat_id = os.environ['CHAT_ID']
    top_users = list(users_collection.sort([("month_points", -1)]))
    if not top_users:
        return

    highest_month_points = top_users[0]["month_points"]
    for i, user in enumerate(top_users):
        user_name = user['name']
        user_id = user['user_id']
        user_month_points = user["month_points"]

        users_collection.update_one({'user_id': user_id},
                                    {'$inc': {'points': 200 * user_month_points / highest_month_points}})
        users_collection.update_one({'user_id': user_id}, {'$set': {'month_points': 0}})

    message = 'The ongoing game has ended. Check your scores with /leaderboard and /points now!'
    await bot.send_message(chat_id=chat_id, text=message, parse_mode='markdown')


# End of game code

@restricted
async def check_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})
    if user_data is None:
        await update.message.reply_text(
            "Please use the /start command to enter your name and class before using this command.")
        return

    user_month_points = user_data['month_points']
    user_points = user_data['points']

    await update.message.reply_text(f"You have {user_month_points} points this month.")
    await update.message.reply_text(f"You currently have {user_points:.2f} points in total.")


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "/start - Register your name and class\n"
        "/answertraining [answer] - Submit your answer for the current training problem\n"
        "/answerpubs [qn number] [answer] - Submit your answer for the current pubs problem set"
        "/game - Play the monthly game\n"
        "/points - Check your current points\n"
        "/leaderboard - Display game leaderboard\n"
        "/cancel - Cancel the current operation\n"
        "/help - Show this help message"
    )
    await update.message.reply_text(help_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('game_active', False):
        await game_answer(update, context)


async def announce():
    chat_id = os.environ['CHAT_ID']
    # problem_number = problems_collection.find_one({'_id': 'current_problem'})['number']

    # if problem_number > 0:
    #     await notify_users()

    text_message = "btw the bot handle is @nush_mig_bot"
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
    application.add_handler(CommandHandler("answertraining", answer_training))
    application.add_handler(CommandHandler("answerpubs", answer_pubs))
    application.add_handler(CommandHandler("game", game_start))
    application.add_handler(CommandHandler("points", check_points))
    application.add_handler(CommandHandler("leaderboard", game_leaderboard))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("endgame", end_ongoing_game))
    application.add_handler(CommandHandler("announcetraining", announce_new_training_problem))
    application.add_handler(CommandHandler("announcepubs", announce_new_pubs_problem))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()