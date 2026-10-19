import argparse
import json
import sys

from . import __version__
from .config import ANIME_PROVIDERS, ConfigError, load_settings
from .resolve import EXIT_PARSE_ERROR, EXIT_RUNTIME_ERROR, resolve


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="skipper",
        description=(
            "Print intro, recap and credits timestamps for a TV or anime episode file as JSON. "
            "TV shows are looked up on TMDB and TheIntroDB, anime on AniList or MyAnimeList (Jikan) and AniSkip."
        ),
        epilog=(
            "Each option overrides its environment variable, which overrides the config file "
            "($SKIPPER_CONFIG or ~/.config/skipper/config.toml). "
            "Exit codes: 0 ok, 2 bad input (file name, duration or configuration), 3 not found, "
            "4 provider or runtime error."
        ),
    )
    parser.add_argument("path", nargs="?", help="episode file path (read from stdin when omitted)")
    parser.add_argument(
        "--duration-ms",
        type=int,
        help="video duration in milliseconds; skips running ffprobe on the file",
    )
    parser.add_argument(
        "--tmdb-token",
        metavar="TOKEN",
        help="TMDB API read access token (env: SKIPPER_TMDB_TOKEN). Visible to other users in the "
        "process list, so prefer the config file on shared machines.",
    )
    parser.add_argument(
        "--anime-providers",
        metavar="LIST",
        help=f"comma-separated anime title providers in priority order, from: {', '.join(ANIME_PROVIDERS)} "
        "(env: SKIPPER_ANIME_PROVIDERS; default: anilist,jikan)",
    )
    parser.add_argument("--config", metavar="PATH", help="config file to use (env: SKIPPER_CONFIG)")
    parser.add_argument("--version", action="version", version=f"skipper {__version__}")
    return parser.parse_args(argv)


def error_response(path, message, exit_code):
    return {"ok": False, "status": "error", "input_path": path, "error": message, "exit_code": exit_code}


def main(argv=None):
    args = parse_args(argv)
    path = args.path or sys.stdin.readline().strip()
    if not path:
        print("skipper: no file path given", file=sys.stderr)
        return 2

    try:
        settings = load_settings(
            tmdb_token=args.tmdb_token,
            anime_providers=args.anime_providers,
            config_path=args.config,
        )
        response, exit_code = resolve(path, duration_ms=args.duration_ms, settings=settings)
    except ConfigError as exc:
        exit_code = EXIT_PARSE_ERROR
        response = error_response(path, str(exc), exit_code)
    except Exception as exc:
        # Callers parse stdout, so even unexpected failures must produce JSON.
        exit_code = EXIT_RUNTIME_ERROR
        response = error_response(path, f"Unexpected error: {exc}", exit_code)

    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    return exit_code
