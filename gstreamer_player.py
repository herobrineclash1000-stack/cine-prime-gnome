"""GTK4/GStreamer playback backend for Cine Prime.

The UI owns all playback controls. GStreamer is responsible for decoding,
demuxing, audio output, stream selection and video frames only.
"""
from __future__ import annotations

from pathlib import Path
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst, Gtk


Gst.init(None)


def _tag_string(tags, tag: str) -> str | None:
    if not tags:
        return None
    try:
        result = tags.get_string(tag)
        if isinstance(result, tuple):
            return result[1] if result[0] else None
        return result or None
    except (AttributeError, TypeError):
        return None


class EmbeddedGStreamerSession(Gtk.Box):
    """Video surface and playback session backed by GStreamer playbin3."""

    def __init__(
        self,
        paths,
        start_at=0.0,
        on_save=None,
        on_exit=None,
        on_state=None,
        on_progress=None,
        on_tracks=None,
        on_error=None,
        start_index=0,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.paths = [str(Path(p).expanduser()) for p in paths]
        self.start_at = max(0.0, float(start_at or 0.0))
        self.start_index = max(0, int(start_index or 0))
        self._on_save = on_save
        self._on_exit = on_exit
        self._on_state = on_state
        self._on_progress = on_progress
        self._on_tracks = on_tracks
        self._on_error = on_error

        self._pipeline = None
        self._sink = None
        self._bus = None
        self._bus_watch = False
        self._bus_handler_id = 0
        self._tick_source = 0
        self._running = False
        self._closing = False
        self._user_paused = False
        self._buffering = False
        self._index = self.start_index
        self._position = 0.0
        self._duration = 0.0
        self._speed = 1.0
        self._pending_seek = self.start_at
        self._subtitle_uri = None
        self._external_subtitle_enabled = False
        self._external_subtitle_stream_id = None
        self._subtitle_offset = 0
        self._ticks = 0
        self._dirty = {}
        self._streams = {}
        self._selected_stream_ids = []
        self._tracks = []
        self._hardware_decoder_used = False
        self._software_decoder_used = False
        self._software_fallback_attempted = False
        self._software_fallback_active = False
        self._fallback_original_error = None
        self._fallback_selected_tracks = []
        self._saved_decoder_ranks = []
        self._fallback_timeout_source = 0

        self.set_hexpand(True)
        self.set_vexpand(True)
        self.add_css_class("gstreamer-video-surface")

        if not self.paths:
            raise ValueError("Não há ficheiros para reproduzir.")
        if self.start_index >= len(self.paths):
            raise ValueError("O episódio inicial está fora da lista de reprodução.")
        if not Path(self.paths[self.start_index]).is_file():
            raise FileNotFoundError(f"Ficheiro não encontrado: {self.paths[self.start_index]}")

        try:
            self._build_pipeline()
            self._load_index(self.start_index)
            if not self._closing:
                self._tick_source = GLib.timeout_add(500, self._tick)
        except Exception:
            # O construtor pode falhar depois de criar playbin3 ou ligar o bus.
            # Nunca deixar recursos GStreamer vivos num objeto que não chegou a nascer.
            self._closing = True
            self._running = False
            self._release_resources()
            raise

    @property
    def running(self) -> bool:
        return self._running and not self._closing

    @property
    def paused(self) -> bool:
        """Pausa solicitada pelo utilizador, distinta da pausa técnica por buffering."""
        return self._user_paused

    @property
    def tracks(self) -> list[dict]:
        return list(self._tracks)

    @property
    def current_path(self) -> str | None:
        if 0 <= self._index < len(self.paths):
            return self.paths[self._index]
        return None

    def _build_pipeline(self) -> None:
        self._pipeline = Gst.ElementFactory.make("playbin3", "cineprime-playbin")
        if self._pipeline is None:
            raise RuntimeError("O elemento GStreamer playbin3 não está instalado.")

        self._sink = Gst.ElementFactory.make("gtk4paintablesink", "cineprime-video-sink")
        if self._sink is None:
            raise RuntimeError(
                "Falta o plugin gtk4paintablesink. Instala o pacote gstreamer1.0-gtk4."
            )

        paintable = self._sink.get_property("paintable")
        if paintable is None:
            raise RuntimeError("GStreamer não criou a superfície de vídeo GTK4.")
        try:
            paintable.set_property("force-aspect-ratio", True)
        except (AttributeError, TypeError):
            pass

        self.picture = Gtk.Picture.new_for_paintable(paintable)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_can_shrink(True)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.set_size_request(1, 1)
        self.picture.add_css_class("gstreamer-video-picture")
        self.append(self.picture)

        self._pipeline.set_property("video-sink", self._sink)
        self._pipeline.set_property("volume", 1.0)
        self._pipeline.connect("element-setup", self._on_element_setup)

        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus_handler_id = self._bus.connect("message", self._on_bus_message)
        self._bus_watch = True

    def _on_element_setup(self, _playbin, element) -> None:
        """Regista o descodificador realmente instanciado pelo playbin3."""
        try:
            factory = element.get_factory()
            if factory is None:
                return
            klass = factory.get_metadata("klass") or ""
            if "Decoder/Video" not in klass:
                return
            if "Hardware" in klass:
                self._hardware_decoder_used = True
            else:
                self._software_decoder_used = True
                if getattr(self, "_software_fallback_active", False):
                    # O descodificador por software já foi instanciado; é seguro
                    # devolver as prioridades globais ao estado original.
                    self._restore_decoder_ranks()
        except (AttributeError, TypeError, RuntimeError):
            logging.getLogger(__name__).debug("Não foi possível identificar o descodificador GStreamer", exc_info=True)

    def _disable_hardware_decoder_ranks(self) -> bool:
        """Desprioriza temporariamente descodificadores de vídeo por hardware.

        playbin3 não implementa force-sw-decoders (a própria propriedade avisa
        que não tem efeito). A selecção do decodebin usa o rank do registry.
        Como só existe uma sessão de reprodução na UI, alteramos os ranks
        durante a reconstrução e restauramo-los assim que o software é criado.
        """
        registry = Gst.Registry.get()
        saved = []
        try:
            for feature in registry.get_feature_list(Gst.ElementFactory):
                klass = feature.get_metadata("klass") or ""
                rank = feature.get_rank()
                if "Decoder/Video/Hardware" in klass and rank > Gst.Rank.NONE:
                    saved.append((feature, rank))
                    feature.set_rank(Gst.Rank.NONE)
                elif "Decoder/Video" in klass and "Hardware" not in klass:
                    # Dá prioridade explícita aos descodificadores software;
                    # rank NONE sozinho não garante que decodebin3 os prefira.
                    saved.append((feature, rank))
                    feature.set_rank(max(rank, Gst.Rank.PRIMARY + 256))
        except Exception:
            for feature, rank in saved:
                try:
                    feature.set_rank(rank)
                except Exception:
                    pass
            logging.getLogger(__name__).exception("Falha ao despriorizar descodificadores de hardware")
            return False
        self._saved_decoder_ranks = saved
        return bool(saved)

    def _restore_decoder_ranks(self) -> None:
        timeout_source = getattr(self, "_fallback_timeout_source", 0)
        if timeout_source:
            try:
                GLib.source_remove(timeout_source)
            except Exception:
                pass
            self._fallback_timeout_source = 0
        saved = getattr(self, "_saved_decoder_ranks", [])
        self._saved_decoder_ranks = []
        for feature, rank in saved:
            try:
                feature.set_rank(rank)
            except Exception:
                logging.getLogger(__name__).warning("Não foi possível restaurar o rank de %s", feature.get_name())
        self._software_fallback_active = False

    def _software_fallback_timeout(self):
        """Evita deixar ranks globais alterados se o software não arrancar."""
        self._fallback_timeout_source = 0
        if self._software_fallback_active and not self._software_decoder_used:
            self._restore_decoder_ranks()
            self._fail(
                "A aceleração gráfica falhou e não foi possível iniciar um "
                "descodificador por software dentro do tempo limite."
            )
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _is_hardware_decode_error(detail: str, source=None) -> bool:
        """Só activa fallback para erros identificáveis do caminho gráfico/VA-API."""
        clues = [str(detail or "").lower()]
        if source is not None:
            try:
                clues.append(str(source.get_name() or "").lower())
            except (AttributeError, TypeError, RuntimeError):
                pass
            try:
                factory = source.get_factory()
                if factory is not None:
                    clues.append(str(factory.get_name() or "").lower())
                    clues.append(str(factory.get_metadata("klass") or "").lower())
            except (AttributeError, TypeError, RuntimeError):
                pass
        diagnostic = " ".join(clues)
        return any(token in diagnostic for token in (
            "vaapi", "vah264", "vah265", "vavp", "vampeg", "vajpeg",
            "intel media", "ihd", "va decoder", "va device", "hardware decoder", "gpu decoder",
            "render node", "surface allocation", "device lost",
            "failed to create va", "failed to initialize va",
        ))

    def _recover_with_software(self, original_error: str) -> bool:
        """Tenta uma única recuperação sem VA-API, preservando estado e faixas."""
        if (self._closing or not self._running or getattr(self, "_software_fallback_attempted", False)
                or not getattr(self, "_hardware_decoder_used", False)):
            return False
        if not self._disable_hardware_decoder_ranks():
            return False

        position = self._query_timeline()[0]
        was_paused = self._user_paused
        # Os IDs de stream podem ser recriados pela pipeline; guardamos também
        # idioma/título/posição para voltar a seleccionar a mesma faixa.
        self._fallback_selected_tracks = []
        for kind in ("audio", "sub"):
            choices = [track for track in getattr(self, "_tracks", []) if track.get("type") == kind]
            selected = next((track for track in choices if track.get("selected")), None)
            if selected is not None:
                self._fallback_selected_tracks.append({
                    "type": kind, "lang": selected.get("lang"),
                    "title": selected.get("title"), "index": choices.index(selected),
                })
        try:
            volume = self._pipeline.get_property("volume")
        except Exception:
            volume = 1.0
        self._software_fallback_attempted = True
        self._software_fallback_active = True
        self._hardware_decoder_used = False
        self._software_decoder_used = False
        self._fallback_original_error = original_error
        self._pending_seek = position if position > 0 else 0.0
        self._buffering = False

        try:
            # playbin3 mantém o decodebin interno e pode conservar a lista de
            # candidatos já descoberta. Criar uma pipeline nova força a consulta
            # do registry com os ranks temporários de software.
            old_bus = self._bus
            if old_bus and self._bus_handler_id:
                old_bus.disconnect(self._bus_handler_id)
                self._bus_handler_id = 0
            if old_bus and self._bus_watch:
                old_bus.remove_signal_watch()
                self._bus_watch = False
            old_pipeline = self._pipeline
            if old_pipeline:
                old_pipeline.set_state(Gst.State.NULL)
            if getattr(self, "picture", None):
                self.remove(self.picture)
            self._pipeline = None
            self._sink = None
            self._bus = None
            self._build_pipeline()
            self._pipeline.set_property("volume", volume)
            self._pipeline.set_property("suburi", self._subtitle_uri)
            self._pipeline.set_property("text-offset", self._subtitle_offset)
            self._pipeline.set_property("uri", Path(self.current_path).resolve().as_uri())
            target_state = Gst.State.PAUSED if was_paused else Gst.State.PLAYING
            result = self._pipeline.set_state(target_state)
            if result == Gst.StateChangeReturn.FAILURE:
                self._restore_decoder_ranks()
                return False
            self._fallback_timeout_source = GLib.timeout_add_seconds(
                10, self._software_fallback_timeout
            )
            logging.getLogger(__name__).warning(
                "Falha no descodificador por hardware; a tentar retomar com descodificação por software."
            )
            self._emit_state()
            return True
        except Exception:
            self._restore_decoder_ranks()
            logging.getLogger(__name__).exception("Falha ao reiniciar reprodução com descodificação por software")
            return False

    def _release_resources(self) -> None:
        """Liberta timer, bus e pipeline mesmo quando outra fase da sessão falha."""
        if self._tick_source:
            try:
                GLib.source_remove(self._tick_source)
            except Exception:
                pass
            self._tick_source = 0

        if self._bus and self._bus_handler_id:
            try:
                self._bus.disconnect(self._bus_handler_id)
            except (TypeError, RuntimeError):
                pass
            self._bus_handler_id = 0

        if self._bus and self._bus_watch:
            try:
                self._bus.remove_signal_watch()
            except (TypeError, RuntimeError):
                pass
            self._bus_watch = False

        if self._pipeline:
            try:
                self._pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
        self._restore_decoder_ranks()

    def _restore_fallback_state_for_new_item(self) -> None:
        """Repõe o registry e permite uma nova recuperação no próximo ficheiro."""
        self._restore_decoder_ranks()
        self._hardware_decoder_used = False
        self._software_decoder_used = False
        self._software_fallback_attempted = False
        self._software_fallback_active = False
        self._fallback_original_error = None
        self._fallback_selected_tracks = []

    def _load_index(self, index: int) -> None:
        if self._closing or not 0 <= index < len(self.paths):
            return
        path = self.paths[index]
        if not Path(path).is_file():
            self._fail(f"Ficheiro não encontrado: {path}")
            return

        if index != self._index:
            # Cada ficheiro/episódio tem direito a uma tentativa independente de
            # fallback; a falha de um episódio não bloqueia a recuperação do seguinte.
            self._restore_fallback_state_for_new_item()
            self._subtitle_uri = None
            self._external_subtitle_enabled = False
            self._external_subtitle_stream_id = None
            self._subtitle_offset = 0
        self._index = index
        self._position = 0.0
        self._duration = 0.0
        self._streams.clear()
        self._selected_stream_ids.clear()
        self._tracks = []
        self._pending_seek = self.start_at if index == self.start_index else 0.0

        self._user_paused = False
        self._buffering = False
        self._pipeline.set_state(Gst.State.NULL)
        # playbin3 recebe a legenda externa antes do URI principal.
        self._pipeline.set_property("suburi", self._subtitle_uri)
        self._pipeline.set_property("text-offset", self._subtitle_offset)
        self._pipeline.set_property("uri", Path(path).resolve().as_uri())
        result = self._pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            self._fail("GStreamer não conseguiu iniciar a reprodução.")
            return
        self._running = True
        self._emit_state()

    def _on_bus_message(self, _bus, message) -> None:
        if self._closing:
            return
        kind = message.type

        if kind == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            detail = f"{error.message}"
            if debug:
                detail += f" ({debug})"
            if (self._is_hardware_decode_error(detail, getattr(message, "src", None))
                    and self._recover_with_software(detail)):
                return
            if getattr(self, "_software_fallback_attempted", False) and getattr(self, "_fallback_original_error", None):
                detail = (
                    "A aceleração gráfica falhou e a reprodução também não conseguiu "
                    f"recuperar por software. Erro inicial: {self._fallback_original_error}. "
                    f"Erro na recuperação: {detail}"
                )
            self._fail(detail)
        elif kind == Gst.MessageType.EOS:
            if self._index + 1 < len(self.paths):
                self.save_now()
                self._load_index(self._index + 1)
            else:
                # _finish() já grava o progresso; não duplicar a escrita no último episódio.
                self._finish()
        elif kind == Gst.MessageType.STREAM_COLLECTION:
            self._read_stream_collection(message)
        elif kind == Gst.MessageType.ASYNC_DONE:
            if self._pending_seek > 0:
                target = self._pending_seek
                self._pending_seek = 0.0
                self.seek_absolute(target)
        elif kind == Gst.MessageType.DURATION_CHANGED:
            self._query_timeline()
        elif kind == Gst.MessageType.BUFFERING:
            self._buffering = message.parse_buffering() < 100
            if self._buffering and not self._user_paused:
                self._pipeline.set_state(Gst.State.PAUSED)
            elif not self._buffering and not self._user_paused and self._running:
                self._pipeline.set_state(Gst.State.PLAYING)
        elif kind == Gst.MessageType.STATE_CHANGED and message.src == self._pipeline:
            _old, new, _pending = message.parse_state_changed()
            if new in (Gst.State.PLAYING, Gst.State.PAUSED):
                self._emit_state()

    def _read_stream_collection(self, message) -> None:
        collection = message.parse_stream_collection()
        if collection is None:
            return

        streams = {}
        selected = []
        tracks = []
        for index in range(collection.get_size()):
            stream = collection.get_stream(index)
            stream_id = stream.get_stream_id()
            stream_type = stream.get_stream_type()
            flags = stream.get_stream_flags()
            tags = stream.get_tags()
            if flags & Gst.StreamFlags.SELECT:
                selected.append(stream_id)

            if stream_type & Gst.StreamType.AUDIO:
                kind = "audio"
                label = "Faixa de áudio"
            elif stream_type & Gst.StreamType.TEXT:
                kind = "sub"
                label = "Legenda"
            else:
                streams[stream_id] = {"type": "video"}
                continue

            language = _tag_string(tags, Gst.TAG_LANGUAGE_CODE)
            title = _tag_string(tags, Gst.TAG_TITLE)
            track = {
                "id": stream_id,
                "type": kind,
                "lang": language,
                "title": title or label,
                "selected": bool(flags & Gst.StreamFlags.SELECT),
            }
            streams[stream_id] = track
            tracks.append(track)

        # Uma legenda externa recém-adicionada não vem necessariamente marcada
        # como SELECT por playbin3; seleccionamo-la explicitamente sem perder áudio/vídeo.
        external_text = (
            next((t["id"] for t in reversed(tracks) if t["type"] == "sub"), None)
            if self._subtitle_uri else None
        )
        self._external_subtitle_stream_id = external_text
        if self._external_subtitle_enabled and external_text:
            selected = [sid for sid in selected if streams.get(sid, {}).get("type") != "sub"]
            selected.append(external_text)

        # Após fallback, repõe a faixa interna equivalente mesmo que o GStreamer
        # tenha atribuído IDs novos ao reconstruir playbin3.
        if self._software_fallback_active and self._fallback_selected_tracks:
            requested = [sid for sid in selected if streams.get(sid, {}).get("type") not in ("audio", "sub")]
            for previous in self._fallback_selected_tracks:
                kind = previous["type"]
                if kind == "sub" and self._external_subtitle_enabled and external_text:
                    requested.append(external_text)
                    continue
                choices = [track for track in tracks if track["type"] == kind]
                match = next((track for track in choices
                              if track.get("lang") == previous.get("lang")
                              and track.get("title") == previous.get("title")), None)
                if match is None and previous.get("lang"):
                    match = next((track for track in choices
                                  if track.get("lang") == previous.get("lang")), None)
                if match is None and choices:
                    index = min(previous.get("index", 0), len(choices) - 1)
                    match = choices[index]
                if match is not None:
                    requested.append(match["id"])
            if requested != selected and self._pipeline.send_event(Gst.Event.new_select_streams(requested)):
                selected = requested
            self._fallback_selected_tracks = []

        for track in tracks:
            track["selected"] = track["id"] in selected

        self._streams = streams
        self._selected_stream_ids = selected
        self._tracks = tracks
        self._emit_tracks()

    def _select_streams(self, kind: str, stream_id: str | None) -> bool:
        if not self.running:
            return False
        selected = [
            sid for sid in self._selected_stream_ids
            if self._streams.get(sid, {}).get("type") != kind
        ]
        if stream_id is not None:
            selected.append(str(stream_id))
        if not selected:
            # Preserve the video stream and any non-target stream selected by playbin3.
            selected = [
                sid for sid, info in self._streams.items()
                if info.get("type") == "video"
            ]
        event = Gst.Event.new_select_streams(selected)
        accepted = self._pipeline.send_event(event)
        if accepted:
            self._selected_stream_ids = selected
            for track in self._tracks:
                if track["type"] == kind:
                    track["selected"] = track["id"] == stream_id
            self._emit_tracks()
        return bool(accepted)

    def _emit_tracks(self) -> None:
        if self._on_tracks and not self._closing:
            GLib.idle_add(self._deliver_tracks)

    def _deliver_tracks(self):
        if self._on_tracks and not self._closing:
            self._on_tracks(self.tracks)
        return GLib.SOURCE_REMOVE

    def _emit_state(self) -> None:
        if self._on_state and not self._closing:
            GLib.idle_add(self._deliver_state)

    def _deliver_state(self):
        if self._on_state and not self._closing:
            self._on_state()
        return GLib.SOURCE_REMOVE

    def _query_timeline(self) -> tuple[float, float]:
        if not self._pipeline:
            return self._position, self._duration
        ok_pos, position = self._pipeline.query_position(Gst.Format.TIME)
        ok_dur, duration = self._pipeline.query_duration(Gst.Format.TIME)
        if ok_pos:
            self._position = max(0.0, position / Gst.SECOND)
        if ok_dur:
            self._duration = max(0.0, duration / Gst.SECOND)
        return self._position, self._duration

    def _tick(self) -> bool:
        if self._closing:
            self._tick_source = 0
            return GLib.SOURCE_REMOVE
        if self._running:
            self._query_timeline()
            path = self.current_path
            if path and self._duration > 0:
                self._dirty[path] = (self._position, self._duration)
            if self._on_progress:
                self._on_progress(self._position, self._duration)
            self._ticks += 1
            if self._ticks % 10 == 0:
                self.save_now()
            return GLib.SOURCE_CONTINUE
        self._tick_source = 0
        return GLib.SOURCE_REMOVE

    def _save_current(self) -> None:
        self._query_timeline()
        path = self.current_path
        if path and self._duration > 0:
            self._dirty[path] = (self._position, self._duration)

    def save_now(self) -> None:
        self._save_current()
        if self._dirty and self._on_save:
            entries = dict(self._dirty)
            self._dirty.clear()
            self._on_save(entries)

    def toggle_pause(self) -> None:
        if not self.running:
            return
        previous = self._user_paused
        self._user_paused = not previous
        target = (
            Gst.State.PAUSED
            if self._user_paused or self._buffering
            else Gst.State.PLAYING
        )
        result = self._pipeline.set_state(target)
        if result == Gst.StateChangeReturn.FAILURE:
            self._user_paused = previous
        else:
            self._emit_state()

    def seek(self, seconds: float) -> None:
        if self.running:
            self._query_timeline()
            self.seek_absolute(max(0.0, self._position + float(seconds)))

    def seek_absolute(self, seconds: float) -> None:
        if not self.running:
            return
        target = max(0.0, float(seconds))
        flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
        self._pipeline.seek(
            self._speed,
            Gst.Format.TIME,
            flags,
            Gst.SeekType.SET,
            int(target * Gst.SECOND),
            Gst.SeekType.NONE,
            -1,
        )

    def next_track(self) -> None:
        if self.running and self._index + 1 < len(self.paths):
            self.save_now()
            self._load_index(self._index + 1)

    def play_index(self, index: int) -> None:
        """Muda para um episódio da lista, guardando o progresso atual."""
        if self.running and 0 <= int(index) < len(self.paths) and int(index) != self._index:
            self.save_now()
            self._load_index(int(index))

    def set_volume(self, value: float) -> None:
        if self.running:
            self._pipeline.set_property("volume", max(0.0, min(1.5, float(value) / 100.0)))

    def set_speed(self, speed: float) -> None:
        if not self.running:
            return
        self._speed = max(0.25, min(4.0, float(speed)))
        self._query_timeline()
        self.seek_absolute(self._position)

    def set_audio_track(self, track_id) -> None:
        self._select_streams("audio", str(track_id))

    def set_subtitle_track(self, track_id) -> None:
        if track_id == "no":
            self._external_subtitle_enabled = False
            self._select_streams("sub", None)
        else:
            self._external_subtitle_enabled = str(track_id) == self._external_subtitle_stream_id
            self._select_streams("sub", str(track_id))

    def cycle_audio(self) -> None:
        choices = [t for t in self._tracks if t["type"] == "audio"]
        if len(choices) < 2:
            return
        current = next((i for i, t in enumerate(choices) if t["selected"]), -1)
        self.set_audio_track(choices[(current + 1) % len(choices)]["id"])

    def cycle_subtitle(self) -> None:
        choices = [t for t in self._tracks if t["type"] == "sub"]
        if not choices:
            return
        current = next((i for i, t in enumerate(choices) if t["selected"]), -1)
        self.set_subtitle_track(
            "no" if current == len(choices) - 1 else choices[(current + 1) % len(choices)]["id"]
        )

    def _restart_current_with_subtitle(self) -> None:
        if not self.running or not self.current_path:
            return
        position = self._query_timeline()[0]
        was_paused = self._user_paused
        self._pending_seek = position
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.set_property("suburi", self._subtitle_uri)
        self._pipeline.set_property("text-offset", self._subtitle_offset)
        self._pipeline.set_property("uri", Path(self.current_path).resolve().as_uri())
        target_state = Gst.State.PAUSED if was_paused else Gst.State.PLAYING
        result = self._pipeline.set_state(target_state)
        if result == Gst.StateChangeReturn.FAILURE:
            self._fail("Não foi possível aplicar a legenda externa.")

    def load_subtitle(self, path: str) -> None:
        if not self.running:
            return
        subtitle = Path(path).expanduser().resolve()
        if not subtitle.is_file():
            raise FileNotFoundError(str(subtitle))
        self._subtitle_uri = subtitle.as_uri()
        self._external_subtitle_enabled = True
        self._external_subtitle_stream_id = None
        self._restart_current_with_subtitle()

    def remove_external_subtitle(self) -> None:
        if not self.running or not self._subtitle_uri:
            return
        self._subtitle_uri = None
        self._external_subtitle_enabled = False
        self._external_subtitle_stream_id = None
        self._restart_current_with_subtitle()

    def set_subtitle_delay(self, seconds: float) -> None:
        self._subtitle_offset = int(float(seconds) * Gst.SECOND)
        if self.running:
            self._pipeline.set_property("text-offset", self._subtitle_offset)

    def set_subtitle_font_size(self, size: int) -> None:
        """Atualiza o tamanho da fonte das legendas renderizadas pelo playbin."""
        size = max(16, min(40, int(size)))
        if self._pipeline:
            self._pipeline.set_property("subtitle-font-desc", f"Sans {size}")

    def stop(self) -> None:
        # _finish() é o único responsável por persistir e libertar recursos.
        self._finish()

    def close(self) -> None:
        self.stop()

    def _fail(self, message: str) -> None:
        if self._on_error and not self._closing:
            GLib.idle_add(self._on_error, message)
        self._finish(notify=False)

    def _finish(self, notify: bool = True):
        if self._closing:
            return GLib.SOURCE_REMOVE

        # Bloqueia imediatamente novos eventos/callbacks antes de persistir.
        self._closing = True
        self._running = False
        try:
            self.save_now()
        except Exception as exc:
            # Uma falha na base de dados não pode impedir o fecho da pipeline.
            logging.getLogger(__name__).warning(
                "Cine Prime: falha ao guardar progresso no encerramento: %s", exc
            )
        finally:
            self._release_resources()

        if notify and self._on_exit:
            GLib.idle_add(self._on_exit)
        return GLib.SOURCE_REMOVE