import os
import time
from dotenv import load_dotenv
import telebot

#Loads .env file
load_dotenv()

#Reads the value of chosen variable from .env file
def read_env_variable(env_variable_name):
    result = os.getenv(env_variable_name)
    if not result:
        raise ValueError(env_variable_name + " was not found!")
    return result

#Reading bot token
BOT_TOKEN = read_env_variable('BOT_TOKEN')

#Reading channel id
CHANNEL_ID = read_env_variable('CHANNEL_ID')

#Initializing telebot
bot = telebot.TeleBot(BOT_TOKEN)

#For testing
@bot.message_handler(commands=['chat'])
def send_welcome(message):
    bot.reply_to(message, "testing " + message.text)
    bot.send_message(CHANNEL_ID, "test")

#For testing
@bot.message_handler(func=lambda msg: msg.content_type == 'text')
def send_message(msg):
    bot.reply_to(msg, "Gotcha")

#Start the bot and polling
bot.infinity_polling()