"""Testes do classifier.py: decide se um vídeo é filme ou série e limpa o nome.

Rodar (na pasta do projeto):   python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from classifier import classify  # noqa: E402

ROOT = Path("/r")

# (caminho relativo à pasta da biblioteca, tipo forçado, esperado: (tipo, título, ano, temporada, episódio))
CASES = [
    # --- a sua estrutura real ---
    ("Spider Noir/T1/Spider-Noir-EP01.mp4", "series", ("series", "Spider Noir", None, 1, 1)),
    ("Series/Spider Noir/T1/Spider-Noir-EP01.mp4", None, ("series", "Spider Noir", None, 1, 1)),
    ("Series/Spider Noir/T2/Spider-Noir-EP10.mp4", None, ("series", "Spider Noir", None, 2, 10)),
    ("HMEMARNHUMNVODIA.mp4", "movie", ("movie", "HMEMARNHUMNVODIA", None, None, None)),
    # --- marcadores de episódio e de temporada ---
    ("Series/Dark/Season 1/Episódio 5.mp4", None, ("series", "Dark", None, 1, 5)),
    ("Series/Dark/S02/ep 3.mkv", None, ("series", "Dark", None, 2, 3)),
    ("Series/Foo/Capítulo 7.mp4", None, ("series", "Foo", None, 1, 7)),   # sem pista de temporada: assume 1
    ("Series/Foo-EP04.mp4", None, ("series", "Foo", None, 1, 4)),         # arquivo solto
    ("Series/Breaking Bad/Temporada 1/Breaking.Bad.S01E02.mkv", None, ("series", "Breaking Bad", None, 1, 2)),
    ("Series/The Witcher/The.Witcher.2x03.mkv", None, ("series", "The Witcher", None, 2, 3)),
    ("Solto/The.Witcher.S02E03.mkv", None, ("series", "The Witcher", None, 2, 3)),
    # --- filmes ---
    ("Filmes/John Wick (2014).mkv", None, ("movie", "John Wick", 2014, None, None)),
    ("Filmes/Interestelar.2014.1080p.mp4", None, ("movie", "Interestelar", 2014, None, None)),
    ("Solto/Forrest Gump (1994).AVI", None, ("movie", "Forrest Gump", 1994, None, None)),
    # --- falsos positivos: NÃO podem virar série ---
    ("Filmes/Se7en (1995).mkv", None, ("movie", "Se7en", 1995, None, None)),
    ("Filmes/Step 3 (2015).mkv", None, ("movie", "Step 3", 2015, None, None)),
    ("Filmes/Apollo 13 (1995).mkv", None, ("movie", "Apollo 13", 1995, None, None)),
    ("Filmes/Escape Room 2019.mkv", None, ("movie", "Escape Room", 2019, None, None)),
    ("Filmes/Tenet (2020).mkv", None, ("movie", "Tenet", 2020, None, None)),
]


class ClassifyTests(unittest.TestCase):
    def test_known_cases(self):
        for rel, forced, expected in CASES:
            with self.subTest(rel):
                item = classify(ROOT / rel, ROOT, forced)
                self.assertEqual((item.kind, item.title, item.year, item.season, item.episode), expected)

    def test_episodes_of_one_series_share_the_same_key(self):
        a = classify(ROOT / "Series/Spider Noir/T1/Spider-Noir-EP01.mp4", ROOT)
        b = classify(ROOT / "Series/Spider Noir/T2/Spider-Noir-EP10.mp4", ROOT)
        self.assertEqual(a.key, b.key)

    def test_same_name_different_year_are_different_titles(self):
        a = classify(ROOT / "Filmes/Dune (1984).mkv", ROOT)
        b = classify(ROOT / "Filmes/Dune (2021).mkv", ROOT)
        self.assertNotEqual(a.key, b.key)

    def test_forced_kind_wins_over_folder_name(self):
        item = classify(ROOT / "Series/Algo (2020).mkv", ROOT, "movie")
        self.assertEqual(item.kind, "movie")


if __name__ == "__main__":
    unittest.main()