import io
import math
import os

from PIL import Image, ImageDraw, ImageFont
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
from quote_generator.quote_image_generator import QuoteImageGenerator
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
quote_image_generator = QuoteImageGenerator(quote_text_generator, date_parser)

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
    waiting_for_name_end = State()
    waiting_for_if_add_image_answer = State()
    waiting_for_speaker_image = State()


# ------------------ Utils ------------------

# Retrieve Quote object from current user’s state
def get_quote_from_state(user_id, chat_id):
    with bot.retrieve_data(user_id, chat_id) as data:
        return data.get("quote")

# Retrieve bypass image variable from current user’s state
def get_bypass_image_from_state(user_id, chat_id):
    with bot.retrieve_data(user_id, chat_id) as data:
        return data.get("bypass_image")

def download_sticker_images_for_quote(quote: Quote):
    indexed_images = {}

    for phrase in quote.phrases:
        if phrase.speaker.speaker_image_id is None:
            continue

        # 1. Download sticker from Telegram
        file_info = bot.get_file(phrase.speaker.speaker_image_id)
        downloaded_file = bot.download_file(file_info.file_path)

        indexed_images[phrase.speaker.speaker_image_id] = downloaded_file

    return indexed_images

def get_quote_text_and_image(quote: Quote):
    indexed_images = download_sticker_images_for_quote(quote)

    image = quote_image_generator.generate_quote_image(quote, indexed_images)
    quote_text = quote_text_generator.generate_quote_with_tags(quote)

    return quote_text, image

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
@bot.message_handler(func=lambda m: speaker_repository.get_speaker(m.text) is None, state=SpeakerState.waiting_for_name)
def process_new_speaker_name(message):
    name = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Create and attach speaker to last phrase
    quote.phrases[-1].speaker = Speaker(name)

    # Save speaker for future suggestions
    speaker_repository.save_speaker(quote.phrases[-1].speaker)

    bot.add_data(message.from_user.id, message.chat.id, quote=quote)

    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name_end, message.chat.id)
    continue_after_speaker_set(message)

@bot.message_handler(func=lambda m: speaker_repository.get_speaker(m.text) is not None, state=SpeakerState.waiting_for_name)
def process_existing_speaker_name(message):
    name = message.text
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    # Get speaker
    speaker = speaker_repository.get_speaker(name)

    # Attach speaker to last phrase
    quote.phrases[-1].speaker = speaker

    bot.add_data(message.from_user.id, message.chat.id, quote=quote)

    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name_end, message.chat.id)
    continue_after_speaker_set(message)

def continue_after_speaker_set(message):
    quote = get_quote_from_state(message.from_user.id, message.chat.id)
    speaker = quote.phrases[-1].speaker

    if speaker.speaker_image_id is None and not get_bypass_image_from_state(message.from_user.id, message.chat.id):
        bot.set_state(message.from_user.id, SpeakerState.waiting_for_if_add_image_answer, message.chat.id)
        keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
        keyboard.add(types.KeyboardButton("✔️ Yes"), types.KeyboardButton("❌ No"))
        bot.send_message(message.chat.id, f"Seems like {speaker.name} has no image. Do you want to add one?", reply_markup=keyboard)
    else:
        bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)
        process_speaker_name_end(message)

@bot.message_handler(func=lambda m: m.text.lower().strip() in ["yes", "✔️ yes"], state=SpeakerState.waiting_for_if_add_image_answer)
def process_speaker_id_add_image_answer_yes(message):
    bot.set_state(message.from_user.id, SpeakerState.waiting_for_speaker_image, message.chat.id)
    bot.send_message(message.chat.id, "Awesome! Send me a sticker and I will add it to this person! 📸")

@bot.message_handler(func=lambda m: m.text.lower().strip() in ["no", "❌ no"], state=SpeakerState.waiting_for_if_add_image_answer)
def process_speaker_id_add_image_answer_no(message):
    bot.add_data(message.from_user.id, message.chat.id, bypass_image=True)
    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name_end, message.chat.id)
    bot.send_message(message.chat.id, "That's unfortunate(( Ok, let's continue.")
    continue_after_speaker_set(message)

@bot.message_handler(state=SpeakerState.waiting_for_if_add_image_answer)
def process_speaker_id_add_image_answer_unknown(message):
    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name_end, message.chat.id)
    bot.send_message(message.chat.id, "...What? Next time say Yes ot No, please.")

