import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skipper import cache, cli, providers
from skipper.config import ConfigError, Settings, load_settings
from skipper.resolve import resolve


TMDB_BREAKING_BAD = {
    "results": [
        {
            "id": 1396,
            "name": "Breaking Bad",
            "original_name": "Breaking Bad",
            "original_language": "en",
            "genre_ids": [18, 80],
        }
    ]
}
TMDB_FRIEREN = {
    "results": [
        {
            "id": 209867,
            "name": "Frieren: Beyond Journey's End",
            "original_name": "葬送のフリーレン",
            "original_language": "ja",
            "genre_ids": [16, 10759, 10765],
        }
    ]
}
JIKAN_FRIEREN = {
    "data": [
        {
            "mal_id": 52991,
            "type": "TV",
            "title": "Sousou no Frieren",
            "title_english": "Frieren: Beyond Journey's End",
            "title_japanese": "葬送のフリーレン",
            "title_synonyms": [],
            "popularity": 150,
        },
        {
            "mal_id": 59978,
            "type": "TV",
            "title": "Sousou no Frieren 2nd Season",
            "title_english": "Frieren: Beyond Journey's End Season 2",
            "title_japanese": "葬送のフリーレン 第2期",
            "title_synonyms": [],
            "popularity": 900,
        },
        {
            "mal_id": 56885,
            "type": "Special",
            "title": "Sousou no Frieren: ●● no Mahou",
            "title_english": None,
            "title_japanese": "",
            "title_synonyms": [],
            "popularity": 3000,
        },
    ]
}
THEINTRODB_BREAKING_BAD = {
    "tmdb_id": 1396,
    "type": "tv",
    "season": 1,
    "episode": 1,
    "recap": [{"start_ms": None, "end_ms": 0}],
    "intro": [{"start_ms": 228664, "end_ms": 246143}],
    "credits": [{"start_ms": 3431000, "end_ms": None}],
}
ANILIST_FRIEREN = {
    "data": {
        "Page": {
            "media": [
                {
                    "idMal": 52991,
                    "format": "TV",
                    "popularity": 491032,
                    "title": {
                        "romaji": "Sousou no Frieren",
                        "english": "Frieren: Beyond Journey’s End",
                        "native": "葬送のフリーレン",
                    },
                    "synonyms": ["Frieren at the Funeral"],
                },
                {
                    "idMal": 63816,
                    "format": "TV",
                    "popularity": 46975,
                    "title": {"romaji": "Sousou no Frieren 3rd Season", "english": None, "native": "葬送のフリーレン 第3期"},
                    "synonyms": [],
                },
                {
                    "idMal": None,
                    "format": "TV",
                    "popularity": 10,
                    "title": {"romaji": "Sousou no Frieren", "english": None, "native": None},
                    "synonyms": [],
                },
            ]
        }
    }
}
ANISKIP_FRIEREN = {
    "found": True,
    "results": [
        {"interval": {"startTime": 1317.66, "endTime": 1375.66}, "skipType": "ed", "skipId": "b"},
        {"interval": {"startTime": 3.392, "endTime": 93.392}, "skipType": "op", "skipId": "a"},
    ],
}


