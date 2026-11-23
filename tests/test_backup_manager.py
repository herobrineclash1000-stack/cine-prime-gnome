"""Testes de backup/restauro: apenas XDG e SQLite temporários, sem dados reais."""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_manager
import config
import covers
from database import Database


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.root / "data"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db_path = self.root / "library.sqlite"
        self.db = Database(self.db_path)
        self.addCleanup(self.db.conn.close)
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO titles (id,key,kind,name) VALUES (1,'film:test','movie','Filme de teste')"
            )
            self.db.conn.execute(
                "INSERT INTO files (id,title_id,path) VALUES (1,1,'/media/nao-copiar.mp4')"
            )
        self.db.save_progress("/media/nao-copiar.mp4", 42.5, 100.0)
        config.add_folder("/media/Filmes", "movie")
        config.set_omdb_key("TEST-SECRET")
        self.cover = covers.covers_dir() / "1-cover.jpg"
        self.cover.write_bytes(b"fake-cover-data")
        self.backup = self.root / "backups" / "library.gnometv"

    def test_backup_contains_consistent_db_config_and_managed_covers(self):
        result = backup_manager.create_backup(self.db.conn, self.backup)
        self.assertTrue(self.backup.is_file())
        self.assertEqual(result["files"], 3)
        info = backup_manager.inspect_backup(self.backup)
        self.assertEqual(info["covers"], 1)
        self.assertEqual(info["database_size"] > 0, True)
        with zipfile.ZipFile(self.backup) as archive:
            names = set(archive.namelist())
            self.assertIn("database/library.sqlite", names)
            self.assertIn("config/config.json", names)
            self.assertIn("covers/1-cover.jpg", names)
            self.assertFalse(any("nao-copiar.mp4" in name for name in names))

    def test_restore_replaces_db_and_config_and_restores_cover(self):
        backup_manager.create_backup(self.db.conn, self.backup)
        with self.db.conn:
            self.db.conn.execute("UPDATE titles SET name='Estado posterior' WHERE id=1")
        config.set_omdb_key("CHANGED")
        self.cover.unlink()

        result = backup_manager.restore_backup(self.backup, self.db.conn)

        self.assertEqual(self.db.conn.execute("SELECT name FROM titles WHERE id=1").fetchone()[0],
                         "Filme de teste")
        self.assertEqual(self.db.conn.execute("SELECT position FROM progress WHERE file_id=1").fetchone()[0],
                         42.5)
        self.assertEqual(config.get_omdb_key(), "TEST-SECRET")
        self.assertEqual(self.cover.read_bytes(), b"fake-cover-data")
        self.assertEqual(result["covers"], 1)

    def test_corrupt_archive_is_rejected_without_changing_database(self):
        backup_manager.create_backup(self.db.conn, self.backup)
        corrupt = self.root / "corrupt.gnometv"
        with zipfile.ZipFile(self.backup) as source, zipfile.ZipFile(corrupt, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "database/library.sqlite":
                    data += b"tamper"
                target.writestr(name, data)
        with self.db.conn:
            self.db.conn.execute("UPDATE titles SET name='Não alterar' WHERE id=1")
        with self.assertRaises(backup_manager.BackupError):
            backup_manager.restore_backup(corrupt, self.db.conn)
        self.assertEqual(self.db.conn.execute("SELECT name FROM titles WHERE id=1").fetchone()[0],
                         "Não alterar")

    def test_restore_failure_rolls_back_database_config_and_overwritten_covers(self):
        backup_manager.create_backup(self.db.conn, self.backup)
        second = covers.covers_dir() / "2-cover.jpg"
        second.write_bytes(b"second-before")
        # Recreate the archive with two managed covers so the second replacement can fail.
        self.cover.write_bytes(b"first-before")
        backup_manager.create_backup(self.db.conn, self.backup)
        self.cover.write_bytes(b"first-after")
        second.write_bytes(b"second-after")
        with self.db.conn:
            self.db.conn.execute("UPDATE titles SET name='current-state' WHERE id=1")
        config.set_omdb_key("CURRENT-SECRET")

        real_replace = backup_manager.os.replace
        calls = {"count": 0}
        def fail_on_second_cover(source, destination):
            calls["count"] += 1
            if calls["count"] == 3:  # config, first cover, then fail on second cover
                raise OSError("simulated disk failure")
            return real_replace(source, destination)

        with mock.patch("backup_manager.os.replace", side_effect=fail_on_second_cover):
            with self.assertRaises(OSError):
                backup_manager.restore_backup(self.backup, self.db.conn)

        self.assertEqual(self.db.conn.execute("SELECT name FROM titles WHERE id=1").fetchone()[0],
                         "current-state")
        self.assertEqual(config.get_omdb_key(), "CURRENT-SECRET")
        self.assertEqual(self.cover.read_bytes(), b"first-after")
        self.assertEqual(second.read_bytes(), b"second-after")

    def test_path_traversal_archive_is_rejected(self):
        malicious = self.root / "malicious.gnometv"
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("../escape", "bad")
        with self.assertRaises(backup_manager.BackupError):
            backup_manager.inspect_backup(malicious)


if __name__ == "__main__":
    unittest.main()