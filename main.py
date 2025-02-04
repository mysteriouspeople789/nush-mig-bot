import logging
import math
import random
import re
import time

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

# Define announcement states
NEW_TYPE, ANNOUNCE_PREV, NEW_QN_LINK, NEW_ANS_LINK, NEW_ANS_TEXT, NEW_ANNOUNCEMENT_TEXT, WRITE_NEW_DATA = range(7)

# Define question categories
TRAINING, PUBS = range(100, 102) # Offset by 100 as a workaround for the database being not cleared yet

# Define type identity states
CATEGORY, POINTS = range(2)

# Define type categories and points
type_identity = {'easy': [TRAINING, 30],
                 'medium': [TRAINING, 50],
                 'hard': [TRAINING, 70],
                 'pubs': [PUBS, 10]}

valid_types = list(type_identity.keys())

# Database
db = client.get_database('main')
users_collection = db.get_collection('users2025')
problems_collection = db.get_collection('problems')

bot = Bot(token=os.environ['BOT_TOKEN'])

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
            else:
                return None
        except Exception as e:
            return None

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

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

@restricted
async def answer_pubs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})

    if context.args and len(context.args) == 2:
        qn_number = int(context.args[0].strip())
        answer = int(context.args[1].strip())
        user_data = users_collection.find_one({'user_id': user.id})
        answers = user_data.get("pubs_answers", [None for i in range(10)])
        answers[qn_number - 1] = answer
        users_collection.update_one({'user_id': user.id}, {'$set': {'pubs_answers': answers}}, upsert=True)

        await update.message.reply_text(
            f"Your answer {answer} for question {qn_number} has been saved. If you would like to change your answer, call the /answerpubs command again.")
    else:
        await update.message.reply_text("Please provide a question number and an answer after the command. Example: /answerpubs 1 42")

@restricted
async def answer_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})

    if context.args and len(context.args) == 1:
        answer = context.args[0].strip()
        users_collection.update_one({'user_id': user.id}, {'$set': {'training_answer': answer}}, upsert=True)

        await update.message.reply_text(
            f"Your answer {answer} has been saved. If you would like to change your answer, call the /answertraining command again.")
    else:
        await update.message.reply_text("Please provide an answer after the command. Example: /answertraining 42")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, sendConfirmation=True, clearUserData=True):
    if context.user_data.get('game_active', False):
        job = context.user_data.get('game_end_job')
        if job:
            job.schedule_removal()
        if sendConfirmation:
            await update.message.reply_text("Game cancelled!")
        if clearUserData:
            context.user_data.clear()

