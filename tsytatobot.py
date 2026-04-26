import io
import os
from datetime import datetime

import telebot
from dotenv import load_dotenv
from telebot import custom_filters, types
from telebot.apihelper import ApiTelegramException
from telebot.handler_backends import State, StatesGroup
from telebot.states.sync.middleware import StateMiddleware
from telebot.storage import StateMemoryStorage, StatePickleStorage

from date_parser.date_parser import DateParser
from domain.phrase import Phrase
from domain.quote import Quote
from domain.speaker import Speaker
from persistence.impl.local_files.json.json_speaker_repository import JsonSpeakerRepository
from persistence.speaker_repository import SpeakerRepository
from quote_generator.quote_image_generator import QuoteImageGenerator
from quote_generator.quote_text_generator import QuoteTextGenerator


# ------------------ Infrastructure ------------------

load_dotenv()


def read_env_variable(env_variable_name: str) -> str:
    result = os.getenv(env_variable_name)
    if not result:
        raise ValueError(f"{env_variable_name} was not found!")
    return result


BOT_TOKEN = read_env_variable("BOT_TOKEN")
CHANNEL_ID = read_env_variable("CHANNEL_ID")

STATE_STORAGE_TYPE = os.getenv("STATE_STORAGE", "memory").strip().lower()
if STATE_STORAGE_TYPE == "pickle":
    state_file_path = os.getenv("STATE_FILE_PATH", ".bot_state.pkl")
    storage = StatePickleStorage(file_path=state_file_path)
else:
    storage = StateMemoryStorage()
bot = telebot.TeleBot(BOT_TOKEN, state_storage=storage, use_class_middlewares=True)
bot.add_custom_filter(custom_filters.StateFilter(bot))
bot.setup_middleware(StateMiddleware(bot))

speaker_repository: SpeakerRepository = JsonSpeakerRepository("speakers.json")

date_parser = DateParser()
quote_text_generator = QuoteTextGenerator(date_parser)
quote_image_generator = QuoteImageGenerator(quote_text_generator, date_parser)

# Cache downloaded sticker bytes by Telegram file_id.
sticker_file_cache: dict[str, bytes] = {}


# ------------------ States ------------------

class QuoteState(StatesGroup):
    waiting_for_date = State()
    waiting_for_phrase_image_selection = State()
    waiting_for_speaker_image_decision = State()
    waiting_for_speaker_image_sticker = State()
    waiting_for_context_decision = State()
    waiting_for_main_speaker = State()
    waiting_for_next_step = State()


class PhraseState(StatesGroup):
    waiting_for_text = State()
    waiting_for_context_text = State()
    waiting_for_edit_last_text = State()


class SpeakerState(StatesGroup):
    waiting_for_name = State()


class SpeakerEditState(StatesGroup):
    waiting_for_speaker_name = State()
    waiting_for_action = State()
    waiting_for_new_name = State()
    waiting_for_new_image = State()


# ------------------ Callback data ------------------

DATE_TODAY = "quote:date:today"
PHRASE_IMAGE_PREFIX = "quote:phrase_image:"
PHRASE_IMAGE_NONE = "quote:phrase_image:none"
ADD_SPEAKER_IMAGE_YES = "quote:add_speaker_image:yes"
ADD_SPEAKER_IMAGE_NO = "quote:add_speaker_image:no"
ADD_SPEAKER_IMAGE_CANCEL = "quote:add_speaker_image:cancel"
ADD_CONTEXT_YES = "quote:add_context:yes"
ADD_CONTEXT_NO = "quote:add_context:no"
MAIN_SELECT_PREFIX = "quote:main:"
NEXT_ADD = "quote:next:add"
NEXT_MAIN = "quote:next:main"
NEXT_FINALIZE = "quote:next:finalize"
NEXT_CANCEL = "quote:next:cancel"

EDIT_ACTION_RENAME = "edit_speaker:action:rename"
EDIT_ACTION_ADD_IMAGE = "edit_speaker:action:add_image"
EDIT_ACTION_SET_PRIMARY = "edit_speaker:action:set_primary"
EDIT_ACTION_REMOVE_IMAGE = "edit_speaker:action:remove_image"
EDIT_ACTION_DONE = "edit_speaker:action:done"
EDIT_SELECT_PREFIX = "edit_speaker:select:"
EDIT_SELECT_CANCEL = "edit_speaker:select:cancel"
EDIT_SET_PRIMARY_PREFIX = "edit_speaker:set_primary:"
EDIT_REMOVE_IMAGE_PREFIX = "edit_speaker:remove_image:"


# ------------------ Utils ------------------

def clear_session(user_id: int, chat_id: int) -> None:
    bot.delete_state(user_id, chat_id)
    with bot.retrieve_data(user_id, chat_id) as data:
        data.clear()


def get_quote_from_state(user_id: int, chat_id: int) -> Quote | None:
    with bot.retrieve_data(user_id, chat_id) as data:
        return data.get("quote")


def set_quote_to_state(user_id: int, chat_id: int, quote: Quote) -> None:
    bot.add_data(user_id, chat_id, quote=quote)


def clear_edit_speaker_state_data(user_id: int, chat_id: int) -> None:
    with bot.retrieve_data(user_id, chat_id) as data:
        data.pop("edit_speaker_name", None)


def set_edit_speaker_name_to_state(user_id: int, chat_id: int, speaker_name: str) -> None:
    bot.add_data(user_id, chat_id, edit_speaker_name=speaker_name)


def get_edit_speaker_name_from_state(user_id: int, chat_id: int) -> str | None:
    with bot.retrieve_data(user_id, chat_id) as data:
        return data.get("edit_speaker_name")


def get_edit_speaker_from_state(user_id: int, chat_id: int) -> Speaker | None:
    speaker_name = get_edit_speaker_name_from_state(user_id, chat_id)
    if not speaker_name:
        return None
    return speaker_repository.get_speaker(speaker_name, chat_id=chat_id)


def get_speaker_display_name_from_user(user: types.User) -> str:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    if full_name:
        return full_name
    return user.username or str(user.id)


