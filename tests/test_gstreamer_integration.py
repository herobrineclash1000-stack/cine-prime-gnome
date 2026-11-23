"""Integração real com GStreamer; executar em sessão GTK (X11/Wayland)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Gst, GLib

from gstreamer_player import EmbeddedGStreamerSession


@unittest.skipUnless(
    os.environ.get("CINEPRIME_GST_INTEGRATION") == "1",
    "Definir CINEPRIME_GST_INTEGRATION=1 numa sessão gráfica para integração GStreamer.",
)
class FatalBusErrorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg não está instalado")
        Gtk.init()
        cls.temp = tempfile.TemporaryDirectory(prefix="cineprime-gst-")
        cls.media = Path(cls.temp.name) / "valid-test.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "5", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cls.media),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def test_real_fatal_bus_error_notifies_once_and_releases_pipeline(self):
        loop = GLib.MainLoop()
        errors = []
        normal_exits = []
        session = EmbeddedGStreamerSession(
            [str(self.media)],
            on_error=lambda message: (errors.append(message), loop.quit()),
            on_exit=lambda: normal_exits.append(True),
        )
        missing_uri = (Path(self.temp.name) / "missing-file.mp4").as_uri()

        def inject_real_error():
            session._pipeline.set_state(Gst.State.NULL)
            session._pipeline.set_property("uri", missing_uri)
            session._pipeline.set_state(Gst.State.PLAYING)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(900, inject_real_error)
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()

        self.assertEqual(len(errors), 1, "O erro fatal deve chegar ao callback exactamente uma vez.")
        self.assertTrue(session._closing)
        self.assertFalse(session.running)
        self.assertEqual(normal_exits, [], "Um erro fatal não deve ser tratado como fim normal.")
        self.assertTrue(errors[0].strip())
        self.assertIn("não encontrado", errors[0].lower())