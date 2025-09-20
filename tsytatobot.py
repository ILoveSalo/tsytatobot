import os
from datetime import datetime

from dotenv import load_dotenv
import telebot
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from telebot import custom_filters
from telebot.states.sync.middleware import StateMiddleware
from telebot import types

from domain.phrase import Phrase
from domain.quote import Quote
from domain.speaker import Speaker
from persistence.impl.local_files.json.json_speaker_repository import JsonSpeakerRepository
from persistence.speaker_repository import SpeakerRepository

# ------------------ Infrastructure ------------------

# Load environment variables from .env file
load_dotenv()

# Helper function: reads an env variable or throws if missing
def read_env_variable(env_variable_name):
    result = os.getenv(env_variable_name)
    if not result:
        raise ValueError(env_variable_name + " was not found!")
    return result

# Read bot credentials/config from environment
BOT_TOKEN = read_env_variable('BOT_TOKEN')
CHANNEL_ID = read_env_variable('CHANNEL_ID')

# Use in-memory state storage (lost on restart)
storage = StateMemoryStorage()

# Initialize bot with middleware for states
bot = telebot.TeleBot(BOT_TOKEN, state_storage=storage, use_class_middlewares=True)
bot.add_custom_filter(custom_filters.StateFilter(bot))  # allow @message_handler(state=...) decorators
bot.setup_middleware(StateMiddleware(bot))              # middleware for state transitions

#Initialize speaker repository
speaker_repository: SpeakerRepository = JsonSpeakerRepository("speakers.json")


# ------------------ States ------------------

# Conversation states for quotes
class QuoteState(StatesGroup):
    waiting_for_date = State()         # step 1: waiting for date
    waiting_for_next_step = State()    # after phrase+speaker, ask: add more or finalize?

# Conversation states for phrases
class PhraseState(StatesGroup):
    waiting_for_text = State()         # waiting for phrase text
    waiting_for_context_text = State() # placeholder for future context input

# Conversation states for speakers
class SpeakerState(StatesGroup):
    waiting_for_name = State()         # waiting for speaker’s name


# ------------------ Utils ------------------

# Convert string (like "today" or "25.06.2005") → datetime
def parse_string_to_date(date: str):
    if date.lower().strip() == "today" or date.lower().strip() == "📅 today":
        return datetime.today()
    return datetime.strptime(date, "%d.%m.%Y")

# Convert datetime → "dd.mm.yyyy" string
def parse_date_to_string(date: datetime):
    return date.strftime('%d.%m.%Y')

# Retrieve stored Quote object from current user's state
def get_quote_from_state(user_id, chat_id):
    with bot.retrieve_data(user_id, chat_id) as data:
        return data.get("quote")

def get_unique_names(quote: Quote):
    used_speakers = set()
    for phrase in quote.phrases:
        name = phrase.speaker.name
        used_speakers.add(name)
    return used_speakers

# Generate hashtags from all speakers in a quote
def generate_tags(quote: Quote):
    result = ""
    used_speakers = get_unique_names(quote)

    for speaker in used_speakers:
        result += f"#{speaker} "
    return result

# Generate human-readable text for a quote
def generate_quote(quote: Quote):
    if len(quote.phrases) == 1:
        # single phrase → "text" - speaker, date
        return (f"\"{quote.phrases[0].text}\" - {quote.phrases[0].speaker.name}, {parse_date_to_string(quote.date)}\n"
                f"{generate_tags(quote)}")

    # multiple phrases → print as dialogue
    result = ""
    for phrase in quote.phrases:
        result += f"{phrase.speaker.name}: {phrase.text}\n"
    result += (f"{parse_date_to_string(quote.date)}\n"
               f"{generate_tags(quote)}")

    return result


# ------------------ Handlers ------------------

# Entry point: /quote → start quote creation
@bot.message_handler(commands=['quote'])
def create_quote(message):
    bot.reply_to(message, "Nice! Let's create a new quote!")

    # Create a keyboard
    keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    # Add buttons
    answers = ["📅 Today"]
    buttons = [types.KeyboardButton(answer) for answer in answers]
    keyboard.add(*buttons)

    bot.send_message(message.chat.id, "Let's start with the date. When did you hear these words? (dd.mm.yyyy or today)", reply_markup=keyboard)
    # Move state machine → waiting for date
    bot.set_state(message.from_user.id, QuoteState.waiting_for_date, message.chat.id)