def get_or_create_speaker(name: str, chat_id: int) -> Speaker:
    normalized_name = " ".join(name.split()).strip()
    existing = speaker_repository.get_speaker(normalized_name, chat_id=chat_id)
    if existing:
        return existing

    speaker = Speaker(name=normalized_name)
    speaker_repository.save_speaker(speaker, chat_id=chat_id)
    return speaker


def get_unique_speakers_from_quote(quote: Quote) -> list[Speaker]:
    seen: set[str] = set()
    unique: list[Speaker] = []
    for phrase in quote.phrases:
        if not phrase.speaker:
            continue
        key = phrase.speaker.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(phrase.speaker)
    return unique


def ensure_main_speaker_name(quote: Quote) -> None:
    unique_speakers = get_unique_speakers_from_quote(quote)
    if not unique_speakers:
        quote.main_speaker_name = None
        return

    if quote.main_speaker_name:
        current_key = quote.main_speaker_name.casefold()
        for speaker in unique_speakers:
            if speaker.name.casefold() == current_key:
                quote.main_speaker_name = speaker.name
                return

    quote.main_speaker_name = unique_speakers[0].name


def build_quote_signature(quote: Quote) -> tuple:
    phrase_signature = tuple(
        (
            phrase.text,
            phrase.context_text,
            phrase.speaker_image_id,
            phrase.speaker.name if phrase.speaker else None,
            tuple(phrase.speaker.speaker_image_ids) if phrase.speaker else tuple(),
        )
        for phrase in quote.phrases
    )
    return quote.date.isoformat(), quote.main_speaker_name, phrase_signature


def bytes_to_image_io(image_bytes: bytes, name: str = "quote.png") -> io.BytesIO:
    output = io.BytesIO(image_bytes)
    output.name = name
    output.seek(0)
    return output


def download_sticker_images_for_quote(quote: Quote) -> dict[str, bytes]:
    indexed_images: dict[str, bytes] = {}
    for phrase in quote.phrases:
        if not phrase.speaker:
            continue

        file_id = phrase.speaker_image_id or phrase.speaker.speaker_image_id
        if not file_id:
            continue

        if file_id in indexed_images:
            continue

        if file_id in sticker_file_cache:
            indexed_images[file_id] = sticker_file_cache[file_id]
            continue

        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        sticker_file_cache[file_id] = downloaded_file
        indexed_images[file_id] = downloaded_file

    return indexed_images


def get_quote_text_and_image(quote: Quote, user_id: int, chat_id: int) -> tuple[str, io.BytesIO]:
    signature = build_quote_signature(quote)

    with bot.retrieve_data(user_id, chat_id) as data:
        cached_signature = data.get("rendered_signature")
        cached_text = data.get("rendered_quote_text")
        cached_image = data.get("rendered_image_bytes")
        if cached_signature == signature and cached_text and cached_image:
            return cached_text, bytes_to_image_io(cached_image)

    indexed_images = download_sticker_images_for_quote(quote)
    image = quote_image_generator.generate_quote_image(quote, indexed_images)
    quote_text = quote_text_generator.generate_quote_with_tags(quote)

    image_bytes = image.getvalue()
    bot.add_data(
        user_id,
        chat_id,
        rendered_signature=signature,
        rendered_quote_text=quote_text,
        rendered_image_bytes=image_bytes,
    )

    return quote_text, bytes_to_image_io(image_bytes)


def reset_render_cache(user_id: int, chat_id: int) -> None:
    with bot.retrieve_data(user_id, chat_id) as data:
        data.pop("rendered_signature", None)
        data.pop("rendered_quote_text", None)
        data.pop("rendered_image_bytes", None)


def safe_send_message(chat_id: int | str, text: str, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except ApiTelegramException:
        return None


def safe_send_photo(chat_id: int | str, photo: io.BytesIO, caption: str | None = None, **kwargs):
    try:
        return bot.send_photo(chat_id, photo=photo, caption=caption, **kwargs)
    except ApiTelegramException:
        return None


def ensure_quote_exists_for_session(user_id: int, chat_id: int) -> Quote | None:
    quote = get_quote_from_state(user_id, chat_id)
    if quote is None:
        safe_send_message(chat_id, "There is no active quote. Use /quote to start one.")
    return quote


def ensure_quote_exists(message: types.Message) -> Quote | None:
    return ensure_quote_exists_for_session(message.from_user.id, message.chat.id)


def build_date_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📅 Today", callback_data=DATE_TODAY))
    return keyboard


def build_phrase_image_keyboard(speaker: Speaker) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    for index, _ in enumerate(speaker.speaker_image_ids):
        keyboard.add(types.InlineKeyboardButton(f"Image {index + 1}", callback_data=f"{PHRASE_IMAGE_PREFIX}{index}"))
    keyboard.add(types.InlineKeyboardButton("No image", callback_data=PHRASE_IMAGE_NONE))
    return keyboard


def build_add_speaker_image_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("Add sticker", callback_data=ADD_SPEAKER_IMAGE_YES),
        types.InlineKeyboardButton("Skip", callback_data=ADD_SPEAKER_IMAGE_NO),
    )
    keyboard.add(types.InlineKeyboardButton("Cancel", callback_data=ADD_SPEAKER_IMAGE_CANCEL))
    return keyboard


def build_add_context_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("Add context", callback_data=ADD_CONTEXT_YES),
        types.InlineKeyboardButton("Skip", callback_data=ADD_CONTEXT_NO),
    )
    return keyboard


def build_edit_speaker_selection_keyboard(speakers: list[Speaker]) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for index, speaker in enumerate(speakers):
        images_count = len(speaker.speaker_image_ids)
        image_label = "image" if images_count == 1 else "images"
        keyboard.add(
            types.InlineKeyboardButton(
                f"{speaker.name} ({images_count} {image_label})",
                callback_data=f"{EDIT_SELECT_PREFIX}{index}",
            )
        )
    keyboard.add(types.InlineKeyboardButton("Cancel", callback_data=EDIT_SELECT_CANCEL))
    return keyboard


