# skipper

Prints intro, recap and credits timestamps for a TV or anime episode file as JSON, so a
media player or plugin can turn them into chapters or skip buttons.

```console
$ skipper "/tv/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mkv"
{"ok":true,"status":"ok","provider":"theintrodb","tmdb_id":1396,"matched_title":"Breaking Bad",
 "segments":[{"type":"intro","start_ms":228664,"end_ms":246143,"source":"theintrodb"},
             {"type":"credits","start_ms":3431000,"end_ms":null,"source":"theintrodb"}], ...}
```

## How it works

1. The title, year, season and episode come from the file name (and `Season NN` folders)
   using [guessit](https://github.com/guessit-io/guessit).
2. The show is looked up on [TMDB](https://www.themoviedb.org/).
3. Anime (Japanese animation on TMDB, or anything TMDB doesn't match exactly) is matched
   to a MyAnimeList ID through [AniList](https://anilist.co/) or [Jikan](https://jikan.moe/),
   in your configured order, and skip times come from [AniSkip](https://aniskip.com/).
   If AniSkip has nothing, TheIntroDB is tried.
4. Other shows get skip times from [TheIntroDB](https://theintrodb.org/).

Every lookup is cached in `$XDG_CACHE_HOME/skipper/cache.db` (default `~/.cache/skipper/`).
Matches and skip times are kept for 7–30 days; "nothing found" answers expire after a day.

## Configuration

Settings live in `~/.config/skipper/config.toml` (or `$XDG_CONFIG_HOME/skipper/config.toml`,
or the file named by `SKIPPER_CONFIG` / `--config`). The file is optional.

```toml
[tmdb]
# TMDB API read access token (TMDB → Settings → API), the long "eyJ..." one.
token = "eyJhbGciOi..."

[anime]
# Anime title lookup providers, tried in this order. Choose from "anilist", "jikan".
providers = ["anilist", "jikan"]
```

Keep it private (`chmod 600`), since it holds your token. Every setting can be overridden;
a flag beats an environment variable, which beats the config file:

| Setting | Flag | Environment variable | Default |
|---|---|---|---|
| TMDB token | `--tmdb-token TOKEN` | `SKIPPER_TMDB_TOKEN` | none: only anime can be looked up |
| Anime providers | `--anime-providers anilist,jikan` | `SKIPPER_ANIME_PROVIDERS` | `anilist,jikan` |
| Config file | `--config PATH` | `SKIPPER_CONFIG` | `~/.config/skipper/config.toml` |

A provider that fails (network error, rate limit) is skipped and noted in `warnings`; the
next one in the list is tried. `--tmdb-token` is visible to other users in the process
list, so prefer the config file on shared machines.

Anime lookups need the episode duration. skipper runs `ffprobe` on the file, or you can
pass `--duration-ms` (players already know it) or set `SKIPPER_DURATION_MS`.

## Output

Always one JSON object on stdout, even on failure.

| Field | Meaning |
|---|---|
| `ok`, `status` | `true`/`"ok"`, or `false` with `"error"` / `"not_found"` |
| `segments` | list of `{type, start_ms, end_ms, source}`; `end_ms` may be `null` (runs to the end) |
| `provider` | `theintrodb` or `aniskip` |
| `media_kind` | `tv` or `anime` |
| `tmdb_id`, `mal_id`, `matched_title` | what the file was matched to |
| `error`, `exit_code` | present on failure |
| `warnings` | non-fatal problems, e.g. a missing TMDB token |

Segment types: TheIntroDB gives `recap`, `intro`, `credits`, `preview`; AniSkip gives
`op`, `ed`, `mixed-op`, `mixed-ed`, `recap`.

Exit codes: `0` ok, `2` bad input (file name, duration or configuration), `3` not found,
`4` provider or runtime error.

From Python: `skipper.resolve(path, duration_ms=None, settings=None)` returns
`(response, exit_code)`. Settings default to the config file and environment; build your own
with `skipper.config.load_settings(tmdb_token=..., anime_providers=[...])`, which raises
`ConfigError` for invalid settings.

## mpv

`mpv/skipper.lua` runs skipper whenever a local file loads and adds the segments as
chapters (named for [uosc](https://github.com/tomasklaen/uosc)'s chapter ranges). It skips
chapters already within 5 seconds of an existing one. Install it with the `mpv-skipper`
package, or copy it to `~/.config/mpv/scripts/`. Options go in
`~/.config/mpv/script-opts/skipper.conf`:

```ini
command=skipper                 # skipper executable
min_segment_ms=1000             # ignore shorter segments
chapter_match_tolerance_ms=5000 # reuse existing chapters this close
```

## Installing on Arch Linux

`packaging/arch/PKGBUILD` builds two packages from a tagged GitHub release:

- `skipper`: the command and Python package
- `mpv-skipper`: the mpv script, installed to `/etc/mpv/scripts/`, so remove any copy in
  `~/.config/mpv/scripts/` or it runs twice

To install the current release:

```sh
cd packaging/arch
makepkg -si
```

To test unreleased changes, put a tarball of this checkout where makepkg expects the
download, and skip the checksum (it only matches the tagged release):

```sh
cd packaging/arch
git -C ../.. archive --prefix=skipper-0.2.0/ -o "$PWD/skipper-0.2.0.tar.gz" HEAD
makepkg -si --skipchecksums
```

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests
```

Releasing: bump `__version__` in `skipper/__init__.py` and `pkgver` in the PKGBUILD,
tag `vX.Y.Z` and push it, then run `updpkgsums` in `packaging/arch`.

## License

MIT