# Step 1: process date input
@bot.message_handler(state=QuoteState.waiting_for_date)
def process_quote_date(message):
    try:
        date = parse_string_to_date(message.text)
    except:
        # Invalid date → re-ask
        bot.reply_to(message, "I don't know what to do with that. Your date should look like this: 25.06.2005. Let's try again.")
        bot.set_state(message.from_user.id, QuoteState.waiting_for_date, message.chat.id)
        return

    # Save empty quote with this date
    bot.add_data(message.from_user.id, message.chat.id, quote=Quote([], date))
    # Ask for first phrase
    bot.send_message(message.chat.id, f"Cool! So, what was said on {parse_date_to_string(date)}?")
    # Next state → waiting for phrase
    bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)


# Step 2: process phrase text
@bot.message_handler(state=PhraseState.waiting_for_text)
def process_phrase_text(message):
    text = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Add phrase with placeholder speaker (will set later)
    quote.phrases.append(Phrase(None, text, None))
    bot.add_data(message.from_user.id, message.chat.id, quote=quote)

    # Next state → waiting for speaker name
    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name, message.chat.id)

    #Get known speakers
    speakers = speaker_repository.get_speakers()
    answers = list()
    for speaker in speakers:
        answers.append(speaker.name)

    # Create a keyboard
    keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    # Add buttons
    buttons = [types.KeyboardButton(answer) for answer in answers]
    keyboard.add(*buttons)


    bot.send_message(
        message.chat.id,
        f"\"{text}\", such wise words! ദ്ദി(˵ •̀ ᴗ - ˵ ) ✧\n"
        f"Now, who is the wise person that said this?",
        reply_markup=keyboard
    )


# Step 3: process speaker name
@bot.message_handler(state=SpeakerState.waiting_for_name)
def process_speaker_name(message):
    name = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Attach speaker to last phrase
    quote.phrases[-1].speaker = Speaker(name)
    bot.add_data(message.from_user.id, message.chat.id, quote=quote)

    #Save speaker for future use
    speaker_repository.save_speaker(quote.phrases[-1].speaker)

    # Next state → ask user what to do next
    bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)

    # Show preview
    bot.send_message(
        message.chat.id,
        f"Did {name} really say that??? (¬_¬\")\n"
        f"Your phrase is:"
    )
    bot.send_message(message.chat.id, generate_quote(quote))

    # Create a keyboard
    keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    # Add buttons
    answers = ["➕ Add", "✔️ Finalize"]
    buttons = [types.KeyboardButton(answer) for answer in answers]
    keyboard.add(*buttons)

    # Offer choices
    bot.send_message(message.chat.id, "Do you want to add a new phrase or finalize the quote?", reply_markup=keyboard)


# Step 4: next step choice → add another phrase OR finalize
@bot.message_handler(state=QuoteState.waiting_for_next_step)
def process_next_step(message):
    next_step = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    if next_step.lower().strip() == "finalize" or next_step.lower().strip() == "✔️ finalize":
        # Send final quote to channel
        bot.send_message(CHANNEL_ID, generate_quote(quote))
        bot.send_message(message.chat.id, "Done! (⸝⸝> ᴗ•⸝⸝)")
        # End conversation
        bot.delete_state(message.from_user.id, message.chat.id)
        return

    if next_step.lower().strip() != "add" and next_step.lower().strip() != "➕ add":
        # Invalid option → re-ask
        bot.reply_to(message, "I don't know what to do with that. Let's try again.")
        bot.send_message(message.chat.id, "Do you want to add a new phrase or finalize the quote?")
        # Stay in same state
        bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)
        return

    # If user chose "add" → back to phrase input
    bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)
    bot.send_message(message.chat.id, "Yes, captain! What is the text of the next phrase?")


# ------------------ Run ------------------

# Run bot forever (polling loop)
bot.infinity_polling()
