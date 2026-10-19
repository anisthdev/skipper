import json
import sqlite3
import time

from .config import cache_db_path


DAY = 24 * 60 * 60

# How long each kind of lookup stays fresh. Empty results expire quickly so
# newly added shows and newly submitted skip times get picked up.
TTL_FOUND = {
    "tmdb_search": 30 * DAY,
    "anilist_search": 30 * DAY,
    "jikan_search": 30 * DAY,
    "theintrodb": 7 * DAY,
    "aniskip": 7 * DAY,
}
TTL_EMPTY = 1 * DAY


def connect(path=None):
    path = path or cache_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute(
        """
        create table if not exists cache (
            namespace text not null,
            key text not null,
            payload_json text,
            fetched_at integer not null,
            primary key (namespace, key)
        )
        """
    )
    return conn


def get(conn, namespace, key, ttl_found, now=None):
    """Return (payload, hit). A cached null payload is a hit with payload None."""
    row = conn.execute(
        "select payload_json, fetched_at from cache where namespace = ? and key = ?",
        (namespace, key),
    ).fetchone()
    if row is None:
        return None, False

    payload_json, fetched_at = row
    ttl = ttl_found if payload_json is not None else TTL_EMPTY
    if (now or time.time()) - fetched_at > ttl:
        return None, False

    return (json.loads(payload_json) if payload_json is not None else None), True


def put(conn, namespace, key, payload, now=None):
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload is not None else None
    conn.execute(
        """
        insert into cache (namespace, key, payload_json, fetched_at)
        values (?, ?, ?, ?)
        on conflict(namespace, key) do update set
            payload_json = excluded.payload_json,
            fetched_at = excluded.fetched_at
        """,
        (namespace, key, payload_json, int(now or time.time())),
    )
    conn.commit()


def cached(conn, namespace, key, fetch):
    """Return (payload, hit), calling fetch() on a miss and storing its result.

    fetch() returning None is cached as an empty result.
    """
    payload, hit = get(conn, namespace, key, TTL_FOUND[namespace])
    if hit:
        return payload, True

    payload = fetch()
    put(conn, namespace, key, payload)
    return payload, False
