"""Testes da biblioteca: impressão digital, varredura, banco e retomada.

Tudo roda em pastas e bancos TEMPORÁRIOS: nunca toca nos seus vídeos nem na sua biblioteca real.

Rodar (na pasta do projeto):   python3 -m unittest discover -s tests -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from database import Database, Episode, resume_from  # noqa: E402
from scanner import CHUNK, find_videos, fingerprint, scan_folders  # noqa: E402

MOVIE = "Filmes/Tenet (2020).mp4"
EP = "Series/Show/T1/Show-EP{:02d}.mp4"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "videos"
        self.db = Database(self.tmp / "test.db")
        self.addCleanup(self.db.conn.close)

    def put(self, rel: str, size_extra: int = 0) -> Path:
        """Cria um vídeo falso. O conteúdo depende do nome, então cada um tem impressão única."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((rel.encode() * 100) + b"x" * size_extra)
        return path

    def scan(self):
        result = scan_folders([(self.root, None)], self.db.known_fingerprints())
        return result, self.db.save_scan(result.found, result.prune_roots)

    def title(self, name: str):
        rows = [t for t in self.db.list_titles() if t["name"] == name]
        self.assertEqual(len(rows), 1, f"esperava 1 título '{name}', achei {len(rows)}")
        return rows[0]


class FingerprintTests(Base):
    def test_same_content_other_name_is_same_fingerprint(self):
        a = self.put("a.mp4")
        b = self.root / "outro nome.mp4"
        b.write_bytes(a.read_bytes())
        self.assertEqual(fingerprint(a), fingerprint(b))

    def test_different_content_is_different(self):
        self.assertNotEqual(fingerprint(self.put("a.mp4")), fingerprint(self.put("b.mp4")))

    def test_empty_and_missing_files_have_none(self):
        empty = self.root / "vazio.mp4"
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_bytes(b"")
        self.assertIsNone(fingerprint(empty))
        self.assertIsNone(fingerprint(self.root / "nao-existe.mp4"))

    def test_change_at_the_end_of_a_big_file_is_detected(self):
        big = self.root / "grande.mp4"
        big.parent.mkdir(parents=True, exist_ok=True)
        big.write_bytes(b"a" * (CHUNK * 4))
        before = fingerprint(big)
        big.write_bytes(b"a" * (CHUNK * 4 - 1) + b"b")      # mesmo tamanho, último byte diferente
        self.assertNotEqual(before, fingerprint(big))


class FindVideosTests(Base):
    def test_finds_videos_in_subfolders_and_ignores_the_rest(self):
        self.put("a.mp4")
        self.put("Sub/Pasta/b.MKV")           # extensão em maiúsculas
        self.put("c.AVI")
        for other in ("legenda.srt", "capa.jpg", "notas.txt"):
            (self.root / other).write_text("x")
        found = sorted(p.name for p in find_videos(self.root))
        self.assertEqual(found, ["a.mp4", "b.MKV", "c.AVI"])


