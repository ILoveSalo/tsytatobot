import os
from datetime import datetime

from dotenv import load_dotenv
import telebot
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from telebot import custom_filters
from telebot.states.sync.middleware import StateMiddleware
from telebot import types

from date_parser.date_parser import DateParser
from domain.phrase import Phrase
from domain.quote import Quote
from domain.speaker import Speaker
from persistence.impl.local_files.json.json_speaker_repository import JsonSpeakerRepository
from persistence.speaker_repository import SpeakerRepository
from quote_generator.quote_text_generator import QuoteTextGenerator

# ------------------ Infrastructure ------------------

# Load variables from .env file (BOT_TOKEN, CHANNEL_ID, etc.)
load_dotenv()

# Helper: read env variable or throw an error if missing
def read_env_variable(env_variable_name):
    result = os.getenv(env_variable_name)
    if not result:
        raise ValueError(env_variable_name + " was not found!")
    return result

# Bot configuration from environment
BOT_TOKEN = read_env_variable('BOT_TOKEN')
CHANNEL_ID = read_env_variable('CHANNEL_ID')

# Use in-memory storage for states (will reset on restart)
storage = StateMemoryStorage()

# Initialize bot with state middleware
bot = telebot.TeleBot(BOT_TOKEN, state_storage=storage, use_class_middlewares=True)
bot.add_custom_filter(custom_filters.StateFilter(bot))  # enables @bot.message_handler(state=...)
bot.setup_middleware(StateMiddleware(bot))              # enables state transitions

# Repository for saving/retrieving speakers
speaker_repository: SpeakerRepository = JsonSpeakerRepository("speakers.json")

date_parser = DateParser()
quote_text_generator = QuoteTextGenerator(date_parser)

# ------------------ States ------------------

# Quote creation flow
class QuoteState(StatesGroup):
    waiting_for_date = State()         # step 1: wait for date
    waiting_for_next_step = State()    # step 4: after phrase+speaker → add more or finalize?

# Phrase input flow
class PhraseState(StatesGroup):
    waiting_for_text = State()         # step 2: wait for phrase text
    waiting_for_context_text = State() # (optional future feature)

# Speaker input flow
class SpeakerState(StatesGroup):
    waiting_for_name = State()         # step 3: wait for speaker’s name


# ------------------ Utils ------------------

# Retrieve Quote object from current user’s state
def get_quote_from_state(user_id, chat_id):
    with bot.retrieve_data(user_id, chat_id) as data:
        return data.get("quote")


# ------------------ Handlers ------------------

# Step 0: /quote → start quote creation
@bot.message_handler(commands=['quote'])
def create_quote(message):
    bot.delete_state(message.from_user.id, message.chat.id)  # reset previous state

    bot.reply_to(message, "Nice! Let's create a new quote!")

    # Keyboard with quick options (e.g., "Today")
    keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    buttons = [types.KeyboardButton("📅 Today")]
    keyboard.add(*buttons)

    bot.send_message(
        message.chat.id,
        "Let's start with the date. When did you hear these words? (dd.mm.yyyy or today)",
        reply_markup=keyboard
    )
    bot.set_state(message.from_user.id, QuoteState.waiting_for_date, message.chat.id)


# Step 1: handle date input
@bot.message_handler(state=QuoteState.waiting_for_date)
def process_quote_date(message):
    try:
        date = date_parser.parse_string_to_date(message.text)
    except:
        bot.reply_to(message, "That doesn’t look like a valid date. Example: 25.06.2005. Try again.")
        bot.set_state(message.from_user.id, QuoteState.waiting_for_date, message.chat.id)
        return

    # Save empty quote with this date
    bot.add_data(message.from_user.id, message.chat.id, quote=Quote([], date))

    # Ask for first phrase
    bot.send_message(message.chat.id, f"Cool! So, what was said on {date_parser.parse_date_to_string(date)}?")
    bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)


# Step 2: handle phrase text
@bot.message_handler(state=PhraseState.waiting_for_text)
def process_phrase_text(message):
    text = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Add phrase with placeholder speaker
    quote.phrases.append(Phrase(None, text, None))
    bot.add_data(message.from_user.id, message.chat.id, quote=quote)

    # Move to speaker state
    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name, message.chat.id)

    # Suggest known speakers as keyboard options
    speakers = [s.name for s in speaker_repository.get_speakers()]
    keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    keyboard.add(*[types.KeyboardButton(name) for name in speakers])

    bot.send_message(
        message.chat.id,
        f"\"{text}\", such wise words! ദ്ദി(˵ •̀ ᴗ - ˵ ) ✧\n"
        f"Now, who is the wise person that said this?",
        reply_markup=keyboard
    )


# Step 3: handle speaker name
@bot.message_handler(state=SpeakerState.waiting_for_name)
def process_speaker_name(message):
    name = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Attach speaker to last phrase
    quote.phrases[-1].speaker = Speaker(name)
    bot.add_data(message.from_user.id, message.chat.id, quote=quote)

    # Save speaker for future suggestions
    speaker_repository.save_speaker(quote.phrases[-1].speaker)

    # Move to "what next?" state
    bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)

    # Show preview
    bot.send_message(message.chat.id, f"Did {name} really say that??? (¬_¬\")\nYour phrase is:")
    bot.send_message(message.chat.id, quote_text_generator.generate_quote_with_tags(quote))

    # Offer choices: add, finalize, cancel
    keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    buttons = [types.KeyboardButton(x) for x in ["➕ Add", "✔️ Finalize", "❌ Cancel"]]
    keyboard.add(*buttons)

    bot.send_message(message.chat.id, "Do you want to add a new phrase or finalize the quote?", reply_markup=keyboard)


# Step 4: finalize option
@bot.message_handler(func=lambda m: m.text.lower().strip() in ["finalize", "✔️ finalize"], state=QuoteState.waiting_for_next_step)
def process_next_step_finalize_option(message):
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Post final quote to channel
    bot.send_message(CHANNEL_ID, quote_text_generator.generate_quote_with_tags(quote))
    bot.send_message(message.chat.id, "Done! (⸝⸝> ᴗ•⸝⸝)")
    bot.delete_state(message.from_user.id, message.chat.id)


# Step 4: add another phrase option
@bot.message_handler(func=lambda m: m.text.lower().strip() in ["add", "➕ add"], state=QuoteState.waiting_for_next_step)
def process_next_step_add_option(message):
    bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)
    bot.send_message(message.chat.id, "Yes, captain! What is the text of the next phrase?")


# Step 4: cancel option
@bot.message_handler(func=lambda m: m.text.lower().strip() in ["cancel", "❌ cancel"], state="*")
def process_cancel(message):
    bot.delete_state(message.from_user.id, message.chat.id)
    with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
        data.clear()
    bot.send_message(message.chat.id, "❌ Cancelled.")


# Step 4: invalid option (fallback)
@bot.message_handler(state=QuoteState.waiting_for_next_step)
def process_next_step_incorrect_option(message):
    bot.reply_to(message, "I don't know what to do with that. Let's try again.")
    bot.send_message(message.chat.id, "Do you want to add a new phrase or finalize the quote?")
    bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)


# ------------------ Run ------------------

# Start polling loop (runs until stopped)
bot.infinity_polling()