@bot.message_handler(state=SpeakerState.waiting_for_speaker_image, content_types=['sticker'])
def process_speaker_image(message):
    sticker_id = message.sticker.file_id
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    quote.phrases[-1].speaker.speaker_image_id = sticker_id
    speaker_repository.save_speaker(quote.phrases[-1].speaker)

    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name_end, message.chat.id)

    bot.add_data(message.from_user.id, message.chat.id, quote=quote)

    process_speaker_name_end(message)

@bot.message_handler(state=SpeakerState.waiting_for_speaker_image)
def process_speaker_image_unknown(message):
    bot.send_message(message.chat.id, "Send a sticker, please.")

@bot.message_handler(
    func=lambda m: get_quote_from_state(m.from_user.id, m.chat.id).phrases[-1].speaker.speaker_image_id is not None or
                   get_bypass_image_from_state(m.from_user.id, m.chat.id) is True,
    state=SpeakerState.waiting_for_name_end)
def process_speaker_name_end(message):
    quote = get_quote_from_state(message.from_user.id, message.chat.id)
    name = quote.phrases[-1].speaker.name

    # Move to "what next?" state
    bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)

    quote_text, image = get_quote_text_and_image(quote)

    # Show preview
    bot.send_message(message.chat.id, f"Did {name} really say that??? (¬_¬\")\nYour phrase is:")
    bot.send_photo(message.chat.id, photo=image, caption=quote_text)
    #bot.send_message(message.chat.id, quote_text_generator.generate_quote_with_tags(quote))

    # Offer choices: add, finalize, cancel
    keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    buttons = [types.KeyboardButton(x) for x in ["➕ Add", "✔️ Finalize", "❌ Cancel"]]
    keyboard.add(*buttons)

    bot.send_message(message.chat.id, "Do you want to add a new phrase or finalize the quote?", reply_markup=keyboard)

# Step 4: finalize option
@bot.message_handler(func=lambda m: m.text.lower().strip() in ["finalize", "✔️ finalize"], state=QuoteState.waiting_for_next_step)
def process_next_step_finalize_option(message):
    quote = get_quote_from_state(message.from_user.id, message.chat.id)

    quote_text, image = get_quote_text_and_image(quote)

    # Post final quote to channel
    #bot.send_message(CHANNEL_ID, quote_text_generator.generate_quote_with_tags(quote))
    bot.send_photo(CHANNEL_ID, photo=image, caption=quote_text)
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