class ScanTests(Base):
    def library(self):
        self.put(MOVIE)
        for n in (1, 2):
            self.put(EP.format(n))
        self.put("Series/Show/T2/Show-EP01.mp4")
        self.put(EP.format(10))

    def test_first_scan_adds_everything_and_groups_series(self):
        self.library()
        _result, stats = self.scan()
        self.assertEqual(stats.added, 5)
        self.assertEqual(len(self.db.list_titles()), 2)             # Tenet + Show
        self.assertEqual(self.title("Show")["n_files"], 4)

    def test_media_validation_signature_is_persisted_and_reused(self):
        path = self.put(MOVIE)
        signature = __import__("scanner").file_signature(path)
        from scanner import Found
        from classifier import classify
        found = Found(path, classify(path, self.root, None), "fingerprint", signature, signature)
        self.db.save_scan([found], [self.root])
        self.assertEqual(self.db.known_media_signatures()[str(path)], signature)

    def test_rescan_without_changes_does_nothing(self):
        self.library()
        self.scan()
        _r, stats = self.scan()
        self.assertEqual((stats.added, stats.moved, stats.removed), (0, 0, 0))
        self.assertEqual(len(self.db.list_titles()), 2)

    def test_titles_expose_a_sample_path_for_the_format_badge(self):
        self.library()
        self.scan()
        self.assertTrue(self.title("Tenet")["sample_path"].endswith("Tenet (2020).mp4"))
        self.assertTrue(self.title("Show")["sample_path"].endswith(".mp4"))

    def test_episodes_come_in_numeric_order(self):
        self.library()
        self.scan()
        order = [(e.season, e.episode) for e in self.db.episodes_for_title(self.title("Show")["id"])]
        self.assertEqual(order, [(1, 1), (1, 2), (1, 10), (2, 1)])   # EP10 depois do EP2

    def test_renamed_file_keeps_name_cover_and_progress(self):
        path = self.put(MOVIE)
        self.scan()
        tid = self.title("Tenet")["id"]
        self.db.set_display_name(tid, "Meu nome")
        self.db.set_cover(tid, "/algum/lugar/capa.png")
        self.db.save_progress(str(path), 600, 7200)

        new = path.with_name("nome totalmente novo.mp4")
        path.rename(new)
        _r, stats = self.scan()

        self.assertEqual(stats.moved, 1)
        self.assertEqual((stats.added, stats.removed), (0, 0))
        self.assertEqual(len(self.db.list_titles()), 1)
        row = self.db.get_title(tid)
        self.assertEqual((row["display_name"], row["cover_path"]), ("Meu nome", "/algum/lugar/capa.png"))
        self.assertEqual(self.db.files_for_title(tid), [str(new)])
        self.assertEqual(self.db.resume_plan(tid).start, 600)

    def test_moved_to_another_folder_keeps_everything(self):
        path = self.put(MOVIE)
        self.scan()
        tid = self.title("Tenet")["id"]
        self.db.set_display_name(tid, "Meu nome")
        moved = self.root / "Filmes/Outros/Tenet.mp4"
        moved.parent.mkdir(parents=True)
        path.rename(moved)
        _r, stats = self.scan()
        self.assertEqual(stats.moved, 1)
        self.assertEqual(self.db.get_title(tid)["display_name"], "Meu nome")

    def test_ambiguous_duplicate_content_does_not_transfer_progress(self):
        first = self.put("Filmes/Alpha (2020).mp4")
        second = self.put("Filmes/Beta (2021).mp4")
        second.write_bytes(first.read_bytes())
        self.scan()

        alpha = self.title("Alpha")["id"]
        self.db.save_progress(str(first), 900, 3600)

        content = first.read_bytes()
        first.unlink()
        second.unlink()
        replacement = self.root / "Filmes/Gamma (2022).mp4"
        replacement.write_bytes(content)

        _result, stats = self.scan()

        self.assertEqual((stats.added, stats.moved, stats.removed), (1, 0, 2))
        self.assertEqual([t["name"] for t in self.db.list_titles()], ["Gamma"])
        gamma = self.title("Gamma")["id"]
        self.assertNotEqual(gamma, alpha)
        self.assertEqual(self.db.resume_plan(gamma).start, 0.0)

    def test_duplicate_groups_detect_same_content_across_different_movie_titles(self):
        first = self.put("Filmes/Alpha (2020).mp4")
        second = self.put("Filmes/Beta (2021).mp4")
        second.write_bytes(first.read_bytes())
        self.scan()

        groups = self.db.duplicate_groups()

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["files"]), 2)
        self.assertEqual(
            {row["name"] for row in groups[0]["files"]},
            {"Alpha", "Beta"},
        )
        # Diagnóstico não funde títulos nem remove caminhos.
        self.assertEqual(len(self.db.list_titles()), 2)
        self.assertEqual(len(self.db.files_for_title(self.title("Alpha")["id"])), 1)
        self.assertEqual(len(self.db.files_for_title(self.title("Beta")["id"])), 1)

    def test_duplicate_episode_content_is_detected_inside_same_series(self):
        first = self.put(EP.format(1))
        second = self.put(EP.format(2))
        second.write_bytes(first.read_bytes())
        self.scan()

        groups = self.db.duplicate_groups()

        self.assertEqual(len(groups), 1)
        self.assertEqual(
            {(row["season"], row["episode"]) for row in groups[0]["files"]},
            {(1, 1), (1, 2)},
        )

    def test_unique_content_is_not_reported_as_duplicate(self):
        self.put(MOVIE)
        self.scan()
        self.assertEqual(self.db.duplicate_groups(), [])

    def test_deleted_file_removes_title_and_reports_orphan_cover(self):
        path = self.put(MOVIE)
        self.put(EP.format(1))
        self.scan()
        self.db.set_cover(self.title("Tenet")["id"], "/capas/1.png")
        path.unlink()
        _r, stats = self.scan()
        self.assertEqual(stats.removed, 1)
        self.assertEqual(stats.orphan_covers, ["/capas/1.png"])
        self.assertEqual([t["name"] for t in self.db.list_titles()], ["Show"])

    def test_unmounted_folder_deletes_nothing(self):
        """HD externo desligado: a pasta some, mas a biblioteca NÃO pode ser apagada."""
        self.library()
        self.scan()
        away = self.tmp / "videos-escondida"
        self.root.rename(away)                               # simula o HD desmontado
        result, stats = self.scan()
        self.assertEqual(result.missing, [self.root])
        self.assertEqual(result.prune_roots, [])
        self.assertEqual(stats.removed, 0)
        self.assertEqual(len(self.db.list_titles()), 2)
        away.rename(self.root)                               # HD volta
        _r, stats = self.scan()
        self.assertEqual((stats.added, stats.moved, stats.removed), (0, 0, 0))

    def test_empty_but_existing_folder_deletes_nothing(self):
        """Pasta que existe mas veio vazia (HD que não terminou de montar) também é protegida."""
        self.library()
        self.scan()
        for video in list(find_videos(self.root)):
            video.unlink()
        result, stats = self.scan()
        self.assertEqual(result.empty, [self.root])
        self.assertEqual(stats.removed, 0)
        self.assertEqual(len(self.db.list_titles()), 2)

    def test_replaced_file_loses_old_progress(self):
        path = self.put(MOVIE)
        self.scan()
        tid = self.title("Tenet")["id"]
        self.db.save_progress(str(path), 600, 7200)
        self.put(MOVIE, size_extra=500)
        self.scan()
        self.assertEqual(self.db.resume_plan(tid).start, 0.0)

    def test_same_size_replacement_invalidates_cached_fingerprint(self):
        path = self.put(MOVIE)
        self.scan()
        tid = self.title("Tenet")["id"]
        self.db.save_progress(str(path), 600, 7200)

        original = path.read_bytes()
        replacement = bytes((byte ^ 0x01) for byte in original)
        self.assertEqual(len(original), len(replacement))
        old_mtime = path.stat().st_mtime_ns
        path.write_bytes(replacement)
        os.utime(path, ns=(old_mtime + 2_000_000_000, old_mtime + 2_000_000_000))

        self.scan()
        self.assertEqual(self.db.resume_plan(tid).start, 0.0)

    def test_remove_files_under_does_not_touch_other_folders(self):
        self.put(MOVIE)
        other = self.tmp / "outra"
        (other / "Filmes").mkdir(parents=True)
        (other / "Filmes/Dune (2021).mp4").write_bytes(b"dune" * 100)
        result = scan_folders([(self.root, None), (other, None)], {})
        self.db.save_scan(result.found, result.prune_roots)
        self.db.remove_files_under(other)
        self.assertEqual([t["name"] for t in self.db.list_titles()], ["Tenet"])
        self.assertTrue((other / "Filmes/Dune (2021).mp4").exists())     # o vídeo NÃO é apagado


