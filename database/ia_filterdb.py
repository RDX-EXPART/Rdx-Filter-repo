import logging
from struct import pack
import re
import base64
from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError
from info import CAPTION_LANGUAGES, DATABASE_URI, DATABASE_URI2, DATABASE_NAME, COLLECTION_NAME, USE_CAPTION_FILTER, MAX_B_TN, DEENDAYAL_MOVIE_UPDATE_CHANNEL, OWNERID
from utils import get_settings, save_group_settings, temp, get_status, clean_index_name
from database.users_chats_db import add_name
from .tmdb_client import get_tmdb_movie, fetch_tmdb_image
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from search_filters import (
    build_search_pattern,
    make_filter_query,
    matches_filter,
    parse_filter_query,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
#---------------------------------------------------------
# Some basic variables needed
tempDict = {'indexDB': DATABASE_URI}

# Primary DB
client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

#secondary db
client2 = AsyncIOMotorClient(DATABASE_URI2)
db2 = client2[DATABASE_NAME]
instance2 = Instance.from_db(db2)


# Primary DB Model
@instance.register
class Media(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    original_file_name = fields.StrField(allow_none=True)
    search_name = fields.StrField(allow_none=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)
    source_chat_id = fields.IntField(allow_none=True)
    source_message_id = fields.IntField(allow_none=True)
    thumb_file_id = fields.StrField(allow_none=True)
    file_unique_id = fields.StrField(allow_none=True)

    class Meta:
        indexes = ('$file_name', 'file_unique_id')
        collection_name = COLLECTION_NAME

@instance2.register
class Media2(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    original_file_name = fields.StrField(allow_none=True)
    search_name = fields.StrField(allow_none=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)
    source_chat_id = fields.IntField(allow_none=True)
    source_message_id = fields.IntField(allow_none=True)
    thumb_file_id = fields.StrField(allow_none=True)
    file_unique_id = fields.StrField(allow_none=True)

    class Meta:
        indexes = ('$file_name', 'file_unique_id')
        collection_name = COLLECTION_NAME

async def choose_mediaDB():
    """This Function chooses which database to use based on the value of indexDB key in the dict tempDict."""
    global saveMedia
    if tempDict['indexDB'] == DATABASE_URI:
        logger.info("Using first db (Media)")
        saveMedia = Media
    else:
        logger.info("Using second db (Media2)")
        saveMedia = Media2

def _extract_thumb_file_id(media):
    """Extract the largest available thumbnail file_id from a Pyrogram media object."""
    try:
        thumbs = []
        for attribute in ('video_cover', 'cover', 'covers', 'video_thumbs', 'thumbs'):
            value = getattr(media, attribute, None)
            if not value:
                continue
            if isinstance(value, (list, tuple)):
                thumbs.extend(value)
            else:
                thumbs.append(value)
        if thumbs:
            thumb = max(
                thumbs,
                key=lambda item: (
                    (getattr(item, 'width', 0) or 0)
                    * (getattr(item, 'height', 0) or 0),
                    getattr(item, 'file_size', 0) or 0,
                ),
            )
            return thumb if isinstance(thumb, str) else getattr(thumb, 'file_id', None)
    except Exception:
        pass
    return None


def _source_update_fields(
    source_chat_id,
    source_message_id,
    thumb_file_id,
    original_file_name=None,
):
    fields_to_update = {}
    if source_chat_id and source_message_id:
        fields_to_update['source_chat_id'] = source_chat_id
        fields_to_update['source_message_id'] = source_message_id
    if thumb_file_id:
        fields_to_update['thumb_file_id'] = thumb_file_id
    if original_file_name:
        original_file_name = str(original_file_name)
        fields_to_update['file_name'] = original_file_name
        fields_to_update['original_file_name'] = original_file_name
        fields_to_update['search_name'] = clean_index_name(original_file_name)
    return fields_to_update


async def _update_existing_file_sources(file_id, fields_to_update):
    """Update source/thumb metadata in both databases and report duplicates."""
    found = False
    for model in (Media, Media2):
        existing = await model.collection.find_one(
            {'_id': file_id},
            {'_id': 1},
        )
        if not existing:
            continue
        found = True
        if fields_to_update:
            await model.collection.update_one(
                {'_id': file_id},
                {'$set': fields_to_update},
            )
    return found


async def update_file_source(file_id, source_message):
    """Attach a recovered index-channel post to an existing database row."""
    source_chat_id = None
    source_message_id = None
    media = None
    try:
        source_chat_id = int(source_message.chat.id)
        source_message_id = int(source_message.id)
        media_type = getattr(
            getattr(source_message, 'media', None),
            'value',
            getattr(source_message, 'media', None),
        )
        media = getattr(source_message, str(media_type), None)
    except Exception:
        logger.exception('Invalid source message supplied for file %s', file_id)
        return False

    fields_to_update = _source_update_fields(
        source_chat_id,
        source_message_id,
        _extract_thumb_file_id(media),
        getattr(media, 'file_name', None),
    )
    return await _update_existing_file_sources(file_id, fields_to_update)


async def restore_original_filenames(bot, limit=500):
    """Restore legacy cleaned names from saved index-channel source messages."""
    limit = max(1, min(int(limit or 500), 5000))
    restored = 0
    missing = 0
    failed = 0
    criteria = {
        'source_chat_id': {'$ne': None},
        'source_message_id': {'$ne': None},
        '$or': [
            {'original_file_name': {'$exists': False}},
            {'original_file_name': None},
            {'search_name': {'$exists': False}},
            {'search_name': None},
        ],
    }
    remaining = limit
    for model in (Media2, Media):
        if remaining <= 0:
            break
        documents = await model.collection.find(
            criteria,
            {
                '_id': 1,
                'source_chat_id': 1,
                'source_message_id': 1,
                'thumb_file_id': 1,
            },
        ).limit(remaining).to_list(length=remaining)
        for document in documents:
            try:
                message = await bot.get_messages(
                    int(document['source_chat_id']),
                    int(document['source_message_id']),
                )
                media_type = getattr(
                    getattr(message, 'media', None),
                    'value',
                    getattr(message, 'media', None),
                )
                media = getattr(message, str(media_type), None)
                original = getattr(media, 'file_name', None) if media else None
                if not original:
                    missing += 1
                    continue
                original = str(original)
                await model.collection.update_one(
                    {'_id': document['_id']},
                    {
                        '$set': {
                            'file_name': original,
                            'original_file_name': original,
                            'search_name': clean_index_name(original),
                            'thumb_file_id': (
                                _extract_thumb_file_id(media)
                                or document.get('thumb_file_id')
                            ),
                        }
                    },
                )
                restored += 1
            except Exception:
                failed += 1
                logger.exception(
                    'Unable to restore original filename for %s',
                    document.get('_id'),
                )
        remaining -= len(documents)
    return {
        'restored': restored,
        'missing': missing,
        'failed': failed,
        'processed': restored + missing + failed,
        'limit': limit,
    }


async def save_file(bot, media, source_message=None):
  """Save file in database and keep source chat/message for thumbnail-preserving copy."""
  global saveMedia
  file_id, file_ref = unpack_new_file_id(media.file_id)
  file_name = str(media.file_name or '').strip()
  if not file_name:
    file_name = 'Telegram File'
  search_name = clean_index_name(file_name)
  source_chat_id = None
  source_message_id = None
  thumb_file_id = _extract_thumb_file_id(media)
  if source_message is not None:
    try:
      source_chat_id = int(source_message.chat.id)
      source_message_id = int(source_message.id)
    except Exception:
      source_chat_id = None
      source_message_id = None
  update_fields = _source_update_fields(
      source_chat_id,
      source_message_id,
      thumb_file_id,
      file_name,
  )
  file_unique_id = getattr(media, 'file_unique_id', None)
  try:
    # Telegram assigns the same unique ID when identical media is uploaded again,
    # even if the regular file_id differs. Check both configured catalogues.
    if file_unique_id:
      for model in (Media, Media2):
        duplicate = await model.collection.find_one(
            {'file_unique_id': file_unique_id}, {'_id': 1}
        )
        if duplicate:
          logger.info('Duplicate media skipped: %s', file_name)
          return False, 0
    if await _update_existing_file_sources(file_id, update_fields):
        logger.warning(
            '%s is already saved; refreshed source message and thumbnail metadata',
            file_name,
        )
        return False, 0
    file = saveMedia(
        file_id=file_id,
        file_ref=file_ref,
        file_name=file_name,
        original_file_name=file_name,
        search_name=search_name,
        file_size=media.file_size,
        file_type=media.file_type,
        mime_type=media.mime_type,
        caption=media.caption.html if media.caption else None,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        thumb_file_id=thumb_file_id,
        file_unique_id=file_unique_id,
    )
  except ValidationError:
    logger.exception('Error occurred while saving file in database')
    return False, 2
  else:
    try:
      await file.commit()
    except DuplicateKeyError:
      logger.warning(f'{getattr(media, "file_name", "NO_FILE")} is already saved in database')
      if update_fields:
          try:
              await _update_existing_file_sources(file_id, update_fields)
          except Exception:
              logger.exception('Failed to update source/thumb info for duplicate file')
      return False, 0
    else:
        logger.info(f'{getattr(media, "file_name", "NO_FILE")} is saved to database')
        if await get_status(bot.me.id):
            await send_msg(bot, file.file_name, file.caption)
        return True, 1


def _build_media_filter(query, file_type=None):
    raw_pattern = build_search_pattern(query.strip())
    regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    if USE_CAPTION_FILTER:
        mongo_filter = {'$or': [
            {'file_name': regex},
            {'original_file_name': regex},
            {'search_name': regex},
            {'caption': regex},
        ]}
    else:
        mongo_filter = {'$or': [
            {'file_name': regex},
            {'original_file_name': regex},
            {'search_name': regex},
        ]}
    if file_type:
        mongo_filter['file_type'] = file_type
    return mongo_filter


def _record_search_text(file):
    return " ".join(
        part
        for part in (
            getattr(file, 'file_name', None),
            getattr(file, 'original_file_name', None),
            getattr(file, 'search_name', None),
            getattr(file, 'caption', None),
        )
        if part
    )


async def _all_media_records(mongo_filter):
    """Return every matching record in the same order used by pagination."""
    count2 = await Media2.count_documents(mongo_filter)
    count1 = await Media.count_documents(mongo_filter)

    cursor2 = Media2.find(mongo_filter)
    cursor2.sort('$natural', -1)
    cursor1 = Media.find(mongo_filter)
    cursor1.sort('$natural', -1)

    files2 = await cursor2.to_list(length=count2) if count2 else []
    files1 = await cursor1.to_list(length=count1) if count1 else []
    return files2 + files1


async def get_all_search_results(query, file_type=None):
    """Return all database-wide results, including combined episode ranges."""
    query = query.strip()
    _, selected_filters = parse_filter_query(query)
    has_episode_filter = bool(selected_filters.get('episode'))
    candidate_query = (
        make_filter_query(query, episode=None)
        if has_episode_filter
        else query
    )
    try:
        mongo_filter = _build_media_filter(candidate_query, file_type=file_type)
    except re.error:
        logger.exception("Invalid search pattern generated for query: %r", query)
        return [], 0

    files = await _all_media_records(mongo_filter)
    if has_episode_filter:
        files = [
            file
            for file in files
            if matches_filter(_record_search_text(file), query)
        ]
    return files, len(files)


async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False):
    """For given query return (results, next_offset)"""
    if chat_id is not None:
        settings = await get_settings(int(chat_id))
        try:
            if settings['max_btn']:
                max_results = 10
            else:
                max_results = int(MAX_B_TN)
        except KeyError:
            await save_group_settings(int(chat_id), 'max_btn', False)
            settings = await get_settings(int(chat_id))
            if settings['max_btn']:
                max_results = 10
            else:
                max_results = int(MAX_B_TN)
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    if max_results % 2 != 0:
        logger.info(
            "Since max_results is odd (%s), bot will use %s.",
            max_results,
            max_results + 1,
        )
        max_results += 1
    query = query.strip()
    _, selected_filters = parse_filter_query(query)
    if selected_filters.get('episode'):
        files, total_results = await get_all_search_results(
            query,
            file_type=file_type,
        )
        page = files[offset:offset + max_results]
        next_offset = offset + len(page)
        if next_offset >= total_results:
            next_offset = ''
        return page, next_offset, total_results

    try:
        mongo_filter = _build_media_filter(query, file_type=file_type)
    except re.error:
        logger.exception("Invalid search pattern generated for query: %r", query)
        return [], "", 0

    total_results = (
        (await Media.count_documents(mongo_filter))
        + (await Media2.count_documents(mongo_filter))
    )

    cursor = Media.find(mongo_filter)
    cursor2 = Media2.find(mongo_filter)

    cursor.sort('$natural', -1)
    cursor2.sort('$natural', -1)

    cursor2.skip(offset).limit(max_results)

    fileList2 = await cursor2.to_list(length=max_results)
    if len(fileList2)<max_results:
        next_offset = offset+len(fileList2)
        cursorSkipper = (next_offset-(await Media2.count_documents(mongo_filter)))
        cursor.skip(cursorSkipper if cursorSkipper>=0 else 0).limit(max_results-len(fileList2))
        fileList1 = await cursor.to_list(length=(max_results-len(fileList2)))
        files = fileList2+fileList1
        next_offset = next_offset + len(fileList1)
    else:
        files = fileList2
        next_offset = offset + max_results
    if next_offset >= total_results:
        next_offset = ''
    return files, next_offset, total_results


async def get_bad_files(query, file_type=None, filter=False):
    """For given query return (results, next_offset)"""
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_\[\]\{\}\(\)])' + query + r'(\b|[\.\+\-_\[\]\{\}\(\)])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-_\[\]\{\}\(\)]')
    
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        return []

    if USE_CAPTION_FILTER:
        filter = {'$or': [
            {'file_name': regex},
            {'original_file_name': regex},
            {'search_name': regex},
            {'caption': regex},
        ]}
    else:
        filter = {'$or': [
            {'file_name': regex},
            {'original_file_name': regex},
            {'search_name': regex},
        ]}

    if file_type:
        filter['file_type'] = file_type

    cursor = Media.find(filter)
    cursor2 = Media2.find(filter)

    cursor.sort('$natural', -1)
    cursor2.sort('$natural', -1)

    files = ((await cursor2.to_list(length=(await Media2.count_documents(filter))))+(await cursor.to_list(length=(await Media.count_documents(filter)))))

    total_results = len(files)

    return files, total_results

