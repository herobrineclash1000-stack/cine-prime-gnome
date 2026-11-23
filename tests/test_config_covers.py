"""Testes da configuração (config.py) e das capas guardadas (covers.py).

Usam pastas temporárias: a sua configuração e as suas capas reais não são tocadas.

Rodar (na pasta do projeto):   python3 -m unittest discover -s tests -v
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import covers  # noqa: E402


class Isolated(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_DATA_HOME": str(self.tmp / "data"),
        })
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OMDB_API_KEY", None)          # restaurado pelo patch.dict no fim


class ConfigTests(Isolated):
    def test_empty_config_has_no_folders_and_no_key(self):
        self.assertEqual(config.get_folders(), [])
        self.assertIsNone(config.get_omdb_key())

    def test_add_folder_keeps_the_key(self):
        """Defeito antigo: guardar uma pasta apagava a chave da API."""
        config.set_omdb_key("ABC123")
        config.add_folder("/v/Filmes", "movie")
        self.assertEqual(config.get_omdb_key(), "ABC123")
        self.assertEqual(config.get_folders(), [{"path": "/v/Filmes", "kind": "movie"}])

    def test_set_key_keeps_the_folders(self):
        config.add_folder("/v/Filmes", "movie")
        config.set_omdb_key("ABC123")
        self.assertEqual(len(config.get_folders()), 1)

    def test_subtitle_font_size_is_saved_and_clamped(self):
        self.assertEqual(config.get_subtitle_font_size(), 24)
        config.set_subtitle_font_size(32)
        self.assertEqual(config.get_subtitle_font_size(), 32)
        config.set_subtitle_font_size(99)
        self.assertEqual(config.get_subtitle_font_size(), 40)
        config.set_subtitle_font_size(2)
        self.assertEqual(config.get_subtitle_font_size(), 16)

    def test_next_episode_notice_is_saved_and_validated(self):
        self.assertEqual(config.get_next_episode_notice_seconds(), 60)
        config.set_next_episode_notice_seconds(90)
        self.assertEqual(config.get_next_episode_notice_seconds(), 90)
        for seconds in (240, 360):
            config.set_next_episode_notice_seconds(seconds)
            self.assertEqual(config.get_next_episode_notice_seconds(), seconds)
        with self.assertRaises(ValueError):
            config.set_next_episode_notice_seconds(45)

    def test_remove_folder_keeps_the_key(self):
        config.add_folder("/v/Filmes", "movie")
        config.add_folder("/v/Series", "series")
        config.set_omdb_key("ABC123")
        config.remove_folder("/v/Filmes")
        self.assertEqual([f["path"] for f in config.get_folders()], ["/v/Series"])
        self.assertEqual(config.get_omdb_key(), "ABC123")

    def test_adding_the_same_folder_replaces_its_kind(self):
        config.add_folder("/v/X", "movie")
        config.add_folder("/v/X", "series")
        self.assertEqual(config.get_folders(), [{"path": "/v/X", "kind": "series"}])

    def test_invalid_kind_becomes_auto(self):
        config.add_folder("/v/X", "banana")
        self.assertEqual(config.get_folders()[0]["kind"], "auto")

    def test_file_is_private_to_the_owner(self):
        config.set_omdb_key("ABC123")
        mode = stat.S_IMODE((self.tmp / "config/linuxtv/config.json").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_environment_variable_wins_over_file(self):
        config.set_omdb_key("DOARQUIVO")
        with mock.patch.dict(os.environ, {"OMDB_API_KEY": "DOAMBIENTE"}):
            self.assertEqual(config.get_omdb_key(), "DOAMBIENTE")

    def test_key_is_trimmed_and_blank_means_none(self):
        config.set_omdb_key("  ABC123  \n")
        self.assertEqual(config.get_omdb_key(), "ABC123")
        config.set_omdb_key("   ")
        self.assertIsNone(config.get_omdb_key())

    def test_corrupted_file_does_not_crash_and_can_be_rewritten(self):
        target = self.tmp / "config/linuxtv/config.json"
        target.parent.mkdir(parents=True)
        target.write_text("{isto não é json")
        self.assertEqual(config.get_folders(), [])
        config.add_folder("/v/X", "movie")
        self.assertEqual(json.loads(target.read_text())["folders"][0]["path"], "/v/X")

    def test_legacy_config_is_read_without_creating_legacy_directory(self):
        legacy = self.tmp / "config/cineflow/config.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"folders": [{"path": "/media/Filmes", "kind": "movie"}],
                                      "omdb_key": "LEGACY-KEY"}))

        self.assertEqual(config.get_folders(), [{"path": "/media/Filmes", "kind": "movie"}])
        self.assertEqual(config.get_omdb_key(), "LEGACY-KEY")
        self.assertFalse((self.tmp / "config/linuxtv/config.json").exists())

    def test_missing_legacy_config_does_not_create_legacy_directory(self):
        self.assertEqual(config.get_folders(), [])
        self.assertFalse((self.tmp / "config/cineflow").exists())

    def test_current_config_takes_precedence_over_legacy_config(self):
        current = self.tmp / "config/linuxtv/config.json"
        current.parent.mkdir(parents=True)
        current.write_text(json.dumps({"folders": [{"path": "/media/Actual", "kind": "series"}]}))
        legacy = self.tmp / "config/cineflow/config.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"folders": [{"path": "/media/Antiga", "kind": "movie"}]}))

        self.assertEqual(config.get_folders(), [{"path": "/media/Actual", "kind": "series"}])


class CoversTests(Isolated):
    def test_import_copies_into_the_app_folder_and_keeps_the_original(self):
        src = self.tmp / "minha-capa.PNG"
        src.write_bytes(b"png-falso")
        new = covers.import_cover(src, 7)
        self.assertTrue(new.exists())
        self.assertEqual(new.parent, covers.covers_dir())
        self.assertTrue(new.name.startswith("7-"))
        self.assertEqual(new.suffix, ".png")                 # extensão em minúsculas
        self.assertTrue(src.exists())                        # o original NÃO some

    def test_remove_deletes_only_what_the_app_manages(self):
        src = self.tmp / "minha-capa.png"
        src.write_bytes(b"x")
        managed = covers.import_cover(src, 1)
        covers.remove_if_managed(str(managed))
        self.assertFalse(managed.exists())
        covers.remove_if_managed(str(src))                   # fora da pasta do app: NUNCA apaga
        self.assertTrue(src.exists())

    def test_remove_of_missing_file_is_harmless(self):
        covers.remove_if_managed(str(covers.covers_dir() / "nao-existe.png"))


if __name__ == "__main__":
    unittest.main()