class FakeHttp:
    """Routes provider requests to canned responses and records each call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, params=None, headers=None, json_body=None):
        self.calls.append(url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        return None


class ResolveTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = cache.connect(Path(self.tmp.name) / "cache.db")
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("SKIPPER_TMDB_TOKEN", None)
        os.environ.pop("SKIPPER_DURATION_MS", None)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_resolve(self, path, routes, token="token", duration_ms=1_400_000, anime_providers=("anilist", "jikan")):
        http = FakeHttp(routes)
        settings = Settings(tmdb_token=token, anime_providers=anime_providers)
        with mock.patch.object(providers, "http_get_json", http):
            response, code = resolve(path, duration_ms=duration_ms, settings=settings, cache_conn=self.conn)
        return response, code, http


class ResolveTests(ResolveTestCase):
    def test_tv_show_uses_theintrodb(self):
        response, code, _ = self.run_resolve(
            "/tv/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mkv",
            {
                providers.TMDB_SEARCH_TV_URL: TMDB_BREAKING_BAD,
                providers.THEINTRODB_MEDIA_URL: THEINTRODB_BREAKING_BAD,
            },
        )
        self.assertEqual(code, 0)
        self.assertEqual(response["provider"], "theintrodb")
        self.assertEqual(response["tmdb_id"], 1396)
        self.assertEqual(
            response["segments"],
            [
                {"type": "intro", "start_ms": 228664, "end_ms": 246143, "source": "theintrodb"},
                {"type": "credits", "start_ms": 3431000, "end_ms": None, "source": "theintrodb"},
            ],
        )

    def test_anime_on_tmdb_uses_aniskip(self):
        response, code, _ = self.run_resolve(
            "[SubsPlease] Sousou no Frieren - S01E05 (1080p).mkv",
            {
                providers.TMDB_SEARCH_TV_URL: TMDB_FRIEREN,
                providers.JIKAN_ANIME_SEARCH_URL: JIKAN_FRIEREN,
                providers.ANISKIP_SKIP_TIMES_URL: ANISKIP_FRIEREN,
            },
        )
        self.assertEqual(code, 0)
        self.assertEqual(response["provider"], "aniskip")
        self.assertEqual(response["mal_id"], 52991)
        self.assertEqual([s["type"] for s in response["segments"]], ["op", "ed"])
        self.assertEqual(response["segments"][0]["start_ms"], 3392)

    def test_anime_falls_back_to_theintrodb_when_aniskip_is_empty(self):
        response, code, _ = self.run_resolve(
            "Sousou no Frieren S01E05.mkv",
            {
                providers.TMDB_SEARCH_TV_URL: TMDB_FRIEREN,
                providers.JIKAN_ANIME_SEARCH_URL: JIKAN_FRIEREN,
                providers.ANISKIP_SKIP_TIMES_URL: None,
                providers.THEINTRODB_MEDIA_URL: {"intro": [{"start_ms": 1000, "end_ms": 90000}]},
            },
        )
        self.assertEqual(code, 0)
        self.assertEqual(response["provider"], "theintrodb")
        self.assertEqual(response["media_kind"], "anime")

    def test_jikan_failure_on_tmdb_anime_still_tries_theintrodb(self):
        response, code, _ = self.run_resolve(
            "Sousou no Frieren S01E05.mkv",
            {
                providers.TMDB_SEARCH_TV_URL: TMDB_FRIEREN,
                providers.JIKAN_ANIME_SEARCH_URL: OSError("timed out"),
                providers.THEINTRODB_MEDIA_URL: {"intro": [{"start_ms": 1000, "end_ms": 90000}]},
            },
        )
        self.assertEqual(code, 0)
        self.assertEqual(response["provider"], "theintrodb")
        self.assertIn("Jikan request failed", response["warnings"][0])

    def test_season_two_picks_season_two_entry(self):
        response, code, _ = self.run_resolve(
            "/anime/Sousou no Frieren/Season 2/Sousou no Frieren - 03.mkv",
            {
                providers.JIKAN_ANIME_SEARCH_URL: JIKAN_FRIEREN,
                providers.ANISKIP_SKIP_TIMES_URL: ANISKIP_FRIEREN,
            },
            token=None,
        )
        self.assertEqual(code, 0)
        self.assertEqual(response["mal_id"], 59978)
        self.assertIn("No TMDB token configured", response["warnings"][0])

    def test_misspelled_title_is_not_matched_to_first_result(self):
        response, code, _ = self.run_resolve(
            "Sosou no Frirren S01E01.mkv",
            {providers.JIKAN_ANIME_SEARCH_URL: JIKAN_FRIEREN},
            token=None,
        )
        self.assertEqual(code, 3)
        self.assertEqual(response["status"], "not_found")

    def test_results_are_cached(self):
        routes = {
            providers.TMDB_SEARCH_TV_URL: TMDB_BREAKING_BAD,
            providers.THEINTRODB_MEDIA_URL: THEINTRODB_BREAKING_BAD,
        }
        path = "Breaking Bad S01E01.mkv"
        _, _, first = self.run_resolve(path, routes)
        response, _, second = self.run_resolve(path, routes)
        self.assertEqual(len(first.calls), 2)
        self.assertEqual(second.calls, [])
        self.assertTrue(response["cache_hit"])

    def test_empty_results_expire_after_a_day(self):
        cache.put(self.conn, "theintrodb", "k", None, now=1_000)
        self.assertEqual(cache.get(self.conn, "theintrodb", "k", cache.TTL_FOUND["theintrodb"], now=1_000 + 60), (None, True))
        self.assertEqual(
            cache.get(self.conn, "theintrodb", "k", cache.TTL_FOUND["theintrodb"], now=1_000 + cache.DAY + 1),
            (None, False),
        )

    def test_provider_error_is_reported_as_json_error(self):
        response, code, _ = self.run_resolve(
            "Breaking Bad S01E01.mkv",
            {providers.TMDB_SEARCH_TV_URL: OSError("timed out")},
        )
        self.assertEqual(code, 4)
        self.assertFalse(response["ok"])
        self.assertIn("TMDB request failed", response["error"])

    def test_anime_without_duration_is_a_parse_error(self):
        response, code, _ = self.run_resolve(
            "/does/not/exist/Sousou no Frieren - 05.mkv",
            {providers.JIKAN_ANIME_SEARCH_URL: JIKAN_FRIEREN},
            token=None,
            duration_ms=None,
        )
        self.assertEqual(code, 2)
        self.assertIn("duration", response["error"])

    def test_missing_episode_is_a_parse_error(self):
        response, code, _ = self.run_resolve("Breaking Bad.mkv", {})
        self.assertEqual(code, 2)


class AnimeProviderTests(ResolveTestCase):
    PATH = "/anime/Sousou no Frieren/Season 1/Sousou no Frieren - 05.mkv"

    def test_anilist_is_tried_first_by_default(self):
        response, code, http = self.run_resolve(
            self.PATH,
            {
                providers.ANILIST_GRAPHQL_URL: ANILIST_FRIEREN,
                providers.JIKAN_ANIME_SEARCH_URL: JIKAN_FRIEREN,
                providers.ANISKIP_SKIP_TIMES_URL: ANISKIP_FRIEREN,
            },
            token=None,
        )
        self.assertEqual(code, 0)
        self.assertEqual(response["mal_id"], 52991)
        self.assertNotIn(providers.JIKAN_ANIME_SEARCH_URL, http.calls)

    def test_provider_order_is_respected(self):
        _, code, http = self.run_resolve(
            self.PATH,
            {
                providers.ANILIST_GRAPHQL_URL: ANILIST_FRIEREN,
                providers.JIKAN_ANIME_SEARCH_URL: JIKAN_FRIEREN,
                providers.ANISKIP_SKIP_TIMES_URL: ANISKIP_FRIEREN,
            },
            token=None,
            anime_providers=("jikan", "anilist"),
        )
        self.assertEqual(code, 0)
        self.assertEqual(http.calls[0], providers.JIKAN_ANIME_SEARCH_URL)
        self.assertNotIn(providers.ANILIST_GRAPHQL_URL, http.calls)

    def test_failing_provider_falls_through_to_the_next(self):
        response, code, _ = self.run_resolve(
            self.PATH,
            {
                providers.ANILIST_GRAPHQL_URL: OSError("timed out"),
                providers.JIKAN_ANIME_SEARCH_URL: JIKAN_FRIEREN,
                providers.ANISKIP_SKIP_TIMES_URL: ANISKIP_FRIEREN,
            },
            token=None,
        )
        self.assertEqual(code, 0)
        self.assertEqual(response["mal_id"], 52991)
        self.assertTrue(any("AniList request failed" in w for w in response["warnings"]))

    def test_all_providers_failing_is_an_error(self):
        response, code, _ = self.run_resolve(
            self.PATH,
            {
                providers.ANILIST_GRAPHQL_URL: OSError("timed out"),
                providers.JIKAN_ANIME_SEARCH_URL: OSError("timed out"),
            },
            token=None,
        )
        self.assertEqual(code, 4)
        self.assertIn("AniList request failed", response["error"])
        self.assertIn("Jikan request failed", response["error"])

    def test_fuzzy_tmdb_match_still_tries_anime_providers(self):
        live_action = {
            "results": [
                {"id": 333686, "name": "The Apothecary Diaries", "original_name": "薬屋のひとりごと",
                 "original_language": "ja", "genre_ids": [18, 9648]}
            ]
        }
        response, code, _ = self.run_resolve(
            self.PATH,
            {
                providers.TMDB_SEARCH_TV_URL: live_action,
                providers.ANILIST_GRAPHQL_URL: ANILIST_FRIEREN,
                providers.ANISKIP_SKIP_TIMES_URL: ANISKIP_FRIEREN,
            },
        )
        self.assertEqual(code, 0)
        self.assertEqual(response["provider"], "aniskip")
        self.assertEqual(response["mal_id"], 52991)

    def test_anilist_graphql_errors_are_not_cached_as_no_match(self):
        routes = {providers.ANILIST_GRAPHQL_URL: {"data": None, "errors": [{"message": "Too Many Requests"}]}}
        _, code, _ = self.run_resolve(self.PATH, routes, token=None, anime_providers=("anilist",))
        self.assertEqual(code, 4)
        _, hit = cache.get(self.conn, "anilist_search", "sousou no frieren|1", cache.TTL_FOUND["anilist_search"])
        self.assertFalse(hit)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "skipper" / "config.toml"
        self.config.parent.mkdir()
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        for name in ("SKIPPER_CONFIG", "SKIPPER_TMDB_TOKEN", "SKIPPER_ANIME_PROVIDERS"):
            os.environ.pop(name, None)

    def test_defaults_without_config_file(self):
        settings = load_settings()
        self.assertIsNone(settings.tmdb_token)
        self.assertEqual(settings.anime_providers, ("anilist", "jikan"))

    def test_config_file_is_read(self):
        self.config.write_text('[tmdb]\ntoken = "from-file"\n[anime]\nproviders = ["jikan"]\n')
        settings = load_settings()
        self.assertEqual(settings.tmdb_token, "from-file")
        self.assertEqual(settings.anime_providers, ("jikan",))

    def test_flag_beats_env_beats_file(self):
        self.config.write_text('[tmdb]\ntoken = "from-file"\n[anime]\nproviders = ["jikan"]\n')
        os.environ["SKIPPER_TMDB_TOKEN"] = "from-env"
        os.environ["SKIPPER_ANIME_PROVIDERS"] = "jikan,anilist"
        self.assertEqual(load_settings().tmdb_token, "from-env")
        self.assertEqual(load_settings().anime_providers, ("jikan", "anilist"))
        settings = load_settings(tmdb_token="from-flag", anime_providers="anilist")
        self.assertEqual(settings.tmdb_token, "from-flag")
        self.assertEqual(settings.anime_providers, ("anilist",))

    def test_invalid_settings_raise_config_error(self):
        with self.assertRaises(ConfigError):
            load_settings(anime_providers="anilist,kitsu")
        with self.assertRaises(ConfigError):
            load_settings(config_path=Path(self.tmp.name) / "missing.toml")
        self.config.write_text("[tmdb\n")
        with self.assertRaises(ConfigError):
            load_settings()

    def test_cli_reports_config_errors_as_json(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main(["--anime-providers", "kitsu", "Show S01E01.mkv"])
        self.assertEqual(code, 2)
        self.assertIn("unknown anime provider 'kitsu'", json.loads(stdout.getvalue())["error"])


class HttpTests(unittest.TestCase):
    def test_connection_errors_are_retried_once(self):
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok": 1}')
        urlopen = mock.Mock(side_effect=[ConnectionResetError("reset"), response])
        with mock.patch.object(providers.urllib.request, "urlopen", urlopen):
            self.assertEqual(providers.http_get_json("https://example.test", {"q": "x"}), {"ok": 1})
        self.assertEqual(urlopen.call_count, 2)

    def test_connection_errors_give_up_after_retrying(self):
        urlopen = mock.Mock(side_effect=ConnectionResetError("reset"))
        with mock.patch.object(providers.urllib.request, "urlopen", urlopen), self.assertRaises(ConnectionResetError):
            providers.http_get_json("https://example.test")
        self.assertEqual(urlopen.call_count, 2)


class CliTests(unittest.TestCase):
    def test_cli_prints_json_even_on_unexpected_errors(self):
        stdout = io.StringIO()
        with mock.patch.object(cli, "resolve", side_effect=RuntimeError("boom")), contextlib.redirect_stdout(stdout):
            code = cli.main(["Breaking Bad S01E01.mkv"])
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(stdout.getvalue())["error"], "Unexpected error: boom")


if __name__ == "__main__":
    unittest.main()
