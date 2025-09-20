import os
from datetime import datetime

from dotenv import load_dotenv
import telebot
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from telebot import custom_filters
from telebot.states.sync.middleware import StateMiddleware

from domain.phrase import Phrase
from domain.quote import Quote
from domain.speaker import Speaker


# ------------------ Infrastructure ------------------

# Load environment variables from .env
load_dotenv()

# Helper to read environment variable or raise an error if missing
def read_env_variable(env_variable_name):
    result = os.getenv(env_variable_name)
    if not result:
        raise ValueError(env_variable_name + " was not found!")
    return result

# Read bot token and channel id from .env
BOT_TOKEN = read_env_variable('BOT_TOKEN')
CHANNEL_ID = read_env_variable('CHANNEL_ID')

# State storage (in-memory) – good for development, use Redis in production
storage = StateMemoryStorage()

# Initialize bot with state middleware
bot = telebot.TeleBot(BOT_TOKEN, state_storage=storage, use_class_middlewares=True)
bot.add_custom_filter(custom_filters.StateFilter(bot))
bot.setup_middleware(StateMiddleware(bot))


# ------------------ States ------------------

# Define states for different stages of the conversation
class QuoteState(StatesGroup):
    waiting_for_date = State()         # expecting date of the quote

class PhraseState(StatesGroup):
    waiting_for_text = State()         # expecting phrase text
    waiting_for_context_text = State() # (optional future use) expecting context

class SpeakerState(StatesGroup):
    waiting_for_name = State()         # expecting speaker name


# ------------------ Utils ------------------

# Parse string into datetime
def parse_string_to_date(date: str):
    if date == "today":
        return datetime.today()
    return datetime.strptime(date, "%d.%m.%Y")

# Format datetime into dd.mm.yyyy string
def parse_date_to_string(date: datetime):
    return date.strftime('%d.%m.%Y')

# Retrieve stored Quote object from state
def get_quote_from_state(user_id, chat_id):
    with bot.retrieve_data(user_id, chat_id) as data:
        return data.get("quote")

# Generate formatted text representation of a Quote
def generate_quote(quote: Quote):
    if len(quote.phrases) == 1:  # if only one phrase
        return f"\"{quote.phrases[0].text}\" - {quote.phrases[0].speaker.name}, {parse_date_to_string(quote.date)}"

    # If multiple phrases → build a dialogue-like output
    result = ""
    for phrase in quote.phrases:
        result += f"{phrase.speaker}: {phrase.text}\n"
    result += f"{parse_date_to_string(quote.date)}"

    return result


# ------------------ Handlers ------------------

# Command /quote → start the conversation
@bot.message_handler(commands=['quote'])
def create_quote(message):
    bot.reply_to(message, "Nice! Let's create a new quote!")
    bot.send_message(message.chat.id, "Let's start with the date. When did you hear these words? (dd.mm.yyyy or today)")
    # Switch state → waiting for date
    bot.set_state(message.from_user.id, QuoteState.waiting_for_date, message.chat.id)


# Handle quote date
@bot.message_handler(state=QuoteState.waiting_for_date)
def process_quote_date(message):
    date = parse_string_to_date(message.text)
    # Switch state → waiting for phrase text
    bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)
    # Store empty Quote object in state
    bot.add_data(message.from_user.id, message.chat.id, quote=Quote([], date))
    bot.send_message(message.chat.id, f"Cool! So, what was said on {parse_date_to_string(date)}?")


# Handle phrase text
@bot.message_handler(state=PhraseState.waiting_for_text)
def process_phrase_text(message):
    text = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Add new phrase to the quote (speaker is None for now)
    quote.phrases.append(Phrase(None, text, None))

    # Switch state → waiting for speaker name
    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name, message.chat.id)
    # Update state with modified quote
    bot.add_data(message.from_user.id, message.chat.id, quote=quote)

    bot.send_message(
        message.chat.id,
        f"\"{text}\", such wise words! ദ്ദി(˵ •̀ ᴗ - ˵ ) ✧\n"
        f"Now, who is the wise person that said this?"
    )


# Handle speaker name
@bot.message_handler(state=SpeakerState.waiting_for_name)
def process_speaker_name(message):
    name = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Update last phrase with the speaker name
    quote.phrases[-1].speaker = Speaker(name)

    # Optionally store the speaker name separately
    bot.add_data(message.from_user.id, message.chat.id, speaker_name=name)

    # Send the final result back
    bot.send_message(
        message.chat.id,
        f"Did {name} really say that??? (¬_¬\")\n"
        f"Your phrase is: \n\n{generate_quote(quote)}"
    )

    # Clear state (conversation finished)
    bot.delete_state(message.from_user.id, message.chat.id)


# ------------------ Run ------------------

# Start bot polling (infinite loop)
bot.infinity_polling()