class ResumeTests(Base):
    def setUp(self):
        super().setUp()
        self.paths = [str(self.put(EP.format(n))) for n in (1, 2, 3)]
        self.scan()
        self.tid = self.title("Show")["id"]

    def test_no_progress_starts_at_first_episode(self):
        plan = self.db.resume_plan(self.tid)
        self.assertEqual((plan.paths, plan.start, plan.episode, plan.completed), (self.paths, 0.0, 1, False))

    def test_stopped_in_the_middle_resumes_there(self):
        self.db.save_progress(self.paths[1], 30, 100)
        plan = self.db.resume_plan(self.tid)
        self.assertEqual((plan.paths, plan.start, plan.duration, plan.episode), (self.paths[1:], 30, 100, 2))

    def test_stopped_in_the_first_seconds_restarts_from_zero(self):
        self.db.save_progress(self.paths[1], 3, 100)
        plan = self.db.resume_plan(self.tid)
        self.assertEqual((plan.paths, plan.start), (self.paths[1:], 0.0))

    def test_finished_episode_goes_to_the_next(self):
        self.db.save_progress(self.paths[0], 96, 100)        # passou de 95%
        plan = self.db.resume_plan(self.tid)
        self.assertEqual((plan.paths, plan.start, plan.episode), (self.paths[1:], 0.0, 2))

    def test_exactly_95_percent_counts_as_finished(self):
        self.db.save_progress(self.paths[0], 95, 100)
        self.assertEqual(self.db.resume_plan(self.tid).episode, 2)

    def test_finished_the_last_one_offers_rewatch_from_the_start(self):
        self.db.save_progress(self.paths[2], 99, 100)
        plan = self.db.resume_plan(self.tid)
        self.assertTrue(plan.completed)
        self.assertEqual(plan.paths, self.paths)

    def test_tie_in_time_prefers_the_later_episode(self):
        self.db.save_progress(self.paths[0], 96, 100)
        self.db.save_progress(self.paths[1], 30, 100)
        self.assertEqual(self.db.resume_plan(self.tid).episode, 2)

    def test_progress_of_unknown_file_is_ignored(self):
        self.db.save_progress("/nao/registrado.mp4", 30, 100)       # não pode quebrar nem criar nada
        self.assertEqual(self.db.resume_plan(self.tid).start, 0.0)

    def test_episode_rules(self):
        self.assertTrue(Episode("p", 1, 1, 96, 100).finished)
        self.assertFalse(Episode("p", 1, 1, 50, 100).finished)
        self.assertFalse(Episode("p", 1, 1, 50, None).finished)     # sem duração: não dá para saber
        self.assertEqual(Episode("p", 1, 1, 50, 100).resume_at, 50)
        self.assertEqual(Episode("p", 1, 1, 2, 100).resume_at, 0.0)
        self.assertEqual(Episode("p", 1, 1, 96, 100).resume_at, 0.0)
        self.assertEqual(Episode("p", 1, 1, None, None).resume_at, 0.0)

    def test_subtitle_delay_is_saved_independently_per_episode(self):
        self.db.save_subtitle_delay(self.paths[0], -0.4)
        self.db.save_subtitle_delay(self.paths[1], 1.2)
        self.assertEqual(self.db.subtitle_delay_for(self.paths[0]), -0.4)
        self.assertEqual(self.db.subtitle_delay_for(self.paths[1]), 1.2)
        self.assertEqual(self.db.subtitle_delay_for(self.paths[2]), 0.0)

    def test_subtitle_delay_is_clamped_to_ten_seconds(self):
        self.db.save_subtitle_delay(self.paths[0], 99)
        self.assertEqual(self.db.subtitle_delay_for(self.paths[0]), 10.0)

    def test_credits_start_is_saved_per_episode(self):
        self.db.save_credits_start(self.paths[1], 1325.5)
        episodes = self.db.episodes_for_title(self.tid)
        self.assertIsNone(episodes[0].credits_start)
        self.assertEqual(episodes[1].credits_start, 1325.5)
        self.assertIsNone(episodes[2].credits_start)

    def test_resume_from_a_chosen_episode_plays_it_and_the_following(self):
        self.db.save_progress(self.paths[1], 30, 100)
        episodes = self.db.episodes_for_title(self.tid)
        plan = resume_from(episodes, 1)
        self.assertEqual((plan.paths, plan.start, plan.episode), (self.paths[1:], 30, 2))
        plan0 = resume_from(episodes, 0)
        self.assertEqual((plan0.paths, plan0.start), (self.paths, 0.0))


