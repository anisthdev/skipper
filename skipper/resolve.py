from contextlib import closing

from . import cache, providers
from .config import load_settings
from .media import duration_ms_from_env, duration_ms_from_file, normalize_title, parse_media_path


EXIT_OK = 0
EXIT_PARSE_ERROR = 2
EXIT_NOT_FOUND = 3
EXIT_RUNTIME_ERROR = 4


class ProviderError(Exception):
    pass


class LookupFailed(Exception):
    def __init__(self, message, exit_code):
        super().__init__(message)
        self.exit_code = exit_code


def call_provider(name, fetch):
    try:
        return fetch()
    except Exception as exc:
        raise ProviderError(f"{name} request failed: {exc}") from exc


def lookup_tmdb_show(conn, token, title, year):
    key = f"{normalize_title(title)}|{year or ''}"
    show, _hit = cache.cached(
        conn,
        "tmdb_search",
        key,
        lambda: providers.pick_tmdb_show(
            call_provider("TMDB", lambda: providers.fetch_tmdb_search(token, title, year)), title
        ),
    )
    return show


PROVIDER_NAMES = {"anilist": "AniList", "jikan": "Jikan"}


def lookup_anime_search(conn, provider, title, season):
    fetch, to_candidates = providers.ANIME_SEARCH[provider]
    key = f"{normalize_title(title)}|{season or ''}"
    anime, _hit = cache.cached(
        conn,
        f"{provider}_search",
        key,
        lambda: providers.pick_anime(
            to_candidates(call_provider(PROVIDER_NAMES[provider], lambda: fetch(title))), title, season
        ),
    )
    return anime


def find_anime(conn, media, show, anime_providers, warnings):
    """Try each provider in priority order with the file's title, then TMDB's names.

    A provider that fails is skipped. Raises ProviderError only when every
    provider failed, so "not found" is only reported when one actually answered.
    """
    titles = []
    for title in [media["title"], *((show["name"], show["original_name"]) if show else ())]:
        if title and normalize_title(title) not in {normalize_title(seen) for seen in titles}:
            titles.append(title)

    errors = []
    for provider in anime_providers:
        try:
            for title in titles:
                anime = lookup_anime_search(conn, provider, title, media["season"])
                if anime:
                    warnings.extend(errors)
                    return anime
        except ProviderError as exc:
            errors.append(str(exc))

    if len(errors) == len(anime_providers):
        raise ProviderError("; ".join(errors))
    warnings.extend(errors)
    return None


def lookup_aniskip(conn, anime, episode, duration_ms):
    episode_length = round(duration_ms / 1000)
    payload, hit = cache.cached(
        conn,
        "aniskip",
        f"{anime['mal_id']}|{episode}|{episode_length}",
        lambda: call_provider(
            "AniSkip", lambda: providers.fetch_aniskip_segments(anime["mal_id"], episode, episode_length)
        ),
    )
    return providers.format_aniskip_segments(payload), hit


def lookup_theintrodb(conn, show, season, episode, duration_ms):
    payload, hit = cache.cached(
        conn,
        "theintrodb",
        f"{show['tmdb_id']}|{season}|{episode}|{duration_ms or ''}",
        lambda: call_provider(
            "TheIntroDB",
            lambda: providers.fetch_theintrodb_segments(show["tmdb_id"], season, episode, duration_ms),
        ),
    )
    return providers.format_theintrodb_segments(payload), hit


def base_response(media):
    return {
        "ok": True,
        "status": "ok",
        "input_path": str(media["path"]),
        "title": media["title"],
        "season": media["season"],
        "episode": media["episode"],
    }


