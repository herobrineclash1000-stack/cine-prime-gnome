"""Testes focados no estado do backend GStreamer, sem abrir um vídeo real."""
import sys
import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Gst, GLib

from gstreamer_player import EmbeddedGStreamerSession


class FakePipeline:
    def __init__(self, events=None):
        self.states = []
        self.events = events if events is not None else []
        self.properties = {}
        self.sent_events = []

    def send_event(self, event):
        self.sent_events.append(event)
        return True

    def set_state(self, state):
        self.states.append(state)
        self.events.append(("state", state))
        return Gst.StateChangeReturn.SUCCESS

    def set_property(self, name, value):
        self.properties[name] = value
        if name == "uri":
            self.events.append(("uri", value))

    def query_position(self, _fmt):
        return True, 42 * Gst.SECOND

    def query_duration(self, _fmt):
        return True, 100 * Gst.SECOND


class BufferingMessage:
    type = Gst.MessageType.BUFFERING

    def __init__(self, percent):
        self.percent = percent

    def parse_buffering(self):
        return self.percent


class BufferingTests(unittest.TestCase):
    def make_session(self):
        session = EmbeddedGStreamerSession.__new__(EmbeddedGStreamerSession)
        Gtk.Box.__init__(session, orientation=Gtk.Orientation.VERTICAL)
        session._closing = False
        session._running = True
        session._user_paused = False
        session._buffering = False
        session._pipeline = FakePipeline()
        session._on_state = None
        return session

    def test_pipeline_resumes_when_buffering_reaches_100_percent(self):
        session = self.make_session()

        session._on_bus_message(None, BufferingMessage(35))
        self.assertEqual(session._pipeline.states, [Gst.State.PAUSED])

        session._on_bus_message(None, BufferingMessage(100))

        self.assertEqual(
            session._pipeline.states,
            [Gst.State.PAUSED, Gst.State.PLAYING],
            "O pipeline deve retomar quando o buffering termina, se o utilizador não pediu pausa.",
        )

    def test_pipeline_stays_paused_when_user_requested_pause_during_buffering(self):
        session = self.make_session()
        session._on_bus_message(None, BufferingMessage(35))
        session.toggle_pause()

        session._on_bus_message(None, BufferingMessage(100))

        self.assertEqual(session._pipeline.states, [Gst.State.PAUSED, Gst.State.PAUSED])
        self.assertTrue(session.paused)


class EpisodeTransitionTests(unittest.TestCase):
    def make_session(self):
        temp = tempfile.TemporaryDirectory()
        first = Path(temp.name) / "episode-1.mp4"
        second = Path(temp.name) / "episode-2.mp4"
        first.touch()
        second.touch()
        events = []
        saved = []
        session = EmbeddedGStreamerSession.__new__(EmbeddedGStreamerSession)
        Gtk.Box.__init__(session, orientation=Gtk.Orientation.VERTICAL)
        session.paths = [str(first), str(second)]
        session.start_at = 0.0
        session.start_index = 0
        session._index = 0
        session._position = 42.0
        session._duration = 100.0
        session._closing = False
        session._tick_source = 0
        session._running = True
        session._user_paused = False
        session._buffering = False
        session._subtitle_uri = None
        session._external_subtitle_enabled = False
        session._external_subtitle_stream_id = None
        session._subtitle_offset = 0
        session._pending_seek = 0.0
        session._streams = {}
        session._selected_stream_ids = []
        session._tracks = []
        session._dirty = {}
        session._on_save = lambda entries: (saved.append(dict(entries)), events.append(("save", dict(entries))))
        session._on_state = None
        session._pipeline = FakePipeline(events)
        session._query_timeline = lambda: (42.0, 100.0)
        session._emit_state = lambda: None
        session._fail = lambda message: self.fail(message)
        return temp, session, events, saved

    def assert_saved_before_next_uri(self, trigger):
        temp, session, events, saved = self.make_session()
        try:
            trigger(session)
            save_index = next(i for i, event in enumerate(events) if event[0] == "save")
            next_uri_index = next(i for i, event in enumerate(events) if event[0] == "uri")
            self.assertLess(save_index, next_uri_index)
            self.assertEqual(saved[0][str(Path(session.paths[0]))], (42.0, 100.0))
            self.assertEqual(session._index, 1)
        finally:
            temp.cleanup()

    def test_eos_persists_current_episode_before_loading_next(self):
        class EndMessage:
            type = Gst.MessageType.EOS
        self.assert_saved_before_next_uri(lambda session: session._on_bus_message(None, EndMessage()))

    def test_manual_next_persists_current_episode_before_loading_next(self):
        self.assert_saved_before_next_uri(lambda session: session.next_track())

    def test_manual_episode_selection_persists_current_before_loading_selected(self):
        self.assert_saved_before_next_uri(lambda session: session.play_index(1))


    def test_last_episode_eos_saves_once_and_notifies_exit_once(self):
        temp, session, events, saved = self.make_session()
        try:
            session.paths = [session.paths[0]]
            exits = []
            session._on_exit = lambda: exits.append("exit")
            session._bus = None
            session._bus_watch = False
            with patch("gstreamer_player.GLib.idle_add", side_effect=lambda callback, *args: callback(*args)):
                session._on_bus_message(None, type("EndMessage", (), {"type": Gst.MessageType.EOS})())
                session._on_bus_message(None, type("EndMessage", (), {"type": Gst.MessageType.EOS})())
            self.assertEqual(len(saved), 1)
            self.assertEqual(exits, ["exit"])
            self.assertTrue(session._closing)
        finally:
            temp.cleanup()



