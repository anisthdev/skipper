"""Settings, resolved per setting as: CLI flag > environment variable > config file > default.

The config file is TOML, at $SKIPPER_CONFIG or $XDG_CONFIG_HOME/skipper/config.toml:

    [tmdb]
    token = "eyJhbGciOi..."

    [anime]
    providers = ["anilist", "jikan"]
"""

from dataclasses import dataclass
from pathlib import Path
import os
import tomllib


ANIME_PROVIDERS = ("anilist", "jikan")
DEFAULT_ANIME_PROVIDERS = ("anilist", "jikan")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    tmdb_token: str | None = None
    anime_providers: tuple[str, ...] = DEFAULT_ANIME_PROVIDERS
    config_path: Path | None = None


def _xdg_dir(env_name, fallback):
    value = os.environ.get(env_name)
    return Path(value) if value else Path.home() / fallback


def config_dir():
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / "skipper"


def default_config_path():
    value = os.environ.get("SKIPPER_CONFIG")
    return Path(value).expanduser() if value else config_dir() / "config.toml"


def cache_db_path():
    value = os.environ.get("SKIPPER_CACHE_DB_PATH")
    if value:
        return Path(value).expanduser()
    return _xdg_dir("XDG_CACHE_HOME", ".cache") / "skipper" / "cache.db"


def read_config_file(path, required):
    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except FileNotFoundError:
        if required:
            raise ConfigError(f"Config file not found: {path}") from None
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc

    for section in ("tmdb", "anime"):
        if not isinstance(data.get(section, {}), dict):
            raise ConfigError(f"{path}: [{section}] must be a table")
    return data


def parse_anime_providers(value, source):
    """Accept a list or a comma-separated string, e.g. "jikan,anilist"."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ConfigError(f"{source}: anime providers must be a list of names")

    providers = []
    for name in (name.strip().lower() for name in value):
        if not name or name in providers:
            continue
        if name not in ANIME_PROVIDERS:
            raise ConfigError(
                f"{source}: unknown anime provider {name!r}; choose from {', '.join(ANIME_PROVIDERS)}"
            )
        providers.append(name)

    if not providers:
        raise ConfigError(f"{source}: at least one anime provider is required")
    return tuple(providers)


def load_settings(tmdb_token=None, anime_providers=None, config_path=None):
    """Arguments are CLI-level overrides; None means "not given"."""
    required = config_path is not None or "SKIPPER_CONFIG" in os.environ
    path = Path(config_path).expanduser() if config_path is not None else default_config_path()
    data = read_config_file(path, required)

    token = (
        (tmdb_token or "").strip()
        or os.environ.get("SKIPPER_TMDB_TOKEN", "").strip()
        or str(data.get("tmdb", {}).get("token") or "").strip()
        or None
    )

    if anime_providers is not None:
        providers = parse_anime_providers(anime_providers, "--anime-providers")
    elif os.environ.get("SKIPPER_ANIME_PROVIDERS"):
        providers = parse_anime_providers(os.environ["SKIPPER_ANIME_PROVIDERS"], "SKIPPER_ANIME_PROVIDERS")
    elif "providers" in data.get("anime", {}):
        providers = parse_anime_providers(data["anime"]["providers"], f"{path}: anime.providers")
    else:
        providers = DEFAULT_ANIME_PROVIDERS

    return Settings(tmdb_token=token, anime_providers=providers, config_path=path)