class ContinueWatchingTests(Base):
    """A lista do Início: o que foi começado e não terminado, filmes e séries misturados."""

    def setUp(self):
        super().setUp()
        self.movie = str(self.put(MOVIE))
        self.eps = [str(self.put(EP.format(n))) for n in (1, 2, 3)]
        self.scan()

    def names(self):
        return [item.title["name"] for item in self.db.continue_watching()]

    def stamp(self, path, when):
        """Troca a hora do progresso de um arquivo (o banco só guarda segundos)."""
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE progress SET updated_at = ? WHERE file_id = (SELECT id FROM files WHERE path = ?)",
                (when, path))

    def test_empty_when_nothing_was_watched(self):
        self.assertEqual(self.db.continue_watching(), [])

    def test_movie_in_the_middle_is_listed_with_its_fraction(self):
        self.db.save_progress(self.movie, 1800, 7200)
        [item] = self.db.continue_watching()
        self.assertEqual(item.title["name"], "Tenet")
        self.assertEqual((item.season, item.episode), (None, None))
        self.assertAlmostEqual(item.fraction, 0.25)

    def test_opened_for_a_moment_is_not_listed(self):
        self.db.save_progress(self.movie, 3, 7200)               # menos de 5 s
        self.db.save_progress(self.eps[0], 4, 1500)
        self.assertEqual(self.names(), [])

    def test_finished_movie_is_not_listed(self):
        self.db.save_progress(self.movie, 7000, 7200)            # passou de 95%
        self.assertEqual(self.names(), [])

    def test_series_in_the_middle_points_to_that_episode(self):
        self.db.save_progress(self.eps[1], 30, 100)
        [item] = self.db.continue_watching()
        self.assertEqual((item.title["name"], item.season, item.episode), ("Show", 1, 2))
        self.assertAlmostEqual(item.fraction, 0.3)

    def test_finished_episode_points_to_the_next_one_without_a_bar(self):
        self.db.save_progress(self.eps[0], 98, 100)
        [item] = self.db.continue_watching()
        self.assertEqual((item.season, item.episode, item.fraction), (1, 2, 0.0))

    def test_finished_the_whole_series_is_not_listed(self):
        self.db.save_progress(self.eps[2], 99, 100)
        self.assertEqual(self.names(), [])

    def test_movies_and_series_come_mixed_most_recent_first(self):
        self.db.save_progress(self.movie, 1800, 7200)
        self.db.save_progress(self.eps[1], 30, 100)
        self.stamp(self.movie, "2026-01-01 10:00:00")
        self.stamp(self.eps[1], "2026-01-02 10:00:00")
        self.assertEqual(self.names(), ["Show", "Tenet"])
        self.stamp(self.movie, "2026-01-03 10:00:00")            # agora o filme é o mais recente
        self.assertEqual(self.names(), ["Tenet", "Show"])

    def test_a_series_appears_only_once_however_many_episodes_were_watched(self):
        self.db.save_progress(self.eps[0], 98, 100)
        self.db.save_progress(self.eps[1], 30, 100)
        self.assertEqual(self.names(), ["Show"])

    def test_title_whose_files_vanished_is_not_listed(self):
        self.db.save_progress(self.movie, 1800, 7200)
        Path(self.movie).unlink()
        self.scan()
        self.assertEqual(self.names(), [])

    def test_continue_watching_uses_a_bounded_number_of_queries(self):
        self.db.save_progress(self.movie, 1800, 7200)
        self.db.save_progress(self.eps[1], 30, 100)
        statements = []
        self.db.conn.set_trace_callback(statements.append)

        items = self.db.continue_watching()

        self.db.conn.set_trace_callback(None)
        self.assertEqual(len(items), 2)
        self.assertLessEqual(len(statements), 2)

    def test_equal_activity_timestamps_keep_title_id_descending_order(self):
        self.db.save_progress(self.movie, 1800, 7200)
        self.db.save_progress(self.eps[1], 30, 100)
        self.stamp(self.movie, "2026-01-02 10:00:00")
        self.stamp(self.eps[1], "2026-01-02 10:00:00")

        items = self.db.continue_watching()

        self.assertEqual(
            [item.title["id"] for item in items],
            sorted((item.title["id"] for item in items), reverse=True),
        )