class LifecycleTests(unittest.TestCase):
    def make_session(self):
        session = EmbeddedGStreamerSession.__new__(EmbeddedGStreamerSession)
        Gtk.Box.__init__(session, orientation=Gtk.Orientation.VERTICAL)
        session.paths = ["/tmp/lifecycle-test.mp4"]
        session._index = 0
        session._position = 12.0
        session._duration = 60.0
        session._closing = False
        session._running = True
        session._dirty = {}
        session._ticks = 0
        session._tick_source = 77
        session._pipeline = FakePipeline()
        session._bus = None
        session._bus_watch = False
        session._bus_handler_id = 0
        session._on_save = unittest.mock.Mock()
        session._on_exit = unittest.mock.Mock()
        session._query_timeline = lambda: (12.0, 60.0)
        return session

    def test_stop_then_close_persists_once_and_removes_tick_source(self):
        session = self.make_session()
        with patch("gstreamer_player.GLib.source_remove") as remove_source, \
             patch("gstreamer_player.GLib.idle_add", side_effect=lambda callback, *args: callback(*args)):
            session.stop()
            session.close()
        session._on_save.assert_called_once_with({"/tmp/lifecycle-test.mp4": (12.0, 60.0)})
        session._on_exit.assert_called_once_with()
        remove_source.assert_called_once_with(77)
        self.assertEqual(session._tick_source, 0)
        self.assertEqual(session._pipeline.states, [Gst.State.NULL])
        self.assertFalse(session.running)

    def test_tick_stops_after_finish(self):
        session = self.make_session()
        session._closing = True
        self.assertEqual(session._tick(), 0)
        self.assertEqual(session._tick_source, 0)

    def test_pipeline_is_released_when_progress_save_raises(self):
        session = self.make_session()
        session._on_save.side_effect = RuntimeError("database unavailable")

        class FakeBus:
            def __init__(self):
                self.removed = 0
                self.disconnected = []

            def disconnect(self, handler_id):
                self.disconnected.append(handler_id)

            def remove_signal_watch(self):
                self.removed += 1

        bus = FakeBus()
        session._bus = bus
        session._bus_watch = True
        session._bus_handler_id = 123

        with patch("gstreamer_player.GLib.source_remove") as remove_source, \
             patch("gstreamer_player.GLib.idle_add", side_effect=lambda callback, *args: callback(*args)):
            session.stop()

        self.assertEqual(session._pipeline.states, [Gst.State.NULL])
        self.assertEqual(bus.disconnected, [123])
        self.assertEqual(session._bus_handler_id, 0)
        self.assertEqual(bus.removed, 1)
        self.assertFalse(session._bus_watch)
        remove_source.assert_called_once_with(77)
        session._on_exit.assert_called_once_with()
        self.assertFalse(session.running)

    def test_partial_constructor_failure_releases_created_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "sample.mp4"
            media.touch()
            pipeline = FakePipeline()
            with patch("gstreamer_player.Gst.ElementFactory.make",
                       side_effect=[pipeline, None]):
                with self.assertRaisesRegex(RuntimeError, "gtk4paintablesink"):
                    EmbeddedGStreamerSession([str(media)])
            self.assertEqual(pipeline.states, [Gst.State.NULL])

    def test_bus_error_notifies_once_and_releases_pipeline(self):
        session = self.make_session()
        errors = []
        session._on_error = errors.append

        class ErrorMessage:
            type = Gst.MessageType.ERROR

            @staticmethod
            def parse_error():
                return (type("GstError", (), {"message": "decoder failed"})(), "decoder debug")

        with patch("gstreamer_player.GLib.source_remove"), \
             patch("gstreamer_player.GLib.idle_add", side_effect=lambda callback, *args: (callback(*args), 1)[1]):
            session._on_bus_message(None, ErrorMessage())
            session._on_bus_message(None, ErrorMessage())

        self.assertEqual(len(errors), 1)
        self.assertIn("decoder failed", errors[0])
        self.assertIn("decoder debug", errors[0])
        self.assertEqual(session._pipeline.states, [Gst.State.NULL])
        self.assertTrue(session._closing)
        session._on_exit.assert_not_called()


