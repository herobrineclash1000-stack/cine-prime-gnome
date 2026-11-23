"""Testes dos caminhos XDG e da compatibilidade de armazenamento do Cine Prime."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database  # noqa: E402


class DatabasePathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_uses_stable_linuxtv_path_for_new_database(self):
        expected = self.root / "data/linuxtv/linuxtv.db"
        self.assertEqual(database.default_db_path(), expected)
        self.assertTrue(expected.parent.is_dir())

    def test_existing_stable_database_takes_precedence(self):
        current = self.root / "data/linuxtv/linuxtv.db"
        current.parent.mkdir(parents=True)
        current.touch()
        legacy = self.root / "data/cineflow/cineflow.db"
        legacy.parent.mkdir(parents=True)
        legacy.touch()
        self.assertEqual(database.default_db_path(), current)

    def test_legacy_database_is_reused_without_moving_it(self):
        legacy = self.root / "data/cineflow/cineflow.db"
        legacy.parent.mkdir(parents=True)
        legacy.touch()
        self.assertEqual(database.default_db_path(), legacy)
        self.assertTrue(legacy.exists())


if __name__ == "__main__":
    unittest.main()