def build_edit_speaker_actions_keyboard(speaker: Speaker) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("Rename", callback_data=EDIT_ACTION_RENAME),
        types.InlineKeyboardButton("Add image", callback_data=EDIT_ACTION_ADD_IMAGE),
    )
    keyboard.add(
        types.InlineKeyboardButton("Set primary", callback_data=EDIT_ACTION_SET_PRIMARY),
        types.InlineKeyboardButton("Remove image", callback_data=EDIT_ACTION_REMOVE_IMAGE),
    )
    keyboard.add(types.InlineKeyboardButton("Done", callback_data=EDIT_ACTION_DONE))
    return keyboard


def build_edit_speaker_image_keyboard(speaker: Speaker, callback_prefix: str) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    if not speaker.speaker_image_ids:
        return keyboard

    for index, _ in enumerate(speaker.speaker_image_ids):
        label = f"Image {index + 1}"
        if index == 0:
            label = f"⭐ {label}"
        keyboard.add(types.InlineKeyboardButton(label, callback_data=f"{callback_prefix}{index}"))
    return keyboard


def build_main_speaker_keyboard(quote: Quote) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    selected_key = quote.main_speaker_name.casefold() if quote.main_speaker_name else None

    for index, speaker in enumerate(get_unique_speakers_from_quote(quote)):
        label = speaker.name
        if selected_key and speaker.name.casefold() == selected_key:
            label = f"⭐ {label}"
        keyboard.add(types.InlineKeyboardButton(label, callback_data=f"{MAIN_SELECT_PREFIX}{index}"))

    return keyboard


def build_next_step_keyboard() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("⭐ Main speaker", callback_data=NEXT_MAIN),
        types.InlineKeyboardButton("➕ Add", callback_data=NEXT_ADD),
    )
    keyboard.add(
        types.InlineKeyboardButton("✅ Finalize", callback_data=NEXT_FINALIZE),
        types.InlineKeyboardButton("❌ Cancel", callback_data=NEXT_CANCEL),
    )
    return keyboard


def prompt_context_for_last_phrase(user_id: int, chat_id: int) -> None:
    quote = ensure_quote_exists_for_session(user_id, chat_id)
    if quote is None:
        return

    bot.set_state(user_id, QuoteState.waiting_for_context_decision, chat_id)
    safe_send_message(
        chat_id,
        "Do you want to add extra context to this phrase?",
        reply_markup=build_add_context_keyboard(),
    )


def prompt_add_speaker_image_for_last_phrase(user_id: int, chat_id: int, speaker: Speaker) -> None:
    bot.set_state(user_id, QuoteState.waiting_for_speaker_image_decision, chat_id)
    safe_send_message(
        chat_id,
        f"Speaker {speaker.name} has no image. Add one from a sticker?",
        reply_markup=build_add_speaker_image_keyboard(),
    )


def prompt_phrase_image_for_last_phrase(user_id: int, chat_id: int) -> None:
    quote = ensure_quote_exists_for_session(user_id, chat_id)
    if quote is None or not quote.phrases:
        return

    phrase = quote.phrases[-1]
    speaker = phrase.speaker
    if speaker is None:
        prompt_context_for_last_phrase(user_id, chat_id)
        return

    if not speaker.speaker_image_ids:
        phrase.speaker_image_id = None
        set_quote_to_state(user_id, chat_id, quote)
        reset_render_cache(user_id, chat_id)
        prompt_add_speaker_image_for_last_phrase(user_id, chat_id, speaker)
        return

    if phrase.speaker_image_id not in speaker.speaker_image_ids:
        phrase.speaker_image_id = speaker.speaker_image_ids[0]
        set_quote_to_state(user_id, chat_id, quote)
        reset_render_cache(user_id, chat_id)

    bot.set_state(user_id, QuoteState.waiting_for_phrase_image_selection, chat_id)
    safe_send_message(
        chat_id,
        f"Choose image for {speaker.name} in this phrase:",
        reply_markup=build_phrase_image_keyboard(speaker),
    )


def prompt_edit_speaker_actions(user_id: int, chat_id: int, speaker: Speaker) -> None:
    set_edit_speaker_name_to_state(user_id, chat_id, speaker.name)
    bot.set_state(user_id, SpeakerEditState.waiting_for_action, chat_id)

    images_count = len(speaker.speaker_image_ids)
    safe_send_message(
        chat_id,
        f"Editing speaker: {speaker.name}\nImages: {images_count}",
        reply_markup=build_edit_speaker_actions_keyboard(speaker),
    )


def prompt_edit_speaker_selection(user_id: int, chat_id: int, intro: str = "Choose speaker to edit:") -> None:
    speakers = speaker_repository.get_speakers(chat_id=chat_id)
    if not speakers:
        bot.delete_state(user_id, chat_id)
        clear_edit_speaker_state_data(user_id, chat_id)
        safe_send_message(chat_id, "No speakers found in this chat yet.")
        return

    bot.set_state(user_id, SpeakerEditState.waiting_for_speaker_name, chat_id)
    safe_send_message(
        chat_id,
        intro,
        reply_markup=build_edit_speaker_selection_keyboard(speakers),
    )


def continue_after_speaker_set(message: types.Message) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id
    quote = ensure_quote_exists_for_session(user_id, chat_id)
    if quote is None:
        return

    speaker = quote.phrases[-1].speaker
    if speaker is None:
        safe_send_message(chat_id, "Speaker is missing. Please send the speaker name.")
        bot.set_state(user_id, SpeakerState.waiting_for_name, chat_id)
        return

    prompt_phrase_image_for_last_phrase(user_id, chat_id)