def resolve_media(conn, media, settings, duration_ms, warnings):
    if not media["title"]:
        raise LookupFailed(f"Could not extract a title from '{media['path']}'.", EXIT_PARSE_ERROR)
    if media["episode"] is None:
        raise LookupFailed(f"Could not extract an episode number from '{media['path']}'.", EXIT_PARSE_ERROR)

    duration_cache = []

    def get_duration_ms():
        if not duration_cache:
            duration_cache.append(
                duration_ms or duration_ms_from_env() or duration_ms_from_file(media["path"])
            )
        return duration_cache[0]

    token = settings.tmdb_token
    show = lookup_tmdb_show(conn, token, media["title"], media["year"]) if token else None

    # Anime gets AniSkip first: it has far better anime coverage than TheIntroDB.
    # TMDB rarely knows romanized Japanese titles, so a fuzzy TMDB match may be
    # the wrong show; check the anime providers for those too.
    if show is None or providers.is_anime(show) or not show.get("exact"):
        try:
            anime = find_anime(conn, media, show, settings.anime_providers, warnings)
        except ProviderError as exc:
            if show is None:
                raise
            warnings.append(str(exc))
            anime = None

        if anime:
            if get_duration_ms() is None:
                if show is None:
                    raise LookupFailed(
                        "Could not read the video duration, which AniSkip requires. "
                        "Pass a real file path, --duration-ms, or set SKIPPER_DURATION_MS.",
                        EXIT_PARSE_ERROR,
                    )
                warnings.append("Skipped AniSkip: could not read the video duration.")
            else:
                segments, hit = lookup_aniskip(conn, anime, media["episode"], get_duration_ms())
                # Fall back to TheIntroDB only when the TMDB match is trustworthy.
                trust_show = show is not None and (show.get("exact") or providers.is_anime(show))
                if segments or not trust_show:
                    return {
                        "media_kind": "anime",
                        "provider": "aniskip",
                        "mal_id": anime["mal_id"],
                        "tmdb_id": show["tmdb_id"] if show else None,
                        "matched_title": anime["title"],
                        "duration_ms": get_duration_ms(),
                        "segments": segments,
                        "cache_hit": hit,
                    }

    if show:
        season = media["season"]
        # Anime releases usually number episodes without a season, e.g. "Show - 05".
        if season is None and providers.is_anime(show):
            season = 1
        if season is None:
            raise LookupFailed(
                f"Could not extract a season number from '{media['path']}'. "
                "Use a 'Season 01' folder or an S01E01-style filename.",
                EXIT_PARSE_ERROR,
            )
        segments, hit = lookup_theintrodb(conn, show, season, media["episode"], get_duration_ms())
        return {
            "media_kind": "anime" if providers.is_anime(show) else "tv",
            "provider": "theintrodb",
            "tmdb_id": show["tmdb_id"],
            "matched_title": show["name"],
            "duration_ms": get_duration_ms(),
            "segments": segments,
            "cache_hit": hit,
        }

    raise LookupFailed(f"No show found for '{media['title']}'.", EXIT_NOT_FOUND)


def resolve(path, duration_ms=None, settings=None, cache_conn=None):
    """Look up skip segments for an episode file.

    Returns (response, exit_code). The response is the JSON object the CLI
    prints; it always has "ok" and "status", plus "segments" on success or
    "error" on failure. settings defaults to load_settings(), which raises
    ConfigError for an invalid config file.
    """
    settings = settings or load_settings()
    media = parse_media_path(path)
    response = base_response(media)
    warnings = []

    if not settings.tmdb_token:
        warnings.append(
            "No TMDB token configured, so only anime can be looked up. Set [tmdb] token in "
            f"{settings.config_path}, SKIPPER_TMDB_TOKEN, or --tmdb-token."
        )

    try:
        if cache_conn is not None:
            response.update(resolve_media(cache_conn, media, settings, duration_ms, warnings))
        else:
            with closing(cache.connect()) as conn:
                response.update(resolve_media(conn, media, settings, duration_ms, warnings))
        exit_code = EXIT_OK
    except LookupFailed as exc:
        exit_code = exc.exit_code
        response.update(error=str(exc), exit_code=exit_code)
    except ProviderError as exc:
        exit_code = EXIT_RUNTIME_ERROR
        response.update(error=str(exc), exit_code=exit_code)

    if exit_code != EXIT_OK:
        response["ok"] = False
        response["status"] = "not_found" if exit_code == EXIT_NOT_FOUND else "error"
    if warnings:
        response["warnings"] = warnings
    return response, exit_code
