from pathlib import Path
import os
import re
import subprocess
import unicodedata

from guessit import guessit


# Language detection mistakes title words for language codes, e.g. the "Ko" in
# "Oshi no Ko". Skipper never needs the language, so turn it off.
GUESSIT_OPTIONS = {"excludes": ["language", "subtitle_language"]}


def normalize_title(value):
    if value is None:
        return ""

    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def first_number(value):
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def find_season_in_parent_dirs(path):
    for parent in path.parents:
        if str(parent) in (".", "/"):
            break

        season = first_number(guessit(parent.name, GUESSIT_OPTIONS).get("season"))
        if season is not None:
            return season

    return None


def parse_media_path(path_text):
    path = Path(path_text).expanduser()
    metadata = guessit(str(path), GUESSIT_OPTIONS)

    season = first_number(metadata.get("season"))
    if season is None:
        season = find_season_in_parent_dirs(path)

    return {
        "path": path,
        "title": metadata.get("title"),
        "year": first_number(metadata.get("year")),
        "season": season,
        "episode": first_number(metadata.get("episode")),
    }


def duration_ms_from_env():
    try:
        duration_ms = int(os.environ.get("SKIPPER_DURATION_MS", ""))
    except ValueError:
        return None
    return duration_ms if duration_ms > 0 else None


def duration_ms_from_file(path):
    if not path.is_file():
        return None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        duration_seconds = float(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None

    return round(duration_seconds * 1000) if duration_seconds > 0 else None
