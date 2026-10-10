import asyncio
import html
import re
import ast
import math
import random
import pytz
from collections import Counter
from datetime import datetime, timedelta, date, time
lock = asyncio.Lock()
from database.users_chats_db import db
from database.refer import referdb
from pyrogram.errors.exceptions.bad_request_400 import MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty
from Script import script
from branding import SOURCE_CODE_LINK
import pyrogram
from database.connections_mdb import active_connection, all_connections, delete_connection, if_active, make_active, \
    make_inactive
from info import *
from info import LANGUAGES
from search_filters import (
    extract_episode_numbers,
    make_filter_query,
    parse_filter_query,
)
from branding import WELCOME_BANNER
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, WebAppInfo
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserIsBlocked, MessageNotModified, PeerIdInvalid
from utils import get_size, is_subscribed, search_gagala, temp, get_settings, save_group_settings, get_shortlink, get_tutorial, send_all, get_cap
from fuzzywuzzy import process
from database.users_chats_db import db
from database.config_db import mdb
from database.ia_filterdb import (
    Media,
    Media2,
    get_all_search_results,
    get_bad_files,
    get_file_details,
    get_search_results,
)
from database.filters_mdb import (
    del_all,
    find_filter,
    get_filters,
)
from database.gfilters_mdb import (
    find_gfilter,
    get_gfilters,
    del_allg
)
import logging
from urllib.parse import quote_plus
from database.tmdb_client import search_tmdb, get_tmdb_movie, get_tmdb_tv, fetch_tmdb_image
from Deendayal_botz.util.file_properties import get_name, get_hash, get_media_file_size
from database.config_db import mdb
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

import requests
import string
import tracemalloc

tracemalloc.start()


TIMEZONE = "Asia/Kolkata"
BUTTON = {}
BUTTONS = {}
FRESH = {}
BUTTONS0 = {}
BUTTONS1 = {}
BUTTONS2 = {}
SPELL_CHECK = {}
BACKGROUND_TASKS = set()
PM_FILE_DELIVERIES = set()


class _PrivateStartMessage:
    """Present a PM callback as a normal /start message to the delivery flow."""

    def __init__(self, query, payload):
        self._message = query.message
        self.from_user = query.from_user
        self.chat = query.message.chat
        self.command = ["start", payload]
        self.text = f"/start {payload}"

    async def react(self, *args, **kwargs):
        # The callback came from a bot-authored result message.  Reacting to
        # that message is unnecessary and may fail in some Telegram clients.
        return None

    def __getattr__(self, name):
        return getattr(self._message, name)


async def _dispatch_private_start(client, query, payload):
    """Reuse the complete /start delivery path without another deep link."""
    # Imported lazily to avoid the commands -> pmfilter import cycle at startup.
    from .commands import start

    message = _PrivateStartMessage(query, payload)
    return await start(client, message)


async def _run_private_delivery(client, query, payload, delivery_key):
    """Deliver once per tap and always stop Telegram's callback spinner."""
    if delivery_key in PM_FILE_DELIVERIES:
        return await query.answer(
            "⏳ This file is already being prepared.",
            show_alert=False,
        )

    PM_FILE_DELIVERIES.add(delivery_key)
    try:
        await query.answer("📥 Preparing your file...")
        await _dispatch_private_start(client, query, payload)
    except Exception as error:
        logger.exception("Private search delivery failed: %s", error)
        try:
            await client.send_message(
                query.from_user.id,
                "<b>❌ Unable to send this file right now.</b>\n\n"
                "Please search again and retry.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            logger.exception("Unable to report private delivery failure")
    finally:
        PM_FILE_DELIVERIES.discard(delivery_key)


def _start_background_task(coroutine):
    """Run delayed cleanup without occupying a Pyrogram update worker."""
    task = asyncio.create_task(coroutine)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


async def _delete_messages_later(delay, *messages):
    await asyncio.sleep(delay)
    for sent_message in messages:
        if sent_message is None:
            continue
        try:
            await sent_message.delete()
        except Exception:
            pass


def _schedule_auto_delete(*messages):
    return _start_background_task(
        _delete_messages_later(DELETE_TIME, *messages)
    )


def generate_random_alphanumeric():
    """Generate a random 8-letter alphanumeric string."""
    characters = string.ascii_letters + string.digits
    random_chars = ''.join(random.choice(characters) for _ in range(8))
    return random_chars
  
def get_shortlink_sync(url):
    try:
        rget = requests.get(f"https://{STREAM_SITE}/api?api={STREAM_API}&url={url}&alias={generate_random_alphanumeric()}")
        rjson = rget.json()
        if rjson["status"] == "success" or rget.status_code == 200:
            return rjson["shortenedUrl"]
        else:
            return url
    except Exception as e:
        print(f"Error in get_shortlink_sync: {e}")
        return url

async def get_shortlink(url):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_shortlink_sync, url)


def build_download_action_buttons(bot_username, ident, file_id, shortlink=False):
    """Build Stream / Direct File URL buttons that replace the clicked file row."""
    normal_link = f"https://t.me/{bot_username}?start={ident}_{file_id}"
    short_link = f"https://t.me/{bot_username}?start=short_{file_id}"
    if shortlink:
        return [
            InlineKeyboardButton("🍿 Stream", url=short_link),
            InlineKeyboardButton("📂 Direct File", url=normal_link),
        ]
    return [
        InlineKeyboardButton("🍿 Stream", url=normal_link),
        InlineKeyboardButton("📂 Direct File", url=normal_link),
    ]


def build_sendall_action_buttons(bot_username, key, shortlink=False):
    """Build Send All URL buttons that replace the clicked Send All row."""
    normal_link = f"https://t.me/{bot_username}?start=allfiles_{key}"
    short_link = f"https://t.me/{bot_username}?start=sendfiles1_{key}"
    if shortlink:
        return [
            InlineKeyboardButton("🔗 Get Link", url=short_link),
            InlineKeyboardButton("📦 Send All", url=normal_link),
        ]
    return [
        InlineKeyboardButton("📦 Send All", url=normal_link),
        InlineKeyboardButton("🚀 Open Bot", url=normal_link),
    ]


async def replace_clicked_button_row(query, new_row, fallback_text="⇊ Link Ready ⇊"):
    """Replace the exact clicked inline keyboard row with generated buttons."""
    old_markup = query.message.reply_markup
    keyboard = []
    replaced = False
    if old_markup and old_markup.inline_keyboard:
        for row in old_markup.inline_keyboard:
            row_has_clicked = any(getattr(button, "callback_data", None) == query.data for button in row)
            if row_has_clicked and not replaced:
                keyboard.append([InlineKeyboardButton(fallback_text, callback_data="pages")])
                keyboard.append(new_row)
                replaced = True
            else:
                keyboard.append(row)
    if not replaced:
        keyboard.append([InlineKeyboardButton(fallback_text, callback_data="pages")])
        keyboard.append(new_row)
    await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))


def _language_item(item):
    """Support both old string LANGUAGES and new tuple/list LANGUAGES."""
    if isinstance(item, (tuple, list)) and len(item) >= 2:
        return str(item[0]), str(item[1])
    return str(item).title(), str(item).lower()


def _language_display(code):
    for item in LANGUAGES:
        text, lang_code = _language_item(item)
        if lang_code.lower() == str(code).lower():
            return text
    return str(code).upper()


def _action_filter_row(key):
    """Build Send All plus a state-aware Combined/All Files button."""
    search = BUTTONS.get(key) or FRESH.get(key) or ""
    _, selected = parse_filter_query(search)
    combined_active = bool(selected.get("combined"))
    return [
        InlineKeyboardButton(
            "📦 Send All",
            callback_data=f"sendfiles#{key}",
        ),
        InlineKeyboardButton(
            "📂 All Files" if combined_active else "🗂 Combined Files",
            callback_data=(
                f"combinedoff#{key}"
                if combined_active
                else f"combined#{key}"
            ),
        ),
    ]


def _filter_result_buttons(files, settings, pre, key):
    """Build the common result keyboard used by stackable filter callbacks."""
    if settings["button"]:
        btn = [
            [
                InlineKeyboardButton(
                    text=clean_file_button_text(file),
                    callback_data=f"{pre}#{file.file_id}",
                )
            ]
            for file in files
        ]
    else:
        btn = []
    btn.insert(0, [
        InlineKeyboardButton("🎞 Quality", callback_data=f"qualities#{key}"),
        InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
        InlineKeyboardButton("📺 Season", callback_data=f"seasons#{key}"),
    ])
    btn.insert(0, [
        InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
        InlineKeyboardButton("📅 Years", callback_data=f"years#{key}"),
    ])
    btn.insert(0, _action_filter_row(key))
    return btn


def _filter_nav_row(settings, req, key, next_offset, total_results):
    """Build first-page navigation for database-level filter results."""
    if next_offset in ("", None):
        return [
            InlineKeyboardButton(
                "↭ Back To Files ↭",
                callback_data=f"next_{req}_{key}_0",
            )
        ]
    page_size = 10 if settings.get("max_btn", True) else int(MAX_B_TN)
    pages = max(1, math.ceil(int(total_results) / page_size))
    return [
        InlineKeyboardButton("PAGE", callback_data="pages"),
        InlineKeyboardButton(f"1/{pages}", callback_data="pages"),
        InlineKeyboardButton(
            "NEXT ➢",
            callback_data=f"next_{req}_{key}_{next_offset}",
        ),
    ]


def _episode_source_text(file):
    return " ".join(
        part
        for part in (
            getattr(file, "file_name", None),
            getattr(file, "caption", None),
        )
        if part
    )


async def _available_episode_counts(search):
    """Count episodes across every matching DB result, not the visible page."""
    episode_free_search = make_filter_query(search, episode=None)
    files, _ = await get_all_search_results(episode_free_search)
    counts = Counter()
    for file in files:
        counts.update(extract_episode_numbers(_episode_source_text(file)))
    return dict(sorted(counts.items()))



_LANGUAGE_ALIASES = {
    "english": "English", "eng": "English",
    "hindi": "Hindi", "hin": "Hindi",
    "tamil": "Tamil", "tam": "Tamil",
    "telugu": "Telugu", "tel": "Telugu",
    "malayalam": "Malayalam", "mal": "Malayalam",
    "kannada": "Kannada", "kan": "Kannada",
    "bengali": "Bengali", "bangla": "Bengali", "ben": "Bengali",
    "marathi": "Marathi", "mar": "Marathi",
    "punjabi": "Punjabi", "pun": "Punjabi",
    "gujarati": "Gujarati", "guj": "Gujarati",
    "urdu": "Urdu", "urd": "Urdu",
    "arabic": "Arabic", "ara": "Arabic",
    "korean": "Korean", "kor": "Korean",
    "japanese": "Japanese", "jpn": "Japanese",
    "chinese": "Chinese", "chi": "Chinese",
    "spanish": "Spanish", "spa": "Spanish",
    "french": "French", "fra": "French",
    "german": "German", "deu": "German",
    "portuguese": "Portuguese", "por": "Portuguese",
    "russian": "Russian", "rus": "Russian",
    "turkish": "Turkish", "tur": "Turkish",
}

_FILE_META_RE = re.compile(
    r"\b(?:2160p|1440p|1080p|720p|576p|480p|360p|240p|4k|2k|\d{3,4}x\d{3,4}|"
    r"web[- .]?dl|web[- .]?rip|webrip|bluray|blu[- .]?ray|brrip|bdrip|hdrip|dvdrip|hdts|hdtc|camrip|cam|"
    r"x264|x265|h264|h265|hevc|av1|10bit|8bit|aac|ddp(?:[0-9.]+)?|dd(?:[0-9.]+)?|ac3|eac3|atmos|dts(?:[- ]?hd)?|"
    r"proper|repack|remux|uncut|extended|directors?\s*cut|multi[- .]?audio|dual[- .]?audio|dubbed|subs?|esub)\b",
    re.I,
)

def _file_text(file):
    return str(
        getattr(file, "original_file_name", None)
        or getattr(file, "file_name", None)
        or getattr(file, "caption", None)
        or ""
    )

def _extract_file_languages(files):
    """Collect languages mentioned in matching filenames/captions, preserving order."""
    found, seen = [], set()
    for file in files or []:
        text_value = _file_text(file).lower()
        for raw, label in _LANGUAGE_ALIASES.items():
            if re.search(rf"(?<![a-z]){re.escape(raw)}(?![a-z])", text_value):
                if label not in seen:
                    seen.add(label)
                    found.append(label)
    return found

def _is_series_file(file):
    """Detect TV/series files without changing their database filename."""
    text = _file_text(file)
    return bool(re.search(r"(?i)(?:\bS\d{1,3}(?:E\d{1,4}(?:[-_. ]E?\d{1,4})*)?\b|\bSeason[ ._-]?\d{1,3}\b|\b\d{1,3}x\d{1,4}\b)", text))


def _clean_display_filename(file):
    """Clean only the visual filename shown in search results.

    The database/Telegram filename is never changed.  The displayed name is
    otherwise kept intact: only dot separators are converted to spaces.
    This applies equally to movies and series, including normal episodes and
    combined/multi-episode files.
    """
    raw = _file_text(file).strip()
    # Keep the original extension text (e.g. .mkv -> " mkv") as requested.
    display = raw.replace(".", " ")
    display = re.sub(r"[ \t]+", " ", display).strip()
    return display


def clean_file_button_text(file, tmdb=None):
    """Display the stored filename with dot separators cleaned only.

    No title/episode/season/quality rewriting is performed.  The stored
    database filename remains untouched.
    """
    try:
        size = get_size(file.file_size)
        display = _clean_display_filename(file)
        return "🎬 " + display + f" • {size}"
    except Exception:
        try:
            return f"🎬 {_clean_display_filename(file)}"
        except Exception:
            return "🎬 File"

