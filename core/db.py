"""MongoDB access. db: twitter, collection: posts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from .config import Config
from .logger import setup_logger

logger = setup_logger(__name__)


def get_client(cfg: Config) -> MongoClient:
    return MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=5000)


def get_db(cfg: Config) -> Database:
    return get_client(cfg)[cfg.mongo_db]


EXPECTED_INDEXES = {"_id_", "post_id_1", "username_1"}


def get_posts_collection(cfg: Config) -> Collection:
    db = get_db(cfg)
    coll = db["posts"]

    # Drop legacy indexes from earlier schemas (e.g. tweet_id_1) so they don't
    # collide with our post_id-based unique key.
    for idx_name in list(coll.index_information().keys()):
        if idx_name not in EXPECTED_INDEXES:
            logger.info("Dropping legacy index %s", idx_name)
            coll.drop_index(idx_name)

    coll.create_index([("post_id", ASCENDING)], unique=True)
    coll.create_index([("username", ASCENDING)])
    return coll


def upsert_post(coll: Collection, *, post_id: str, username: str, text: str, url: str) -> bool:
    """Insert or update a post. Returns True if a new document was inserted."""
    result = coll.update_one(
        {"post_id": post_id},
        {
            "$set": {
                "username": username,
                "text": text,
                "url": url,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )
    return result.upserted_id is not None


def fetch_all_texts(coll: Collection, username: str | None = None) -> list[str]:
    query: dict = {}
    if username:
        query["username"] = username
    return [doc["text"] for doc in coll.find(query, {"text": 1, "_id": 0}) if doc.get("text")]


def clear_posts(coll: Collection, username: str | None = None) -> int:
    query: dict = {}
    if username:
        query["username"] = username
    result = coll.delete_many(query)
    logger.info("Cleared %d posts from collection", result.deleted_count)
    return result.deleted_count


def count_posts(coll: Collection, username: str | None = None) -> int:
    query: dict = {}
    if username:
        query["username"] = username
    return coll.count_documents(query)