async def get_file_details(query):
    mongo_filter = {'file_id': query}
    primary = await Media.find(mongo_filter).to_list(length=1)
    secondary = await Media2.find(mongo_filter).to_list(length=1)
    candidates = primary + secondary
    if not candidates:
        return []

    # Prefer the record that can copy the exact original index-channel post.
    best = max(
        candidates,
        key=lambda item: (
            bool(
                getattr(item, 'source_chat_id', None)
                and getattr(item, 'source_message_id', None)
            ),
            bool(getattr(item, 'thumb_file_id', None)),
        ),
    )
    return [best]


def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0

    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0

            r += bytes([i])

    return base64.urlsafe_b64encode(r).decode().rstrip("=")

def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")

def unpack_new_file_id(new_file_id):
    """Return file_id, file_ref"""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref


async def send_msg(bot, filename, caption): 
    try:
        filename = re.sub(r'\(\@\S+\)|\[\@\S+\]|\b@\S+|\bwww\.\S+', '', filename).strip()
        caption = re.sub(r'\(\@\S+\)|\[\@\S+\]|\b@\S+|\bwww\.\S+', '', caption).strip()
        
        year_match = re.search(r"\b(19|20)\d{2}\b", caption)
        year = year_match.group(0) if year_match else None

        pattern = r"(?i)(?:s|season)0*(\d{1,2})"
        season = re.search(pattern, caption) or re.search(pattern, filename)
        season = season.group(1) if season else None 

        if year:
            filename = filename[: filename.find(year) + 4]  
        elif season and season in filename:
            filename = filename[: filename.find(season) + 1]

        qualities = ["ORG", "org", "hdcam", "HDCAM", "HQ", "hq", "HDRip", "hdrip", "camrip", "CAMRip", "hdtc", "predvd", "DVDscr", "dvdscr", "dvdrip", "dvdscr", "HDTC", "dvdscreen", "HDTS", "hdts"]
        quality = await get_qualities(caption.lower(), qualities) or "HDRip"

        language = ""
        possible_languages = CAPTION_LANGUAGES
        for lang in possible_languages:
            if lang.lower() in caption.lower():
                language += f"{lang}, "
        language = language[:-2] if language else "Not idea 😄"

        filename = re.sub(r"[\(\)\[\]\{\}:;'\-!]", "", filename)

        text = "#𝑵𝒆𝒘_𝑭𝒊𝒍𝒆_𝑨𝒅𝒅𝒆𝒅 ✅\n\n👷𝑵𝒂𝒎𝒆: `{}`\n\n🌳𝑸𝒖𝒂𝒍𝒊𝒕𝒚: {}\n\n🍁𝑨𝒖𝒅𝒊𝒐: {}"
        text = text.format(filename, quality, language)

        if await add_name(OWNERID, filename):
            tmdb = await get_tmdb_movie(filename)
            resized_poster = None

            if tmdb:
                image_url = tmdb.get("backdrop") or tmdb.get("poster")
                if image_url:
                    resized_poster = await fetch_tmdb_image(image_url, backdrop=bool(tmdb.get("backdrop")))

            filenames = filename.replace(" ", '-')
            btn = [[InlineKeyboardButton('🌲 Get Files 🌲', url=f"https://telegram.me/{temp.U_NAME}?start=getfile-{filenames}")]]
            
            if resized_poster:
                await bot.send_photo(chat_id=DEENDAYAL_MOVIE_UPDATE_CHANNEL, photo=resized_poster, caption=text, reply_markup=InlineKeyboardMarkup(btn))
            else:              
                await bot.send_message(chat_id=DEENDAYAL_MOVIE_UPDATE_CHANNEL, text=text, reply_markup=InlineKeyboardMarkup(btn))

    except:
        pass

async def get_qualities(text, qualities: list):
    """Get all Quality from text"""
    quality = []
    for q in qualities:
        if q in text:
            quality.append(q)
    quality = ", ".join(quality)
    return quality[:-2] if quality.endswith(", ") else quality