@restricted_admin
async def set_new_type(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("Hello! If you unintentionally wrote this command, type /cancel at any time to cancel the operation, and nothing will be saved.")

    qn_data = {
        '_id': TRAINING,
        'type': 'easy',
        'qn_link': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        'ans_link': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        'ans_text': ['69'],
        'announcement_text': 'Hello MIG!'
    }

    await update.message.reply_text(f"What type will the new question be? Acceptable types: {valid_types}")
    return ANNOUNCE_PREV

async def announce_prev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    if message not in valid_types:
        await update.message.reply_text(f"Invalid type. Acceptable types: {valid_types}")
        return ANNOUNCE_PREV

    qn_type = message
    context.user_data['type'] = qn_type
    qn_category = type_identity[message][CATEGORY]
    context.user_data['_id'] = qn_category

    prev_problem = problems_collection.find_one({'_id': qn_category})
    if prev_problem is None:
        await update.message.reply_text("Enter the link for the new question.")
        return NEW_QN_LINK

    prev_answer = prev_problem['ans_text']
    prev_category = type_identity[prev_problem['type']][CATEGORY]
    prev_points = type_identity[prev_problem['type']][POINTS]
    prev_category_string = 'pubs_answers' if prev_category == PUBS else 'training_answer'
    prev_answer_link = prev_problem['ans_link']
    context.user_data['prev_ans_link'] = prev_answer_link

    if prev_answer is None:
        # no correct answer for prev problem
        await update.message.reply_text("Enter the link for the new question.")
        return NEW_QN_LINK

    # Notify each user who submitted an answer
    users = users_collection.find({prev_category_string: {'$exists': True}})
    for user in users:
        user_id = user['user_id']
        answer = user[prev_category_string]
        points = user['points']
        message = ""
        
        if prev_category == PUBS:
            for i in range(min(len(answer), len(prev_answer))):
                if answer[i] == prev_answer[i]:
                    users_collection.update_one({'user_id': user_id}, {'$inc': {'points': prev_points}})
                    points += prev_points

                users_collection.update_one({'user_id': user_id}, {'$unset': {'pubs_answers': None}})

            message = f"The previous MIG Pubs Question Set is over. Your new score is {points}."

        elif prev_category == TRAINING:
            if answer == prev_answer[0]:
                users_collection.update_one({'user_id': user_id}, {'$inc': {'points': prev_points}})
                points += prev_points

            users_collection.update_one({'user_id': user_id}, {'$unset': {'training_answer': None}})

            message = f"The previous MIG Training Question is over. Your new score is {points}."

        await bot.send_message(chat_id=user_id, text=message)

    await update.message.reply_text("Enter the link for the new question.")
    return NEW_QN_LINK

async def set_new_qn_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["qn_link"] = update.message.text
    await update.message.reply_text("Enter the link for the answers to the new question.")
    return NEW_ANS_LINK

async def set_new_ans_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ans_link"] = update.message.text
    await update.message.reply_text("Enter the answers to the new question, in a string separated by underscores. ie: 6_nine_42_zero")
    return NEW_ANS_TEXT

async def set_new_ans_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ans_text"] = list(update.message.text.split("_"))
    await update.message.reply_text("Enter the text you want to send as the announcement. The format in which the announcement will be made is as follows: \n\n<message>\n\nPrevious Answer Link: <link>\nNew Question Link: <link>")
    return NEW_ANNOUNCEMENT_TEXT

async def set_new_announcement_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["announcement_text"] = update.message.text

    problems_collection.update_one({'_id': context.user_data["_id"]}, {'$set': {'type': context.user_data['type']}}, upsert=True)
    problems_collection.update_one({'_id': context.user_data["_id"]}, {'$set': {'qn_link': context.user_data['qn_link']}}, upsert=True)
    problems_collection.update_one({'_id': context.user_data["_id"]}, {'$set': {'ans_link': context.user_data['ans_link']}}, upsert=True)
    problems_collection.update_one({'_id': context.user_data["_id"]}, {'$set': {'ans_text': context.user_data['ans_text']}}, upsert=True)

    message = f"{context.user_data['announcement_text']}\n\n"

    if context.user_data.get('prev_ans_link') is not None:
        message += f"Previous Answer Link: {context.user_data['prev_ans_link']}\n"

    message += f"New Question Link: {context.user_data['qn_link']}"

    await bot.send_message(chat_id=os.environ['CHAT_ID'], text=message)

    return ConversationHandler.END

# End of questions code

# Start of game code

async def check_last_qn(update: Update, context: ContextTypes.DEFAULT_TYPE):

    prev_qn = context.user_data['game_qn'] - 1
    correct_ans = context.user_data['game_correct_ans']

    if prev_qn == 0: return

    user_answer = update.message.text.strip()

    correct = False

    if prev_qn <= 2:
        try:
            correct = list(map(int, correct_ans.split())) == sorted(map(int, user_answer.split()))
        except:
            correct = False

    else:
        correct = correct_ans == user_answer

    if correct:
        await update.message.reply_text("Correct!")
        points_distribution = [3, 5, 7]
        context.user_data["game_score"] += points_distribution[(prev_qn - 1) // 10]
    else:
        await update.message.reply_text(f"Incorrect :(. The correct answer is {correct_ans}")
        context.user_data["game_wrongs"] += 1

def find_prime_factors(n):
    i = 2
    factors = []
    while i * i <= n:
        if n % i:
            i += 1
        else:
            n //= i
            factors.append(i)

    if n > 1:
        factors.append(n)

    return factors

async def gen_new_qn(update: Update, context: ContextTypes.DEFAULT_TYPE):

    curr_qn = context.user_data['game_qn']
    qn_text = ""

    if curr_qn <= 2:
        number = random.randrange(100, 10000)
        factors = find_prime_factors(number)
        context.user_data['game_correct_ans'] = " ".join(map(str, sorted(factors)))
        qn_text = f"Find the prime factorisation of {number}. Give your answer in space separated integers, ie: 2 3 3 5 7 7 7"

    elif curr_qn == 3:
        qn_text = f"Find the sum of roots of:\n"
        order = random.randrange(5, 17)

        coeffs = [random.choice((-1, 1)) * random.randrange(1, 100) for i in range(order + 1)]
        negscndlst = -coeffs[-2]
        lst = coeffs[-1]

        if lst < 0:
            negscndlst = -negscndlst
            lst = -lst

        gcd = math.gcd(abs(negscndlst), abs(lst))
        negscndlst //= gcd
        lst //= gcd

        context.user_data['game_correct_ans'] = str(100 * negscndlst + lst)

        for i in range(order, -1, -1):
            if i != order:
                qn_text += [" + ", " - "][coeffs[i] < 0]

            elif coeffs[i] < 0:
                qn_text += "-"

            qn_text += f"{abs(coeffs[i])}"

            if i == 1:
                qn_text += "x"

            elif i != 0:
                qn_text += f"x^{i}"

        qn_text += " = 0\n"

        qn_text += "Express your answer in a single integer x, where x = 100A + B, and the sum of roots is A / B."

    else:
        qn_text = "The next question is a Multiple Choice Question."

        qns = [
            ("Let a and b be positive real numbers such that a <= b and 1/a + 1/b = 1/ab."
             "Find the smallest possible value of k such that b - a < k for all a and b that satisfy the above criteria.",
            ["1", "2", "5/2", "1/2", "2/3"]),

            ("Find the smallest root of x^3 + 7x^2 + 7x - 15 = 0.",
            ["-5", "-8", "-3", "1", "5"]),

            ("If sqrt(x + a) - sqrt(2x + b) = sqrt(y + a) - sqrt(2025y + b) = 0, find the prime factorisation of n if x = ny.",
            ["2^3 x 11 x 23", "3^4 x 5^2", "43 x 47", "7^2 x 41", "2^2 x 3 x 13^2"]),

            ("Find the roots of x^2 - 10x + 21 = 0.",
            ["3, 7", "-3, -7", "3, 5", "5 + i sqrt(5), 5 - i sqrt(5)", "-5 + sqrt(46), -5 - sqrt(46)"]),

            ("Find the roots of 6x^2 - 17x = 198.\n"
             "I.   -9/2\n"
             "II.   9/2\n"
             "III.    7\n"
             "IV.  22/3\n",
            ["II and IV", "I and III", "II and III", "I and IV", "III and IV"]),

            ("Which of the following is a square?",
            ["254016", "121211", "141400", "156009", "182818"]),

            ("How many real roots are there to the equation\n"
             "x^11 + x^10 + x^9 + x^8 + ... + x + 1 = 0?",
            ["1", "3", "4", "5", "7"]),

            ("d/dx [x / (x^2 + 2)] =",
            ["(2-x^2)/(x^2+2)^2", "(x^2-2)/(x^2+2)^2", "2/(x^2+2)^2", "(2-x)/(x^2+2)^2", "(x-2)/(x^2+2)^2"]),

            ("d/dx [ln(tan(x))] =",
            ["sec^2(x)cot(x)", "sec^2(x)tan(x)", "sec^2(x)csc(x)", "csc^2(x)sec(x)", "csc^2(x)cot(x)"]),

            ("d/dx [sin(x^2) / cos(x^2)] =",
            ["2x sec^2(x^2)", "2x csc^2(x^2)", "-2x sec^2(x^2)", "-2x csc^2(x^2)", "-2x cot^2(x^2)"]),

            ("d/dx [e^(4tan(x))] =",
            ["4sec^2(x) e^(4tan(x))", "16sec^2(x) e^(4tan(x))", "sec^2(x) e^(4tan(x))", "16csc^2(x) e^(4tan(x))", "4csc^2(x) e^(4tan(x))"]),

            ("d/dx [ln(sin(x^2+1))] =",
            ["2x cot(x^2+1)", "2x csc(x^2+1)", "2x sec(x^2+1)", "2x cos(x^2+1)", "2x tan(x^2+1)"]),

            ("∫[ln(3x)] dx =",
            ["xln(3x)-x+c", "xln(3x)-3+c", "xln(3x)-3x+c", "3xln(3x)-3x+c", "3xln(3x)-x+c"]),

            ("∫[tan(x)] dx =",
            ["ln|sec x|+c", "ln|sin x|+c", "ln|cot x|+c", "ln|cos x|+c", "ln|csc x|+c"]),

            ("∫[(1/sqrt(x))+(1/x^2)] dx =",
            ["2sqrt(x)-1/x+c", "2sqrt(x)-2/x+c", "sqrt(x)+2/x+c", "2sqrt(x)+1/x+c", "sqrt(x)+1/x+c"]),

            ("∫[1/(x^2+2)] dx =",
            ["arctan(x/sqrt2)/sqrt2+c", "arctan(x/2)/sqrt2+c", "arctan(x/sqrt2)/2+c", "arctan(x)/sqrt2+c", "arctan(x)/2+c"]),

            ("∫[1/sqrt(x^2+1)] dx =",
            ["ln|sqrt(x^2+1)+x|+c", "ln|x^2+1+sqrt(x)|+c", "ln|sqrt(x^2+1)+2x|+c", "ln|x^2+1+2sqrt(x)|+c", "ln|x^2+1+sqrt(2x)|+c"]),

            ("∫[x/(x+1)] dx =",
            ["x-ln|x+1|+c", "x-ln(x+1)+c", "x+1-ln(x+1)+c", "x^2-x-ln|x+1|+c", "x-ln|x|+c"]),

            ("∫[4sinx/(1-cosx)] dx =",
            ["4ln|1-cosx|+c", "4ln|4-cosx|+c", "4ln(2sin^2(x))+c", "4ln(2sinx)+c", "4ln(4-cosx)+c"]),

            ("∫[arctan(x)] dx =",
            ["xarctan(x)-½(ln(1+x^2))+c", "arctan(x)-ln(1+x^2)+c", "arctan(x)-ln(x^2)+c", "xarctan(x)-ln(1+x^2)+c", "arctan(x)-½(ln(1+x^2))+c"]),

            ("∫[sin(2x)sin(3x)] dx =",
            ["(1/10)(5sin(x)-sin(5x))+c", "(1/10)(sin(x)-sin(5x))+c", "(1/10)(sin(5x)-sin(x))+c", "(1/5)(sin(x)-sin(5x))", "(1/5)(sin(5x)-5sin(x))+c"]),

            ("∫[x^3/(x^2+4x+3)] dx =",
            ["½(x^2)-4x-½(ln|x+1|)+27/2(ln|x+3|)+c", "½(x^2)-4-½(ln|x+1|)+27/2(ln|x+3|)+c", "½(x^2)-4-ln|x+1|+27/2(ln|x+3|)+c", "x^2-4x-ln|x+1|+27/2(ln(x+3))+c", "x^2-4-ln|x+1|+27/2(ln(x+3))+c"]),

            ("∫[(x^3-2x+1)/x^2] dx =",
            ["½(x^2)-2ln|x|-(1/x)+c", "½(x^2)-ln|x|-(1/x)+c", "¼(x^2)-2ln|x|-(2/x)+c", "¼(x^2)-ln|x|-(2/x)+c", "¼(x^2)-ln|x|-(1/x)+c"]),

            ("∫[(x+4)/(x^2+4x+13)] dx =",
            ["½(ln|x^2+4x+13|)+⅔tan^-1((x+2)/3)+c", "½(ln|x^2+4x+13|)+⅓tan^-1(x+2)+c", "½(ln|x^2+4x+13|)+⅓tan^-1((x+2)/3)+c", "(ln|x^2+4x+13|)+⅔tan^-1((x+2)/3)+c", "(ln|x^2+4x+13|)+⅓tan^-1(x+2)+c"]),

            ("∫[1/sqrt(-x^2+2x+15)] dx =",
            ["arcsin((x-1)/4)+c", "arcsin((x-2)/4)+c", "arcsin((x-1)/2)+c", "arcsin(x/4)+c", "arcsin(x/2)+c"]),

            ("∫[tan^2(x)] dx =",
            ["tanx-x+c", "xtanx-x+c", "xtan^2x-x+c", "xtanx+c", "tanx+c"]),

            ("∫[sin^2(x)] dx =",
            ["¼(2x-sin2x)+c", "½(2x-sin2x)+c", "¼(2x-sinx)+c", "¼(x-sin2x)+c", "½(x-sinx)+c"]),
        ]

        qn, anss = qns[curr_qn - 4]
        order = [0, 1, 2, 3, 4]
        random.shuffle(order)

        context.user_data['game_correct_ans'] = chr(ord('A') + order[0])

        anss_ = [None for i in range(5)]
        for i in range(5):
            anss_[order[i]] = anss[i]

        qn_text += f"{qn}\n"

        for i in range(5):
            qn_text += f"{chr(ord('A') + i)}. {anss_[i]}\n"

        qn_text += "\nMake sure you enter your option in upper case!"

    await update.message.reply_text(qn_text)

async def send_next_qn(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if 'game_score' not in context.user_data: return

    if 'game_wrongs' not in context.user_data: return

    if 'game_qn' not in context.user_data: return

    if 'game_correct_ans' not in context.user_data: return

    if not context.user_data.get('game_active', False): return

    await check_last_qn(update, context)

    # Game ended
    if context.user_data["game_wrongs"] >= 3 or context.user_data['game_qn'] > 30:
        await update.message.reply_text(f"The game is over. You can try again by typing /game!")
        await cancel(update, context, False, False)
        context.user_data['game_end_job'] = context.job_queue.run_once(game_end_job, when=0,
                                                                       data=(update.message.chat_id, update.message.from_user.id, context))
        return

    await gen_new_qn(update, context)

    context.user_data['game_qn'] += 1


async def game_end_job(context: ContextTypes.DEFAULT_TYPE):

    chat_id, user_id, context = context.job.data
    game_score = context.user_data['game_score']
    user_data = users_collection.find_one({'user_id': user_id})
    high_score = user_data.get("month_points", game_score)
    high_score = max(high_score, game_score)
    await context.bot.send_message(chat_id=chat_id,
                                   text=f"Your score is {game_score}. Your (updated) high score is {high_score}.")

    users_collection.update_one({'user_id': user_id}, {'$set': {'month_points': high_score}}, upsert=True)
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
    context.user_data['game_score'] = 0
    context.user_data['game_wrongs'] = 0
    context.user_data['game_correct_ans'] = 0
    context.user_data['game_qn'] = 1
    context.user_data['game_end_job'] = context.job_queue.run_once(game_end_job, when=180,
                                                                   data=(update.message.chat_id, user.id, context))

    await update.message.reply_text(f"In the following minute, you will be doing 30 questions.\n"
                                    f"The first 10 are easy, being worth 3 points each.\n"
                                    f"The next 10 are medium, and are worth 5 points each.\n"
                                    f"The last 10 are hard, and are worth 7 points each.\n"
                                    f"However, if you answer 3 questions wrong, the game is over.\n"
                                    f"Good luck!")

    await send_next_qn(update, context)


async def game_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Probably should improve this code to do away with code duplication
    # Print month leaderboard
    leaderboard_text = "*Month Leaderboard*\n\n"
    top_users = list(users_collection.find({"month_points": {'$exists': True}}).sort([("month_points", -1)]))
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
    leaderboard_text += "*Semester Leaderboard (excludes the ongoing monthly game)*\n\n"
    top_users = list(users_collection.find({"points": {'$exists': True}}).sort([("points", -1)]))
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
async def end_ongoing_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = os.environ['CHAT_ID']
    top_users = list(users_collection.find({"month_points": {'$exists': True}}).sort([("month_points", -1)]))
    if not top_users:
        return

    highest_month_points = top_users[0]["month_points"]
    if highest_month_points == 0:
        return

    for i, user in enumerate(top_users):
        user_name = user['name']
        user_id = user['user_id']
        user_month_points = user["month_points"]

        users_collection.update_one({'user_id': user_id},
                                    {'$inc': {'points': 200 * user_month_points / highest_month_points}})
        users_collection.update_one({'user_id': user_id}, {'$unset': {'month_points' : ''}})

        message = 'The ongoing game has ended. Check your scores with /leaderboard and /points now!'
        await bot.send_message(chat_id=user_id, text=message, parse_mode='markdown')

@restricted_admin
async def reset_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users_collection.update_many({}, {'$unset': {"month_points": ''}})
    users_collection.update_many({}, {'$set': {"points": 0}})

# End of game code

@restricted
async def check_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = users_collection.find_one({'user_id': user.id})
    if user_data is None:
        await update.message.reply_text(
            "Please use the /start command to enter your name and class before using this command.")
        return

    user_month_points = user_data.get("month_points", 0)
    user_points = user_data['points']
    message = f"You have {user_month_points} points this month.\n"
    message += f"You currently have {user_points:.2f} points in total."

    await update.message.reply_text(message)


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
        await send_next_qn(update, context)


async def announce():
    chat_id = os.environ['CHAT_ID']
    text_message = "btw the bot handle is @nush_mig_bot"
    await bot.send_message(chat_id=chat_id, text=text_message)

# Main function
if __name__ == '__main__':
    application = ApplicationBuilder().token(os.environ['BOT_TOKEN']).build()

    conv_start_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name)],
            CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, clas)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
    )

    announce_qn_handler = ConversationHandler(
        entry_points=[CommandHandler("announce", set_new_type)],
        states={
            ANNOUNCE_PREV: [MessageHandler(filters.TEXT & ~filters.COMMAND, announce_prev)],
            NEW_QN_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_new_qn_link)],
            NEW_ANS_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_new_ans_link)],
            NEW_ANS_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_new_ans_text)],
            NEW_ANNOUNCEMENT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_new_announcement_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
    )

    application.add_handler(conv_start_handler)
    application.add_handler(announce_qn_handler)
    application.add_handler(CommandHandler("answertraining", answer_training))
    application.add_handler(CommandHandler("answerpubs", answer_pubs))
    application.add_handler(CommandHandler("game", game_start))
    application.add_handler(CommandHandler("points", check_points))
    application.add_handler(CommandHandler("leaderboard", game_leaderboard))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("endgame", end_ongoing_game))
    application.add_handler(CommandHandler("resetscores", reset_scores))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()