# def crop_transparency(img: Image.Image) -> Image.Image:
#     """Crop transparent borders from RGBA image."""
#     if img.mode != "RGBA":
#         img = img.convert("RGBA")
#     bbox = img.getbbox()  # bounding box of non-zero regions in alpha
#     if bbox:
#         return img.crop(bbox)
#     return img
#
#
# def create_radial_gradient(size, inner_color, outer_color):
#     """Generate a radial gradient image (RGBA)."""
#     width, height = size
#     center_x, center_y = width // 2, height // 2
#     max_radius = math.sqrt(center_x**2 + center_y**2)
#
#     gradient = Image.new("RGBA", (width, height), outer_color)
#     draw = ImageDraw.Draw(gradient)
#
#     for y in range(height):
#         for x in range(width):
#             # Distance from center
#             dx = x - center_x
#             dy = y - center_y
#             dist = math.sqrt(dx*dx + dy*dy) / max_radius
#
#             # Clamp [0..1]
#             dist = min(1, dist)
#
#             # Interpolate colors
#             r = int(inner_color[0] + (outer_color[0] - inner_color[0]) * dist)
#             g = int(inner_color[1] + (outer_color[1] - inner_color[1]) * dist)
#             b = int(inner_color[2] + (outer_color[2] - inner_color[2]) * dist)
#             a = int(inner_color[3] + (outer_color[3] - inner_color[3]) * dist)
#
#             draw.point((x, y), (r, g, b, a))
#
#     return gradient
#
# @bot.message_handler(content_types=['sticker'])
# def handle_sticker(message):
#     # 1. Download sticker from Telegram
#     file_info = bot.get_file(message.sticker.file_id)
#     downloaded_file = bot.download_file(file_info.file_path)
#
#     # 2. Open sticker (usually .webp with alpha channel)
#     sticker_img = Image.open(io.BytesIO(downloaded_file)).convert("RGBA")
#     quote_sign_img = Image.open("assets/quote-sign.png").convert("RGBA")
#     scroll_img = Image.open("assets/scroll.png").convert("RGBA")
#
#     # --- Crop empty space ---
#     sticker_img = crop_transparency(sticker_img)
#
#     # 3. Create radial gradient background
#     new_width = 1100
#     new_height = 512
#     canvas = create_radial_gradient(
#         (new_width, new_height),
#         inner_color=(44, 211, 189, 255),  # center color
#         outer_color=(36, 129, 117, 255)  # edge color
#     )
#
#     #canvas = Image.new("RGBA", (new_width, new_height), (36, 129, 117, 255))  # transparent background
#
#     # --- Make second sticker zoomed + semitransparent ---
#     zoom_factor = 1.7
#     zoomed_size = (int(sticker_img.width * zoom_factor), int(sticker_img.height * zoom_factor))
#     sticker_zoomed = sticker_img.resize(zoomed_size, Image.LANCZOS)
#
#     # Convert zoomed image to grayscale but keep alpha
#     sticker_zoomed = sticker_zoomed.convert("LA").convert("RGBA")
#
#     # Reduce opacity
#     sticker_alpha = sticker_zoomed.split()[3]  # get alpha channel
#     sticker_alpha = sticker_alpha.point(lambda p: p * 0.25)  # 25% transparent
#     sticker_zoomed.putalpha(sticker_alpha)
#
#     # Reduce opacity
#     scroll_alpha = scroll_img.split()[3]  # get alpha channel
#     scroll_alpha = scroll_alpha.point(lambda p: p * 0.33)  # 33% transparent
#     scroll_img.putalpha(scroll_alpha)
#
#     # Ensure canvas is RGBA
#     canvas = canvas.convert("RGBA")
#
#     # 1️⃣ Scroll image layer
#     scroll_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
#     scroll_layer.paste(scroll_img, (new_width - scroll_img.width - 10, 0), scroll_img)
#     canvas = Image.alpha_composite(canvas, scroll_layer)
#
#     # 2️⃣ Zoomed sticker layer
#     canvas.paste(sticker_zoomed, (512 - zoomed_size[0], 0), sticker_zoomed)
#
#     # 3️⃣ Original sticker layer (bottom)
#     sticker_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
#     sticker_layer.paste(sticker_img, (0, new_height - sticker_img.height), sticker_img)
#     canvas = Image.alpha_composite(canvas, sticker_layer)
#
#     # 4. Draw text
#     draw = ImageDraw.Draw(canvas)
#     try:
#         font = ImageFont.truetype("assets/SourceSans3-BoldItalic.ttf", 60)  # needs Arial installed
#     except:
#         font = ImageFont.load_default()
#
#     text = "Верни очко!"
#     text_width, text_height = draw.textbbox((0, 0), text, font=font)[2:]
#     x = 512 + (new_width - 512 - text_width) // 2
#     y = (new_height - text_height) // 2
#     draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
#
#     # 4b. Draw second text
#     try:
#         font2 = ImageFont.truetype("assets/Gabriela-Regular.ttf", 40)  # smaller
#     except:
#         font2 = ImageFont.load_default()
#
#     text2 = "Ігорьочок"
#     text2_width, text2_height = draw.textbbox((0, 0), text2, font=font2)[2:]
#     x2 = 512 + 20
#     y2 = new_height - text2_height - 10  # 10 px space below first text
#     draw.text((x2, y2), text2, font=font2, fill=(255, 255, 255, 255))
#
#     # 4c. Draw third text
#     text3 = "21.09.2025"
#     text3_width, text3_height = draw.textbbox((0, 0), text2, font=font2)[2:]
#     x3 = new_width - text3_width - 40
#     y3 = new_height - text3_height - 10  # 10 px space below first text
#     draw.text((x3, y3), text3, font=font2, fill=(255, 255, 255, 255))
#
#     # 4️⃣ Quote sign (if you want it on top)
#     quote_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
#     quote_layer.paste(quote_sign_img, (x - 15, y - quote_sign_img.height), quote_sign_img)
#     canvas = Image.alpha_composite(canvas, quote_layer)
#
#     # 5. Save to memory as PNG
#     output = io.BytesIO()
#     output.name = "sticker_with_text.png"
#     canvas.save(output, format="PNG")
#     output.seek(0)
#
#     # 6. Send as photo
#     #bot.send_document(message.chat.id, output, visible_file_name="sticker_with_text.png")
#     bot.send_photo(message.chat.id, photo=output, caption="Here’s your sticker as an image 📸")


# ------------------ Run ------------------

# Start polling loop (runs until stopped)
bot.infinity_polling()