class FavoriteTests(Base):
    def setUp(self):
        super().setUp()
        self.movie = self.put(MOVIE)
        self.put("Filmes/Dune (2021).mp4")
        self.put(EP.format(1))
        self.scan()

    def favs(self, **kwargs):
        return [t["name"] for t in self.db.list_titles(favorites_only=True, **kwargs)]

    def id_of(self, name):
        return self.title(name)["id"]

    def test_nothing_is_a_favorite_at_first(self):
        self.assertEqual(self.favs(), [])
        self.assertTrue(all(t["favorite"] == 0 for t in self.db.list_titles()))

    def test_set_and_unset(self):
        self.assertTrue(self.db.set_favorite(self.id_of("Tenet"), True))
        self.assertEqual(self.favs(), ["Tenet"])
        self.assertEqual(self.title("Tenet")["favorite"], 1)
        self.db.set_favorite(self.id_of("Tenet"), False)
        self.assertEqual(self.favs(), [])

    def test_filters_combine_and_come_in_alphabetical_order(self):
        for name in ("Tenet", "Show", "Dune"):
            self.db.set_favorite(self.id_of(name), True)
        self.assertEqual(self.favs(), ["Dune", "Show", "Tenet"])
        self.assertEqual(self.favs(kind="movie"), ["Dune", "Tenet"])
        self.assertEqual(self.favs(kind="series"), ["Show"])

    def test_unknown_title_changes_nothing(self):
        self.assertFalse(self.db.set_favorite(9999, True))
        self.assertEqual(self.favs(), [])

    def test_survives_rescan_rename_and_name_edit(self):
        tid = self.id_of("Tenet")
        self.db.set_favorite(tid, True)
        self.scan()                                                  # nova varredura sem mudanças
        self.assertEqual(self.favs(), ["Tenet"])
        self.db.set_display_name(tid, "Outro nome")
        renamed = self.movie.with_name("renomeado.mp4")
        self.movie.rename(renamed)
        _r, stats = self.scan()
        self.assertEqual(stats.moved, 1)
        self.assertEqual([t["shown"] for t in self.db.list_titles(favorites_only=True)], ["Outro nome"])

    def test_deleting_the_files_removes_the_favorite_with_the_title(self):
        self.db.set_favorite(self.id_of("Tenet"), True)
        self.movie.unlink()
        self.scan()
        self.assertEqual(self.favs(), [])