def process_speaker_name_end(user_id: int, chat_id: int) -> None:
    quote = ensure_quote_exists_for_session(user_id, chat_id)
    if quote is None:
        return

    name = quote.phrases[-1].speaker.name if quote.phrases[-1].speaker else "Unknown"
    bot.set_state(user_id, QuoteState.waiting_for_next_step, chat_id)

    try:
        quote_text, image = get_quote_text_and_image(quote, user_id, chat_id)
    except ApiTelegramException:
        safe_send_message(chat_id, "Failed to build preview because Telegram API request failed.")
        return
    except Exception:
        safe_send_message(chat_id, "Failed to build preview due to image rendering error.")
        return

    safe_send_message(chat_id, f"Did {name} really say that? Your quote preview:")
    safe_send_photo(chat_id, photo=image, caption=quote_text)
    safe_send_message(
        chat_id,
        "Do you want to add a new phrase or finalize the quote?",
        reply_markup=build_next_step_keyboard(),
    )


def finalize_quote(user_id: int, chat_id: int) -> None:
    quote = get_quote_from_state(user_id, chat_id)
    if quote is None:
        safe_send_message(chat_id, "There is no active quote. Use /quote.")
        clear_session(user_id, chat_id)
        return

    try:
        quote_text, image = get_quote_text_and_image(quote, user_id, chat_id)
    except Exception:
        safe_send_message(chat_id, "Failed to finalize quote because preview data is unavailable.")
        return

    channel_message = safe_send_photo(CHANNEL_ID, photo=image, caption=quote_text)
    if channel_message is None:
        safe_send_message(chat_id, "Failed to post in channel. Check bot permissions and CHANNEL_ID.")
        return

    safe_send_message(chat_id, "Done.", reply_markup=types.ReplyKeyboardRemove())
    clear_session(user_id, chat_id)


# ------------------ Handlers ------------------

@bot.message_handler(commands=["quote"])
def create_quote(message: types.Message):
    clear_session(message.from_user.id, message.chat.id)
    safe_send_message(message.chat.id, "Let\'s create a new quote.")
    safe_send_message(
        message.chat.id,
        "When did you hear these words? Send date as dd.mm.yyyy or use the button.",
        reply_markup=build_date_keyboard(),
    )
    bot.set_state(message.from_user.id, QuoteState.waiting_for_date, message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == DATE_TODAY)
def process_today_callback(call: types.CallbackQuery):
    if bot.get_state(call.from_user.id, call.message.chat.id) != QuoteState.waiting_for_date.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    quote = Quote([], datetime.today())
    set_quote_to_state(call.from_user.id, call.message.chat.id, quote)

    safe_send_message(
        call.message.chat.id,
        f"Great. What was said on {date_parser.parse_date_to_string(quote.date)}?",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    bot.set_state(call.from_user.id, PhraseState.waiting_for_text, call.message.chat.id)


@bot.message_handler(state=QuoteState.waiting_for_date, content_types=["text"])
def process_quote_date(message: types.Message):
    try:
        date = date_parser.parse_string_to_date(message.text)
    except ValueError:
        safe_send_message(message.chat.id, "Invalid date. Example: 25.06.2005")
        bot.set_state(message.from_user.id, QuoteState.waiting_for_date, message.chat.id)
        return

    set_quote_to_state(message.from_user.id, message.chat.id, Quote([], date))
    safe_send_message(message.chat.id, f"Nice. What was said on {date_parser.parse_date_to_string(date)}?")
    bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)


@bot.message_handler(state=QuoteState.waiting_for_date)
def process_quote_date_non_text(message: types.Message):
    safe_send_message(message.chat.id, "Please send the date as text in dd.mm.yyyy format.")


