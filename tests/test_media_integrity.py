import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_integrity import check_media_integrity


class MediaIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sample.mp4"
        self.path.write_bytes(b"video-test-data")

    @patch("media_integrity.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("media_integrity.subprocess.run")
    def test_successful_full_decode(self, run, _which):
        run.return_value = Mock(returncode=0, stderr="")
        result = check_media_integrity(self.path)
        self.assertTrue(result.ok)
        command = run.call_args.args[0]
        self.assertIn("-xerror", command)
        self.assertIn("-err_detect", command)
        self.assertEqual(command[-2:], ["null", "-"])

    @patch("media_integrity.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("media_integrity.subprocess.run")
    def test_decode_error_is_reported(self, run, _which):
        run.return_value = Mock(returncode=1, stderr="invalid packet\ncorrupt frame")
        result = check_media_integrity(self.path)
        self.assertFalse(result.ok)
        self.assertIn("corrupt frame", result.detail)

    @patch("media_integrity.shutil.which", return_value=None)
    def test_missing_ffmpeg_is_reported(self, _which):
        self.assertIn("não está instalado", check_media_integrity(self.path).detail)

    @patch("media_integrity.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("media_integrity.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("ffmpeg", 10))
    def test_timeout_is_reported(self, _run, _which):
        result = check_media_integrity(self.path, timeout=10)
        self.assertFalse(result.ok)
        self.assertIn("Tempo limite", result.detail)

    @patch("media_integrity.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_empty_file_is_rejected_without_running_ffmpeg(self, _which):
        self.path.write_bytes(b"")
        with patch("media_integrity.subprocess.run") as run:
            result = check_media_integrity(self.path)
        run.assert_not_called()
        self.assertIn("vazio", result.detail)


if __name__ == "__main__":
    unittest.main()