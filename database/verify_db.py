from os import environ
from datetime import timedelta, datetime
from hashlib import sha256
import logging

from pymongo import MongoClient, ReturnDocument
import pytz
from info import DATABASE_URI, DATABASE_NAME

logger = logging.getLogger(__name__)


class VR_db:
    def __init__(self, db_url, db_name, timezone):
        self.client = MongoClient(db_url)
        self.db = self.client[db_name]
        self.collection = self.db.verifications
        self.tokens = self.db.verification_tokens
        self.timezone = pytz.timezone(timezone)
        self._token_indexes_ready = False

    @staticmethod
    def _token_hash(token):
        return sha256(str(token).encode("utf-8")).hexdigest()

    def _ensure_token_indexes(self):
        if self._token_indexes_ready:
            return
        self.tokens.create_index("expires_at", expireAfterSeconds=0)
        self.tokens.create_index([("user_id", 1), ("token_hash", 1)])
        self._token_indexes_ready = True

    async def save_token(self, user_id, token, file_id, expires_at):
        """Persist one active verification token so restarts do not break it."""
        try:
            self._ensure_token_indexes()
            now = datetime.utcnow()
            self.tokens.update_many(
                {"user_id": int(user_id), "used_at": None},
                {"$set": {"used_at": now, "invalidated": True}},
            )
            self.tokens.insert_one(
                {
                    "user_id": int(user_id),
                    "token_hash": self._token_hash(token),
                    "file_id": str(file_id),
                    "created_at": now,
                    "expires_at": expires_at,
                    "used_at": None,
                }
            )
            return True
        except Exception:
            logger.exception("Unable to persist verification token")
            return None

    async def consume_token(self, user_id, token, file_id):
        """Atomically validate and consume a token.

        True means accepted, False means invalid/expired/used, and None means
        MongoDB was unavailable so the caller may use its in-memory fallback.
        """
        try:
            token_doc = self.tokens.find_one_and_update(
                {
                    "user_id": int(user_id),
                    "token_hash": self._token_hash(token),
                    "file_id": str(file_id),
                    "used_at": None,
                    "expires_at": {"$gt": datetime.utcnow()},
                },
                {"$set": {"used_at": datetime.utcnow()}},
                return_document=ReturnDocument.BEFORE,
            )
            return token_doc is not None
        except Exception:
            logger.exception("Unable to validate verification token")
            return None

    async def save_verification(self, user_id):
        now = datetime.now(self.timezone)
        year = now.year  
        verification = {"user_id": user_id, "verified_at": now, "year": year}
        self.collection.insert_one(verification)

    def get_start_end_dates(self, time_period, year=None):
        now = datetime.now(self.timezone)
        
        if time_period == 'today':
            start_datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_datetime = now
        elif time_period == 'yesterday':
            start_datetime = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_datetime = start_datetime + timedelta(days=1)
        elif time_period == 'this_week':
            start_datetime = now - timedelta(days=now.weekday())
            start_datetime = start_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
            end_datetime = now            
        elif time_period == 'this_month':
            start_datetime = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_datetime = now
        elif time_period == 'last_month':
            first_day_of_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_month_end_datetime = first_day_of_current_month - timedelta(microseconds=1)
            start_datetime = last_month_end_datetime.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_datetime = last_month_end_datetime
        elif time_period == 'year' and year:
            start_datetime = datetime(year, 1, 1, tzinfo=self.timezone)  # Start of the year
            end_datetime = datetime(year + 1, 1, 1, tzinfo=self.timezone) - timedelta(microseconds=1)  # End of the year
        else:
            raise ValueError("Invalid time period")
        
        return start_datetime, end_datetime

    async def get_vr_count(self, time_period, year=None):
        start_datetime, end_datetime = self.get_start_end_dates(time_period, year)
        count = self.collection.count_documents({'verified_at': {'$gt': start_datetime, '$lt': end_datetime}})
        return count

vr_db = VR_db(DATABASE_URI, DATABASE_NAME, 'Asia/Kolkata')
          
