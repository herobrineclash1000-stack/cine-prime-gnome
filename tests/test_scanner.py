"""Testes do scanner: varredura, classificação forçada e pastas sobrepostas.

Tudo roda em pastas TEMPORÁRIAS: nunca varre a sua biblioteca real.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scanner  # noqa: E402
from scanner import scan_folders  # noqa: E402

EP = "Series/Show/T1/Show-EP{:02d}.mp4"


class ScannerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "videos"

    def put(self, rel: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode() * 100)
        return path

    def scan(self, folders, known=None):
        return scan_folders(folders, {} if known is None else known)

    def test_forced_kind_overrides_the_folder_layout(self):
        """Coloquei a pasta de série, mas o usuário disse 'movie': tudo vira filme."""
        self.put(EP.format(1))
        result = self.scan([(self.root, "movie")])
        self.assertEqual(len(result.found), 1)
        self.assertEqual(result.found[0].item.kind, "movie")

    def test_overlapping_folders_first_wins(self):
        """Pastas de biblioteca sobrepostas: a primeira vence, não duplica."""
        self.put(EP.format(1))
        result = self.scan([(self.root, None), (self.root, None)])
        self.assertEqual(len(result.found), 1)

    def test_missing_and_empty_folders_are_reported(self):
        self.put("Filmes/Tenet (2020).mp4")
        (self.tmp / "vazia").mkdir()                    # pasta que EXISTE e veio sem vídeo
        result = self.scan([(self.tmp / "nao-existe", None), (self.tmp / "vazia", None)])
        self.assertEqual(result.missing, [self.tmp / "nao-existe"])
        self.assertEqual(result.empty, [self.tmp / "vazia"])
        self.assertEqual(len(result.found), 0)        # o vídeo criado está em outra pasta, não escaneia aqui

    def test_empty_video_is_ignored_and_blocks_pruning(self):
        empty = self.root / "empty.mp4"
        empty.parent.mkdir(parents=True)
        empty.write_bytes(b"")

        result = self.scan([(self.root, "movie")])

        self.assertEqual(result.found, [])
        self.assertEqual(result.prune_roots, [])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("vazio", result.errors[0][1])

    def test_unreadable_video_is_ignored_and_blocks_pruning(self):
        readable = self.root / "readable.mp4"
        unreadable = self.root / "unreadable.mp4"
        readable.parent.mkdir(parents=True)
        readable.write_bytes(b"readable bytes")
        unreadable.write_bytes(b"private bytes")
        original_open = Path.open

        def deny_one_file(path, *args, **kwargs):
            if path == unreadable:
                raise PermissionError(13, "Permission denied", str(path))
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", deny_one_file):
            result = self.scan([(self.root, "movie")])

        self.assertEqual([entry.path for entry in result.found], [readable])
        self.assertEqual(result.prune_roots, [])
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0][0], unreadable)

    def test_invalid_media_is_ignored_and_blocks_pruning(self):
        path = self.put("broken.mp4")
        with patch.object(scanner, "discover_video", return_value=(False, "stream inválido")):
            result = scan_folders([(self.root, "movie")], {}, validate_media=True)
        self.assertEqual(result.found, [])
        self.assertEqual(result.prune_roots, [])
        self.assertEqual(result.errors[0][0], path)
        self.assertIn("inválido", result.errors[0][1])

    def test_valid_media_signature_is_attached_to_found_item(self):
        path = self.put("valid.mp4")
        with patch.object(scanner, "discover_video", return_value=(True, None)) as discover:
            result = scan_folders([(self.root, "movie")], {}, validate_media=True)
        discover.assert_called_once_with(path)
        self.assertEqual(result.found[0].media_signature, scanner.file_signature(path))
        self.assertEqual(result.prune_roots, [self.root])

    def test_unchanged_media_signature_skips_discoverer(self):
        path = self.put("cached.mp4")
        signature = scanner.file_signature(path)
        with patch.object(scanner, "discover_video") as discover:
            result = scan_folders(
                [(self.root, "movie")], {}, {str(path): signature}, validate_media=True
            )
        discover.assert_not_called()
        self.assertEqual(result.found[0].media_signature, signature)

    def test_inaccessible_subfolder_makes_scan_incomplete_and_blocks_pruning(self):
        visible = self.root / "visible.mp4"
        visible.parent.mkdir(parents=True)
        visible.write_bytes(b"video")

        def partial_walk(root, topdown=True, onerror=None, followlinks=False):
            if onerror:
                onerror(PermissionError(13, "Permission denied", str(root / "private")))
            yield str(root), [], [visible.name]

        with patch.object(scanner.os, "walk", side_effect=partial_walk):
            result = scan_folders([(self.root, None)], {})

        self.assertEqual([entry.path for entry in result.found], [visible])
        self.assertEqual(result.prune_roots, [])
        self.assertEqual(result.empty, [])
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0][0], self.root / "private")


if __name__ == "__main__":
    unittest.main()