class SoftwareFallbackTests(unittest.TestCase):
    def make_session(self):
        session = EmbeddedGStreamerSession.__new__(EmbeddedGStreamerSession)
        Gtk.Box.__init__(session, orientation=Gtk.Orientation.VERTICAL)
        session.paths = ["/tmp/fallback-test.mp4"]
        session._index = 0
        session._running = True
        session._closing = False
        session._hardware_decoder_used = True
        session._software_decoder_used = False
        session._software_fallback_attempted = False
        session._software_fallback_active = False
        session._fallback_original_error = None
        session._saved_decoder_ranks = []
        session._user_paused = False
        session._buffering = False
        session._position = 35.0
        session._duration = 120.0
        session._pending_seek = 0.0
        session._subtitle_uri = "file:///tmp/test.srt"
        session._subtitle_offset = 250_000_000
        session._speed = 1.25
        session._tracks = [
            {"id": "audio-en", "type": "audio", "lang": "en", "title": "English", "selected": True},
            {"id": "sub-pt", "type": "sub", "lang": "pt", "title": "Português", "selected": True},
        ]
        session._fallback_selected_tracks = []
        session._pipeline = FakePipeline()
        session._bus = None
        session._bus_watch = False
        session._bus_handler_id = 0
        session.picture = None
        session._query_timeline = lambda: (35.0, 120.0)
        session._emit_state = unittest.mock.Mock()
        session._on_error = unittest.mock.Mock()
        return session

    def test_hardware_failure_restarts_same_file_with_software_and_preserves_position(self):
        session = self.make_session()
        old_pipeline = session._pipeline
        def rebuild():
            session._pipeline = FakePipeline()
            session._bus = None
            session._bus_watch = False
            session._bus_handler_id = 0
        with patch.object(session, "_disable_hardware_decoder_ranks", return_value=True), \
             patch.object(session, "_build_pipeline", side_effect=rebuild), \
             patch("gstreamer_player.GLib.idle_add", side_effect=lambda callback, *args: callback(*args)):
            recovered = session._recover_with_software("VA decoder device lost")

        self.assertTrue(recovered)
        self.assertEqual(old_pipeline.states, [Gst.State.NULL])
        self.assertEqual(session._pipeline.states, [Gst.State.PLAYING])
        self.assertEqual(session._pending_seek, 35.0)
        self.assertTrue(session._software_fallback_attempted)
        self.assertTrue(session._software_fallback_active)
        self.assertEqual(session._fallback_original_error, "VA decoder device lost")
        self.assertEqual(session._pipeline.properties["suburi"], "file:///tmp/test.srt")
        self.assertEqual(session._pipeline.properties["text-offset"], 250_000_000)
        self.assertEqual(session._pipeline.properties["uri"], Path(session.paths[0]).resolve().as_uri())
        self.assertEqual(session._fallback_selected_tracks, [
            {"type": "audio", "lang": "en", "title": "English", "index": 0},
            {"type": "sub", "lang": "pt", "title": "Português", "index": 0},
        ])
        self.assertEqual(session._speed, 1.25)

    def test_hardware_failure_preserves_user_pause(self):
        session = self.make_session()
        session._user_paused = True
        def rebuild():
            session._pipeline = FakePipeline()
            session._bus = None
            session._bus_watch = False
            session._bus_handler_id = 0
        with patch.object(session, "_disable_hardware_decoder_ranks", return_value=True), \
             patch.object(session, "_build_pipeline", side_effect=rebuild):
            self.assertTrue(session._recover_with_software("decoder failed"))
        self.assertEqual(session._pipeline.states, [Gst.State.PAUSED])

    def test_fallback_is_attempted_only_once(self):
        session = self.make_session()
        session._software_fallback_attempted = True
        with patch.object(session, "_disable_hardware_decoder_ranks") as disable:
            self.assertFalse(session._recover_with_software("second failure"))
        disable.assert_not_called()

    def test_fallback_is_not_attempted_without_hardware_decoder(self):
        session = self.make_session()
        session._hardware_decoder_used = False
        with patch.object(session, "_disable_hardware_decoder_ranks") as disable:
            self.assertFalse(session._recover_with_software("unrelated error"))
        disable.assert_not_called()


    def test_element_setup_marks_software_decoder_and_restores_ranks(self):
        session = self.make_session()
        session._software_fallback_active = True
        session._saved_decoder_ranks = []
        factory = unittest.mock.Mock()
        factory.get_metadata.return_value = "Codec/Decoder/Video"
        element = unittest.mock.Mock()
        element.get_factory.return_value = factory
        session._on_element_setup(None, element)
        self.assertTrue(session._software_decoder_used)
        self.assertFalse(session._software_fallback_active)


    def test_hardware_error_classifier_accepts_vaapi_source(self):
        source = type("Source", (), {"get_name": lambda self: "vah264dec0"})()
        self.assertTrue(EmbeddedGStreamerSession._is_hardware_decode_error("Internal data stream error", source))

    def test_hardware_error_classifier_rejects_unrelated_audio_error(self):
        source = type("Source", (), {"get_name": lambda self: "avdec_aac0"})()
        self.assertFalse(EmbeddedGStreamerSession._is_hardware_decode_error("Internal data stream error", source))

    def test_fallback_restores_selected_audio_and_internal_subtitle_tracks(self):
        session = self.make_session()
        session._software_fallback_active = True
        session._fallback_selected_tracks = [
            {"type": "audio", "lang": "en", "title": "English", "index": 0},
            {"type": "sub", "lang": "pt", "title": "Português", "index": 0},
        ]
        session._subtitle_uri = None
        session._external_subtitle_enabled = False
        session._on_tracks = None

        class Stream:
            def __init__(self, stream_id, stream_type, flags):
                self.stream_id = stream_id
                self.stream_type = stream_type
                self.flags = flags
            def get_stream_id(self): return self.stream_id
            def get_stream_type(self): return self.stream_type
            def get_stream_flags(self): return self.flags
            def get_tags(self): return None

        class Collection:
            def __init__(self):
                self.streams = [
                    Stream("new-audio-en", Gst.StreamType.AUDIO, Gst.StreamFlags.SELECT),
                    Stream("new-sub-pt", Gst.StreamType.TEXT, Gst.StreamFlags.NONE),
                ]
            def get_size(self): return len(self.streams)
            def get_stream(self, index): return self.streams[index]

        class Message:
            def parse_stream_collection(self): return Collection()

        with patch("gstreamer_player.Gst.Event.new_select_streams", side_effect=lambda ids: tuple(ids)):
            session._read_stream_collection(Message())

        self.assertEqual(session._pipeline.sent_events, [("new-audio-en", "new-sub-pt")])
        self.assertTrue(all(track["selected"] for track in session._tracks))
        self.assertEqual(session._fallback_selected_tracks, [])

    def test_new_episode_resets_fallback_attempt_state(self):
        session = self.make_session()
        session._software_fallback_attempted = True
        session._software_fallback_active = False
        session._fallback_original_error = "old VA error"
        with patch.object(session, "_restore_decoder_ranks") as restore:
            session._restore_fallback_state_for_new_item()
        restore.assert_called_once()
        self.assertFalse(session._software_fallback_attempted)
        self.assertFalse(session._software_fallback_active)
        self.assertFalse(session._hardware_decoder_used)
        self.assertFalse(session._software_decoder_used)
        self.assertIsNone(session._fallback_original_error)

    def test_fallback_timeout_restores_ranks_and_reports_failure(self):
        session = self.make_session()
        session._software_fallback_active = True
        session._software_decoder_used = False
        session._saved_decoder_ranks = []
        session._fallback_timeout_source = 0
        session._fail = unittest.mock.Mock()
        self.assertEqual(session._software_fallback_timeout(), GLib.SOURCE_REMOVE)
        session._fail.assert_called_once()
        self.assertIn("tempo limite", session._fail.call_args.args[0])

    def test_registry_ranks_are_restored_after_cleanup(self):
        session = self.make_session()
        feature = unittest.mock.Mock()
        feature.get_name.return_value = "vah264dec"
        feature.set_rank = unittest.mock.Mock()
        session._saved_decoder_ranks = [(feature, 257)]
        session._software_fallback_active = True
        session._restore_decoder_ranks()
        feature.set_rank.assert_called_once_with(257)
        self.assertEqual(session._saved_decoder_ranks, [])
        self.assertFalse(session._software_fallback_active)

    def test_error_message_reports_failed_software_recovery(self):
        session = self.make_session()
        session._software_fallback_attempted = True
        session._fallback_original_error = "VA device lost"
        errors = []
        session._on_error = errors.append
        session._finish = unittest.mock.Mock()

        class ErrorMessage:
            type = Gst.MessageType.ERROR
            @staticmethod
            def parse_error():
                return (type("GstError", (), {"message": "software decoder failed"})(), None)

        ErrorMessage.src = unittest.mock.Mock()
        ErrorMessage.src.get_name.return_value = "vah264dec"
        with patch.object(session, "_recover_with_software", return_value=False), \
             patch("gstreamer_player.GLib.idle_add", side_effect=lambda callback, *args: callback(*args)):
            session._on_bus_message(None, ErrorMessage())
        self.assertIn("VA device lost", errors[0])
        self.assertIn("software decoder failed", errors[0])


if __name__ == "__main__":
    unittest.main()