class MigrationTests(unittest.TestCase):
    def test_old_database_without_fingerprint_column_is_upgraded_without_losing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "antigo.db"
            old = sqlite3.connect(path)
            old.executescript("""
                CREATE TABLE titles (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                    name TEXT NOT NULL, display_name TEXT, year INTEGER, cover_path TEXT);
                CREATE TABLE files (id INTEGER PRIMARY KEY, title_id INTEGER NOT NULL, path TEXT UNIQUE NOT NULL,
                    season INTEGER, episode INTEGER);
                CREATE TABLE progress (file_id INTEGER PRIMARY KEY, position REAL NOT NULL,
                    duration REAL NOT NULL, updated_at TEXT NOT NULL);
                INSERT INTO titles (key, kind, name, display_name, year, cover_path)
                    VALUES ('movie:x:', 'movie', 'X', 'Meu X', 2020, '/capas/x.png');
                INSERT INTO files (title_id, path, season, episode) VALUES (1, '/v/x.mp4', 1, 2);
                INSERT INTO progress (file_id, position, duration, updated_at)
                    VALUES (1, 125.5, 3600, '2026-01-01 12:00:00');
            """)
            old.commit()
            old.close()
            db = Database(path)
            self.addCleanup(db.conn.close)
            columns = {r["name"] for r in db.conn.execute("PRAGMA table_info(files)")}
            self.assertTrue({
                "fingerprint", "fingerprint_size", "fingerprint_mtime_ns",
                "fingerprint_inode", "fingerprint_device", "media_validated_size",
                "media_validated_mtime_ns", "media_validated_inode",
                "media_validated_device", "credits_start", "subtitle_delay",
            }.issubset(columns))
            title = db.get_title(1)
            self.assertEqual((title["display_name"], title["year"], title["cover_path"]),
                             ("Meu X", 2020, "/capas/x.png"))
            self.assertEqual(db.files_for_title(1), ["/v/x.mp4"])
            self.assertEqual(db.get_title(1)["favorite"], 0)         # coluna nova, valor padrão
            progress = db.conn.execute(
                "SELECT position, duration, updated_at FROM progress WHERE file_id = 1"
            ).fetchone()
            self.assertEqual(tuple(progress), (125.5, 3600.0, "2026-01-01 12:00:00"))
            self.assertEqual(db.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertTrue(db.set_favorite(1, True))
            self.assertEqual([t["name"] for t in db.list_titles(favorites_only=True)], ["X"])


if __name__ == "__main__":
    unittest.main()