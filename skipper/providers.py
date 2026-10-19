"""HTTP clients for TMDB, AniList, Jikan, TheIntroDB and AniSkip.

Each fetch_* function returns parsed JSON, or None when the provider has no
data (HTTP 404). Other HTTP and network errors are raised.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .media import normalize_title


TMDB_SEARCH_TV_URL = "https://api.themoviedb.org/3/search/tv"
ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"
JIKAN_ANIME_SEARCH_URL = "https://api.jikan.moe/v4/anime"
THEINTRODB_MEDIA_URL = "https://api.theintrodb.org/v3/media"
ANISKIP_SKIP_TIMES_URL = "https://api.aniskip.com/v2/skip-times"

ANISKIP_SKIP_TYPES = ("op", "ed", "mixed-op", "mixed-ed", "recap")
THEINTRODB_SEGMENT_TYPES = ("recap", "intro", "credits", "preview")
TMDB_ANIMATION_GENRE_ID = 16
JIKAN_EPISODIC_TYPES = {"TV", "ONA", "OVA", "TV Special", "Special"}
ANILIST_EPISODIC_FORMATS = {"TV", "TV_SHORT", "ONA", "OVA", "SPECIAL"}

TIMEOUT_SECONDS = 10
CONNECTION_RETRIES = 1


def http_get_json(url, params=None, headers=None, json_body=None):
    """GET url with params, or POST json_body when given."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = None
    headers = {"Accept": "application/json", "User-Agent": f"skipper/{__version__}", **(headers or {})}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers)
    for attempt in range(1 + CONNECTION_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            # Connection resets and timeouts are often transient; HTTP errors are not.
            if attempt == CONNECTION_RETRIES:
                raise


# TMDB


def fetch_tmdb_search(token, title, year=None):
    params = {"query": title, "include_adult": "false"}
    if year:
        params["first_air_date_year"] = year
    return http_get_json(TMDB_SEARCH_TV_URL, params, {"Authorization": f"Bearer {token}"})


def pick_tmdb_show(payload, title):
    """Prefer an exact title match; otherwise trust TMDB's relevance order."""
    results = (payload or {}).get("results") or []
    if not results:
        return None

    normalized = normalize_title(title)
    exact = [
        show
        for show in results
        if normalized in (normalize_title(show.get("name")), normalize_title(show.get("original_name")))
    ]
    chosen = (exact or results)[0]
    return {
        "exact": bool(exact),
        "tmdb_id": chosen.get("id"),
        "name": chosen.get("name"),
        "original_name": chosen.get("original_name"),
        "original_language": chosen.get("original_language"),
        "genre_ids": chosen.get("genre_ids") or [],
    }


def is_anime(show):
    return show["original_language"] == "ja" and TMDB_ANIMATION_GENRE_ID in show["genre_ids"]


# Anime search: AniList and Jikan both resolve a title to a MyAnimeList ID,
# which AniSkip needs. Each provider's results are converted to candidates:
#   {"mal_id", "title", "titles": [...], "episodic": bool, "popularity_rank": lower is more popular}


ANILIST_SEARCH_QUERY = """
query ($search: String) {
  Page(perPage: 25) {
    media(search: $search, type: ANIME) {
      idMal
      format
      popularity
      title { romaji english native }
      synonyms
    }
  }
}
"""


def fetch_anilist_search(title):
    payload = http_get_json(ANILIST_GRAPHQL_URL, json_body={"query": ANILIST_SEARCH_QUERY, "variables": {"search": title}})
    # GraphQL reports failures in the body; raise so they aren't cached as "no match".
    if payload and payload.get("errors") and not payload.get("data"):
        raise RuntimeError("; ".join(error.get("message", "unknown error") for error in payload["errors"]))
    return payload


def anilist_candidates(payload):
    media = (((payload or {}).get("data") or {}).get("Page") or {}).get("media") or []
    for anime in media:
        # Entries without a MyAnimeList ID can't be looked up on AniSkip.
        if not anime.get("idMal"):
            continue
        titles = anime.get("title") or {}
        yield {
            "mal_id": anime["idMal"],
            "title": titles.get("english") or titles.get("romaji"),
            "titles": [titles.get("romaji"), titles.get("english"), titles.get("native"), *(anime.get("synonyms") or [])],
            "episodic": anime.get("format") in ANILIST_EPISODIC_FORMATS,
            # AniList popularity counts users, so more is better.
            "popularity_rank": -(anime.get("popularity") or 0),
        }


def fetch_jikan_search(title):
    return http_get_json(JIKAN_ANIME_SEARCH_URL, {"q": title, "limit": 25})


def jikan_candidates(payload):
    for anime in (payload or {}).get("data") or []:
        if not anime.get("mal_id"):
            continue
        titles = [anime.get("title"), anime.get("title_english"), anime.get("title_japanese")]
        titles.extend(anime.get("title_synonyms") or [])
        titles.extend(entry.get("title") for entry in anime.get("titles") or [])
        yield {
            "mal_id": anime["mal_id"],
            "title": anime.get("title_english") or anime.get("title"),
            "titles": titles,
            "episodic": anime.get("type") in JIKAN_EPISODIC_TYPES,
            # MAL popularity is a rank, so less is better; 0 means unranked.
            "popularity_rank": anime.get("popularity") or 999_999,
        }


ANIME_SEARCH = {
    "anilist": (fetch_anilist_search, anilist_candidates),
    "jikan": (fetch_jikan_search, jikan_candidates),
}


def rank_anime_title(candidate, normalized, season):
    """Lower is better. MAL splits seasons into separate entries, so prefer the
    entry whose title names the requested season."""
    rank = 0 if candidate == normalized else 10

    later_season = re.compile(r"(\bseason [2-9]\b|\b[2-9](st|nd|rd|th)? season\b|\b[2-9]\b$)")
    if season == 1:
        if later_season.search(candidate):
            rank += 50
    elif season is not None:
        requested_season = re.compile(rf"(\bseason {season}\b|\b{season}(st|nd|rd|th)? season\b|\b{season}\b$)")
        if requested_season.search(candidate):
            rank -= 50
        elif candidate == normalized:
            rank += 100
        elif later_season.search(candidate):
            rank += 25
        else:
            rank += 50

    if " special" in candidate or candidate.endswith("specials"):
        rank += 75

    return rank


def pick_anime(candidates, title, season):
    """Only accept entries with a title equal to, or starting with, the query.

    Taking the top search result regardless would attach confident but wrong
    skip times to a misspelled filename.
    """
    normalized = normalize_title(title)
    best = None
    for anime in candidates:
        ranks = [
            rank_anime_title(candidate, normalized, season)
            for candidate in {normalize_title(value) for value in anime["titles"]} - {""}
            if candidate == normalized or candidate.startswith(normalized + " ")
        ]
        if not ranks:
            continue

        rank = min(ranks) + (0 if anime["episodic"] else 200)
        key = (rank, anime["popularity_rank"])
        if best is None or key < best[0]:
            best = (key, anime)

    if best is None:
        return None
    return {"mal_id": best[1]["mal_id"], "title": best[1]["title"]}


# TheIntroDB


def fetch_theintrodb_segments(tmdb_id, season, episode, duration_ms=None):
    params = {"tmdb_id": tmdb_id, "season": season, "episode": episode}
    if duration_ms is not None:
        params["duration_ms"] = duration_ms
    return http_get_json(THEINTRODB_MEDIA_URL, params)


def format_theintrodb_segments(payload):
    """TheIntroDB returns {"intro": [{"start_ms", "end_ms"}], "credits": [...], ...}.

    A null start means the segment starts at 0; a null end means it runs to
    the end of the file.
    """
    formatted = []
    for segment_type in THEINTRODB_SEGMENT_TYPES:
        for segment in (payload or {}).get(segment_type) or []:
            start_ms = segment.get("start_ms")
            end_ms = segment.get("end_ms")
            if start_ms is None and end_ms is None:
                continue
            start_ms = start_ms if start_ms is not None else 0
            # Some submissions are zero-length placeholders, e.g. a recap of 0–0 ms.
            if end_ms is not None and end_ms <= start_ms:
                continue
            formatted.append(
                {
                    "type": segment_type,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "source": "theintrodb",
                }
            )

    return sorted(formatted, key=lambda segment: segment["start_ms"])


# AniSkip


def fetch_aniskip_segments(mal_id, episode, episode_length):
    params = [("types", skip_type) for skip_type in ANISKIP_SKIP_TYPES]
    params.append(("episodeLength", episode_length))
    return http_get_json(f"{ANISKIP_SKIP_TIMES_URL}/{mal_id}/{episode}", params)


def format_aniskip_segments(payload):
    if not payload or not payload.get("found"):
        return []

    formatted = []
    for result in payload.get("results") or []:
        interval = result.get("interval") or {}
        start = interval.get("startTime")
        end = interval.get("endTime")
        if start is None:
            continue
        formatted.append(
            {
                "type": result.get("skipType") or "segment",
                "start_ms": round(float(start) * 1000),
                "end_ms": round(float(end) * 1000) if end is not None else None,
                "source": "aniskip",
                "id": result.get("skipId"),
            }
        )

    return sorted(formatted, key=lambda segment: segment["start_ms"])