@bot.message_handler(state=PhraseState.waiting_for_text, content_types=["text"])
def process_phrase_text(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None:
        return

    text = message.text.strip()
    if not text:
        safe_send_message(message.chat.id, "Phrase text cannot be empty.")
        return

    quote.phrases.append(Phrase(speaker=None, text=text))
    set_quote_to_state(message.from_user.id, message.chat.id, quote)
    reset_render_cache(message.from_user.id, message.chat.id)

    if message.reply_to_message and message.reply_to_message.from_user:
        speaker_name = get_speaker_display_name_from_user(message.reply_to_message.from_user)
        speaker = get_or_create_speaker(speaker_name, chat_id=message.chat.id)
        quote.phrases[-1].speaker = speaker
        ensure_main_speaker_name(quote)
        set_quote_to_state(message.from_user.id, message.chat.id, quote)

        safe_send_message(message.chat.id, f"Using replied user as speaker: {speaker.name}")
        continue_after_speaker_set(message)
        return

    bot.set_state(message.from_user.id, SpeakerState.waiting_for_name, message.chat.id)

    speakers = [speaker.name for speaker in speaker_repository.get_speakers(chat_id=message.chat.id)]
    keyboard = types.ReplyKeyboardMarkup(row_width=2, one_time_keyboard=True, resize_keyboard=True)
    for name in speakers[:30]:
        keyboard.add(types.KeyboardButton(name))

    safe_send_message(
        message.chat.id,
        f'"{text}"\nWho said this?',
        reply_markup=keyboard if speakers else types.ReplyKeyboardRemove(),
    )


@bot.message_handler(state=PhraseState.waiting_for_text)
def process_phrase_text_non_text(message: types.Message):
    safe_send_message(message.chat.id, "Please send phrase text.")


@bot.message_handler(state=SpeakerState.waiting_for_name, content_types=["text"])
def process_speaker_name(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None:
        return

    name = message.text.strip()
    if not name:
        safe_send_message(message.chat.id, "Speaker name cannot be empty.")
        return

    speaker = get_or_create_speaker(name, chat_id=message.chat.id)
    quote.phrases[-1].speaker = speaker
    ensure_main_speaker_name(quote)
    set_quote_to_state(message.from_user.id, message.chat.id, quote)
    reset_render_cache(message.from_user.id, message.chat.id)

    safe_send_message(message.chat.id, "Speaker saved.", reply_markup=types.ReplyKeyboardRemove())
    continue_after_speaker_set(message)


@bot.message_handler(state=SpeakerState.waiting_for_name)
def process_speaker_name_non_text(message: types.Message):
    safe_send_message(message.chat.id, "Please send the speaker name as text.")

@bot.message_handler(commands=["editspeaker"])
def process_edit_speaker_command(message: types.Message):
    clear_edit_speaker_state_data(message.from_user.id, message.chat.id)

    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) == 2 and command_parts[1].strip():
        speaker_name = command_parts[1].strip()
        speaker = speaker_repository.get_speaker(speaker_name, chat_id=message.chat.id)
        if speaker is None:
            prompt_edit_speaker_selection(
                message.from_user.id,
                message.chat.id,
                intro=f"Speaker '{speaker_name}' not found. Choose speaker to edit:",
            )
            return
        prompt_edit_speaker_actions(message.from_user.id, message.chat.id, speaker)
        return

    prompt_edit_speaker_selection(message.from_user.id, message.chat.id)


@bot.message_handler(state=SpeakerEditState.waiting_for_speaker_name, content_types=["text"])
def process_edit_speaker_name(message: types.Message):
    speaker_name = message.text.strip()
    if not speaker_name:
        safe_send_message(message.chat.id, "Speaker name cannot be empty.")
        return

    speaker = speaker_repository.get_speaker(speaker_name, chat_id=message.chat.id)
    if speaker is None:
        prompt_edit_speaker_selection(
            message.from_user.id,
            message.chat.id,
            intro=f"Speaker '{speaker_name}' not found. Choose speaker to edit:",
        )
        return

    prompt_edit_speaker_actions(message.from_user.id, message.chat.id, speaker)


@bot.message_handler(state=SpeakerEditState.waiting_for_speaker_name)
def process_edit_speaker_name_non_text(message: types.Message):
    speakers = speaker_repository.get_speakers(chat_id=message.chat.id)
    if not speakers:
        safe_send_message(message.chat.id, "No speakers found in this chat yet.")
        return

    safe_send_message(
        message.chat.id,
        "Use the buttons to choose speaker.",
        reply_markup=build_edit_speaker_selection_keyboard(speakers),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith(EDIT_SELECT_PREFIX))
def process_edit_speaker_selection(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != SpeakerEditState.waiting_for_speaker_name.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)

    if call.data == EDIT_SELECT_CANCEL:
        bot.delete_state(user_id, chat_id)
        clear_edit_speaker_state_data(user_id, chat_id)
        safe_send_message(chat_id, "Speaker edit cancelled.", reply_markup=types.ReplyKeyboardRemove())
        return

    try:
        selected_index = int(call.data[len(EDIT_SELECT_PREFIX):])
    except ValueError:
        safe_send_message(chat_id, "Invalid speaker selection.")
        prompt_edit_speaker_selection(user_id, chat_id)
        return

    speakers = speaker_repository.get_speakers(chat_id=chat_id)
    if selected_index < 0 or selected_index >= len(speakers):
        safe_send_message(chat_id, "Invalid speaker selection.")
        prompt_edit_speaker_selection(user_id, chat_id)
        return

    prompt_edit_speaker_actions(user_id, chat_id, speakers[selected_index])


@bot.callback_query_handler(func=lambda call: call.data in (EDIT_ACTION_RENAME, EDIT_ACTION_ADD_IMAGE, EDIT_ACTION_SET_PRIMARY, EDIT_ACTION_REMOVE_IMAGE, EDIT_ACTION_DONE))
def process_edit_speaker_action(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != SpeakerEditState.waiting_for_action.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    speaker = get_edit_speaker_from_state(user_id, chat_id)
    if speaker is None:
        safe_send_message(chat_id, "No speaker selected. Use /edit_speaker again.")
        bot.delete_state(user_id, chat_id)
        clear_edit_speaker_state_data(user_id, chat_id)
        return

    if call.data == EDIT_ACTION_DONE:
        bot.delete_state(user_id, chat_id)
        clear_edit_speaker_state_data(user_id, chat_id)
        safe_send_message(chat_id, "Speaker edit finished.", reply_markup=types.ReplyKeyboardRemove())
        return

    if call.data == EDIT_ACTION_RENAME:
        bot.set_state(user_id, SpeakerEditState.waiting_for_new_name, chat_id)
        safe_send_message(chat_id, f"Send new name for '{speaker.name}'.")
        return

    if call.data == EDIT_ACTION_ADD_IMAGE:
        bot.set_state(user_id, SpeakerEditState.waiting_for_new_image, chat_id)
        safe_send_message(chat_id, f"Send sticker to add to '{speaker.name}'.")
        return

    if call.data == EDIT_ACTION_SET_PRIMARY:
        if not speaker.speaker_image_ids:
            safe_send_message(chat_id, "This speaker has no images.")
            prompt_edit_speaker_actions(user_id, chat_id, speaker)
            return
        safe_send_message(
            chat_id,
            "Choose primary image:",
            reply_markup=build_edit_speaker_image_keyboard(speaker, EDIT_SET_PRIMARY_PREFIX),
        )
        return

    if call.data == EDIT_ACTION_REMOVE_IMAGE:
        if not speaker.speaker_image_ids:
            safe_send_message(chat_id, "This speaker has no images.")
            prompt_edit_speaker_actions(user_id, chat_id, speaker)
            return
        safe_send_message(
            chat_id,
            "Choose image to remove:",
            reply_markup=build_edit_speaker_image_keyboard(speaker, EDIT_REMOVE_IMAGE_PREFIX),
        )
        return


@bot.message_handler(state=SpeakerEditState.waiting_for_action)
def process_edit_speaker_action_text_fallback(message: types.Message):
    speaker = get_edit_speaker_from_state(message.from_user.id, message.chat.id)
    if speaker is None:
        safe_send_message(message.chat.id, "No speaker selected. Use /edit_speaker again.")
        return

    safe_send_message(
        message.chat.id,
        "Use buttons to choose edit action.",
        reply_markup=build_edit_speaker_actions_keyboard(speaker),
    )


@bot.message_handler(state=SpeakerEditState.waiting_for_new_name, content_types=["text"])
def process_edit_speaker_new_name(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    speaker = get_edit_speaker_from_state(user_id, chat_id)
    if speaker is None:
        safe_send_message(chat_id, "No speaker selected. Use /edit_speaker again.")
        bot.delete_state(user_id, chat_id)
        clear_edit_speaker_state_data(user_id, chat_id)
        return

    new_name = " ".join(message.text.split()).strip()
    if not new_name:
        safe_send_message(chat_id, "New name cannot be empty.")
        return

    renamed = speaker_repository.rename_speaker(speaker.name, new_name, chat_id=chat_id)
    if renamed is None:
        safe_send_message(chat_id, "Failed to rename speaker.")
        prompt_edit_speaker_actions(user_id, chat_id, speaker)
        return

    safe_send_message(chat_id, f"Speaker renamed to '{renamed.name}'.")
    prompt_edit_speaker_actions(user_id, chat_id, renamed)


@bot.message_handler(state=SpeakerEditState.waiting_for_new_name)
def process_edit_speaker_new_name_non_text(message: types.Message):
    safe_send_message(message.chat.id, "Please send new speaker name as text.")


@bot.message_handler(state=SpeakerEditState.waiting_for_new_image, content_types=["sticker"])
def process_edit_speaker_new_image(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    speaker = get_edit_speaker_from_state(user_id, chat_id)
    if speaker is None:
        safe_send_message(chat_id, "No speaker selected. Use /edit_speaker again.")
        bot.delete_state(user_id, chat_id)
        clear_edit_speaker_state_data(user_id, chat_id)
        return

    speaker.add_image_id(message.sticker.file_id)
    speaker_repository.save_speaker(speaker, chat_id=chat_id)
    safe_send_message(chat_id, f"Image added to '{speaker.name}'.")
    prompt_edit_speaker_actions(user_id, chat_id, speaker)


@bot.message_handler(state=SpeakerEditState.waiting_for_new_image)
def process_edit_speaker_new_image_non_sticker(message: types.Message):
    safe_send_message(message.chat.id, "Send a sticker to add as speaker image.")


@bot.callback_query_handler(func=lambda call: call.data.startswith(EDIT_SET_PRIMARY_PREFIX))
def process_edit_speaker_set_primary(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != SpeakerEditState.waiting_for_action.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    speaker = get_edit_speaker_from_state(user_id, chat_id)
    if speaker is None:
        safe_send_message(chat_id, "No speaker selected. Use /edit_speaker again.")
        return

    try:
        selected_index = int(call.data[len(EDIT_SET_PRIMARY_PREFIX):])
    except ValueError:
        safe_send_message(chat_id, "Invalid image selection.")
        return

    if selected_index < 0 or selected_index >= len(speaker.speaker_image_ids):
        safe_send_message(chat_id, "Invalid image selection.")
        return

    selected_image = speaker.speaker_image_ids[selected_index]
    speaker.speaker_image_ids = [selected_image] + [image_id for image_id in speaker.speaker_image_ids if image_id != selected_image]
    speaker_repository.save_speaker(speaker, chat_id=chat_id)
    safe_send_message(chat_id, f"Primary image updated for '{speaker.name}'.")
    prompt_edit_speaker_actions(user_id, chat_id, speaker)


@bot.callback_query_handler(func=lambda call: call.data.startswith(EDIT_REMOVE_IMAGE_PREFIX))
def process_edit_speaker_remove_image(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != SpeakerEditState.waiting_for_action.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    speaker = get_edit_speaker_from_state(user_id, chat_id)
    if speaker is None:
        safe_send_message(chat_id, "No speaker selected. Use /edit_speaker again.")
        return

    try:
        selected_index = int(call.data[len(EDIT_REMOVE_IMAGE_PREFIX):])
    except ValueError:
        safe_send_message(chat_id, "Invalid image selection.")
        return

    if selected_index < 0 or selected_index >= len(speaker.speaker_image_ids):
        safe_send_message(chat_id, "Invalid image selection.")
        return

    removed_image = speaker.speaker_image_ids.pop(selected_index)
    speaker_repository.save_speaker(speaker, chat_id=chat_id)
    # Best-effort local cache cleanup.
    sticker_file_cache.pop(removed_image, None)
    safe_send_message(chat_id, f"Image removed from '{speaker.name}'.")
    prompt_edit_speaker_actions(user_id, chat_id, speaker)


@bot.callback_query_handler(func=lambda call: call.data in (ADD_SPEAKER_IMAGE_YES, ADD_SPEAKER_IMAGE_NO, ADD_SPEAKER_IMAGE_CANCEL))
def process_add_speaker_image_decision(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != QuoteState.waiting_for_speaker_image_decision.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)

    if call.data == ADD_SPEAKER_IMAGE_CANCEL:
        clear_session(user_id, chat_id)
        safe_send_message(chat_id, "Cancelled.", reply_markup=types.ReplyKeyboardRemove())
        return

    quote = ensure_quote_exists_for_session(user_id, chat_id)
    if quote is None or not quote.phrases or not quote.phrases[-1].speaker:
        safe_send_message(chat_id, "No active phrase for speaker image.")
        return

    phrase = quote.phrases[-1]
    speaker = phrase.speaker

    if call.data == ADD_SPEAKER_IMAGE_NO:
        phrase.speaker_image_id = None
        set_quote_to_state(user_id, chat_id, quote)
        reset_render_cache(user_id, chat_id)
        prompt_context_for_last_phrase(user_id, chat_id)
        return

    bot.set_state(user_id, QuoteState.waiting_for_speaker_image_sticker, chat_id)
    safe_send_message(chat_id, f"Send a sticker to use as image for {speaker.name}.")


@bot.message_handler(state=QuoteState.waiting_for_speaker_image_decision)
def process_add_speaker_image_decision_text_fallback(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None or not quote.phrases or not quote.phrases[-1].speaker:
        return

    safe_send_message(
        message.chat.id,
        "Use the buttons to choose whether to add a speaker image.",
        reply_markup=build_add_speaker_image_keyboard(),
    )


@bot.message_handler(state=QuoteState.waiting_for_speaker_image_sticker, content_types=["sticker"])
def process_add_speaker_image_sticker(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    quote = ensure_quote_exists(message)
    if quote is None or not quote.phrases or not quote.phrases[-1].speaker:
        safe_send_message(chat_id, "No active phrase for speaker image.")
        return

    phrase = quote.phrases[-1]
    speaker = phrase.speaker
    image_id = message.sticker.file_id

    speaker.add_image_id(image_id)
    speaker_repository.save_speaker(speaker, chat_id=chat_id)
    phrase.speaker_image_id = image_id
    set_quote_to_state(user_id, chat_id, quote)
    reset_render_cache(user_id, chat_id)

    safe_send_message(chat_id, f"Image added to '{speaker.name}'.")
    prompt_context_for_last_phrase(user_id, chat_id)


@bot.message_handler(state=QuoteState.waiting_for_speaker_image_sticker)
def process_add_speaker_image_sticker_non_sticker(message: types.Message):
    safe_send_message(message.chat.id, "Send a sticker to use as speaker image.")


@bot.callback_query_handler(func=lambda call: call.data.startswith(PHRASE_IMAGE_PREFIX) or call.data == PHRASE_IMAGE_NONE)
def process_phrase_image_selection(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != QuoteState.waiting_for_phrase_image_selection.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)

    quote = ensure_quote_exists_for_session(user_id, chat_id)
    if quote is None or not quote.phrases or not quote.phrases[-1].speaker:
        safe_send_message(chat_id, "No active phrase for image selection.")
        return

    phrase = quote.phrases[-1]
    speaker = phrase.speaker

    if call.data == PHRASE_IMAGE_NONE:
        phrase.speaker_image_id = None
    else:
        try:
            selected_index = int(call.data[len(PHRASE_IMAGE_PREFIX):])
        except ValueError:
            safe_send_message(chat_id, "Invalid image selection.")
            safe_send_message(chat_id, "Choose phrase image:", reply_markup=build_phrase_image_keyboard(speaker))
            return

        if selected_index < 0 or selected_index >= len(speaker.speaker_image_ids):
            safe_send_message(chat_id, "Invalid image selection.")
            safe_send_message(chat_id, "Choose phrase image:", reply_markup=build_phrase_image_keyboard(speaker))
            return

        phrase.speaker_image_id = speaker.speaker_image_ids[selected_index]

    set_quote_to_state(user_id, chat_id, quote)
    reset_render_cache(user_id, chat_id)
    prompt_context_for_last_phrase(user_id, chat_id)


@bot.message_handler(state=QuoteState.waiting_for_phrase_image_selection)
def process_phrase_image_text_fallback(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None or not quote.phrases or not quote.phrases[-1].speaker:
        return
    safe_send_message(
        message.chat.id,
        "Use the buttons to choose image for this phrase.",
        reply_markup=build_phrase_image_keyboard(quote.phrases[-1].speaker),
    )


@bot.callback_query_handler(func=lambda call: call.data in (ADD_CONTEXT_YES, ADD_CONTEXT_NO))
def process_add_context_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != QuoteState.waiting_for_context_decision.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)

    if call.data == ADD_CONTEXT_YES:
        bot.set_state(user_id, PhraseState.waiting_for_context_text, chat_id)
        safe_send_message(chat_id, "Send context text for this phrase.")
        return

    bot.set_state(user_id, QuoteState.waiting_for_next_step, chat_id)
    process_speaker_name_end(user_id, chat_id)


@bot.message_handler(state=PhraseState.waiting_for_context_text, content_types=["text"])
def process_phrase_context_text(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None:
        return

    if not quote.phrases:
        safe_send_message(message.chat.id, "Quote has no phrases yet.")
        bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)
        return

    context_text = message.text.strip()
    if not context_text:
        safe_send_message(message.chat.id, "Context cannot be empty. Send text or use Skip.")
        return

    quote.phrases[-1].context_text = context_text
    set_quote_to_state(message.from_user.id, message.chat.id, quote)
    reset_render_cache(message.from_user.id, message.chat.id)

    bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)
    process_speaker_name_end(message.from_user.id, message.chat.id)


@bot.message_handler(state=PhraseState.waiting_for_context_text)
def process_phrase_context_non_text(message: types.Message):
    safe_send_message(message.chat.id, "Please send the context as text.")


@bot.message_handler(state=QuoteState.waiting_for_context_decision)
def process_context_decision_text_fallback(message: types.Message):
    safe_send_message(
        message.chat.id,
        "Use the buttons to choose whether to add context.",
        reply_markup=build_add_context_keyboard(),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith(MAIN_SELECT_PREFIX))
def process_main_speaker_selection(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != QuoteState.waiting_for_main_speaker.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    quote = ensure_quote_exists_for_session(user_id, chat_id)
    if quote is None:
        return

    unique_speakers = get_unique_speakers_from_quote(quote)
    if not unique_speakers:
        safe_send_message(chat_id, "No speakers available. Add at least one phrase with speaker.")
        bot.set_state(user_id, QuoteState.waiting_for_next_step, chat_id)
        return

    try:
        selected_index = int(call.data[len(MAIN_SELECT_PREFIX):])
    except ValueError:
        safe_send_message(chat_id, "Invalid speaker selection.")
        safe_send_message(chat_id, "Choose a main speaker:", reply_markup=build_main_speaker_keyboard(quote))
        return

    if selected_index < 0 or selected_index >= len(unique_speakers):
        safe_send_message(chat_id, "Invalid speaker selection.")
        safe_send_message(chat_id, "Choose a main speaker:", reply_markup=build_main_speaker_keyboard(quote))
        return

    selected_name = unique_speakers[selected_index].name
    if quote.main_speaker_name != selected_name:
        quote.main_speaker_name = selected_name
        set_quote_to_state(user_id, chat_id, quote)
        reset_render_cache(user_id, chat_id)

    safe_send_message(chat_id, f"Main speaker set to: {selected_name}")
    process_speaker_name_end(user_id, chat_id)


@bot.message_handler(state=QuoteState.waiting_for_main_speaker)
def process_main_speaker_text_fallback(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None:
        return

    safe_send_message(
        message.chat.id,
        "Use the buttons to select the main speaker.",
        reply_markup=build_main_speaker_keyboard(quote),
    )


@bot.callback_query_handler(func=lambda call: call.data in (NEXT_ADD, NEXT_MAIN, NEXT_FINALIZE, NEXT_CANCEL))
def process_next_step_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = bot.get_state(user_id, chat_id)

    if state != QuoteState.waiting_for_next_step.name:
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)

    if call.data == NEXT_MAIN:
        quote = ensure_quote_exists_for_session(user_id, chat_id)
        if quote is None:
            return

        previous_main = quote.main_speaker_name
        ensure_main_speaker_name(quote)
        if quote.main_speaker_name != previous_main:
            set_quote_to_state(user_id, chat_id, quote)
            reset_render_cache(user_id, chat_id)

        unique_speakers = get_unique_speakers_from_quote(quote)
        if not unique_speakers:
            safe_send_message(chat_id, "No speakers available. Add at least one phrase with speaker.")
            return

        if len(unique_speakers) == 1:
            safe_send_message(chat_id, f"Only one speaker in quote: {unique_speakers[0].name}")
            process_speaker_name_end(user_id, chat_id)
            return

        bot.set_state(user_id, QuoteState.waiting_for_main_speaker, chat_id)
        safe_send_message(chat_id, "Choose the main speaker:", reply_markup=build_main_speaker_keyboard(quote))
        return

    if call.data == NEXT_ADD:
        bot.set_state(user_id, PhraseState.waiting_for_text, chat_id)
        safe_send_message(chat_id, "Send the next phrase text.")
        return

    if call.data == NEXT_CANCEL:
        clear_session(user_id, chat_id)
        safe_send_message(chat_id, "Cancelled.", reply_markup=types.ReplyKeyboardRemove())
        return

    finalize_quote(user_id=user_id, chat_id=chat_id)


@bot.message_handler(state=QuoteState.waiting_for_next_step)
def process_next_step_text_fallback(message: types.Message):
    safe_send_message(
        message.chat.id,
        "Use the buttons to continue.",
        reply_markup=build_next_step_keyboard(),
    )


@bot.message_handler(commands=["preview"], state="*")
def process_preview_command(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None:
        return

    try:
        quote_text, image = get_quote_text_and_image(quote, message.from_user.id, message.chat.id)
    except Exception:
        safe_send_message(message.chat.id, "Could not build preview right now.")
        return

    safe_send_photo(message.chat.id, photo=image, caption=quote_text)


@bot.message_handler(commands=["done"], state="*")
def process_done_command(message: types.Message):
    if bot.get_state(message.from_user.id, message.chat.id) != QuoteState.waiting_for_next_step.name:
        safe_send_message(message.chat.id, "Use /done after at least one complete phrase with speaker.")
        return

    finalize_quote(user_id=message.from_user.id, chat_id=message.chat.id)


@bot.message_handler(commands=["remove_last"], state="*")
def process_remove_last(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None:
        return

    if not quote.phrases:
        safe_send_message(message.chat.id, "Quote has no phrases yet.")
        return

    removed = quote.phrases.pop()
    ensure_main_speaker_name(quote)
    set_quote_to_state(message.from_user.id, message.chat.id, quote)
    reset_render_cache(message.from_user.id, message.chat.id)

    safe_send_message(message.chat.id, f"Removed: {removed.text}")

    if not quote.phrases:
        bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)
        safe_send_message(message.chat.id, "Quote is empty now. Send a new phrase.")
        return

    bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)
    process_speaker_name_end(message.from_user.id, message.chat.id)


@bot.message_handler(commands=["edit_last"], state="*")
def process_edit_last(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None:
        return

    if not quote.phrases:
        safe_send_message(message.chat.id, "Quote has no phrases yet.")
        return

    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) == 2 and command_parts[1].strip():
        quote.phrases[-1].text = command_parts[1].strip()
        set_quote_to_state(message.from_user.id, message.chat.id, quote)
        reset_render_cache(message.from_user.id, message.chat.id)
        bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)
        safe_send_message(message.chat.id, "Last phrase updated.")
        process_speaker_name_end(message.from_user.id, message.chat.id)
        return

    bot.set_state(message.from_user.id, PhraseState.waiting_for_edit_last_text, message.chat.id)
    safe_send_message(message.chat.id, "Send new text for the last phrase.")


@bot.message_handler(state=PhraseState.waiting_for_edit_last_text, content_types=["text"])
def process_edit_last_text(message: types.Message):
    quote = ensure_quote_exists(message)
    if quote is None:
        return

    if not quote.phrases:
        safe_send_message(message.chat.id, "Quote has no phrases.")
        bot.set_state(message.from_user.id, PhraseState.waiting_for_text, message.chat.id)
        return

    new_text = message.text.strip()
    if not new_text:
        safe_send_message(message.chat.id, "Text cannot be empty.")
        return

    quote.phrases[-1].text = new_text
    set_quote_to_state(message.from_user.id, message.chat.id, quote)
    reset_render_cache(message.from_user.id, message.chat.id)
    bot.set_state(message.from_user.id, QuoteState.waiting_for_next_step, message.chat.id)
    process_speaker_name_end(message.from_user.id, message.chat.id)


@bot.message_handler(state=PhraseState.waiting_for_edit_last_text)
def process_edit_last_text_non_text(message: types.Message):
    safe_send_message(message.chat.id, "Send text for the updated phrase.")


@bot.message_handler(commands=["cancel"], state="*")
def process_cancel(message: types.Message):
    clear_session(message.from_user.id, message.chat.id)
    safe_send_message(message.chat.id, "Cancelled.", reply_markup=types.ReplyKeyboardRemove())


# ------------------ Run ------------------

bot.infinity_polling(skip_pending=True)