def _setting_enabled(value):
    """Treat persisted bools and legacy string bools consistently."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}
    return bool(value)


@Client.on_message(filters.group & filters.text & filters.incoming)
async def give_filter(client, message):
    if EMOJI_MODE:
        await message.react(emoji=random.choice(REACTIONS), big=True)
    await mdb.update_top_messages(message.from_user.id, message.text)
    if message.chat.id != SUPPORT_CHAT_ID:
        manual = await manual_filters(client, message)
        if manual == False:
            settings = await get_settings(message.chat.id)
            try:
                if settings['auto_ffilter']:
                    await auto_filter(client, message)
            except KeyError:
                grpid = await active_connection(str(message.from_user.id))
                await save_group_settings(grpid, 'auto_ffilter', True)
                settings = await get_settings(message.chat.id)
                if settings['auto_ffilter']:
                    await auto_filter(client, message) 
    else:
        search = message.text
        temp_files, temp_offset, total_results = await get_search_results(chat_id=message.chat.id, query=search.lower(), offset=0, filter=True)
        if total_results == 0:
            return
        else:
            return await message.reply_text(f"<b>Hᴇʏ {message.from_user.mention},\n\nʏᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ✅\n\n📂 ꜰɪʟᴇꜱ ꜰᴏᴜɴᴅ : {str(total_results)}\n🔍 ꜱᴇᴀʀᴄʜ :</b> <code>{search}</code>\n\n<b>‼️ ᴛʜɪs ɪs ᴀ <u>sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ</u> sᴏ ᴛʜᴀᴛ ʏᴏᴜ ᴄᴀɴ'ᴛ ɢᴇᴛ ғɪʟᴇs ғʀᴏᴍ ʜᴇʀᴇ...\n\n📝 ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ : 👇</b>",   
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 ᴊᴏɪɴ ᴀɴᴅ ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)]]))

@Client.on_message(filters.private & filters.text & filters.incoming)
async def pm_text(bot, message):
    bot_id = bot.me.id
    content = message.text
    user = message.from_user.first_name
    user_id = message.from_user.id
    if EMOJI_MODE:
        await message.react(emoji=random.choice(REACTIONS), big=True)
    if content.startswith(("/", "#")):
        return  
    try:
        await mdb.update_top_messages(user_id, content)
        pm_search = await db.pm_search_status(bot_id)
        if pm_search:
            await auto_filter(bot, message)
        else:
            await message.reply_text(
             text=f"<b>🙋 ʜᴇʏ {user} 😍 ,\n\n𝒀𝒐𝒖 𝒄𝒂𝒏 𝒔𝒆𝒂𝒓𝒄𝒉 𝒇𝒐𝒓 𝒎𝒐𝒗𝒊𝒆𝒔 𝒐𝒏𝒍𝒚 𝒐𝒏 𝒐𝒖𝒓 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑. 𝒀𝒐𝒖 𝒂𝒓𝒆 𝒏𝒐𝒕 𝒂𝒍𝒍𝒐𝒘𝒆𝒅 𝒕𝒐 𝒔𝒆𝒂𝒓𝒄𝒉 𝒇𝒐𝒓 𝒎𝒐𝒗𝒊𝒆𝒔 𝒐𝒏 𝑫𝒊𝒓𝒆𝒄𝒕 𝑩𝒐𝒕. 𝑷𝒍𝒆𝒂𝒔𝒆 𝒋𝒐𝒊𝒏 𝒐𝒖𝒓 𝒎𝒐𝒗𝒊𝒆 𝒈𝒓𝒐𝒖𝒑 𝒃𝒚 𝒄𝒍𝒊𝒄𝒌𝒊𝒏𝒈 𝒐𝒏 𝒕𝒉𝒆  𝑹𝑬𝑸𝑼𝑬𝑺𝑻 𝑯𝑬𝑹𝑬 𝒃𝒖𝒕𝒕𝒐𝒏 𝒈𝒊𝒗𝒆𝒏 𝒃𝒆𝒍𝒐𝒘 𝒂𝒏𝒅 𝒔𝒆𝒂𝒓𝒄𝒉 𝒚𝒐𝒖𝒓 𝒇𝒂𝒗𝒐𝒓𝒊𝒕𝒆 𝒎𝒐𝒗𝒊𝒆 𝒕𝒉𝒆𝒓𝒆 👇\n\n<blockquote>आप केवल हमारे 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑 पर ही 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 कर सकते हो । आपको 𝑫𝒊𝒓𝒆𝒄𝒕 𝑩𝒐𝒕 पर 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 करने की 𝑷𝒆𝒓𝒎𝒊𝒔𝒔𝒊𝒐𝒏 नहीं है कृपया नीचे दिए गए 𝑹𝑬𝑸𝑼𝑬𝑺𝑻 𝑯𝑬𝑹𝑬 वाले 𝑩𝒖𝒕𝒕𝒐𝒏 पर क्लिक करके हमारे 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑 को 𝑱𝒐𝒊𝒏 करें और वहां पर अपनी मनपसंद 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 सर्च करें ।</blockquote></b>",   
             reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ʀᴇǫᴜᴇsᴛ ʜᴇʀᴇ ", url=GRP_LNK)]])
            )
            await bot.send_message(
                chat_id=LOG_CHANNEL,
                text=f"<b>#𝐏𝐌_𝐌𝐒𝐆\n\n👤 Nᴀᴍᴇ : {user}\n🆔 ID : {user_id}\n💬 Mᴇssᴀɢᴇ : {content}</b>"
            )
    except Exception as e:
        # Log the error
        print(f"An error occurred: {str(e)}")


@Client.on_callback_query(filters.regex(r"^reffff"))
async def refercall(bot, query):
    btn = [[
        InlineKeyboardButton('invite link', url=f'https://telegram.me/share/url?url=https://t.me/{bot.me.username}?start=reff_{query.from_user.id}&text=Hello%21%20Experience%20a%20bot%20that%20offers%20a%20vast%20library%20of%20unlimited%20movies%20and%20series.%20%F0%9F%98%83'),
        InlineKeyboardButton(f'⏳ {referdb.get_refer_points(query.from_user.id)}', callback_data='ref_point'),
        InlineKeyboardButton('Back', callback_data='premium_info')
    ]]
    reply_markup = InlineKeyboardMarkup(btn)
    await bot.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/1a2e64aee3d4d10edd930.jpg")
        )
    await query.message.edit_text(
        text=f'Hay Your refer link:\n\nhttps://t.me/{bot.me.username}?start=reff_{query.from_user.id}\n\nShare this link with your friends, Each time they join,  you will get 10 refferal points and after 100 points you will get 1 month premium subscription.',
        reply_markup=reply_markup,
        parse_mode=enums.ParseMode.HTML
        )
    await query.answer()

@Client.on_callback_query(filters.regex(r"^next"))
async def next_page(bot, query):
    ident, req, key, offset = query.data.split("_")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    if int(req) not in [query.from_user.id, 0]:
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    try:
        offset = int(offset)
    except:
        offset = 0
    if BUTTONS.get(key)!=None:
        search = BUTTONS.get(key)
    else:
        search = FRESH.get(key)
    if not search:
        await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name),show_alert=True)
        return

    files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=offset, filter=True)
    try:
        n_offset = int(n_offset)
    except:
        n_offset = 0

    if not files:
        return
    temp.GETALL[key] = files
    temp.SHORT[query.from_user.id] = query.message.chat.id
    settings = await get_settings(query.message.chat.id)
    pre = 'filep' if settings['file_secure'] else 'file'
    if settings['button']:
        btn = [
            [
                InlineKeyboardButton(
                    text=clean_file_button_text(file), callback_data=f'{pre}#{file.file_id}'
                ),
            ]
            for file in files
        ]
        btn.insert(0, 
            [ 
                InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
                InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
                InlineKeyboardButton("📺 Season",  callback_data=f"seasons#{key}")
            ]
        )
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))

    else:
        btn = []
        btn.insert(0, 
            [
                InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
                InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
                InlineKeyboardButton("📺 Season",  callback_data=f"seasons#{key}")
            ]
        )
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))

    try:
        if settings['max_btn']:
            if 0 < offset <= 10:
                off_set = 0
            elif offset == 0:
                off_set = None
            else:
                off_set = offset - 10
            if n_offset == 0:
                btn.append(
                    [InlineKeyboardButton("⬅ BACK", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages")]
                )
            elif off_set is None:
                btn.append([InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton("NEXT ➢", callback_data=f"next_{req}_{key}_{n_offset}")])
            else:
                btn.append(
                    [
                        InlineKeyboardButton("⬅ BACK", callback_data=f"next_{req}_{key}_{off_set}"),
                        InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"),
                        InlineKeyboardButton("NEXT ➢", callback_data=f"next_{req}_{key}_{n_offset}")
                    ],
                )
        else:
            if 0 < offset <= int(MAX_B_TN):
                off_set = 0
            elif offset == 0:
                off_set = None
            else:
                off_set = offset - int(MAX_B_TN)
            if n_offset == 0:
                btn.append(
                    [InlineKeyboardButton("⬅ BACK", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages")]
                )
            elif off_set is None:
                btn.append([InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton("NEXT ➢", callback_data=f"next_{req}_{key}_{n_offset}")])
            else:
                btn.append(
                    [
                        InlineKeyboardButton("⬅ BACK", callback_data=f"next_{req}_{key}_{off_set}"),
                        InlineKeyboardButton(f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages"),
                        InlineKeyboardButton("NEXT ➢", callback_data=f"next_{req}_{key}_{n_offset}")
                    ],
                )
    except KeyError:
        await save_group_settings(query.message.chat.id, 'max_btn', True)
        if 0 < offset <= 10:
            off_set = 0
        elif offset == 0:
            off_set = None
        else:
            off_set = offset - 10
        if n_offset == 0:
            btn.append(
                [InlineKeyboardButton("⬅ BACK", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages")]
            )
        elif off_set is None:
            btn.append([InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton("NEXT ➢", callback_data=f"next_{req}_{key}_{n_offset}")])
        else:
            btn.append(
                [
                    InlineKeyboardButton("⬅ BACK", callback_data=f"next_{req}_{key}_{off_set}"),
                    InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"),
                    InlineKeyboardButton("NEXT ➢", callback_data=f"next_{req}_{key}_{n_offset}")
                ],
            )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total, search)
        await safe_edit(
            query.message,
            cap,
            reply_markup=InlineKeyboardMarkup(btn),
        )
    else:
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(btn)
            )
        except MessageNotModified:
            pass
    await query.answer()



@Client.on_callback_query(filters.regex(r"^spol"))
async def advantage_spoll_choker(bot, query):
    _, id, user = query.data.split('#')
    if int(user) != 0 and query.from_user.id != int(user):
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    
    movie_data = await get_tmdb_movie(id, id=True)
    if not movie_data:
        return await query.answer("TMDB movie not found", show_alert=True)
    movie = movie_data.get('title')
    movie = re.sub(r"[:-]", " ", movie)
    movie = re.sub(r"\s+", " ", movie).strip()
    
    await query.answer(script.TOP_ALRT_MSG)
    gl = await global_filters(bot, query.message, text=movie)
    
    if gl == False:
        k = await manual_filters(bot, query.message, text=movie)
        
        if k == False:
            files, offset, total_results = await get_search_results(query.message.chat.id, movie, offset=0, filter=True)
            
            if files:
                k = (movie, files, offset, total_results)
                await auto_filter(bot, query, k)
            else:
                reqstr1 = query.from_user.id if query.from_user else 0
                reqstr = await bot.get_users(reqstr1)
                
                if NO_RESULTS_MSG:
                    await bot.send_message(chat_id=BIN_CHANNEL, text=(script.NORSLTS.format(reqstr.id, reqstr.mention, movie)))
                
                # Create the button for contacting admin
                contact_admin_button = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔰Cʟɪᴄᴋ ʜᴇʀᴇ & ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴀᴅᴍɪɴ🔰", url=OWNER_LNK)]]
                )
                
                k = await query.message.edit(script.MVE_NT_FND, reply_markup=contact_admin_button)
                await asyncio.sleep(10)
                await k.delete()
                
# Combined files
@Client.on_callback_query(filters.regex(r"^combined(?:off)?#"))
async def combined_files_cb_handler(client: Client, query: CallbackQuery):
    action, key = query.data.split("#", 1)
    curr_time = datetime.now(pytz.timezone("Asia/Kolkata")).time()
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message

    try:
        if int(query.from_user.id) not in [
            query.message.reply_to_message.from_user.id,
            0,
        ]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\n"
                "ᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\n"
                "ʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except Exception:
        pass

    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(
            script.OLD_ALRT_TXT.format(query.from_user.first_name),
            show_alert=True,
        )

    enable_combined = action == "combined"
    filtered_search = make_filter_query(
        search.replace("_", " "),
        combined=True if enable_combined else None,
    )
    files, offset, total_results = await get_search_results(
        chat_id,
        filtered_search,
        offset=0,
        filter=True,
    )
    if not files:
        return await query.answer(
            "🚫 No combined files were found in these results.",
            show_alert=True,
        )

    BUTTONS[key] = filtered_search
    BUTTONS0[key] = filtered_search
    BUTTONS1[key] = filtered_search
    BUTTONS2[key] = filtered_search
    temp.GETALL[key] = files

    settings = await get_settings(chat_id)
    pre = "filep" if settings["file_secure"] else "file"
    btn = _filter_result_buttons(files, settings, pre, key)
    btn.append(
        _filter_nav_row(settings, req, key, offset, total_results)
    )

    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone("Asia/Kolkata")).time()
        time_difference = (
            timedelta(
                hours=cur_time.hour,
                minutes=cur_time.minute,
                seconds=cur_time.second + (cur_time.microsecond / 1000000),
            )
            - timedelta(
                hours=curr_time.hour,
                minutes=curr_time.minute,
                seconds=curr_time.second + (curr_time.microsecond / 1000000),
            )
        )
        remaining_seconds = f"{time_difference.total_seconds():.2f}"
        cap = await get_cap(
            settings,
            remaining_seconds,
            files,
            query,
            total_results,
            filtered_search,
        )
        await safe_edit(
            query.message,
            cap,
            reply_markup=InlineKeyboardMarkup(btn),
        )
    else:
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(btn)
            )
        except MessageNotModified:
            pass

    await query.answer(
        "✅ Combined Files filter enabled"
        if enable_combined
        else "✅ Showing all matching files"
    )


#Qualities 
@Client.on_callback_query(filters.regex(r"^qualities#"))
async def qualities_cb_handler(client: Client, query: CallbackQuery):

    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    _, key = query.data.split("#")
    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(
            script.OLD_ALRT_TXT.format(query.from_user.first_name),
            show_alert=True,
        )
    btn = []
    available_qualities = [quality for quality in QUALITIES if quality]
    for i in range(0, len(available_qualities), 2):
        row = []
        for quality in available_qualities[i:i + 2]:
            row.append(
                InlineKeyboardButton(
                    text=quality.upper(),
                    callback_data=f"fq#{quality.lower()}#{key}",
                )
            )
        btn.append(row)

    btn.insert(
        0,
        [
            InlineKeyboardButton(
                text="⇊ ꜱᴇʟᴇᴄᴛ ǫᴜᴀʟɪᴛʏ ⇊", callback_data="ident"
            )
        ],
    )
    req = query.from_user.id
    btn.append([
        InlineKeyboardButton(
            text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
            callback_data=f"next_{req}_{key}_0",
        )
    ])

    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
 

@Client.on_callback_query(filters.regex(r"^fq#"))
async def filter_qualities_cb_handler(client: Client, query: CallbackQuery):
    _, qual, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(
            script.OLD_ALRT_TXT.format(query.from_user.first_name),
            show_alert=True,
        )
    search = search.replace("_", " ")
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    if qual == "homepage":
        search = make_filter_query(search, quality=None)
    else:
        search = make_filter_query(search, quality=qual)
    BUTTONS[key] = search

    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings['file_secure'] else 'file'
    if settings["button"]:
        btn = [
            [
                InlineKeyboardButton(
                    text=clean_file_button_text(file), callback_data=f'{pre}#{file.file_id}'
                ),
            ]
            for file in files
        ]
        btn.insert(0, 
            [
                InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
                InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
                InlineKeyboardButton("📺 Season",  callback_data=f"seasons#{key}")
            ]
        )
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))

    else:
        btn = []
        btn.insert(0, 
            [
                InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
                InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
                InlineKeyboardButton("📺 Season",  callback_data=f"seasons#{key}")
            ]
        )
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))

    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages"), InlineKeyboardButton(text="NEXT ➢",callback_data=f"next_{req}_{key}_{offset}")]
                )
    
            else:
                btn.append(
                    [InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}",callback_data="pages"), InlineKeyboardButton(text="NEXT ➢",callback_data=f"next_{req}_{key}_{offset}")]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages"), InlineKeyboardButton(text="NEXT ➢",callback_data=f"next_{req}_{key}_{offset}")]
            )
    else:
        btn.append(
            [InlineKeyboardButton(text="↭ No More Pages ↭",callback_data="pages")]
        )
    
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        await safe_edit(
            query.message,
            cap,
            reply_markup=InlineKeyboardMarkup(btn),
        )
    else:
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(btn)
            )
        except MessageNotModified:
            pass
    await query.answer()

#languages

@Client.on_callback_query(filters.regex(r"^languages#"))
async def languages_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass

    _, key = query.data.split("#", 1)
    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)

    btn = []
    for i in range(0, len(LANGUAGES), 2):
        row = []
        text1, code1 = _language_item(LANGUAGES[i])
        row.append(InlineKeyboardButton(text=text1, callback_data=f"fl#{code1}#{key}"))
        if i + 1 < len(LANGUAGES):
            text2, code2 = _language_item(LANGUAGES[i + 1])
            row.append(InlineKeyboardButton(text=text2, callback_data=f"fl#{code2}#{key}"))
        btn.append(row)

    btn.insert(0, [InlineKeyboardButton("🌐 Select Language", callback_data="pages")])
    btn.append([InlineKeyboardButton(text="🔙 Back To Files", callback_data=f"next_{query.from_user.id}_{key}_0")])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
    await query.answer()


@Client.on_callback_query(filters.regex(r"^fl#"))
async def filter_languages_cb_handler(client: Client, query: CallbackQuery):
    _, lang, key = query.data.split("#", 2)
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message

    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass

    if lang == "homepage":
        query.data = f"next_{req}_{key}_0"
        return await next_page(client, query)

    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    search = search.replace("_", " ")
    search = make_filter_query(search, language=lang)
    BUTTONS[key] = search

    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return

    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings['file_secure'] else 'file'
    if settings["button"]:
        btn = [[InlineKeyboardButton(text=clean_file_button_text(file), callback_data=f'{pre}#{file.file_id}')] for file in files]
        btn.insert(0, [
            InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
            InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
            InlineKeyboardButton("📺 Season", callback_data=f"seasons#{key}")
        ])
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))
    else:
        btn = []
        btn.insert(0, [
            InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
            InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
            InlineKeyboardButton("📺 Season", callback_data=f"seasons#{key}")
        ])
        btn.insert(0, _action_filter_row(key))

    if offset != "":
        try:
            if settings['max_btn']:
                btn.append([InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="NEXT ➢", callback_data=f"next_{req}_{key}_{offset}")])
            else:
                btn.append([InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="NEXT ➢", callback_data=f"next_{req}_{key}_{offset}")])
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append([InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="NEXT ➢", callback_data=f"next_{req}_{key}_{offset}")])
    else:
        btn.append([InlineKeyboardButton(text="↭ No More Pages ↭", callback_data="pages")])

    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        await safe_edit(
            query.message,
            cap,
            reply_markup=InlineKeyboardMarkup(btn),
        )
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer(f"✅ {_language_display(lang)} selected")


@Client.on_callback_query(filters.regex(r"^seasons#"))
async def seasons_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    _, key = query.data.split("#")
    # if BUTTONS.get(key+"2")!=None:
    #     search = BUTTONS.get(key+"2")
    # else:
    #     search = BUTTONS.get(key)
    #     BUTTONS[key+"2"] = search
    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(
            script.OLD_ALRT_TXT.format(query.from_user.first_name),
            show_alert=True,
        )
    btn = []
    for i in range(0, len(SEASONS)-1, 2):
        btn.append([
            InlineKeyboardButton(
                text=SEASONS[i].title(),
                callback_data=f"fs#{SEASONS[i].lower()}#{key}"
            ),
            InlineKeyboardButton(
                text=SEASONS[i+1].title(),
                callback_data=f"fs#{SEASONS[i+1].lower()}#{key}"
            ),
        ])

    btn.insert(
        0,
        [
            InlineKeyboardButton(
                text="⇊ ꜱᴇʟᴇᴄᴛ ꜱᴇᴀꜱᴏɴ ⇊", callback_data="ident"
            )
        ],
    )
    req = query.from_user.id
    offset = 0
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ​↭", callback_data=f"next_{req}_{key}_{offset}")])

    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex(r"^fs#"))
async def filter_seasons_cb_handler(client: Client, query: CallbackQuery):
    _, seas, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(
            script.OLD_ALRT_TXT.format(query.from_user.first_name),
            show_alert=True,
        )
    search = search.replace("_", " ")
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass

    search = make_filter_query(search, season=seas)
    BUTTONS[key] = search
    # Keep the old state keys compatible with legacy Send All callbacks.
    BUTTONS0[key] = search
    BUTTONS1[key] = search
    BUTTONS2[key] = search
    files, offset, total_results = await get_search_results(
        chat_id,
        search,
        offset=0,
        filter=True,
    )
        
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings['file_secure'] else 'file'
    if settings["button"]:
        btn = [
            [
                InlineKeyboardButton(
                    text=clean_file_button_text(file), callback_data=f'{pre}#{file.file_id}'
                ),
            ]
            for file in files
        ]
        btn.insert(0, [
            InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
            InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
            InlineKeyboardButton("📺 Season", callback_data=f"seasons#{key}")
        ])
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))
    else:
        btn = []
        btn.insert(0, 
            [
                InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
                InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
                InlineKeyboardButton("📺 Season",  callback_data=f"seasons#{key}")
            ]
        )
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))
    btn.append(
        _filter_nav_row(settings, req, key, offset, total_results)
    )
    
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        await safe_edit(
            query.message,
            cap,
            reply_markup=InlineKeyboardMarkup(btn),
        )
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()



#episodes
@Client.on_callback_query(filters.regex(r"^episodes#"))
async def episodes_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    parts = query.data.split("#")
    key = parts[1]
    try:
        selector_page = max(0, int(parts[2]))
    except (IndexError, TypeError, ValueError):
        selector_page = 0
    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(
            script.OLD_ALRT_TXT.format(query.from_user.first_name),
            show_alert=True,
        )

    episode_counts = await _available_episode_counts(search)
    if not episode_counts:
        return await query.answer(
            "🚫 No episode numbers were found in these files.",
            show_alert=True,
        )

    episode_items = list(episode_counts.items())
    page_size = 100
    page_count = max(1, math.ceil(len(episode_items) / page_size))
    selector_page = min(selector_page, page_count - 1)
    start = selector_page * page_size
    visible_episodes = episode_items[start:start + page_size]

    btn = []
    for i in range(0, len(visible_episodes), 4):
        row = []
        for episode, count in visible_episodes[i:i + 4]:
            ep = f"E{episode:02d}"
            row.append(
                InlineKeyboardButton(
                    text=f"{ep} • {count}",
                    callback_data=f"fe#{ep.lower()}#{key}",
                )
            )
        btn.append(row)
    btn.insert(
        0,
        [
            InlineKeyboardButton(
                text=(
                    f"⇊ SELECT EPISODE • {len(episode_items)} AVAILABLE "
                    f"• {selector_page + 1}/{page_count} ⇊"
                ),
                callback_data="ident",
            )
        ],
    )

    if page_count > 1:
        nav = []
        if selector_page > 0:
            nav.append(
                InlineKeyboardButton(
                    "⬅ PREV",
                    callback_data=f"episodes#{key}#{selector_page - 1}",
                )
            )
        if selector_page + 1 < page_count:
            nav.append(
                InlineKeyboardButton(
                    "NEXT ➡",
                    callback_data=f"episodes#{key}#{selector_page + 1}",
                )
            )
        btn.append(nav)

    req = query.from_user.id
    btn.append([
        InlineKeyboardButton(
            text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
            callback_data=f"next_{req}_{key}_0",
        )
    ])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
    await query.answer(
        f"✅ {len(episode_items)} available episodes found across all results"
    )


@Client.on_callback_query(filters.regex(r"^fe#"))
async def filter_episodes_cb_handler(client: Client, query: CallbackQuery):
    _, ep, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = BUTTONS.get(key) or FRESH.get(key)
    if not search:
        return await query.answer(
            script.OLD_ALRT_TXT.format(query.from_user.first_name),
            show_alert=True,
        )
    search = search.replace("_", " ")
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    search = make_filter_query(search, episode=ep)
    BUTTONS[key] = search
    # The database regex accepts E01, E1, E 01, S01E01, and S01 E01.
    files, offset, total_results = await get_search_results(
        chat_id,
        search,
        offset=0,
        filter=True,
    )
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings['file_secure'] else 'file'
    if settings["button"]:
        btn = [
            [InlineKeyboardButton(text=clean_file_button_text(file), callback_data=f'{pre}#{file.file_id}')]
            for file in files
        ]
        btn.insert(0, [
            InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
            InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
            InlineKeyboardButton("📺 Season", callback_data=f"seasons#{key}")
        ])
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))
    else:
        btn = []
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, [
            InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
            InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
            InlineKeyboardButton("📺 Season", callback_data=f"seasons#{key}")
        ])
        btn.insert(0, _action_filter_row(key))
    btn.append(
        _filter_nav_row(settings, req, key, offset, total_results)
    )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        await safe_edit(
            query.message,
            cap,
            reply_markup=InlineKeyboardMarkup(btn),
        )
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()


#years
@Client.on_callback_query(filters.regex(r"^years#"))
async def years_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    _, key = query.data.split("#")
    search = FRESH.get(key)
    BUTTONS[key] = None
    btn = []
    years_list = ["1900"] + [str(y) for y in range(1991, 2031)]
    for i in range(0, len(years_list), 4):
        row = []
        for yr in years_list[i:i+4]:
            row.append(InlineKeyboardButton(text=yr, callback_data=f"fy#{yr}#{key}"))
        btn.append(row)
    btn.insert(0, [InlineKeyboardButton(text="⇊ ꜱᴇʟᴇᴄᴛ ʏᴇᴀʀ ⇊", callback_data="ident")])
    req = query.from_user.id
    offset = 0
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭", callback_data=f"next_{req}_{key}_{offset}")])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex(r"^fy#"))
async def filter_years_cb_handler(client: Client, query: CallbackQuery):
    _, yr, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    search = search.replace("_", " ")
    yr_pattern = re.compile(r'\b(19|20)\d{2}\b')
    search = yr_pattern.sub('', search).strip()
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    search = f"{search} {yr}"
    BUTTONS[key] = search
    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    files = [file for file in files if re.search(yr, file.file_name, re.IGNORECASE)]
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings['file_secure'] else 'file'
    if settings["button"]:
        btn = [
            [InlineKeyboardButton(text=clean_file_button_text(file), callback_data=f'{pre}#{file.file_id}')]
            for file in files
        ]
        btn.insert(0, [InlineKeyboardButton("📅 ꜱᴇʟᴇᴄᴛ ᴀɢᴀɪɴ", callback_data=f"years#{key}")])
    else:
        btn = []
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, [
            InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
            InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
            InlineKeyboardButton("📺 Season", callback_data=f"seasons#{key}")
        ])
        btn.insert(0, _action_filter_row(key))
    offset = 0
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭", callback_data=f"next_{req}_{key}_{offset}")])
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        total_results = len(files)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        await safe_edit(
            query.message,
            cap,
            reply_markup=InlineKeyboardMarkup(btn),
        )
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()


                              
async def safe_edit(msg, text, reply_markup=None, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True):
    """Edit text or caption depending on message type."""
    try:
        await msg.edit_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview
        )
        return msg
    except MessageNotModified:
        return msg
    except Exception:
        try:
            await msg.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            return msg
        except MessageNotModified:
            return msg
        except Exception:
            try:
                replacement = await msg.reply_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                await msg.delete()
                return replacement
            except Exception:
                logger.exception("Unable to update filtered search results")
                return None


@Client.on_callback_query()
async def cb_handler(client: Client, query: CallbackQuery):
    lazyData = query.data
    link = None
    try:
        link = await client.create_chat_invite_link(int(REQST_CHANNEL))
    except:
        pass
    if query.data == "close_data":
        await query.message.delete()
        await query.answer()
    elif query.data == "gfiltersdeleteallconfirm":
        await del_allg(query.message, 'gfilters')
        await query.answer("ᴅᴏɴᴇ !")
        return
    elif query.data == "gfiltersdeleteallcancel": 
        await query.message.reply_to_message.delete()
        await query.message.delete()
        await query.answer("ᴘʀᴏᴄᴇꜱꜱ ᴄᴀɴᴄᴇʟʟᴇᴅ !")
        return
    elif query.data == "delallconfirm":
        userid = query.from_user.id
        chat_type = query.message.chat.type

        if chat_type == enums.ChatType.PRIVATE:
            grpid = await active_connection(str(userid))
            if grpid is not None:
                grp_id = grpid
                try:
                    chat = await client.get_chat(grpid)
                    title = chat.title
                except:
                    await query.message.edit_text("Mᴀᴋᴇ sᴜʀᴇ I'ᴍ ᴘʀᴇsᴇɴᴛ ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ!!", quote=True)
                    return await query.answer(MSG_ALRT)
            else:
                await query.message.edit_text(
                    "I'ᴍ ɴᴏᴛ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴛᴏ ᴀɴʏ ɢʀᴏᴜᴘs !\nCʜᴇᴄᴋ /connections ᴏʀ ᴄᴏɴɴᴇᴄᴛ ᴛᴏ ᴀɴʏ ɢʀᴏᴜᴘs.",
                    quote=True
                )
                return await query.answer(MSG_ALRT)

        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            title = query.message.chat.title

        else:
            return await query.answer(MSG_ALRT)

        st = await client.get_chat_member(grp_id, userid)
        if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
            await del_all(query.message, grp_id, title)
        else:
            await query.answer("Yᴏᴜ ɴᴇᴇᴅ ᴛᴏ ʙᴇ Gʀᴏᴜᴘ Oᴡɴᴇʀ ᴏʀ ᴀɴ Aᴜᴛʜ Usᴇʀ ᴛᴏ ᴅᴏ ᴛʜᴀᴛ !", show_alert=True)
    elif query.data == "delallcancel":
        userid = query.from_user.id
        chat_type = query.message.chat.type

        if chat_type == enums.ChatType.PRIVATE:
            await query.message.reply_to_message.delete()
            await query.message.delete()

        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            st = await client.get_chat_member(grp_id, userid)
            if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
                await query.message.delete()
                try:
                    await query.message.reply_to_message.delete()
                except:
                    pass
            else:
                await query.answer("Tʜᴀᴛ's ɴᴏᴛ ғᴏʀ ʏᴏᴜ!!", show_alert=True)
    elif "groupcb" in query.data:
        await query.answer()

        group_id = query.data.split(":")[1]

        act = query.data.split(":")[2]
        hr = await client.get_chat(int(group_id))
        title = hr.title
        user_id = query.from_user.id

        if act == "":
            stat = "ᴄᴏɴɴᴇᴄᴛ"
            cb = "connectcb"
        else:
            stat = "ᴅɪꜱᴄᴏɴɴᴇᴄᴛ"
            cb = "disconnect"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{stat}", callback_data=f"{cb}:{group_id}"),
             InlineKeyboardButton("ᴅᴇʟᴇᴛᴇ", callback_data=f"deletecb:{group_id}")],
            [InlineKeyboardButton("ʙᴀᴄᴋ", callback_data="backcb")]
        ])

        await query.message.edit_text(
            f"Gʀᴏᴜᴘ Nᴀᴍᴇ : **{title}**\nGʀᴏᴜᴘ ID : `{group_id}`",
            reply_markup=keyboard,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return await query.answer(MSG_ALRT)
    elif "connectcb" in query.data:
        await query.answer()

        group_id = query.data.split(":")[1]

        hr = await client.get_chat(int(group_id))

        title = hr.title

        user_id = query.from_user.id

        mkact = await make_active(str(user_id), str(group_id))

        if mkact:
            await query.message.edit_text(
                f"Cᴏɴɴᴇᴄᴛᴇᴅ ᴛᴏ **{title}**",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        else:
            await query.message.edit_text('Sᴏᴍᴇ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ!!', parse_mode=enums.ParseMode.MARKDOWN)
        return await query.answer(MSG_ALRT)
    elif "disconnect" in query.data:
        await query.answer()

        group_id = query.data.split(":")[1]

        hr = await client.get_chat(int(group_id))

        title = hr.title
        user_id = query.from_user.id

        mkinact = await make_inactive(str(user_id))

        if mkinact:
            await query.message.edit_text(
                f"Dɪsᴄᴏɴɴᴇᴄᴛᴇᴅ ғʀᴏᴍ **{title}**",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        else:
            await query.message.edit_text(
                f"Sᴏᴍᴇ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ!!",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        return await query.answer(MSG_ALRT)
    elif "deletecb" in query.data:
        await query.answer()

        user_id = query.from_user.id
        group_id = query.data.split(":")[1]

        delcon = await delete_connection(str(user_id), str(group_id))

        if delcon:
            await query.message.edit_text(
                "Sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ᴄᴏɴɴᴇᴄᴛɪᴏɴ !"
            )
        else:
            await query.message.edit_text(
                f"Sᴏᴍᴇ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ!!",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        return await query.answer(MSG_ALRT)
    elif query.data == "backcb":
        await query.answer()

        userid = query.from_user.id

        groupids = await all_connections(str(userid))
        if groupids is None:
            await query.message.edit_text(
                "Tʜᴇʀᴇ ᴀʀᴇ ɴᴏ ᴀᴄᴛɪᴠᴇ ᴄᴏɴɴᴇᴄᴛɪᴏɴs!! Cᴏɴɴᴇᴄᴛ ᴛᴏ sᴏᴍᴇ ɢʀᴏᴜᴘs ғɪʀsᴛ.",
            )
            return await query.answer(MSG_ALRT)
        buttons = []
        for groupid in groupids:
            try:
                ttl = await client.get_chat(int(groupid))
                title = ttl.title
                active = await if_active(str(userid), str(groupid))
                act = " - ACTIVE" if active else ""
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"{title}{act}", callback_data=f"groupcb:{groupid}:{act}"
                        )
                    ]
                )
            except:
                pass
        if buttons:
            await query.message.edit_text(
                "Yᴏᴜʀ ᴄᴏɴɴᴇᴄᴛᴇᴅ ɢʀᴏᴜᴘ ᴅᴇᴛᴀɪʟs ;\n\n",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

    elif "gfilteralert" in query.data:
        grp_id = query.message.chat.id
        i = query.data.split(":")[1]
        keyword = query.data.split(":")[2]
        reply_text, btn, alerts, fileid = await find_gfilter('gfilters', keyword)
        if alerts is not None:
            alerts = ast.literal_eval(alerts)
            alert = alerts[int(i)]
            alert = alert.replace("\\n", "\n").replace("\\t", "\t")
            await query.answer(alert, show_alert=True)

    elif "alertmessage" in query.data:
        grp_id = query.message.chat.id
        i = query.data.split(":")[1]
        keyword = query.data.split(":")[2]
        reply_text, btn, alerts, fileid = await find_filter(grp_id, keyword)
        if alerts is not None:
            alerts = ast.literal_eval(alerts)
            alert = alerts[int(i)]
            alert = alert.replace("\\n", "\n").replace("\\t", "\t")
            await query.answer(alert, show_alert=True)
        
    if query.data.startswith("file"):
        clicked = query.from_user.id
        ident, file_id = query.data.split("#", 1)
        files_ = await get_file_details(file_id)
        if not files_:
            return await query.answer('Nᴏ sᴜᴄʜ ғɪʟᴇ ᴇxɪsᴛ.')

        # A deep link to the same bot is unreliable from inside its own PM.
        # Invoke the existing /start flow directly so force-subscribe,
        # verification, shortlink, HD thumbnail copy, and auto-delete behavior
        # stay exactly the same as a file opened from a group result.
        if query.message.chat.type == enums.ChatType.PRIVATE:
            return await _run_private_delivery(
                client,
                query,
                f"{ident}_{file_id}",
                (clicked, "file", file_id),
            )

        pre_user = await db.has_premium_access(clicked)
        settings = await get_settings(query.message.chat.id)
        shortlink_enabled = bool(settings.get('is_shortlink')) and not pre_user

        try:
            temp.SHORT[clicked] = query.message.chat.id
            new_row = build_download_action_buttons(
                temp.U_NAME,
                ident,
                file_id,
                shortlink=shortlink_enabled,
            )
            await replace_clicked_button_row(query, new_row, fallback_text="⇊ Your Link Is Ready ⇊")
            await query.answer("✅ Link buttons are ready here")
            return
        except MessageNotModified:
            await query.answer("✅ Link already ready")
            return
        except UserIsBlocked:
            await query.answer('Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴍᴀʜɴ !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start={ident}_{file_id}")
        except Exception as e:
            logger.exception(e)
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start={ident}_{file_id}")
  
    elif query.data.startswith("sendfiles"):
        clicked = query.from_user.id
        ident, key = query.data.split("#", 1)
        if query.message.chat.type == enums.ChatType.PRIVATE:
            return await _run_private_delivery(
                client,
                query,
                f"allfiles_{key}",
                (clicked, "allfiles", key),
            )
        pre_user = await db.has_premium_access(clicked)
        settings = await get_settings(query.message.chat.id)
        shortlink_enabled = bool(settings.get('is_shortlink')) and not pre_user
        try:
            new_row = build_sendall_action_buttons(
                temp.U_NAME,
                key,
                shortlink=shortlink_enabled,
            )
            await replace_clicked_button_row(query, new_row, fallback_text="⇊ Send All Link Is Ready ⇊")
            await query.answer("✅ Send All buttons are ready here")
            return
        except MessageNotModified:
            await query.answer("✅ Link already ready")
            return
        except UserIsBlocked:
            await query.answer('Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴍᴀʜɴ !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=allfiles_{key}")
        except Exception as e:
            logger.exception(e)
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=allfiles_{key}")
    

    elif query.data.startswith("del"):
        ident, file_id = query.data.split("#")
        files_ = await get_file_details(file_id)
        if not files_:
            return await query.answer('Nᴏ sᴜᴄʜ ғɪʟᴇ ᴇxɪsᴛ.')
        files = files_[0]
        title = files.file_name
        size = get_size(files.file_size)
        f_caption = files.caption
        settings = await get_settings(query.message.chat.id)
        if CUSTOM_FILE_CAPTION:
            try:
                f_caption = CUSTOM_FILE_CAPTION.format(file_name='' if title is None else title,
                                                       file_size='' if size is None else size,
                                                       file_caption='' if f_caption is None else f_caption)
            except Exception as e:
                logger.exception(e)
            f_caption = f_caption
        if f_caption is None:
            f_caption = f"{files.file_name}"
        await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=file_{file_id}")

    elif query.data.startswith("checksub"):
        ident, kk, file_id = query.data.split("#")
        channels = (await get_settings(int(query.from_user.id))).get('fsub')  
        if channels:
            btn = await is_subscribed(client, query, channels)
            if btn:
                await query.answer(
                    f"👋 Hello {query.from_user.first_name},\n\n"
                    "Yᴏᴜ ʜᴀᴠᴇ ɴᴏᴛ ᴊᴏɪɴᴇᴅ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴜᴘᴅᴀᴛᴇ Cʜᴀɴɴᴇʟs.\n"
                    "Pʟᴇᴀsᴇ ᴊᴏɪɴ ᴇᴀᴄʜ ᴄʜᴀɴɴᴇʟ ʟɪsᴛᴇᴅ ʙᴇʟᴏᴡ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.\n\n",
                    show_alert=True
                )
                btn.append([InlineKeyboardButton("♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️", callback_data=f"checksub#{kk}#{file_id}")])
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
                return
        await query.answer(url=f"https://t.me/{temp.U_NAME}?start={kk}_{file_id}")
        await query.message.delete()

    elif query.data == "pages":
        await query.answer()
    
    elif query.data.startswith("send_fsall"):
        temp_var, ident, key, offset = query.data.split("#")
        searches = list(
            dict.fromkeys(
                item
                for item in (
                    BUTTONS0.get(key),
                    BUTTONS1.get(key),
                    BUTTONS2.get(key),
                )
                if item
            )
        )
        if not searches:
            await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name),show_alert=True)
            return
        for search in searches:
            files, n_offset, total = await get_search_results(
                query.message.chat.id,
                search,
                offset=int(offset),
                filter=True,
            )
            await send_all(
                client,
                query.from_user.id,
                files,
                ident,
                query.message.chat.id,
                query.from_user.first_name,
                query,
            )
        await query.answer(f"ʜᴇʏ {query.from_user.first_name}, ᴀʟʟ ꜰɪʟᴇꜱ ᴏɴ ᴛʜɪꜱ ᴘᴀɢᴇ ʜᴀꜱ ʙᴇᴇɴ ꜱᴇɴᴛ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴛᴏ ʏᴏᴜ ʙʏ ᴅᴍ !", show_alert=True)
        
    elif query.data.startswith("send_fall"):
        temp_var, ident, key, offset = query.data.split("#")
        if BUTTONS.get(key)!=None:
            search = BUTTONS.get(key)
        else:
            search = FRESH.get(key)
        if not search:
            await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name),show_alert=True)
            return
        files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=int(offset), filter=True)
        await send_all(client, query.from_user.id, files, ident, query.message.chat.id, query.from_user.first_name, query)
        await query.answer(f"ʜᴇʏ {query.from_user.first_name}, ᴀʟʟ ꜰɪʟᴇꜱ ᴏɴ ᴛʜɪꜱ ᴘᴀɢᴇ ʜᴀꜱ ʙᴇᴇɴ ꜱᴇɴᴛ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴛᴏ ʏᴏᴜ ʙʏ ᴅᴍ !", show_alert=True)
        
    elif query.data.startswith("killfilesdq"):
        ident, keyword = query.data.split("#")
        await query.message.edit_text(f"<b>Fetching Files for your query {keyword} on DB... Please wait...</b>")
        files, total = await get_bad_files(keyword)
        await query.message.edit_text("<b>ꜰɪʟᴇ ᴅᴇʟᴇᴛɪᴏɴ ᴘʀᴏᴄᴇꜱꜱ ᴡɪʟʟ ꜱᴛᴀʀᴛ ɪɴ 5 ꜱᴇᴄᴏɴᴅꜱ !</b>")
        await asyncio.sleep(5)
        deleted = 0
        async with lock:
            try:
                for file in files:
                    file_ids = file.file_id
                    file_name = file.file_name
                    result = await Media.collection.delete_one({
                        '_id': file_ids,
                    })
                    if not result.deleted_count:
                        result = await Media2.collection.delete_one({
                            '_id': file_ids,
                        })
                    if result.deleted_count:
                        logger.info(f'ꜰɪʟᴇ ꜰᴏᴜɴᴅ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword}! ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {file_name} ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.')
                    deleted += 1
                    if deleted % 20 == 0:
                        await query.message.edit_text(f"<b>ᴘʀᴏᴄᴇꜱꜱ ꜱᴛᴀʀᴛᴇᴅ ꜰᴏʀ ᴅᴇʟᴇᴛɪɴɢ ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ. ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {str(deleted)} ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword} !\n\nᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ...</b>")
            except Exception as e:
                logger.exception(e)
                await query.message.edit_text(f'Error: {e}')
            else:
                await query.message.edit_text(f"<b>ᴘʀᴏᴄᴇꜱꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ꜰᴏʀ ꜰɪʟᴇ ᴅᴇʟᴇᴛᴀᴛɪᴏɴ !\n\nꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {str(deleted)} ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword}.</b>")
    
    elif query.data.startswith("opnsetgrp"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        st = await client.get_chat_member(grp_id, userid)
        if (
                st.status != enums.ChatMemberStatus.ADMINISTRATOR
                and st.status != enums.ChatMemberStatus.OWNER
                and str(userid) not in ADMINS
        ):
            await query.answer("ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ʀɪɢʜᴛꜱ ᴛᴏ ᴅᴏ ᴛʜɪꜱ !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        if settings is not None:
            buttons = [
                [
                    InlineKeyboardButton('ʀᴇꜱᴜʟᴛ ᴘᴀɢᴇ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ʙᴜᴛᴛᴏɴ' if settings["button"] else 'ᴛᴇxᴛ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜰɪʟᴇ ꜱᴇɴᴅ ᴍᴏᴅᴇ', callback_data=f'setgs#botpm#{settings["botpm"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ꜱᴛᴀʀᴛ' if settings["botpm"] else 'ᴀᴜᴛᴏ',
                                         callback_data=f'setgs#botpm#{settings["botpm"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜰɪʟᴇ ꜱᴇᴄᴜʀᴇ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["file_secure"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴛᴍᴅʙ ᴘᴏꜱᴛᴇʀ', callback_data=f'setgs#tmdb_poster#{settings["tmdb_poster"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["tmdb_poster"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#tmdb_poster#{settings["tmdb_poster"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴛᴍᴅʙ ʙᴀᴄᴋᴅʀᴏᴘ', callback_data=f'setgs#tmdb_backdrop#{settings["tmdb_backdrop"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["tmdb_backdrop"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#tmdb_backdrop#{settings["tmdb_backdrop"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜱᴘᴇʟʟ ᴄʜᴇᴄᴋ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["spell_check"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴡᴇʟᴄᴏᴍᴇ ᴍꜱɢ', callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["welcome"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["auto_delete"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["auto_ffilter"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴍᴀx ʙᴜᴛᴛᴏɴꜱ',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}'),
                    InlineKeyboardButton('10' if settings["max_btn"] else f'{MAX_B_TN}',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜱʜᴏʀᴛʟɪɴᴋ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["is_shortlink"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('⇋ ᴄʟᴏꜱᴇ ꜱᴇᴛᴛɪɴɢꜱ ᴍᴇɴᴜ ⇋', 
                                         callback_data='close_data'
                                         )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            await safe_edit(
                query.message,
                text=f"<b>ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ꜱᴇᴛᴛɪɴɢꜱ ꜰᴏʀ {title} ᴀꜱ ʏᴏᴜ ᴡɪꜱʜ ⚙</b>",
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML
            )
        
    elif query.data.startswith("opnsetpm"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        st = await client.get_chat_member(grp_id, userid)
        if (
                st.status != enums.ChatMemberStatus.ADMINISTRATOR
                and st.status != enums.ChatMemberStatus.OWNER
                and str(userid) not in ADMINS
        ):
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        btn2 = [[
                 InlineKeyboardButton("ᴄʜᴇᴄᴋ ᴍʏ ᴅᴍ 🗳️", url=f"telegram.me/{temp.U_NAME}")
               ]]
        reply_markup = InlineKeyboardMarkup(btn2)
        await safe_edit(
            query.message,
            text=f"<b>ʏᴏᴜʀ sᴇᴛᴛɪɴɢs ᴍᴇɴᴜ ғᴏʀ {title} ʜᴀs ʙᴇᴇɴ sᴇɴᴛ ᴛᴏ ʏᴏᴜ ʙʏ ᴅᴍ.</b>",
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        if settings is not None:
            buttons = [
                [
                    InlineKeyboardButton('ʀᴇꜱᴜʟᴛ ᴘᴀɢᴇ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ʙᴜᴛᴛᴏɴ' if settings["button"] else 'ᴛᴇxᴛ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜰɪʟᴇ ꜱᴇɴᴅ ᴍᴏᴅᴇ', callback_data=f'setgs#botpm#{settings["botpm"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ꜱᴛᴀʀᴛ' if settings["botpm"] else 'ᴀᴜᴛᴏ',
                                         callback_data=f'setgs#botpm#{settings["botpm"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜰɪʟᴇ ꜱᴇᴄᴜʀᴇ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["file_secure"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴛᴍᴅʙ ᴘᴏꜱᴛᴇʀ', callback_data=f'setgs#tmdb_poster#{settings["tmdb_poster"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["tmdb_poster"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#tmdb_poster#{settings["tmdb_poster"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴛᴍᴅʙ ʙᴀᴄᴋᴅʀᴏᴘ', callback_data=f'setgs#tmdb_backdrop#{settings["tmdb_backdrop"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["tmdb_backdrop"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#tmdb_backdrop#{settings["tmdb_backdrop"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜱᴘᴇʟʟ ᴄʜᴇᴄᴋ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["spell_check"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴡᴇʟᴄᴏᴍᴇ ᴍꜱɢ', callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["welcome"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["auto_delete"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["auto_ffilter"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴍᴀx ʙᴜᴛᴛᴏɴꜱ',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}'),
                    InlineKeyboardButton('10' if settings["max_btn"] else f'{MAX_B_TN}',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜱʜᴏʀᴛʟɪɴᴋ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["is_shortlink"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('⇋ ᴄʟᴏꜱᴇ ꜱᴇᴛᴛɪɴɢꜱ ᴍᴇɴᴜ ⇋', 
                                         callback_data='close_data'
                                         )
                ]
        ]
            reply_markup = InlineKeyboardMarkup(buttons)
            await client.send_message(
                chat_id=userid,
                text=f"<b>ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ꜱᴇᴛᴛɪɴɢꜱ ꜰᴏʀ {title} ᴀꜱ ʏᴏᴜ ᴡɪꜱʜ ⚙</b>",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=query.message.id
            )

    elif query.data.startswith("show_option"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️", callback_data=f"unavailable#{from_user}"),
                InlineKeyboardButton("🟢 ᴜᴘʟᴏᴀᴅᴇᴅ 🟢", callback_data=f"uploaded#{from_user}")
             ],[
                InlineKeyboardButton("♻️ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️", callback_data=f"already_available#{from_user}")
             ],[
                InlineKeyboardButton("📌 Not Released 📌", callback_data=f"Not_Released#{from_user}"),
                InlineKeyboardButton("♨️Type Correct Spelling♨️", callback_data=f"Type_Correct_Spelling#{from_user}")
             ],[
                InlineKeyboardButton("⚜️ Not Available In The Hindi ⚜️", callback_data=f"Not_Available_In_The_Hindi#{from_user}")
             ]]
        btn2 = [[
                 InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Hᴇʀᴇ ᴀʀᴇ ᴛʜᴇ ᴏᴘᴛɪᴏɴs !")
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
        
    elif query.data.startswith("unavailable"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️", callback_data=f"unalert#{from_user}")
              ]]
        btn2 = [[
                 InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link if link else "#"),
                 InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, Sᴏʀʀʏ Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, Sᴏʀʀʏ Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
            
    elif query.data.startswith("Not_Released"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("📌 Not Released 📌", callback_data=f"unalert#{from_user}")
              ]]
        btn2 = [[
                 InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link if link else "#"),
                 InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, The movie you requested has not been released yet. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, The movie you requested has not been released yet. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("Type_Correct_Spelling"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("♨️ Type Correct Spelling ♨️", callback_data=f"unalert#{from_user}")
              ]]
        btn2 = [[
                 InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link if link else "#"),
                 InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Type Correct Spelling !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, The spelling of the movie you requested is incorrect. Please type the correct spelling of the movie name and try again.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, The spelling of the movie you requested is incorrect. Please type the correct spelling of the movie name and try again.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("Not_Available_In_The_Hindi"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("⚜️ Not Available In The Hindi ⚜️", callback_data=f"unalert#{from_user}")
              ]]
        btn2 = [[
                 InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link if link else "#"),
                 InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Not Available In The Hindi  !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, Your request is not available in the Hindi language. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, Your request is not available in the Hindi language. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("uploaded"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("🟢 ᴜᴘʟᴏᴀᴅᴇᴅ 🟢", callback_data=f"upalert#{from_user}")
              ]]
        btn2 = [[
                 InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link if link else "#"),
                 InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
               ],[
                 InlineKeyboardButton("🔍 ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Uᴘʟᴏᴀᴅᴇᴅ !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ʜᴀs ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs. Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ʜᴀs ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs. Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("already_available"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("♻️ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️", callback_data=f"alalert#{from_user}")
              ]]
        btn2 = [[
                 InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link if link else "#"),
                 InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
               ],[
                 InlineKeyboardButton("🔍 ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ᴏɴ ᴏᴜʀ ʙᴏᴛ's ᴅᴀᴛᴀʙᴀsᴇ. Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<u>{content}</u>\n\n<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ᴏɴ ᴏᴜʀ ʙᴏᴛ's ᴅᴀᴛᴀʙᴀsᴇ. Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
            
    
    elif query.data.startswith("alalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(f"Hᴇʏ {user.first_name}, Yᴏᴜʀ Rᴇᴏ̨ᴜᴇsᴛ ɪs Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ !", show_alert=True)
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("upalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(f"Hᴇʏ {user.first_name}, Yᴏᴜʀ Rᴇᴏ̨ᴜᴇsᴛ ɪs Uᴘʟᴏᴀᴅᴇᴅ !", show_alert=True)
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
        
    elif query.data.startswith("unalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(f"Hᴇʏ {user.first_name}, Yᴏᴜʀ Rᴇᴏ̨ᴜᴇsᴛ ɪs Uɴᴀᴠᴀɪʟᴀʙʟᴇ !", show_alert=True)
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    
    elif lazyData.startswith("generate_stream_link"):
        _, file_id = lazyData.split(":", 1)

        try:
            # Create stream/download links using LOG_CHANNEL only.
            # IMPORTANT: Do NOT send another file to the user chat here.
            log_msg = await client.send_cached_media(
                chat_id=LOG_CHANNEL,
                file_id=file_id,
            )

            name = get_name(log_msg) or "file"
            fileName = quote_plus(name)

            lazy_stream = f"{URL}watch/{str(log_msg.id)}/{fileName}?hash={get_hash(log_msg)}"
            lazy_download = f"{URL}{str(log_msg.id)}/{fileName}?hash={get_hash(log_msg)}"

            # Same file/message নিচেই button replace হবে, নতুন file/message user chat-এ যাবে না.
            await query.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🍿 Stream", url=lazy_stream),
                        InlineKeyboardButton("📂 Direct File", url=lazy_download)
                    ]
                ])
            )
            await query.answer("✅ Link ready here")
            return

        except MessageNotModified:
            await query.answer("✅ Link already ready")
            return
        except Exception as e:
            logger.exception(e)
            await query.answer(f"⚠️ SOMETHING WENT WRONG\n\n{e}", show_alert=True)
            return
            
       #@Rdx_movie_series
    
    elif query.data == "pagesn1":
        await query.answer(text=script.PAGE_TXT, show_alert=True)

    elif query.data == "reqinfo":
        await query.answer(text=script.REQINFO, show_alert=True)

    elif query.data == "select":
        await query.answer(text=script.SELECT, show_alert=True)

    elif query.data == "sinfo":
        await query.answer(text=script.SINFO, show_alert=True)

    elif query.data == "start":
        buttons = [[
                    InlineKeyboardButton('🔰 ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ 🔰', url=f'http://telegram.me/{temp.U_NAME}?startgroup=true')
                ],[
                    InlineKeyboardButton('ᴛᴏᴘ sᴇᴀʀᴄʜɪɴɢ ⭐', callback_data="topsearch"),
                    InlineKeyboardButton(' ʜᴇʟᴘ 📢', callback_data='help'),
                ],[
                    InlineKeyboardButton(' ᴀʙᴏᴜᴛ 📖', callback_data='about'),
                    InlineKeyboardButton('ᴇᴀʀɴ ᴍᴏɴᴇʏ..💵', callback_data="shortlink_info")
                ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        current_time = datetime.now(pytz.timezone(TIMEZONE))
        curr_time = current_time.hour        
        if curr_time < 12:
            gtxt = "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞" 
        elif curr_time < 17:
            gtxt = "ɢᴏᴏᴅ ᴀғᴛᴇʀɴᴏᴏɴ 🌓" 
        elif curr_time < 21:
            gtxt = "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌘"
        else:
            gtxt = "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌑"
        welcome_sources = list(PICS) + [WELCOME_BANNER]
        for welcome_photo in dict.fromkeys(welcome_sources):
            try:
                await client.edit_message_media(
                    query.message.chat.id,
                    query.message.id,
                    InputMediaPhoto(welcome_photo),
                )
                break
            except Exception as error:
                logger.warning(
                    "Start callback poster failed; trying fallback: %s",
                    error,
                )
        start_caption = script.START_TXT.format(
            query.from_user.mention,
            gtxt,
            temp.U_NAME,
            temp.B_NAME,
        )
        try:
            await query.message.edit_caption(
                caption=start_caption,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            await query.message.edit_text(
                text=start_caption,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        await query.answer(MSG_ALRT)

    elif query.data == "purchase":
        buttons = [[
            InlineKeyboardButton('💵 ᴘᴀʏ ᴠɪᴀ ᴜᴘɪ ɪᴅ 💵', callback_data='upi_info')
        ],[
            InlineKeyboardButton('📸 ꜱᴄᴀɴ ǫʀ ᴄᴏᴅᴇ 📸', callback_data='qr_info')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='premium_info')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/7519d226226bec1090db7.jpg")
        )
        await query.message.edit_caption(
            caption=script.PURCHASE_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "donation":
        buttons = [[
            InlineKeyboardButton('🌲 Sᴇɴᴅ Dᴏɴᴀᴛᴇ Sᴄʀᴇᴇɴsʜᴏᴛ Hᴇʀᴇ', url=OWNER_LNK)
        ],[
            InlineKeyboardButton('⇍ ʙᴀᴄᴋ ⇏', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto('https://graph.org/file/99eebf5dbe8a134f548e0.jpg')
        )
        await query.message.edit_caption(
            caption=script.DHAKAD_DONATION.format(query.from_user.mention, QR_CODE, OWNER_UPI_ID),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    
    elif query.data == "upi_info":
        buttons = [[
            InlineKeyboardButton('📲 ꜱᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ꜱᴄʀᴇᴇɴꜱʜᴏᴛ ʜᴇʀᴇ', url=OWNER_LNK)
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='purchase')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/7519d226226bec1090db7.jpg")
        )
        await query.message.edit_caption(
            caption=script.UPI_TXT.format(query.from_user.mention, OWNER_UPI_ID),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "qr_info":
        buttons = [[
            InlineKeyboardButton('📲 ꜱᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ꜱᴄʀᴇᴇɴꜱʜᴏᴛ ʜᴇʀᴇ', url=OWNER_LNK)
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='purchase')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/7519d226226bec1090db7.jpg")
        )
        await query.message.edit_caption(
            caption=script.QR_TXT.format(query.from_user.mention, QR_CODE),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "give_trial":
        user_id = query.from_user.id
        has_free_trial = await db.check_trial_status(user_id)
        if has_free_trial:
            await query.answer("🚸 ʏᴏᴜ'ᴠᴇ ᴀʟʀᴇᴀᴅʏ ᴄʟᴀɪᴍᴇᴅ ʏᴏᴜʀ ꜰʀᴇᴇ ᴛʀɪᴀʟ ᴏɴᴄᴇ !\n\n📌 ᴄʜᴇᴄᴋᴏᴜᴛ ᴏᴜʀ ᴘʟᴀɴꜱ ʙʏ : /plan", show_alert=True)
            return
        else:            
            await db.give_free_trial(user_id)
            await query.message.reply_text(
                text="<b>🥳 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ\n\n🎉 ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ꜰʀᴇᴇ ᴛʀᴀɪʟ ꜰᴏʀ <u>5 ᴍɪɴᴜᴛᴇs</u> ꜰʀᴏᴍ ɴᴏᴡ !</b>",
                quote=False,
                disable_web_page_preview=True,                  
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💸 ᴄʜᴇᴄᴋᴏᴜᴛ ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴꜱ 💸", callback_data='seeplans')]]))
            return    

    elif query.data == "seeplans":
        btn = [[
            InlineKeyboardButton('🤝🏻 Rᴇғᴇʀ & Gᴇᴛ Pʀᴇᴍɪᴜᴍ ', callback_data='reffff') 
        ],[
            InlineKeyboardButton('🥉 ʙʀᴏɴᴢᴇ ', callback_data='broze'),
            InlineKeyboardButton('🥈 ꜱɪʟᴠᴇʀ ', callback_data='silver')
        ],[
            InlineKeyboardButton('🥇 ɢᴏʟᴅ ', callback_data='gold'),
            InlineKeyboardButton('💘 ᴘʟᴀᴛɪɴᴜᴍ ', callback_data='platinum')
        ],[
            InlineKeyboardButton('💎 ᴅɪᴀᴍᴏɴᴅ ', callback_data='diamond'),
            InlineKeyboardButton('🤦 ᴏᴛʜᴇʀ ', callback_data='other')
        ],[
            InlineKeyboardButton('ɢᴇᴛ ғʀᴇᴇ ᴛʀᴀɪʟ ғᴏʀ 𝟻 ᴍɪɴᴜᴛᴇs ☺️', callback_data='free')
        ],[            
            InlineKeyboardButton('❌ ᴄʟᴏꜱᴇ ❌', callback_data='close_data')
        ]]
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.reply_photo(
            photo=(SUBSCRIPTION),
            caption=script.PREPLANS_TXT.format(query.from_user.mention, OWNER_UPI_ID, QR_CODE),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
    

    elif query.data == "premium_info":
        buttons = [[
            InlineKeyboardButton('🤝🏻 Rᴇғᴇʀ & Gᴇᴛ Pʀᴇᴍɪᴜᴍ ', callback_data='reffff'),
        ],[
            InlineKeyboardButton('🥉 ʙʀᴏɴᴢᴇ ', callback_data='broze'),
            InlineKeyboardButton('🥈 ꜱɪʟᴠᴇʀ ', callback_data='silver')
        ],[
            InlineKeyboardButton('🥇 ɢᴏʟᴅ ', callback_data='gold'),
            InlineKeyboardButton('💘 ᴘʟᴀᴛɪɴᴜᴍ ', callback_data='platinum')
        ],[
            InlineKeyboardButton('💎 ᴅɪᴀᴍᴏɴᴅ ', callback_data='diamond'),
            InlineKeyboardButton('🤦 ᴏᴛʜᴇʀ ', callback_data='other')
        ],[
            InlineKeyboardButton('ɢᴇᴛ ғʀᴇᴇ ᴛʀᴀɪʟ ғᴏʀ 𝟻 ᴍɪɴᴜᴛᴇs ☺️', callback_data='free')
        ],[            
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
        ]]
        
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/7519d226226bec1090db7.jpg")
        )
        await query.message.edit_caption(
            caption=script.PLAN_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        #@Deendayal_dhakad   
    elif query.data == "free":
        buttons = [[
            InlineKeyboardButton('⚜️ ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ɢᴇᴛ ꜰʀᴇᴇ ᴛʀɪᴀʟ', callback_data="give_trial")
        ],[
            InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='other'),
            InlineKeyboardButton('1 / 7', callback_data='pagesn1'),
            InlineKeyboardButton('ɴᴇxᴛ ⋟', callback_data='broze')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='premium_info')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_caption(
            caption=script.FREE_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        
    elif query.data == "broze":
        buttons = [[
            InlineKeyboardButton('🔐 ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ', callback_data='purchase')
        ],[
            InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='free'),
            InlineKeyboardButton('2 / 7', callback_data='pagesn1'),
            InlineKeyboardButton('ɴᴇxᴛ ⋟', callback_data='silver')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='premium_info')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/670f6df9f755dc2c9a00a.jpg")
        )
        await query.message.edit_caption(
            caption=script.BRONZE_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "silver":
        buttons = [[
            InlineKeyboardButton('🔐 ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ', callback_data='purchase')
        ],[
            InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='broze'),
            InlineKeyboardButton('3 / 7', callback_data='pagesn1'),
            InlineKeyboardButton('ɴᴇxᴛ ⋟', callback_data='gold')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='premium_info')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/670f6df9f755dc2c9a00a.jpg")
        )
        await query.message.edit_caption(
            caption=script.SILVER_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
#Deendayal403
    elif query.data == "gold":
        buttons = [[
            InlineKeyboardButton('🔐 ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ', callback_data='purchase')
        ],[
            InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='silver'),
            InlineKeyboardButton('4 / 7', callback_data='pagesn1'),
            InlineKeyboardButton('ɴᴇxᴛ ⋟', callback_data='platinum')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='premium_info')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/670f6df9f755dc2c9a00a.jpg")
        )
        await query.message.edit_caption(
            caption=script.GOLD_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "platinum":
        buttons = [[
            InlineKeyboardButton('🔐 ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ', callback_data='purchase')
        ],[
            InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='gold'),
            InlineKeyboardButton('5 / 7', callback_data='pagesn1'),
            InlineKeyboardButton('ɴᴇxᴛ ⋟', callback_data='diamond')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='premium_info')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/670f6df9f755dc2c9a00a.jpg")
        )
        await query.message.edit_caption(
            caption=script.PLATINUM_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "diamond":
        buttons = [[
            InlineKeyboardButton('🔐 ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ', callback_data='purchase')
        ],[
            InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='platinum'),
            InlineKeyboardButton('6 / 7', callback_data='pagesn1'),
            InlineKeyboardButton('ɴᴇxᴛ ⋟', callback_data='other')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_caption(
            caption=script.DIAMOND_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "other":
        buttons = [[
            InlineKeyboardButton('☎️ ᴄᴏɴᴛᴀᴄᴛ ᴏᴡɴᴇʀ ᴛᴏ ᴋɴᴏᴡ ᴍᴏʀᴇ', url=OWNER_LNK)
        ],[
            InlineKeyboardButton('⋞ ʙᴀᴄᴋ', callback_data='diamond'),
            InlineKeyboardButton('7 / 7', callback_data='pagesn1'),
            InlineKeyboardButton('ɴᴇxᴛ ⋟', callback_data='free')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='premium_info')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto("https://graph.org/file/670f6df9f755dc2c9a00a.jpg")
        )
        await query.message.edit_caption(
            caption=script.OTHER_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
    
    elif query.data == "channels":
        buttons = [[
            InlineKeyboardButton('🎞 ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ ', url=GRP_LNK),
            InlineKeyboardButton('☸️ ʙᴀᴄᴋᴜᴘ', url=CHNL_LNK)
        ],[
            InlineKeyboardButton('🗣📢 ᴍᴏᴠɪᴇ ɴᴏᴛɪғɪᴄᴀᴛɪᴏɴ ', url=DEENDAYAL_MOVIE_UPDATE_CHANNEL_LNK),
        ],[
            InlineKeyboardButton('⇍ ʙᴀᴄᴋ ⇏', callback_data='help'),
            InlineKeyboardButton('➤ ᴄᴏɴᴛᴀᴄᴛ ', url=OWNER_LNK)
        ]] 
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.CHANNELS.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "users":
        buttons = [[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='help')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.USERS_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "group":
        buttons = [[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='help')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.GROUP_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )


    elif query.data == "admic":
        if query.from_user.id not in ADMINS:
            return await query.answer("⚠️ ʏᴏᴜ'ʀᴇ ɴᴏᴛ ᴀ ʙᴏᴛ ᴀᴅᴍɪɴ !", show_alert=True)
        page = 0  
        buttons = [
            [InlineKeyboardButton('Next ➡️', callback_data=f'admic_next_{page}')],
            [InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=commands[page],  
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data.startswith("admic_next_"):
        page = int(query.data.split('_')[-1]) + 1
        if page >= len(commands):
            page = len(commands) - 1

        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton('Previous ⬅️', callback_data=f'admic_prev_{page}'))

        if page < len(commands) - 1:
            buttons.append(InlineKeyboardButton('Next ➡️', callback_data=f'admic_next_{page}'))

        reply_markup = InlineKeyboardMarkup([buttons, [InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='help')]])
        await query.message.edit_text(
            text=commands[page],
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data.startswith("admic_prev_"):
        page = int(query.data.split('_')[-1]) - 1
        if page < 0:
            page = 0  

        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton('Previous ⬅️', callback_data=f'admic_prev_{page}'))

        if page < len(commands) - 1:
            buttons.append(InlineKeyboardButton('Next ➡️', callback_data=f'admic_next_{page}'))

        reply_markup = InlineKeyboardMarkup([buttons, [InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='help')]])
        await query.message.edit_text(
            text=commands[page],
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )


    elif query.data == "help":
        buttons = [[
            InlineKeyboardButton('🛠️ Aᴅᴍɪɴ', callback_data='admic')
        ],[
            InlineKeyboardButton('👨🏻‍💼 ᴜꜱᴇʀ', callback_data='users'),
            InlineKeyboardButton('🤝 ɢʀᴏᴜᴘ', callback_data='group')
        ],[
            InlineKeyboardButton(' sᴜᴘᴘᴏʀᴛ 🔄', callback_data='channels')
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await safe_edit(
            query.message,
            text=script.HELP_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
    elif query.data == "about":
        buttons = [[
            InlineKeyboardButton('‼️ ᴅɪꜱᴄʟᴀɪᴍᴇʀ ‼️', callback_data='disclaimer'),
            InlineKeyboardButton ('🪔 sᴏᴜʀᴄᴇ', callback_data='source'),
        ],[
            InlineKeyboardButton('ᴅᴏɴᴀᴛɪᴏɴ 💰', callback_data='donation'), 
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await safe_edit(
            query.message,
            text=script.ABOUT_TXT.format(temp.U_NAME, temp.B_NAME, OWNER_LNK),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        
    elif query.data == "source":
        buttons = [[
            InlineKeyboardButton('Oʀɪɢɪɴᴀʟ Sᴏᴜʀᴄᴇ 📜', url=SOURCE_CODE_LINK),
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='about')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await safe_edit(
            query.message,
            text=script.SOURCE_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()

    elif query.data == "ref_point":
        await query.answer(f'You Have: {referdb.get_refer_points(query.from_user.id)} Refferal points.', show_alert=True)
    
   
    elif query.data == "shortlink_info":
            btn = [[
            InlineKeyboardButton("1 / 3", callback_data="pagesn1"),
            InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data="shortlink_info2")
            ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await safe_edit(
                query.message,
                text=script.SHORTLINK_INFO,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML
            )
            await query.answer()
    elif query.data == "shortlink_info2":
            btn = [[
            InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data="shortlink_info"),
            InlineKeyboardButton("2 / 3", callback_data="pagesn1"),
            InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data="shortlink_info3")
            ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await safe_edit(
                query.message,
                text=script.SHORTLINK_INFO2,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML
            )
            await query.answer()
    elif query.data == "shortlink_info3":
            btn = [[
            InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data="shortlink_info2"),
            InlineKeyboardButton("3 / 3", callback_data="pagesn1")
            ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await safe_edit(
                query.message,
                text=script.SHORTLINK_INFO3,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML
            )
            await query.answer()
    
    elif query.data == "disclaimer":
            btn = [[
                    InlineKeyboardButton("⇋ ʙᴀᴄᴋ ⇋", callback_data="about")
                  ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await safe_edit(
                query.message,
                text=script.DISCLAIMER_TXT,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML
            )
            await query.answer()
    elif query.data.startswith("setgs"):
        ident, set_type, status, grp_id = query.data.split("#")
        grpid = await active_connection(str(query.from_user.id))

        if str(grp_id) != str(grpid):
            await query.message.edit("Yᴏᴜʀ Aᴄᴛɪᴠᴇ Cᴏɴɴᴇᴄᴛɪᴏɴ Hᴀs Bᴇᴇɴ Cʜᴀɴɢᴇᴅ. Gᴏ Tᴏ /connections ᴀɴᴅ ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ᴀᴄᴛɪᴠᴇ ᴄᴏɴɴᴇᴄᴛɪᴏɴ.")
            return await query.answer(MSG_ALRT)

        if set_type == 'is_shortlink' and query.from_user.id not in ADMINS:
            return await query.answer(text=f"Hey {query.from_user.first_name}, You can't change shortlink settings for your group !\n\nIt's an admin only setting !", show_alert=True)

        if status == "True":
            await save_group_settings(grpid, set_type, False)
        else:
            await save_group_settings(grpid, set_type, True)

        settings = await get_settings(grpid)

        if settings is not None:
            buttons = [
                [
                    InlineKeyboardButton('ʀᴇꜱᴜʟᴛ ᴘᴀɢᴇ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ʙᴜᴛᴛᴏɴ' if settings["button"] else 'ᴛᴇxᴛ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜰɪʟᴇ ꜱᴇɴᴅ ᴍᴏᴅᴇ', callback_data=f'setgs#botpm#{settings["botpm"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ꜱᴛᴀʀᴛ' if settings["botpm"] else 'ᴀᴜᴛᴏ',
                                         callback_data=f'setgs#botpm#{settings["botpm"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜰɪʟᴇ ꜱᴇᴄᴜʀᴇ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["file_secure"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴛᴍᴅʙ ᴘᴏꜱᴛᴇʀ', callback_data=f'setgs#tmdb_poster#{settings["tmdb_poster"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["tmdb_poster"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#tmdb_poster#{settings["tmdb_poster"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴛᴍᴅʙ ʙᴀᴄᴋᴅʀᴏᴘ', callback_data=f'setgs#tmdb_backdrop#{settings["tmdb_backdrop"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["tmdb_backdrop"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#tmdb_backdrop#{settings["tmdb_backdrop"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜱᴘᴇʟʟ ᴄʜᴇᴄᴋ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["spell_check"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴡᴇʟᴄᴏᴍᴇ ᴍꜱɢ', callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["welcome"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["auto_delete"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["auto_ffilter"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ᴍᴀx ʙᴜᴛᴛᴏɴꜱ',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}'),
                    InlineKeyboardButton('10' if settings["max_btn"] else f'{MAX_B_TN}',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('ꜱʜᴏʀᴛʟɪɴᴋ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}'),
                    InlineKeyboardButton('ᴇɴᴀʙʟᴇ' if settings["is_shortlink"] else 'ᴅɪꜱᴀʙʟᴇ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('⇋ ᴄʟᴏꜱᴇ ꜱᴇᴛᴛɪɴɢꜱ ᴍᴇɴᴜ ⇋', 
                                         callback_data='close_data'
                                         )
                ]
        ]
            reply_markup = InlineKeyboardMarkup(buttons)
            await query.message.edit_reply_markup(reply_markup)
    await query.answer(MSG_ALRT)

    
async def auto_filter(client, msg, spoll=False):
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    # reqstr1 = msg.from_user.id if msg.from_user else 0
    # reqstr = await client.get_users(reqstr1)
    
    if not spoll:
        message = msg
        if message.text.startswith("/"): return  # ignore commands
        if re.findall(r"((^/|^,|^!|^\.|^[\U0001F600-\U000E007F]).*)", message.text):
            return
        if len(message.text) < 100:
            search = message.text         
            search = search.lower()
            m=await message.reply_text(f'**🔎 sᴇᴀʀᴄʜɪɴɢ** `{search}`')
            find = search.split(" ")
            search = ""
            removes = ["in","upload", "series", "full", "horror", "thriller", "mystery", "print", "file"]
            for x in find:
                if x in removes:
                    continue
                else:
                    search = search + x + " "
            #search = re.sub(r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|bro|bruh|broh|helo|that|find|dubbed|link|venum|iruka|pannunga|pannungga|anuppunga|anupunga|anuppungga|anupungga|film|undo|kitti|kitty|tharu|kittumo|kittum|movie|any(one)|with\ssubtitle(s)?)", "", search, flags=re.IGNORECASE)
            #search = re.sub(r"\s+", " ", search).strip()
            search = search.replace("-", " ")
            search = search.replace(":","")
            files, offset, total_results = await get_search_results(message.chat.id ,search, offset=0, filter=True)
            # Always read the latest artwork switches from the database for a search.
            # This avoids stale in-memory settings after toggling TMDB Poster/Backdrop
            # from another settings screen or bot instance.
            settings = await db.get_settings(message.chat.id)
            temp.SETTINGS[message.chat.id] = settings
            # In PM, artwork switches belong to the user's active connected group.
            # The private chat itself has no group settings document, so reading
            # db.get_settings(PM chat id) would silently use the global defaults
            # (often leaving TMDB backdrop enabled even when the group disabled it).
            artwork_settings = settings
            if message.chat.type == enums.ChatType.PRIVATE and message.from_user:
                try:
                    artwork_group_id = await active_connection(str(message.from_user.id))
                    if artwork_group_id is not None:
                        artwork_settings = await db.get_settings(artwork_group_id)
                except Exception:
                    logger.exception("Unable to load active group TMDB artwork settings for PM")
            if not files:
                #await m.delete()
                if settings["spell_check"]:
                    ai_sts = await m.edit('🤖 ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ, ᴀɪ ɪꜱ ᴄʜᴇᴄᴋɪɴɢ ʏᴏᴜʀ ꜱᴘᴇʟʟɪɴɢ...')
                    is_misspelled = await ai_spell_check(chat_id = message.chat.id,wrong_name=search)
                    if is_misspelled:
                        await ai_sts.edit(f'<b>✅Aɪ Sᴜɢɢᴇsᴛᴇᴅ ᴍᴇ<code> {is_misspelled}</code> \nSᴏ Iᴍ Sᴇᴀʀᴄʜɪɴɢ ғᴏʀ <code>{is_misspelled}</code></b>')
                        await asyncio.sleep(2)
                        message.text = is_misspelled
                        await ai_sts.delete()
                        return await auto_filter(client, message)
                    await ai_sts.delete()
                    return await advantage_spell_chok(client, message)
        else:
            return
    else:
        message = msg.message.reply_to_message  # msg will be callback query
        search, files, offset, total_results = spoll
        m=await message.reply_text(f'**🔎 sᴇᴀʀᴄʜɪɴɢ** `{search}`')
        # Callback-based searches must also use the latest persisted artwork switches.
        settings = await db.get_settings(message.chat.id)
        temp.SETTINGS[message.chat.id] = settings
        # Callback searches in PM must use the same active-group artwork switches
        # as normal PM searches.
        artwork_settings = settings
        if message.chat.type == enums.ChatType.PRIVATE and message.from_user:
            try:
                artwork_group_id = await active_connection(str(message.from_user.id))
                if artwork_group_id is not None:
                    artwork_settings = await db.get_settings(artwork_group_id)
            except Exception:
                logger.exception("Unable to load active group TMDB artwork settings for PM callback")
        await msg.message.delete()
    is_series_search = any(_is_series_file(file) for file in (files or []))
    try:
        if is_series_search:
            tmdb = await get_tmdb_tv(search, file=(files[0]).file_name if files else None)
        else:
            tmdb = await get_tmdb_movie(search, file=(files[0]).file_name if files else None)
    except Exception:
        tmdb = None
        logger.exception("TMDB lookup crashed for %s search=%r", "tv" if is_series_search else "movie", search)
    pre = 'filep' if settings['file_secure'] else 'file'
    key = f"{message.chat.id}-{message.id}"
    FRESH[key] = search
    temp.GETALL[key] = files
    temp.SHORT[message.from_user.id] = message.chat.id
    if settings["button"]:
        btn = [
            [
                InlineKeyboardButton(
                    text=clean_file_button_text(file, tmdb), callback_data=f'{pre}#{file.file_id}'
                ),
            ]
            for file in files
        ]
        btn.insert(0, 
            [
                InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
                InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
                InlineKeyboardButton("📺 Season",  callback_data=f"seasons#{key}")
            ]
        )
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))

    else:
        btn = []
        btn.insert(0, 
            [
                InlineKeyboardButton('🎞 Quality', callback_data=f"qualities#{key}"),
                InlineKeyboardButton("🌐 Language", callback_data=f"languages#{key}"),
                InlineKeyboardButton("📺 Season",  callback_data=f"seasons#{key}")
            ]
        )
        btn.insert(0, [
            InlineKeyboardButton("🎬 Episodes", callback_data=f"episodes#{key}"),
            InlineKeyboardButton("📅 Years", callback_data=f"years#{key}")
        ])
        btn.insert(0, _action_filter_row(key))

    if offset != "":
        req = message.from_user.id if message.from_user else 0
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages"), InlineKeyboardButton(text="NEXT ➢",callback_data=f"next_{req}_{key}_{offset}")]
                )
            else:
                btn.append(
                    [InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}",callback_data="pages"), InlineKeyboardButton(text="NEXT ➢",callback_data=f"next_{req}_{key}_{offset}")]
                )
        except KeyError:
            await save_group_settings(message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("PAGE", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages"), InlineKeyboardButton(text="NEXT ➢",callback_data=f"next_{req}_{key}_{offset}")]
            )
    else:
        btn.append(
            [InlineKeyboardButton(text="↭ No More Pages ↭",callback_data="pages")]
        )
    cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
    remaining_seconds = "{:.2f}".format(time_difference.total_seconds())

    audio_languages = _extract_file_languages(files)
    combined_file_text = " ".join(_file_text(file) for file in files or []).lower()
    if len(audio_languages) >= 2:
        audio_text = ", ".join(audio_languages)
    elif re.search(r"\bdual[- .]?audio\b", combined_file_text) or re.search(r"\bmulti[- .]?audio\b", combined_file_text):
        audio_text = "Dual Audio"
    elif audio_languages:
        audio_text = ", ".join(audio_languages)
    else:
        audio_text = "N/A"

    if tmdb:
        cap = TMDB_TEMPLATE.format(
            title=html.escape(tmdb.get("title", search)),
            year=tmdb.get("year", "N/A"),
            rating=tmdb.get("rating", "N/A"),
            genres=html.escape(tmdb.get("genres", "N/A")),
            runtime=tmdb.get("runtime") or "N/A",
            audio=html.escape(audio_text),
            remaining_seconds=remaining_seconds,
            message=message,
        )
        if not settings["button"]:
            cap += "\n\n<b>📚 <u>Your Requested Files</u> 👇</b>\n\n"
            for file in files:
                clean_name = html.escape(clean_file_button_text(file, tmdb))
                cap += f"<b><a href='https://telegram.me/{temp.U_NAME}?start=files_{file.file_id}'>{clean_name}</a></b>\n\n\u2063\n"
    else:
        cap = (
            f"<b>🧿 ᴛɪᴛʟᴇ : <code>{html.escape(search)}</code>\n"
            f"📂 ᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ : <code>{total_results}</code>\n"
            f"📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {message.from_user.mention}\n"
            f"⏰ ʀᴇꜱᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n"
            "🌳 𝑹𝒆𝒒𝒖𝒆𝒔𝒕𝒆𝒅 𝑭𝒊𝒍𝒆𝒔 👇\n\n</b>"
        )
        for file in files:
            clean_name = html.escape(clean_file_button_text(file, tmdb))
            cap += f"<b><a href='https://telegram.me/{temp.U_NAME}?start=files_{file.file_id}'>{clean_name}</a></b>\n\n\u2063\n"

    # Send exactly ONE TMDB artwork. Never mix poster + backdrop and never
    # send a swipeable carousel. The main TMDB poster is preferred when the
    # poster switch is enabled; otherwise the main backdrop is used only when
    # the backdrop switch is enabled. Both disabled = no artwork at all.
    image_buffer = None
    image_kind = None
    if tmdb:
        poster_enabled = _setting_enabled(artwork_settings.get("tmdb_poster"))
        backdrop_enabled = _setting_enabled(artwork_settings.get("tmdb_backdrop"))
        logger.info(
            "TMDB artwork settings: poster=%s backdrop=%s title=%r id=%r",
            poster_enabled, backdrop_enabled, tmdb.get("title"), tmdb.get("id"),
        )
        # The switches are mutually authoritative:
        #   - Backdrop ON + Poster ON  -> show ONE backdrop (backdrop has priority)
        #   - Backdrop ON + Poster OFF -> show ONE backdrop
        #   - Backdrop OFF + Poster ON -> show ONE main poster
        #   - Backdrop OFF + Poster OFF -> show NO artwork
        # Never fall back to a disabled artwork type.
        if backdrop_enabled and tmdb.get("backdrop"):
            image_kind = "backdrop"
            image_buffer = await fetch_tmdb_image(tmdb["backdrop"], backdrop=True)
        elif poster_enabled and tmdb.get("poster"):
            image_kind = "poster"
            image_buffer = await fetch_tmdb_image(tmdb["poster"], backdrop=False)

    if image_buffer:
        try:
            sent = await message.reply_photo(
                photo=image_buffer,
                caption=cap,
                reply_markup=InlineKeyboardMarkup(btn),
            )
            await m.delete()
            try:
                if settings['auto_delete']:
                    _schedule_auto_delete(sent, message)
            except KeyError:
                await save_group_settings(message.chat.id, 'auto_delete', True)
                _schedule_auto_delete(sent, message)
        except Exception:
            logger.exception("TMDB %s send failed; using text result", image_kind or "image")
            sent = await message.reply_text(
                text=cap,
                reply_markup=InlineKeyboardMarkup(btn),
                disable_web_page_preview=True,
            )
            await m.delete()
    else:
        fuk = await message.reply_text(
            text=cap,
            reply_markup=InlineKeyboardMarkup(btn),
            disable_web_page_preview=True,
        )
        await m.delete()
        try:
            if settings['auto_delete']:
                _schedule_auto_delete(fuk, message)
        except KeyError:
            await save_group_settings(message.chat.id, 'auto_delete', True)
            _schedule_auto_delete(fuk, message)

async def ai_spell_check(chat_id, wrong_name):
    search_results = await search_tmdb(wrong_name, bulk=True)
    movie_list = [movie.get('title') for movie in search_results if movie.get('title')]
    if not movie_list:
        return
    for _ in range(5):
        closest_match = process.extractOne(wrong_name, movie_list)
        if not closest_match or closest_match[1] <= 80:
            return 
        movie = closest_match[0]
        files, offset, total_results = await get_search_results(chat_id=chat_id, query=movie)
        if files:
            return movie
        movie_list.remove(movie)

async def advantage_spell_chok(client, message):
    mv_id = message.id
    search = message.text
    chat_id = message.chat.id
    settings = await get_settings(chat_id)
    query = re.sub(
        r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|br((o|u)h?)*|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)",
        "", message.text, flags=re.IGNORECASE)
    query = query.strip() + " movie"
    try:
        movies = await search_tmdb(search, bulk=True)
    except Exception:
        movies = []

    if not movies:
        google = search.replace(" ", "+")
        button = [[
            InlineKeyboardButton("🔍 ᴄʜᴇᴄᴋ sᴘᴇʟʟɪɴɢ ᴏɴ ɢᴏᴏɢʟᴇ 🔍", url=f"https://www.google.com/search?q={google}")
        ]]
        k = await message.reply_text(text=script.I_CUDNT.format(search), reply_markup=InlineKeyboardMarkup(button))
        await asyncio.sleep(60)
        await k.delete()
        try:
            await message.delete()
        except:
            pass
        return
    user = message.from_user.id if message.from_user else 0
    buttons = [[
        InlineKeyboardButton(text=movie.get('title'), callback_data=f"spol#{movie.get('id')}#{user}")
    ] for movie in movies if movie.get('id') and movie.get('title')]
    buttons.append(
        [InlineKeyboardButton(text="🚫 ᴄʟᴏsᴇ 🚫", callback_data='close_data')]
    )
    d = await message.reply_text(text=script.CUDNT_FND.format(message.from_user.mention), reply_markup=InlineKeyboardMarkup(buttons), reply_to_message_id=message.id)
    await asyncio.sleep(60)
    await d.delete()
    try:
        await message.delete()
    except:
        pass


async def manual_filters(client, message, text=False):
    settings = await get_settings(message.chat.id)
    group_id = message.chat.id
    name = text or message.text
    reply_id = message.reply_to_message.id if message.reply_to_message else message.id
    keywords = await get_filters(group_id)
    for keyword in reversed(sorted(keywords, key=len)):
        pattern = r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])"
        if re.search(pattern, name, flags=re.IGNORECASE):
            reply_text, btn, alert, fileid = await find_filter(group_id, keyword)

            if reply_text:
                reply_text = reply_text.replace("\\n", "\n").replace("\\t", "\t")

            if btn is not None:
                try:
                    if fileid == "None":
                        if btn == "[]":
                            joelkb = await client.send_message(
                                group_id, 
                                reply_text, 
                                disable_web_page_preview=True,
                                protect_content=True if settings["file_secure"] else False,
                                reply_to_message_id=reply_id
                            )
                            try:
                                if settings['auto_ffilter']:
                                    await auto_filter(client, message)
                                    try:
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                else:
                                    try:
                                        if settings['auto_delete']:
                                            _schedule_auto_delete(joelkb)
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            _schedule_auto_delete(joelkb)
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_ffilter', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_ffilter']:
                                    await auto_filter(client, message)

                        else:
                            button = eval(btn)
                            joelkb = await client.send_message(
                                group_id,
                                reply_text,
                                disable_web_page_preview=True,
                                reply_markup=InlineKeyboardMarkup(button),
                                protect_content=True if settings["file_secure"] else False,
                                reply_to_message_id=reply_id
                            )
                            try:
                                if settings['auto_ffilter']:
                                    await auto_filter(client, message)
                                    try:
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                else:
                                    try:
                                        if settings['auto_delete']:
                                            _schedule_auto_delete(joelkb)
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            _schedule_auto_delete(joelkb)
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_ffilter', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_ffilter']:
                                    await auto_filter(client, message)

                    elif btn == "[]":
                        joelkb = await client.send_cached_media(
                            group_id,
                            fileid,
                            caption=reply_text or "",
                            protect_content=True if settings["file_secure"] else False,
                            reply_to_message_id=reply_id
                        )
                        try:
                            if settings['auto_ffilter']:
                                await auto_filter(client, message)
                                try:
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                            else:
                                try:
                                    if settings['auto_delete']:
                                        _schedule_auto_delete(joelkb)
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        _schedule_auto_delete(joelkb)
                        except KeyError:
                            grpid = await active_connection(str(message.from_user.id))
                            await save_group_settings(grpid, 'auto_ffilter', True)
                            settings = await get_settings(message.chat.id)
                            if settings['auto_ffilter']:
                                await auto_filter(client, message)

                    else:
                        button = eval(btn)
                        joelkb = await message.reply_cached_media(
                            fileid,
                            caption=reply_text or "",
                            reply_markup=InlineKeyboardMarkup(button),
                            reply_to_message_id=reply_id
                        )
                        try:
                            if settings['auto_ffilter']:
                                await auto_filter(client, message)
                                try:
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                            else:
                                try:
                                    if settings['auto_delete']:
                                        _schedule_auto_delete(joelkb)
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        _schedule_auto_delete(joelkb)
                        except KeyError:
                            grpid = await active_connection(str(message.from_user.id))
                            await save_group_settings(grpid, 'auto_ffilter', True)
                            settings = await get_settings(message.chat.id)
                            if settings['auto_ffilter']:
                                await auto_filter(client, message)

                except Exception as e:
                    logger.exception(e)
                break
    else:
        return False

async def global_filters(client, message, text=False):
    settings = await get_settings(message.chat.id)
    group_id = message.chat.id
    name = text or message.text
    reply_id = message.reply_to_message.id if message.reply_to_message else message.id
    keywords = await get_gfilters('gfilters')
    for keyword in reversed(sorted(keywords, key=len)):
        pattern = r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])"
        if re.search(pattern, name, flags=re.IGNORECASE):
            reply_text, btn, alert, fileid = await find_gfilter('gfilters', keyword)

            if reply_text:
                reply_text = reply_text.replace("\\n", "\n").replace("\\t", "\t")

            if btn is not None:
                try:
                    if fileid == "None":
                        if btn == "[]":
                            joelkb = await client.send_message(
                                group_id, 
                                reply_text, 
                                disable_web_page_preview=True,
                                reply_to_message_id=reply_id
                            )
                            manual = await manual_filters(client, message)
                            if manual == False:
                                settings = await get_settings(message.chat.id)
                                try:
                                    if settings['auto_ffilter']:
                                        await auto_filter(client, message)
                                        try:
                                            if settings['auto_delete']:
                                                await joelkb.delete()
                                        except KeyError:
                                            grpid = await active_connection(str(message.from_user.id))
                                            await save_group_settings(grpid, 'auto_delete', True)
                                            settings = await get_settings(message.chat.id)
                                            if settings['auto_delete']:
                                                await joelkb.delete()
                                    else:
                                        try:
                                            if settings['auto_delete']:
                                                _schedule_auto_delete(joelkb)
                                        except KeyError:
                                            grpid = await active_connection(str(message.from_user.id))
                                            await save_group_settings(grpid, 'auto_delete', True)
                                            settings = await get_settings(message.chat.id)
                                            if settings['auto_delete']:
                                                _schedule_auto_delete(joelkb)
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_ffilter', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_ffilter']:
                                        await auto_filter(client, message) 
                            else:
                                try:
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                            
                        else:
                            button = eval(btn)
                            joelkb = await client.send_message(
                                group_id,
                                reply_text,
                                disable_web_page_preview=True,
                                reply_markup=InlineKeyboardMarkup(button),
                                reply_to_message_id=reply_id
                            )
                            manual = await manual_filters(client, message)
                            if manual == False:
                                settings = await get_settings(message.chat.id)
                                try:
                                    if settings['auto_ffilter']:
                                        await auto_filter(client, message)
                                        try:
                                            if settings['auto_delete']:
                                                await joelkb.delete()
                                        except KeyError:
                                            grpid = await active_connection(str(message.from_user.id))
                                            await save_group_settings(grpid, 'auto_delete', True)
                                            settings = await get_settings(message.chat.id)
                                            if settings['auto_delete']:
                                                await joelkb.delete()
                                    else:
                                        try:
                                            if settings['auto_delete']:
                                                _schedule_auto_delete(joelkb)
                                        except KeyError:
                                            grpid = await active_connection(str(message.from_user.id))
                                            await save_group_settings(grpid, 'auto_delete', True)
                                            settings = await get_settings(message.chat.id)
                                            if settings['auto_delete']:
                                                _schedule_auto_delete(joelkb)
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_ffilter', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_ffilter']:
                                        await auto_filter(client, message) 
                            else:
                                try:
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await joelkb.delete()

                    elif btn == "[]":
                        joelkb = await client.send_cached_media(
                            group_id,
                            fileid,
                            caption=reply_text or "",
                            reply_to_message_id=reply_id
                        )
                        manual = await manual_filters(client, message)
                        if manual == False:
                            settings = await get_settings(message.chat.id)
                            try:
                                if settings['auto_ffilter']:
                                    await auto_filter(client, message)
                                    try:
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                else:
                                    try:
                                        if settings['auto_delete']:
                                            _schedule_auto_delete(joelkb)
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            _schedule_auto_delete(joelkb)
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_ffilter', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_ffilter']:
                                    await auto_filter(client, message) 
                        else:
                            try:
                                if settings['auto_delete']:
                                    await joelkb.delete()
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_delete', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_delete']:
                                    await joelkb.delete()

                    else:
                        button = eval(btn)
                        joelkb = await message.reply_cached_media(
                            fileid,
                            caption=reply_text or "",
                            reply_markup=InlineKeyboardMarkup(button),
                            reply_to_message_id=reply_id
                        )
                        manual = await manual_filters(client, message)
                        if manual == False:
                            settings = await get_settings(message.chat.id)
                            try:
                                if settings['auto_ffilter']:
                                    await auto_filter(client, message)
                                    try:
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                else:
                                    try:
                                        if settings['auto_delete']:
                                            _schedule_auto_delete(joelkb)
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            _schedule_auto_delete(joelkb)
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_ffilter', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_ffilter']:
                                    await auto_filter(client, message) 
                        else:
                            try:
                                if settings['auto_delete']:
                                    await joelkb.delete()
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_delete', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_delete']:
                                    await joelkb.delete()

                                
                except Exception as e:
                    logger.exception(e)
                break
    else:
        return False






def get_name(msg):
    try:
        if getattr(msg, "document", None):
            return msg.document.file_name
        elif getattr(msg, "video", None):
            return msg.video.file_name
        elif getattr(msg, "audio", None):
            return msg.audio.file_name
        else:
            return "Unknown_File"
    except:
        return "Unknown_File"
