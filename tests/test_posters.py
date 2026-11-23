"""Testes do posters.py (OMDb + TVmaze). Rodam sem internet: trocamos a função
que fala com a rede (_get) por respostas falsas.

Como rodar, dentro da pasta do projeto:   python3 -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import posters  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50
JPG = b"\xff\xd8\xff" + b"0" * 50
KEY = "SEGREDO123"


def omdb_ok(*items):
    return json.dumps({"Search": list(items), "totalResults": str(len(items)), "Response": "True"}).encode()


def movie(title, year, poster="https://m.media-amazon.com/images/M/x.jpg", imdb="tt0000001"):
    return {"Title": title, "Year": str(year), "imdbID": imdb, "Type": "movie", "Poster": poster}


def show(name, premiered, image=True, score=1.0, sid=1, summary="<p>Uma <b>serie</b>.</p>"):
    img = {"medium": "https://static.tvmaze.com/m.jpg", "original": "https://static.tvmaze.com/o.jpg"} if image else None
    return {"score": score, "show": {"id": sid, "name": name, "premiered": premiered, "image": img, "summary": summary}}


class SearchTests(unittest.TestCase):
    def test_omdb_parses_and_na_poster_becomes_none(self):
        body = omdb_ok(movie("Tenet", 2020), movie("Sem Capa", 2001, poster="N/A", imdb="tt2"))
        with mock.patch.object(posters, "_get", return_value=body):
            found = posters.search("movie", "tenet", KEY)
        self.assertEqual([c.title for c in found], ["Tenet", "Sem Capa"])
        self.assertEqual(found[0].year, 2020)
        self.assertIsNone(found[1].poster_url)

    def test_omdb_not_found_is_empty_list(self):
        with mock.patch.object(posters, "_get", return_value=b'{"Response":"False","Error":"Movie not found!"}'):
            self.assertEqual(posters.search("movie", "zzz", KEY), [])

    def test_omdb_invalid_key_raises_auth(self):
        with mock.patch.object(posters, "_get", return_value=b'{"Response":"False","Error":"Invalid API key!"}'):
            with self.assertRaises(posters.PosterAuthError):
                posters.search("movie", "x", KEY)

    def test_omdb_daily_limit_is_clear_error_not_auth(self):
        with mock.patch.object(posters, "_get", return_value=b'{"Response":"False","Error":"Request limit reached!"}'):
            with self.assertRaises(posters.PosterError) as ctx:
                posters.search("movie", "x", KEY)
        self.assertNotIsInstance(ctx.exception, posters.PosterAuthError)

    def test_omdb_garbage_and_malformed_items(self):
        with mock.patch.object(posters, "_get", return_value=b"<html>oops</html>"):
            with self.assertRaises(posters.PosterError):
                posters.search("movie", "x", KEY)
        body = omdb_ok({"Title": "Sem id"}, movie("Bom", 2000))   # primeiro item malformado
        with mock.patch.object(posters, "_get", return_value=body):
            self.assertEqual([c.title for c in posters.search("movie", "x", KEY)], ["Bom"])

    def test_movie_without_key_fails_before_network(self):
        with mock.patch.object(posters, "_get", side_effect=AssertionError("nao deveria chamar a rede")):
            with self.assertRaises(posters.PosterAuthError):
                posters.search("movie", "x", None)

    def test_tvmaze_needs_no_key_strips_html_and_handles_null_image(self):
        body = json.dumps([show("Spider Noir", "2026-05-25"), show("Sem Imagem", "2020-01-01", image=False, sid=2)]).encode()
        with mock.patch.object(posters, "_get", return_value=body):
            found = posters.search("series", "spider noir", None)
        self.assertEqual(found[0].overview, "Uma serie .")
        self.assertEqual(found[0].year, 2026)
        self.assertTrue(found[0].poster_url.endswith("o.jpg"))
        self.assertIsNone(found[1].poster_url)


class PickTests(unittest.TestCase):
    def cand(self, title, year, pop=0.0, poster="https://m.media-amazon.com/x.jpg"):
        return posters.Candidate("omdb", "t", title, title, year, "", poster, pop)

    def test_exact_title_ignores_accents_and_punctuation(self):
        c = self.cand("Homem-Aranha: Longe de Casa", 2019)
        self.assertIs(posters.pick_confident([c], "homem aranha longe de casa", 2019), c)

    def test_year_must_match_within_one(self):
        c = self.cand("Tenet", 2020)
        self.assertIs(posters.pick_confident([c], "Tenet", 2021), c)
        self.assertIsNone(posters.pick_confident([c], "Tenet", 2015))

    def test_no_year_and_several_candidates_does_not_guess(self):
        a, b = self.cand("Dune", 1984), self.cand("Dune", 2021)
        self.assertIsNone(posters.pick_confident([a, b], "Dune", None))

    def test_no_year_single_candidate_is_accepted(self):
        a = self.cand("Spider Noir", 2026)
        self.assertIs(posters.pick_confident([a], "Spider Noir", None), a)

    def test_partial_title_is_rejected(self):
        self.assertIsNone(posters.pick_confident([self.cand("Se7en Redux", 2000)], "Se7en", 2000))

    def test_without_poster_is_ignored(self):
        self.assertIsNone(posters.pick_confident([self.cand("Tenet", 2020, poster=None)], "Tenet", 2020))

    def test_clear_popularity_winner_with_year(self):
        a, b = self.cand("X", 2000, pop=10), self.cand("X", 2000, pop=1)
        self.assertIs(posters.pick_confident([b, a], "X", 2000), a)
        c = self.cand("X", 2000, pop=6)
        self.assertIsNone(posters.pick_confident([a, c], "X", 2000))


class ImageSafetyTests(unittest.TestCase):
    def test_allowed_hosts(self):
        for url in ("https://m.media-amazon.com/images/a.jpg",
                    "https://static.tvmaze.com/uploads/a.jpg", "https://tvmaze.com/a.jpg"):
            self.assertTrue(posters._image_allowed(url), url)

    def test_blocked_hosts_and_schemes(self):
        for url in ("http://m.media-amazon.com/a.jpg",           # sem https
                    "https://evil-tvmaze.com/a.jpg",             # parece, mas nao e
                    "https://tvmaze.com.evil.com/a.jpg",
                    "https://m.media-amazon.com.evil.com/a.jpg",
                    "https://localhost/a.jpg", "file:///etc/passwd", "ftp://tvmaze.com/a.jpg", ""):
            self.assertFalse(posters._image_allowed(url), url)

    def test_download_detects_type_by_content_and_sanitizes_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(posters, "_get", return_value=PNG):
                path = posters.download_poster("https://static.tvmaze.com/a", Path(tmp), "../../fuga")
            self.assertEqual(path.parent, Path(tmp))              # nao escapou da pasta
            self.assertEqual(path.suffix, ".png")
            with mock.patch.object(posters, "_get", return_value=JPG):
                self.assertEqual(posters.download_poster("https://static.tvmaze.com/a", Path(tmp), "b").suffix, ".jpg")
            with mock.patch.object(posters, "_get", return_value=b"<html>"):
                with self.assertRaises(posters.PosterError):
                    posters.download_poster("https://static.tvmaze.com/a", Path(tmp), "c")
            with mock.patch.object(posters, "_get", side_effect=AssertionError("rede")):
                with self.assertRaises(posters.PosterError):
                    posters.download_poster("https://evil.com/a.jpg", Path(tmp), "d")


class BatchTests(unittest.TestCase):
    def fake_net(self, url, limit=None):
        if "omdbapi.com" in url:
            if "Tenet" in url:
                return omdb_ok(movie("Tenet", 2020))
            if "Dune" in url:
                return omdb_ok(movie("Dune", 1984, imdb="a"), movie("Dune", 2021, imdb="b"))
            return b'{"Response":"False","Error":"Movie not found!"}'
        if "tvmaze.com/search" in url:
            return json.dumps([show("Spider Noir", "2026-05-25")]).encode()
        return PNG                                                # download da imagem

    def test_mixed_batch(self):
        todo = [(1, "movie", "Tenet", 2020), (2, "movie", "Dune", None),
                (3, "movie", "Inexistente", None), (4, "series", "Spider Noir", None)]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(posters, "_get", side_effect=self.fake_net):
            r = posters.fetch_missing_covers(todo, KEY, Path(tmp))
            self.assertEqual(sorted(i for i, _p, _l in r.found), [1, 4])
            self.assertTrue(all(p.exists() for _i, p, _l in r.found))
        self.assertEqual(r.unsure, ["Dune"])
        self.assertEqual(r.notfound, ["Inexistente"])
        self.assertIsNone(r.error)

    def test_series_work_without_any_key(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(posters, "_get", side_effect=self.fake_net):
            r = posters.fetch_missing_covers([(4, "series", "Spider Noir", None)], None, Path(tmp))
        self.assertEqual(len(r.found), 1)
        self.assertFalse(r.auth_failed)

    def test_bad_key_stops_batch_and_flags_auth(self):
        bad = mock.Mock(side_effect=posters.PosterAuthError("recusada"))
        with mock.patch.object(posters, "search", bad):
            r = posters.fetch_missing_covers([(1, "movie", "A", None), (2, "movie", "B", None)], KEY, Path("/tmp"))
        self.assertTrue(r.auth_failed)
        self.assertEqual(bad.call_count, 1)                        # nem tentou o segundo

    def test_network_down_stops_batch_without_auth_flag(self):
        down = mock.Mock(side_effect=posters.PosterNetworkError("sem internet"))
        with mock.patch.object(posters, "search", down):
            r = posters.fetch_missing_covers([(1, "series", "A", None), (2, "series", "B", None)], None, Path("/tmp"))
        self.assertEqual(r.error, "sem internet")
        self.assertFalse(r.auth_failed)
        self.assertEqual(down.call_count, 1)

    def test_isolated_failure_continues_to_next_title(self):
        def net(url, limit=None):
            if "Quebrado" in url:
                raise posters.PosterError("erro so neste")
            return self.fake_net(url, limit)
        todo = [(1, "movie", "Quebrado", None), (2, "movie", "Tenet", 2020)]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(posters, "_get", side_effect=net):
            r = posters.fetch_missing_covers(todo, KEY, Path(tmp))
        self.assertEqual([i for i, _p, _l in r.found], [2])
        self.assertEqual(r.notfound, ["Quebrado"])


class SecretTests(unittest.TestCase):
    def test_key_never_appears_in_error_messages(self):
        import urllib.error
        import urllib.request
        for exc in (urllib.error.HTTPError("https://x/?apikey=" + KEY, 401, "no", {}, None),
                    urllib.error.HTTPError("https://x/?apikey=" + KEY, 500, "no", {}, None),
                    urllib.error.URLError("falha em https://x/?apikey=" + KEY)):
            with mock.patch.object(urllib.request, "urlopen", side_effect=exc):
                with self.assertRaises(posters.PosterError) as ctx:
                    posters._get("https://www.omdbapi.com/?apikey=" + KEY)
            self.assertNotIn(KEY, str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)            # `from None`: nada escondido na cadeia


if __name__ == "__main__":
    unittest.main()