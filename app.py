#!/usr/bin/env python3
"""Janela GTK4 + libadwaita: abas, grade de capas, pop-up de detalhe,
retomada de onde você parou e edição de nome e capa.

Estrutura (de fora para dentro):

  Adw.Application
   └ Adw.ApplicationWindow
      └ Adw.ToastOverlay            -> avisos rápidos na parte de baixo
         └ Adw.ToolbarView
            ├ Adw.HeaderBar         -> abas (Adw.ViewSwitcher)
            └ Adw.ViewStack         -> Início, Filmes, Séries, Favoritos e pesquisa
               └ Adw.Bin > ScrolledWindow > GridView virtualizado > cartões

Os botões da barra de cima gerenciam as pastas e atualizam a biblioteca (a
varredura roda numa thread, para a janela não travar).

Clicar num cartão abre um Adw.Dialog (o pop-up) com capa, botão Ver/Continuar
e um ícone de lápis (editar) na barra de cima.
"""
import re
import shutil
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

import config  # noqa: E402
import backup_manager  # noqa: E402
import covers  # noqa: E402
import posters  # noqa: E402
from effects import CardBox  # noqa: E402
from database import MIN_RESUME_SECONDS, Database, Resume, resume_from  # noqa: E402
from gstreamer_player import EmbeddedGStreamerSession  # noqa: E402
from player_controls import PlayerProgressBar  # noqa: E402
from scanner import ScanResult, scan_folders  # noqa: E402
from media_integrity import IntegrityResult, check_media_integrity  # noqa: E402

TABS = [
    ("movie", "Filmes", "video-x-generic-symbolic"),
    ("series", "Séries", "view-list-symbolic"),
]
# Páginas do dock: (nome, rótulo, ícone). "movie" e "series" também são o tipo no banco.
PAGES = [
    ("home", "Início", "go-home-symbolic"),
    ("movie", "Filmes", "video-x-generic-symbolic"),
    ("series", "Séries", "view-list-symbolic"),
    ("favorites", "Favoritos", "emblem-favorite-symbolic"),
]
SEARCH_PAGE = "search"          # nome da página que mostra os resultados da busca

HEART = "\u2665\ufe0e"   # coração como TEXTO (+ seletor de texto): segue a cor do CSS em qualquer tema de ícones


def set_accessible_label(widget, label: str) -> None:
    """Define o nome anunciado por leitores de ecrã e guarda-o para testes."""
    widget.update_property([Gtk.AccessibleProperty.LABEL], [str(label)])
    widget._cineprime_accessible_label = str(label)

DOCK_CSS = """
.dock { background: alpha(#141414, 0.9); border-radius: 30px; padding: 6px 10px;
        border: 1px solid alpha(white, 0.1); box-shadow: 0 6px 20px alpha(black, 0.5); }
.dock button.dock-button { background: transparent; color: #d0d0d0; border-radius: 18px;
        min-width: 44px; min-height: 44px; padding: 0; }
.dock button.dock-button:hover { background: alpha(white, 0.1); color: white; }
.dock button.dock-button.active { background: #E50914; color: white; }
.dock button.dock-add { background: alpha(white, 0.1); }
.dock separator { background: alpha(white, 0.15); margin: 10px 4px; }
button.heart-button { background: alpha(black, 0.6); color: alpha(white, 0.75); border-radius: 99px;
        min-width: 40px; min-height: 40px; padding: 0; margin: 6px; font-size: 15px; }
button.heart-button:hover { color: white; background: alpha(black, 0.8); }
button.fav-on, button.heart-button.fav-on { color: #E50914; }
button.heart-header { font-size: 18px; }
progressbar.continue-bar trough { min-height: 4px; background: alpha(white, 0.18); border-radius: 2px; }
progressbar.continue-bar progress { min-height: 4px; background: #E50914; border-radius: 2px; }
"""
STYLE_CSS = """
@define-color accent_bg_color #E50914;
@define-color accent_color #ff4d58;

window.cineprime { background-color: #0b0b0c; color: #f2f2f2; }
window.cineprime headerbar { background: transparent; box-shadow: none; border: none; }
window.cineprime searchentry { background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 999px; min-height: 36px; padding: 0 12px; }
window.cineprime searchentry:focus-within { border-color: #E50914; }
window.cineprime button:focus-visible,
window.cineprime menubutton:focus-visible,
window.cineprime entry:focus-visible,
window.cineprime scale:focus-visible {
    outline: 2px solid #ff4d58;
    outline-offset: 3px;
}

gridview > child { border-radius: 16px; padding: 8px; transition: background-color 150ms ease; }
gridview > child:hover { background-color: rgba(255, 255, 255, 0.06); }

.cover-overlay { border-radius: 12px; box-shadow: 0 8px 20px rgba(0, 0, 0, 0.6); }
frame.cover-frame { border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; }
label.badge { background: rgba(0, 0, 0, 0.72); color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.22);
        border-radius: 6px; padding: 1px 7px; margin: 8px; font-family: monospace; font-size: 10px; font-weight: 700; }
.title-card .title-name { font-weight: 700; font-size: 13px; color: #fafafa; }
.title-card .caption { color: rgba(255, 255, 255, 0.55); }
progressbar.continue-bar trough { min-height: 4px; border-radius: 0; background: rgba(255, 255, 255, 0.28); }
progressbar.continue-bar progress { min-height: 4px; border-radius: 0; background: #E50914; }
"""
PLAYER_CSS = """
.player-surface { background: #000000; }
.player-chrome {
    color: #ffffff;
    background: linear-gradient(to bottom, alpha(#050505, 0.82), alpha(#050505, 0.0));
    padding: 22px 30px;
}
.player-bottom {
    /* Sem blur, espelhamento ou degradê aplicado ao vídeo. */
    background: linear-gradient(to top, alpha(#050505, 0.94), alpha(#050505, 0.0));
    padding: 34px 34px 24px;
}
.player-chrome button, .player-bottom button, .player-bottom menubutton > button {
    color: #ffffff; background: alpha(#181818, 0.84);
    border: 1px solid alpha(white, 0.10); border-radius: 7px;
    min-height: 40px; padding: 0 14px;
}
.player-chrome button:hover, .player-bottom button:hover {
    background: #E50914; border-color: #E50914;
}
.player-title { color: #ffffff; font-size: 17px; font-weight: 700; }
.player-time { color: alpha(white, 0.82); font-size: 12px; font-variant-numeric: tabular-nums; }
.player-progress-track { min-height: 24px; padding: 8px 0; background: transparent; }
.player-progress-track trough { min-height: 4px; border-radius: 999px; background-color: rgba(255, 255, 255, 0.30); }
.player-progress-track highlight { min-height: 4px; border-radius: 999px; background-color: #E50914; }
.player-progress-track slider {
    min-width: 14px; min-height: 14px; margin: -5px 0;
    border-radius: 999px; background-color: #ffffff;
    border: 1px solid rgba(0, 0, 0, 0.25);
}
.player-progress-track.hover trough { background-color: rgba(255, 255, 255, 0.42); }
.player-progress-track.hover highlight { background-color: #ff2633; }
.player-progress-track:disabled slider { opacity: 0.55; }
.player-volume { min-width: 34px; min-height: 116px; }
.player-volume trough { min-width: 5px; border-radius: 999px; background: alpha(white, 0.28); }
.player-volume highlight { min-width: 5px; border-radius: 999px; background: #E50914; }
.player-volume slider { min-width: 13px; min-height: 13px; border-radius: 999px; background: white; border: 1px solid alpha(black, 0.35); }
.player-volume-panel { background: alpha(#111111, 0.82); border: 1px solid alpha(white, 0.14); border-radius: 12px; padding: 10px 8px; }
.next-episode-prompt { min-height: 48px; padding: 0 22px; border-radius: 8px;
    background: alpha(#171717, 0.96); border: 1px solid alpha(white, 0.24);
    border-left: 4px solid #E50914; font-weight: 700; }
.next-episode-prompt:hover { background: #E50914; border-color: #E50914; }
.skip-credits-prompt { min-height: 46px; padding: 0 20px; border-radius: 7px;
    background: alpha(#171717, 0.96); border: 1px solid alpha(white, 0.32);
    border-left: 4px solid #E50914; font-weight: 700; }
.skip-credits-prompt:hover { background: #E50914; border-color: #E50914; }
.player-bottom label { color: white; }
.episode-current { background: alpha(#E50914, 0.20); border: 1px solid #E50914; font-weight: 700; }
.episode-current:hover { background: alpha(#E50914, 0.34); }
"""
ALL_CSS = DOCK_CSS + STYLE_CSS + PLAYER_CSS
_css_installed = False


def install_dock_css() -> None:
    """Carrega o visual do dock uma única vez para o programa inteiro."""
    global _css_installed
    if _css_installed:
        return
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)   # visual de streaming: sempre escuro
    provider = Gtk.CssProvider()
    if hasattr(provider, "load_from_string"):        # GTK 4.12 em diante
        provider.load_from_string(ALL_CSS)
    else:
        provider.load_from_data(ALL_CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _css_installed = True


def message_page(title: str, text: str, icon: str) -> Gtk.Widget:
    """Página provisória das seções que ainda vamos construir."""
    return Adw.StatusPage(title=title, description=text, icon_name=icon)
CARD_W, CARD_H = 160, 240
KIND_LABELS = {"movie": "Filmes", "series": "Séries", "auto": "Automático"}


def fmt_time(seconds: float) -> str:
    """4350 -> '1:12:30'; 750 -> '12:30'."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def attribution_label() -> Gtk.Label:
    """Crédito das fontes de capas. A quebra de linha é manual de propósito: rótulos que
    quebram sozinhos dentro de pop-ups podem ter a altura calculada errada (cortam
    a última linha)."""
    label = Gtk.Label(label=posters.ATTRIBUTION.replace(". Capas de séries", ".\nCapas de séries"), xalign=0)
    label.add_css_class("caption")
    label.add_css_class("dim-label")
    return label


# Cache LRU limitado: evita descodificar a mesma capa em cada reconstrução
# de página. As imagens são reduzidas antes de criar a textura, em vez de
# manter na GPU/RAM a resolução original de posters muito grandes.
_COVER_CACHE_MAX = 128
_COVER_WORKERS = 3
_cover_texture_cache = OrderedDict()
_cover_executor = ThreadPoolExecutor(max_workers=_COVER_WORKERS, thread_name_prefix="cineprime-cover")
_cover_inflight = {}
_cover_inflight_lock = threading.Lock()


def _texture_from_pixbuf(pixbuf: GdkPixbuf.Pixbuf) -> Gdk.Texture:
    """Converte RGB/RGBA de 8 bits para textura GTK4 sem API obsoleta."""
    channels = pixbuf.get_n_channels()
    if pixbuf.get_bits_per_sample() != 8 or channels not in (3, 4):
        raise ValueError(
            f"Formato de pixbuf não suportado: {channels} canais, "
            f"{pixbuf.get_bits_per_sample()} bits por canal"
        )
    memory_format = (
        Gdk.MemoryFormat.R8G8B8A8 if channels == 4 else Gdk.MemoryFormat.R8G8B8
    )
    pixels = GLib.Bytes.new(pixbuf.get_pixels())
    return Gdk.MemoryTexture.new(
        pixbuf.get_width(), pixbuf.get_height(), memory_format,
        pixels, pixbuf.get_rowstride(),
    )


def _load_cover_texture(path: str, width: int, height: int):
    """Devolve uma textura reduzida e reutilizável, invalidando-a se o ficheiro mudar."""
    try:
        source = Path(path)
        stat = source.stat()
        if not source.is_file():
            return None
        key = (str(source.resolve()), stat.st_size, stat.st_mtime_ns, width, height)
        cached = _cover_texture_cache.get(key)
        if cached is not None:
            _cover_texture_cache.move_to_end(key)
            return cached

        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(source), width, height, True
        )
        texture = _texture_from_pixbuf(pixbuf)
        _cover_texture_cache[key] = texture
        _cover_texture_cache.move_to_end(key)
        while len(_cover_texture_cache) > _COVER_CACHE_MAX:
            _cover_texture_cache.popitem(last=False)
        return texture
    except (GLib.Error, OSError, ValueError) as err:
        print(f"Não consegui abrir a capa '{path}': {err}")
        return None


def _decode_cover(path: str, width: int, height: int):
    """Descodifica e reduz a imagem fora da thread GTK; não cria widgets aqui."""
    source = Path(path)
    stat = source.stat()
    if not source.is_file():
        raise FileNotFoundError(path)
    key = (str(source.resolve()), stat.st_size, stat.st_mtime_ns, width, height)
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(source), width, height, True)
    return key, pixbuf


def _cache_cover_texture(key, texture):
    _cover_texture_cache[key] = texture
    _cover_texture_cache.move_to_end(key)
    while len(_cover_texture_cache) > _COVER_CACHE_MAX:
        _cover_texture_cache.popitem(last=False)


def _request_cover_texture(path: str, width: int, height: int, picture, placeholder) -> None:
    """Carrega a capa em worker e actualiza widgets apenas na thread principal."""
    try:
        source = Path(path)
        stat = source.stat()
        key = (str(source.resolve()), stat.st_size, stat.st_mtime_ns, width, height)
    except OSError as err:
        print(f"Não consegui abrir a capa '{path}': {err}")
        return

    cached = _cover_texture_cache.get(key)
    if cached is not None:
        _cover_texture_cache.move_to_end(key)
        picture.set_paintable(cached)
        picture.set_visible(True)
        placeholder.set_visible(False)
        return

    inflight_key = (key[0], width, height)
    with _cover_inflight_lock:
        waiters = _cover_inflight.get(inflight_key)
        if waiters is not None:
            waiters.append((picture, placeholder))
            return
        _cover_inflight[inflight_key] = [(picture, placeholder)]

    def deliver(future):
        try:
            decoded_key, pixbuf = future.result()
            error = None
        except Exception as exc:  # ficheiro removido, formato inválido, erro de I/O
            decoded_key, pixbuf, error = None, None, exc

        def apply_result():
            with _cover_inflight_lock:
                targets = _cover_inflight.pop(inflight_key, [])
            if error is not None:
                print(f"Não consegui abrir a capa '{path}': {error}")
                return GLib.SOURCE_REMOVE
            texture = _cover_texture_cache.get(decoded_key)
            if texture is None:
                texture = _texture_from_pixbuf(pixbuf)
                _cache_cover_texture(decoded_key, texture)
            else:
                _cover_texture_cache.move_to_end(decoded_key)
            for target_picture, target_placeholder in targets:
                target_picture.set_paintable(texture)
                target_picture.set_visible(True)
                target_placeholder.set_visible(False)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(apply_result)

    future = _cover_executor.submit(_decode_cover, path, width, height)
    future.add_done_callback(deliver)


def build_cover(row, width: int, height: int) -> Gtk.Frame:
    """Capa com placeholder imediato e descodificação assíncrona em segundo plano."""
    shown = row["display_name"] or row["name"]
    placeholder = Gtk.Label(label=shown[:1].upper())
    placeholder.add_css_class("title-1")
    placeholder.add_css_class("dim-label")

    picture = Gtk.Picture()
    picture.set_content_fit(Gtk.ContentFit.COVER)
    picture.set_can_shrink(True)
    picture.set_size_request(width, height)
    picture.set_visible(False)

    overlay = Gtk.Overlay()
    overlay.set_child(placeholder)
    overlay.add_overlay(picture)
    picture.set_halign(Gtk.Align.FILL)
    picture.set_valign(Gtk.Align.FILL)

    frame = Gtk.Frame()
    frame.add_css_class("cover-frame")
    frame.set_child(overlay)
    frame.set_size_request(width, height)
    frame.set_halign(Gtk.Align.CENTER)
    frame.set_valign(Gtk.Align.START)

    cover_path = row["cover_path"]
    if cover_path:
        _request_cover_texture(cover_path, width, height, picture, placeholder)
    return frame


def set_heart(button: Gtk.Button, favorite: bool) -> None:
    """Pinta o coração de vermelho (favorito) ou o deixa discreto, e ajusta a dica do mouse."""
    if favorite:
        button.add_css_class("fav-on")
    else:
        button.remove_css_class("fav-on")
    label = "Remover dos favoritos" if favorite else "Adicionar aos favoritos"
    button.set_tooltip_text(label)
    set_accessible_label(button, label)


def iter_cards(widget):
    """Percorre os cartões actualmente materializados numa página virtualizada."""
    if getattr(widget, "title_id", None) is not None:
        yield widget
        return
    if not hasattr(widget, "get_first_child"):
        return
    child = widget.get_first_child()
    while child:
        yield from iter_cards(child)
        child = child.get_next_sibling()


def continue_caption(row, item) -> str:
    """Linha embaixo do nome no Início: onde você vai continuar."""
    if row["kind"] == "movie":
        return f"{round(item.fraction * 100)}% assistido"
    if item.episode is None:
        return f"{row['n_files']} episódio(s)"
    return f"T{item.season} · E{item.episode}" if item.season else f"E{item.episode}"


def format_badge(row) -> str:
    """Formato do arquivo (MKV, MP4...), pela extensão do primeiro arquivo do título."""
    path = row["sample_path"] if "sample_path" in row.keys() else None
    return Path(path).suffix.lstrip(".").upper() if path else ""


def build_card(row, item=None, on_heart=None) -> Gtk.Box:
    """Cartão de um título: capa com etiqueta de formato, coração e barra de progresso POR CIMA,
    e embaixo o nome em negrito e uma linha de apoio. `item` (ContinueItem) só vem no Início;
    `on_heart(id, favorito)` é chamado ao clicar no coração (sem ele, não há coração)."""
    box = CardBox(orientation=Gtk.Orientation.VERTICAL, spacing=8)   # inclina e brilha com o mouse
    box.add_css_class("title-card")
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.START)

    overlay = Gtk.Overlay()                      # etiqueta, coração e barra ficam por cima da capa
    overlay.add_css_class("cover-overlay")
    overlay.set_overflow(Gtk.Overflow.HIDDEN)    # recorta etiqueta e barra nos cantos arredondados
    overlay.set_child(build_cover(row, CARD_W, CARD_H))
    overlay.set_halign(Gtk.Align.CENTER)
    overlay.set_valign(Gtk.Align.START)
    box.cover = overlay                          # o brilho do mouse fica recortado na capa
    fmt = format_badge(row)
    if fmt:
        badge = Gtk.Label(label=fmt)
        badge.add_css_class("badge")
        badge.set_halign(Gtk.Align.START)
        badge.set_valign(Gtk.Align.START)
        overlay.add_overlay(badge)
    if on_heart is not None:
        heart = Gtk.Button(label=HEART)
        heart.add_css_class("heart-button")
        heart.set_halign(Gtk.Align.END)
        heart.set_valign(Gtk.Align.START)
        set_heart(heart, bool(row["favorite"]))
        heart.connect("clicked", lambda b: on_heart(row["id"], not b.has_css_class("fav-on")))
        overlay.add_overlay(heart)
        box.heart = heart
    if item is not None and item.fraction > 0:   # a barra só aparece se já começou a ver
        bar = Gtk.ProgressBar(fraction=item.fraction)
        bar.add_css_class("continue-bar")
        bar.set_halign(Gtk.Align.FILL)
        bar.set_valign(Gtk.Align.END)
        overlay.add_overlay(bar)
    box.append(overlay)

    year = f" ({row['year']})" if row["year"] else ""
    name = Gtk.Label(label=f"{row['shown']}{year}")
    name.add_css_class("title-name")
    name.set_wrap(True)
    name.set_max_width_chars(18)
    name.set_lines(2)
    name.set_ellipsize(3)                        # 3 = Pango.EllipsizeMode.END
    name.set_xalign(0)
    name.set_halign(Gtk.Align.START)
    box.append(name)

    caption = None
    if item is not None:
        caption = continue_caption(row, item)
    elif row["kind"] != "movie":
        caption = f"{row['n_files']} episódio(s)"
    if caption:
        label = Gtk.Label(label=caption)
        label.add_css_class("caption")
        label.set_xalign(0)
        label.set_halign(Gtk.Align.START)
        box.append(label)
    return box


class CinePrimeWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, db: Database):
        super().__init__(application=app, title="Cine Prime")
        self.db = db
        # Ícone da janela: identidade própria do Cine Prime, registada no tema local.
        self.set_icon_name("io.github.helder.cineprime")
        self.session: EmbeddedGStreamerSession | None = None   # sessão GStreamer desenhada nesta janela
        self.player_view = None
        self._controls_timeout = 0       # temporizador periódico de inatividade
        self._last_pointer_motion_us = 0
        self._last_pointer_x = None
        self._last_pointer_y = None
        self._updating_progress = False
        self._is_fullscreen = False
        self._closing_window = False
        self._worker_lock = threading.Lock()
        self._active_workers = 0
        self.scanning = False                    # varredura em andamento?
        self.integrity_running = False
        self._integrity_confirmation = None
        self.rescan_pending = False              # pediram outra varredura durante esta
        self.fetching_covers = False             # busca de capas em andamento?
        self.progress_toast: Adw.Toast | None = None   # aviso "Buscando…" (some ao terminar)
        self.tmp_dir: str | None = None          # pasta temporária das capas baixadas
        self.set_default_size(1000, 700)
        self.set_size_request(360, 360)  # tamanho mínimo da janela
        self.connect("close-request", self.on_close)
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)

        # Cada aba tem um Adw.Bin (caixa de um filho só). Para atualizar a
        # grade, trocamos o filho do Bin, sem mexer nas abas.
        self.query = ""                          # texto da busca, já normalizado ("" = sem busca)
        self.section = "home"                    # página escolhida no dock; o app abre no Início
        install_dock_css()
        self.add_css_class("cineprime")
        self.pages = {}
        self.stack = Adw.ViewStack()
        # SEARCH_PAGE guarda os resultados da busca (Filmes e Séries juntos).
        for name, _label, _icon in PAGES + [(SEARCH_PAGE, "", "")]:
            self.pages[name] = Adw.Bin()
            self.stack.add_named(self.pages[name], name)
        # Páginas fora de vista ficam marcadas como desactualizadas e só são
        # reconstruídas quando forem abertas. Evita criar dezenas/centenas de
        # cartões sem necessidade após cada operação na biblioteca.
        self._dirty_pages = {name for name, _label, _icon in PAGES}
        self._dirty_pages.add(SEARCH_PAGE)
        self.reload()

        # Busca por nome. O "search-changed" só dispara depois de uma pausa na
        # digitação, para não refazer a grade a cada letra.
        self.search_entry = Gtk.SearchEntry(placeholder_text="Pesquisar por nome…")
        # SearchEntry não fica sempre focável por omissão em todas as versões GTK;
        # activamos explicitamente para Tab e para o atalho F.
        self.search_entry.set_focusable(True)
        set_accessible_label(self.search_entry, "Pesquisar filmes e séries na biblioteca")
        self.search_entry.update_property(
            [Gtk.AccessibleProperty.PLACEHOLDER], ["Pesquisar por nome"]
        )
        self.search_entry.connect("search-changed", lambda _e: self.apply_search())
        header = Adw.HeaderBar()
        self.header = header
        header.set_title_widget(Adw.Clamp(maximum_size=420, child=self.search_entry))

        # pack_end: o 1º botão fica mais à direita, os seguintes vêm à esquerda dele.
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Atualizar biblioteca")
        set_accessible_label(self.refresh_button, "Actualizar biblioteca")
        self.refresh_button.connect("clicked", lambda _b: self.start_scan())
        self.covers_button = Gtk.Button(icon_name="insert-image-symbolic")
        self.covers_button.set_tooltip_text("Buscar capas que faltam")
        set_accessible_label(self.covers_button, "Buscar capas em falta")
        self.covers_button.connect("clicked", lambda _b: self.start_cover_batch())
        self.integrity_button = Gtk.Button(icon_name="security-high-symbolic")
        self.integrity_button.set_tooltip_text("Verificação profunda da integridade dos vídeos")
        set_accessible_label(self.integrity_button, "Verificação profunda da integridade dos vídeos")
        self.integrity_button.connect("clicked", lambda _b: self.start_integrity_check())
        self.duplicates_button = Gtk.Button(icon_name="edit-copy-symbolic")
        self.duplicates_button.set_tooltip_text("Verificar ficheiros duplicados")
        set_accessible_label(self.duplicates_button, "Verificar ficheiros duplicados")
        self.duplicates_button.connect("clicked", lambda _b: self.open_duplicates_dialog())
        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        self.backup_button = Gtk.Button(icon_name="document-save-symbolic")
        self.backup_button.set_tooltip_text("Backup e restauro da biblioteca")
        set_accessible_label(self.backup_button, "Backup e restauro da biblioteca")
        self.backup_button.connect("clicked", lambda _b: self.open_backup_menu())

        # As ferramentas de manutenção ficam agrupadas num único ponto de entrada.
        self.settings_button = Gtk.Button(icon_name="emblem-system-symbolic")
        self.settings_button.set_tooltip_text("Definições e manutenção da biblioteca")
        self.settings_button.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        set_accessible_label(self.settings_button, "Abrir definições e manutenção")
        self.settings_button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
        self.settings_button.set_size_request(40, 40)
        self.settings_button.connect("clicked", lambda _b: self.open_settings_dialog())
        header.pack_end(self.settings_button)
        header.pack_end(self.spinner)

        # O dock flutua por cima do conteúdo (Gtk.Overlay): o conteúdo ocupa a janela toda.
        self.dock_buttons = {}
        dock = Gtk.Box(spacing=4)
        dock.add_css_class("dock")
        dock.set_halign(Gtk.Align.CENTER)
        dock.set_valign(Gtk.Align.END)
        dock.set_margin_bottom(18)
        for name, label, icon in PAGES:
            button = Gtk.Button(icon_name=icon)
            button.add_css_class("flat")
            button.add_css_class("dock-button")
            button.set_tooltip_text(label)
            set_accessible_label(button, label)
            # n=name guarda o valor de AGORA; sem isso, todos os botões abririam a última página.
            button.connect("clicked", lambda _b, n=name: self.show_section(n))
            dock.append(button)
            self.dock_buttons[name] = button
        dock.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.add_button = Gtk.Button(icon_name="list-add-symbolic")
        self.add_button.add_css_class("flat")
        self.add_button.add_css_class("dock-button")
        self.add_button.add_css_class("dock-add")
        self.add_button.set_tooltip_text("Adicionar pasta")
        set_accessible_label(self.add_button, "Adicionar pasta à biblioteca")
        self.add_button.connect("clicked", lambda _b: self.open_folders_dialog())
        dock.append(self.add_button)
        self.dock = dock

        # Barra de reprodução: sobre o conteúdo, abaixo do dock.
        self.playback_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.playback_bar.set_margin_top(6)
        self.playback_bar.set_margin_bottom(18)
        self.playback_bar.set_margin_start(18)
        self.playback_bar.set_margin_end(18)
        self.playback_bar.set_halign(Gtk.Align.CENTER)
        self.playback_bar.set_valign(Gtk.Align.END)
        self.playback_bar.set_visible(False)

        self.play_pause_button = Gtk.Button(icon_name="media-playback-pause-symbolic")
        self.play_pause_button.set_tooltip_text("Pausar")
        set_accessible_label(self.play_pause_button, "Pausar reprodução")
        self.play_pause_button.connect("clicked", lambda _b: self.toggle_playback())

        self.next_button = Gtk.Button(icon_name="media-skip-next-symbolic")
        self.next_button.set_tooltip_text("Próximo")
        set_accessible_label(self.next_button, "Reproduzir próximo vídeo")
        self.next_button.connect("clicked", lambda _b: self.next_in_playlist())

        self.stop_button = Gtk.Button(icon_name="media-playback-stop-symbolic")
        self.stop_button.set_tooltip_text("Parar")
        set_accessible_label(self.stop_button, "Parar reprodução")
        self.stop_button.connect("clicked", lambda _b: self.stop_playback())

        self.playback_bar.append(self.play_pause_button)
        self.playback_bar.append(self.next_button)
        self.playback_bar.append(self.stop_button)

        overlay = Gtk.Overlay()
        self.content_overlay = overlay
        overlay.set_child(self.stack)
        overlay.add_overlay(self.playback_bar)
        overlay.add_overlay(dock)

        view = Adw.ToolbarView()
        self.toolbar_view = view
        view.add_top_bar(header)
        view.set_content(overlay)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(view)
        self.set_content(self.toasts)
        self.show_section(self.section)

        self._auto_scan_source = GLib.idle_add(self.auto_scan)  # atualiza sozinho ao abrir

    # ---------- definições e manutenção ----------
    def open_settings_dialog(self) -> None:
        """Abre o painel de definições ancorado ao botão superior direito.

        Gtk.Popover é não-modal e fecha-se automaticamente quando o utilizador
        clica fora dele; assim a janela principal permanece visível e activa.
        """
        existing = getattr(self, "settings_popover", None)
        if existing is not None:
            if existing.get_visible():
                existing.popdown()
            else:
                existing.popup()
            return

        popover = Gtk.Popover()
        self.settings_popover = popover
        popover.add_css_class("settings-popover")
        set_accessible_label(popover, "Definições e manutenção da biblioteca")
        popover.set_autohide(True)
        popover.set_has_arrow(False)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_offset(0, 8)
        current_width = self.get_width() or self.get_default_size()[0] or 1000
        popover.set_size_request(min(440, max(320, current_width - 24)), -1)
        popover.set_focusable(True)
        popover.set_parent(self.settings_button)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(20)
        content.set_margin_end(20)

        intro = Gtk.Label(
            label="Gere a biblioteca, as capas, a integridade dos vídeos e as cópias de segurança.",
            xalign=0, wrap=True,
        )
        intro.add_css_class("dim-label")
        content.append(intro)

        def add_category(title: str, description: str, actions: list[tuple[str, str, str, object, Gtk.Widget | None]]) -> None:
            heading = Gtk.Label(label=title, xalign=0)
            heading.add_css_class("heading")
            content.append(heading)
            hint = Gtk.Label(label=description, xalign=0, wrap=True)
            hint.add_css_class("dim-label")
            content.append(hint)
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for label, icon, tooltip, callback, source_button in actions:
                button = Gtk.Button()
                button.set_halign(Gtk.Align.FILL)
                button.set_tooltip_text(tooltip)
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                row.set_margin_top(7)
                row.set_margin_bottom(7)
                row.set_margin_start(8)
                row.set_margin_end(8)
                image = Gtk.Image.new_from_icon_name(icon)
                image.set_pixel_size(18)
                text = Gtk.Label(label=label, xalign=0)
                text.set_hexpand(True)
                row.append(image)
                row.append(text)
                button.set_child(row)
                button.add_css_class("card")
                if source_button is not None:
                    button.set_sensitive(source_button.get_sensitive())
                button.connect("clicked", lambda _b, fn=callback: (popover.popdown(), fn()))
                group.append(button)
            content.append(group)

        add_category(
            "Biblioteca",
            "Operações de organização e actualização do conteúdo.",
            [
                ("Actualizar biblioteca", "view-refresh-symbolic", "Voltar a analisar as pastas de vídeo", self.start_scan, self.refresh_button),
                ("Buscar capas em falta", "insert-image-symbolic", "Procurar capas para títulos sem imagem", self.start_cover_batch, self.covers_button),
                ("Verificar ficheiros duplicados", "edit-copy-symbolic", "Identificar conteúdos idênticos sem os apagar", self.open_duplicates_dialog, None),
            ],
        )
        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        add_category(
            "Integridade e cópias de segurança",
            "Diagnóstico profundo e protecção dos dados da biblioteca.",
            [
                ("Verificação profunda da integridade", "security-high-symbolic", "Descodificar integralmente os vídeos para detectar erros", self.start_integrity_check, self.integrity_button),
                ("Backup e restauro", "document-save-symbolic", "Criar ou restaurar uma cópia da base de dados, configuração e capas", self.open_backup_menu, None),
            ],
        )

        close = Gtk.Button(label="Fechar")
        close.add_css_class("pill")
        close.set_halign(Gtk.Align.END)
        close.connect("clicked", lambda _b: popover.popdown())
        content.append(close)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(260)
        scroll.set_max_content_height(560)
        scroll.set_child(content)
        popover.set_child(scroll)
        popover.popup()

    # ---------- utilidades ----------
    def dialog_content_width(self, preferred: int) -> int:
        """Limita a largura dos diálogos à janela, preservando margens úteis."""
        current = self.get_width()
        if current <= 1:
            current = self.get_default_size()[0]
        if current <= 1:
            current = 1000
        return min(int(preferred), max(320, int(current) - 32))

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast.new(message))

    def open_backup_menu(self) -> None:
        if self.scanning or self.fetching_covers:
            self.toast("Aguarda que a actualização da biblioteca termine antes do backup.")
            return
        dialog = Adw.AlertDialog.new(
            "Backup e restauro",
            "Cria uma cópia da base de dados, configuração e capas geridas pelo Cine Prime. "
            "Os vídeos originais não são copiados. A configuração pode conter a chave OMDb; "
            "guarda o backup num local privado.",
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("restore", "Restaurar backup…")
        dialog.add_response("backup", "Criar backup…")
        dialog.set_default_response("backup")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("backup", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_backup_menu_response)
        dialog.present(self)

    def _on_backup_menu_response(self, _dialog, response: str) -> None:
        if response == "backup":
            self._choose_backup_file(Gtk.FileChooserAction.SAVE)
        elif response == "restore":
            self._choose_backup_file(Gtk.FileChooserAction.OPEN)

    def _choose_backup_file(self, action) -> None:
        title = "Guardar backup do Cine Prime" if action == Gtk.FileChooserAction.SAVE else "Escolher backup do Cine Prime"
        accept = "Guardar" if action == Gtk.FileChooserAction.SAVE else "Abrir"
        chooser = Gtk.FileChooserNative.new(title, self, action, accept, "Cancelar")
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Backups Cine Prime (*.cineprime, *.cineflow, *.gnometv)")
        file_filter.add_pattern("*.cineprime")
        file_filter.add_pattern("*.cineflow")  # compatibilidade com backups CineFlow
        file_filter.add_pattern("*.gnometv")  # compatibilidade com backups antigos
        chooser.add_filter(file_filter)
        if action == Gtk.FileChooserAction.SAVE:
            chooser.set_current_name(f"cineprime-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.cineprime")
        chooser.connect("response", self._on_backup_file_chosen, action)
        chooser.show()

    def _on_backup_file_chosen(self, chooser, response: int, action) -> None:
        if response != Gtk.ResponseType.ACCEPT:
            chooser.destroy()
            return
        selected = chooser.get_file()
        path = selected.get_path() if selected else None
        chooser.destroy()
        if not path:
            self.toast("Não foi possível obter o caminho escolhido.")
            return
        target = Path(path)
        if action == Gtk.FileChooserAction.SAVE:
            if target.suffix.lower() not in (".cineprime", ".cineflow", ".gnometv"):
                target = target.with_name(target.name + ".cineprime")
            if target.exists():
                dialog = Adw.AlertDialog.new(
                    "Substituir backup?",
                    f"O ficheiro {target.name} já existe. Queres substituí-lo?",
                )
                dialog.add_response("cancel", "Cancelar")
                dialog.add_response("overwrite", "Substituir")
                dialog.set_default_response("cancel")
                dialog.set_close_response("cancel")
                dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
                dialog.connect("response", self._on_backup_overwrite_confirmation, target)
                dialog.present(self)
            else:
                self._create_backup_at(target)
            return
        try:
            info = backup_manager.inspect_backup(target)
        except backup_manager.BackupError as error:
            self.toast(f"Backup inválido: {error}")
            return
        dialog = Adw.AlertDialog.new(
            "Confirmar restauro",
            f"Backup de {info.get('created_at', 'data desconhecida')}. "
            f"Contém {info['files']} ficheiros e {info['covers']} capas. "
            "A base de dados e a configuração actuais serão substituídas. "
            "Os vídeos não serão alterados. Continua?",
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("restore", "Restaurar")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_restore_confirmation, target)
        dialog.present(self)

    def _on_backup_overwrite_confirmation(self, _dialog, response: str, target: Path) -> None:
        if response == "overwrite":
            self._create_backup_at(target)

    def _create_backup_at(self, target: Path) -> None:
        try:
            result = backup_manager.create_backup(self.db.conn, target)
        except Exception as error:
            self.toast(f"Falha ao criar backup: {error}")
            return
        self.toast(f"Backup criado: {result['files']} ficheiros, {result['size'] / 1024:.0f} KiB.")

    def _on_restore_confirmation(self, _dialog, response: str, archive: Path) -> None:
        if response != "restore":
            return
        if self.session and self.session.running:
            self.toast("Pára a reprodução antes de restaurar um backup.")
            return
        if self.scanning or self.fetching_covers:
            self.toast("Aguarda que a actualização da biblioteca termine antes do restauro.")
            return
        try:
            result = backup_manager.restore_backup(archive, self.db.conn)
        except Exception as error:
            self.toast(f"Restauro falhou; os dados anteriores foram preservados: {error}")
            return
        self.reload()
        self.toast(f"Backup restaurado: {result['covers']} capas recuperadas.")

    def start_integrity_check(self) -> None:
        """Descodifica a biblioteca inteira em background, sem alterar os vídeos."""
        if self.integrity_running:
            self.toast("A verificação de integridade já está em execução.")
            return
        if self._integrity_confirmation is not None:
            return
        paths = [Path(path) for path in self.db.all_file_paths()]
        if not paths:
            self.toast("A biblioteca ainda não contém vídeos para verificar.")
            return
        if not shutil.which("ffmpeg"):
            self.toast("FFmpeg não está instalado; não foi possível iniciar o diagnóstico.")
            return

        total_bytes = 0
        for path in paths:
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
        size_gib = total_bytes / (1024 ** 3)
        dialog = Adw.AlertDialog.new(
            "Iniciar verificação profunda?",
            f"O Cine Prime vai descodificar integralmente áudio e vídeo de {len(paths)} ficheiros "
            f"({size_gib:.1f} GiB). Este processo pode demorar bastante e usar muita CPU. "
            "Não modifica os vídeos originais. Podes cancelar sem iniciar a análise.",
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("start", "Iniciar verificação")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
        self._integrity_confirmation = dialog
        dialog.connect("response", self._on_integrity_confirmation, paths)
        dialog.present(self)

    def _on_integrity_confirmation(self, _dialog, response: str, paths: list[Path]) -> None:
        self._integrity_confirmation = None
        if response == "start":
            self._run_integrity_check(paths)

    def _run_integrity_check(self, paths: list[Path]) -> None:
        self.integrity_running = True
        self.integrity_button.set_sensitive(False)
        self.toast(f"A verificar profundamente {len(paths)} vídeo(s). Pode demorar.")

        def worker():
            results = []
            for index, path in enumerate(paths, 1):
                try:
                    result = check_media_integrity(path)
                except Exception as error:  # noqa: BLE001 - um ficheiro não pode abortar o lote
                    result = IntegrityResult(path, False, f"Erro inesperado: {error}")
                results.append(result)
                if index == len(paths) or index % 5 == 0:
                    self.call_main(self.integrity_progress, index, len(paths))
            self.call_main(self.finish_integrity_check, results)

        if self._start_worker(worker) is None:
            self.integrity_running = False
            self.integrity_button.set_sensitive(True)

    def integrity_progress(self, current: int, total: int) -> bool:
        self.toast(f"A verificar integridade: {current}/{total} vídeos…")
        return GLib.SOURCE_REMOVE

    def finish_integrity_check(self, results: list[IntegrityResult]) -> bool:
        self.integrity_running = False
        self.integrity_button.set_sensitive(True)
        failures = [result for result in results if not result.ok]

        dialog = Adw.Dialog()
        dialog.set_title("Diagnóstico de integridade")
        dialog.set_content_width(self.dialog_content_width(680))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(20)
        content.set_margin_end(20)

        if failures:
            heading = Gtk.Label(
                label=f"{len(results) - len(failures)} de {len(results)} vídeos descodificados sem erros. "
                      f"{len(failures)} requerem atenção.", xalign=0, wrap=True
            )
            heading.add_css_class("heading")
            content.append(heading)
            for result in failures:
                label = Gtk.Label(
                    label=f"{result.path}\n{result.detail or 'Descodificação incompleta.'}",
                    xalign=0, wrap=True, selectable=True
                )
                content.append(label)
                content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        else:
            status = Adw.StatusPage(
                title="Todos os vídeos passaram",
                description=f"{len(results)} ficheiros foram descodificados integralmente sem erros reportados pelo FFmpeg.",
                icon_name="emblem-ok-symbolic",
            )
            content.append(status)

        note = Gtk.Label(
            label="A verificação é somente de leitura. Não repara nem modifica os vídeos. "
                  "Alguns formatos podem permitir recuperação de erros sem reportar corrupção.",
            wrap=True, xalign=0
        )
        note.add_css_class("dim-label")
        content.append(note)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(content)
        scroll.set_min_content_height(260)
        scroll.set_max_content_height(620)
        close = Gtk.Button(label="Fechar")
        close.add_css_class("suggested-action")
        close.add_css_class("pill")
        close.set_halign(Gtk.Align.END)
        close.connect("clicked", lambda _button: dialog.close())
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        wrapper.append(scroll)
        wrapper.append(close)
        dialog.set_child(wrapper)
        dialog.present(self)
        return GLib.SOURCE_REMOVE

    def open_duplicates_dialog(self) -> Adw.Dialog:
        """Mostra ficheiros com conteúdo idêntico sem alterar a biblioteca."""
        groups = self.db.duplicate_groups()
        dialog = Adw.Dialog()
        dialog.set_title("Ficheiros duplicados")
        dialog.set_content_width(self.dialog_content_width(680))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(20)
        content.set_margin_end(20)

        if not groups:
            empty = Adw.StatusPage(
                title="Nenhum duplicado encontrado",
                description="A biblioteca não contém ficheiros com conteúdo idêntico.",
                icon_name="emblem-ok-symbolic",
            )
            content.append(empty)
        else:
            total = sum(len(group["files"]) for group in groups)
            intro = Gtk.Label(
                label=f"Foram encontrados {len(groups)} grupos, envolvendo {total} ficheiros. "
                      "A análise compara o conteúdo; não remove nem junta registos.",
                wrap=True,
                xalign=0,
            )
            intro.add_css_class("dim-label")
            content.append(intro)

            for index, group in enumerate(groups, 1):
                files = group["files"]
                heading = Gtk.Label(
                    label=f"Grupo {index} · {len(files)} cópias",
                    xalign=0,
                )
                heading.add_css_class("heading")
                content.append(heading)

                for row in files:
                    title = row["display_name"] or row["name"]
                    if row["year"]:
                        title += f" ({row['year']})"
                    if row["kind"] == "series":
                        code = f"S{row['season'] or 1:02d}E{row['episode']:02d}" if row["episode"] else "Episódio"
                        title += f" · {code}"
                    label = Gtk.Label(
                        label=f"{title}\n{row['path']}",
                        xalign=0,
                        wrap=True,
                        selectable=True,
                    )
                    label.set_margin_start(12)
                    label.set_margin_bottom(6)
                    content.append(label)

                if index < len(groups):
                    content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(content)
        scroll.set_min_content_height(260)
        scroll.set_max_content_height(620)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        close = Gtk.Button(label="Fechar")
        close.add_css_class("suggested-action")
        close.add_css_class("pill")
        close.set_halign(Gtk.Align.END)
        close.set_margin_top(8)
        close.connect("clicked", lambda _b: dialog.close())
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        wrapper.append(scroll)
        wrapper.append(close)
        dialog.set_child(wrapper)
        dialog.present(self)
        return dialog

    def reload(self) -> None:
        """Invalida as páginas e reconstrói apenas a que está actualmente visível."""
        self._dirty_pages.update(name for name, _label, _icon in PAGES)
        self._dirty_pages.add(SEARCH_PAGE)
        visible = self.stack.get_visible_child_name()
        if self.query and visible == SEARCH_PAGE:
            self._refresh_page(SEARCH_PAGE)
        elif visible in self.pages and visible != SEARCH_PAGE:
            self._refresh_page(visible)
        elif not self.query:
            self._refresh_page(self.section)

    def _refresh_page(self, name: str) -> None:
        """Reconstrói uma página específica e limpa o respectivo estado dirty."""
        if name == "home":
            self.pages[name].set_child(self.build_home_page())
        elif name in ("movie", "series"):
            self.pages[name].set_child(self.build_page(name))
        elif name == "favorites":
            self.pages[name].set_child(self.build_favorites_page())
        elif name == SEARCH_PAGE:
            self.pages[name].set_child(self.build_search_page())
        self._dirty_pages.discard(name)

    def _ensure_page_current(self, name: str) -> None:
        if name in self._dirty_pages:
            self._refresh_page(name)

    # ---------- navegação e busca ----------
    def show_section(self, kind: str) -> None:
        """Abre uma página do dock. Escolher uma página encerra a busca."""
        self.section = kind
        self.search_entry.set_text("")
        self.apply_search()

    def set_active_dock(self, name: str | None) -> None:
        """Pinta de vermelho o ícone da página atual (None = nenhum)."""
        for key, button in self.dock_buttons.items():
            if key == name:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")

    def apply_search(self) -> None:
        if self._closing_window:
            return
        self.query = posters.normalize(self.search_entry.get_text())
        if self.query:
            self.set_active_dock(None)           # os resultados vêm de todas as seções
            self.pages[SEARCH_PAGE].set_child(self.build_search_page())
            self._dirty_pages.discard(SEARCH_PAGE)
            self.stack.set_visible_child_name(SEARCH_PAGE)
        else:
            self._ensure_page_current(self.section)
            self.set_active_dock(self.section)
            self.stack.set_visible_child_name(self.section)

    def build_search_page(self) -> Gtk.Widget:
        # normalize() tira acentos, maiúsculas e pontuação: "spider-man" acha "Spider Man".
        rows = [r for r in self.db.list_titles() if self.query in posters.normalize(r["shown"])]
        if not rows:
            return Adw.StatusPage(
                title="Nada encontrado",
                icon_name="system-search-symbolic",
                description="Tente outro nome.",
            )
        return self.make_grid(rows)

    def on_close(self, _window) -> bool:
        # Fecha a pipeline GStreamer antes de destruir a superfície GTK4.
        self._closing_window = True
        if self._auto_scan_source:
            GLib.source_remove(self._auto_scan_source)
            self._auto_scan_source = 0
        # O watchdog da interface é independente do timer GStreamer.
        if self._controls_timeout:
            GLib.source_remove(self._controls_timeout)
            self._controls_timeout = 0
        if self.session:
            # close() encerra a pipeline e persiste o progresso uma única vez.
            self.session.close()
            self.session = None
        # Um worker pode ainda estar a gravar uma capa nesta pasta. Só a
        # removemos depois de todos os workers terminarem, nunca a meio de um
        # download ou de uma escrita.
        with self._worker_lock:
            tmp_dir = self.tmp_dir if self._active_workers == 0 else None
            if tmp_dir is not None:
                self.tmp_dir = None
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return False                          # False = pode fechar

    # ---------- grade ----------
    def build_page(self, kind: str) -> Gtk.Widget:
        rows = self.db.list_titles(kind)
        if not rows:
            page = Adw.StatusPage(
                title="Nada por aqui ainda", icon_name="video-x-generic-symbolic"
            )
            if config.get_folders():
                page.set_description(
                    "Nenhum título nesta categoria. Confira o tipo das pastas ou atualize a biblioteca."
                )
            else:
                page.set_description("Adicione a pasta onde estão seus vídeos.")
                button = Gtk.Button(label="Adicionar pasta…")
                button.add_css_class("suggested-action")
                button.add_css_class("pill")
                button.set_halign(Gtk.Align.CENTER)
                button.connect("clicked", self.choose_folder)
                page.set_child(button)
            return page
        return self.make_grid(rows)

    def refresh_favorites(self) -> None:
        if self.stack.get_visible_child_name() == "favorites":
            self._refresh_page("favorites")
        else:
            self._dirty_pages.add("favorites")

    def build_favorites_page(self) -> Gtk.Widget:
        rows = self.db.list_titles(favorites_only=True)
        if rows:
            return self.make_grid(rows)
        return message_page("Nenhum favorito ainda",
                            "Toque no coração de um título para ele aparecer aqui.",
                            "emblem-favorite-symbolic")

    def toggle_favorite(self, title_id: int, favorite: bool) -> None:
        self.db.set_favorite(title_id, favorite)
        self.sync_hearts(title_id, favorite)     # corações já na tela mudam no lugar (sem refazer a página)
        self.refresh_favorites()

    def sync_hearts(self, title_id: int, favorite: bool) -> None:
        for page in self.pages.values():
            # Actualiza também os dados-base dos itens ainda não materializados.
            # Assim, um cartão criado mais tarde pelo GridView já nasce correcto.
            stack = [page]
            while stack:
                widget = stack.pop()
                if isinstance(widget, Gtk.GridView):
                    row = getattr(widget, "_cineprime_rows", {}).get(title_id)
                    if row is not None:
                        row["favorite"] = int(favorite)
                if hasattr(widget, "get_first_child"):
                    child = widget.get_first_child()
                    while child:
                        stack.append(child)
                        child = child.get_next_sibling()
            for card in iter_cards(page):
                if getattr(card, "title_id", None) == title_id and hasattr(card, "heart"):
                    set_heart(card.heart, favorite)

    def refresh_home(self) -> None:
        if self.stack.get_visible_child_name() == "home":
            self._refresh_page("home")
        else:
            self._dirty_pages.add("home")

    def build_home_page(self) -> Gtk.Widget:
        """Início: o que você começou e não terminou, filmes e séries misturados."""
        items = self.db.continue_watching()
        if items:
            return self.make_grid([i.title for i in items], {i.title["id"]: i for i in items})
        if self.db.list_titles():
            return message_page("Nada para continuar",
                             "O que você começar a ver e parar no meio aparece aqui.",
                             "document-open-recent-symbolic")
        return message_page("Sua biblioteca está vazia",
                         "Clique no + do dock para adicionar uma pasta com filmes ou séries.",
                         "video-x-generic-symbolic")

    def make_grid(self, rows, items=None) -> Gtk.Widget:
        """Grade virtualizada: só cria cartões para os itens próximos da área visível.

        O modelo mantém apenas os IDs e os dados da consulta. O Gtk.GridView reutiliza
        os widgets ao percorrer a biblioteca, evitando milhares de cartões GTK em RAM.
        """
        items = items or {}
        rows_by_id = {int(row["id"]): dict(row) for row in rows}
        items_by_id = dict(items)
        model = Gtk.StringList.new([str(row["id"]) for row in rows])
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, list_item):
            # Um filho simples evita criar capas/widgets antes de o item ser necessário.
            placeholder = Gtk.Box()
            placeholder.set_size_request(CARD_W + 16, CARD_H + 80)
            list_item.set_child(placeholder)

        def bind(_factory, list_item):
            obj = list_item.get_item()
            if obj is None:
                return
            title_id = int(obj.get_string())
            row = rows_by_id.get(title_id)
            if row is None:
                list_item.set_child(Gtk.Box())
                return
            card = build_card(row, items_by_id.get(title_id), self.toggle_favorite)
            card.title_id = title_id
            list_item.set_child(card)

        def unbind(_factory, list_item):
            # Liberta widgets, capas e callbacks do item que saiu da zona virtualizada.
            list_item.set_child(None)

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)

        grid = Gtk.GridView.new(Gtk.NoSelection.new(model), factory)
        grid.set_valign(Gtk.Align.START)
        grid.set_min_columns(2)
        grid.set_max_columns(12)
        grid.set_single_click_activate(True)
        grid.set_enable_rubberband(False)
        grid.set_margin_top(18)
        grid.set_margin_bottom(100)             # espaço para o dock não cobrir a última fileira
        grid.set_margin_start(18)
        grid.set_margin_end(18)
        grid.connect("activate", self.on_card_activated)
        # Manter referências explícitas enquanto a vista existir; os sinais da factory
        # são os donos do ciclo de vida dos cartões, não o modelo de dados.
        grid._cineprime_rows = rows_by_id
        grid._cineprime_items = items_by_id

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(grid)
        return scroll

    def on_card_activated(self, grid, position: int) -> None:
        obj = grid.get_model().get_item(position)
        if obj is not None:
            self.open_detail(int(obj.get_string()))

    # ---------- pop-up de detalhe ----------
    def open_detail(self, title_id: int) -> None:
        row = self.db.get_title(title_id)
        resume = self.db.resume_plan(title_id)
        if row is None or resume is None:
            return
        shown = row["display_name"] or row["name"]
        is_series = row["kind"] == "series"

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(6)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        box.append(build_cover(row, 170, 255))

        title = Gtk.Label(label=shown, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-2")
        box.append(title)

        parts = ["Série" if is_series else "Filme"]
        if row["year"]:
            parts.append(str(row["year"]))
        if is_series:
            parts.append(f"{len(self.db.files_for_title(title_id))} episódio(s)")
        meta = Gtk.Label(label=" · ".join(parts))
        meta.add_css_class("dim-label")
        box.append(meta)

        # Onde você parou
        if is_series and resume.season is not None and resume.episode is not None:
            prefix = "Você parou em" if resume.start > 0 else "Próximo"
            where = Gtk.Label(label=f"{prefix}: T{resume.season} · E{resume.episode}")
            box.append(where)
        if resume.start > 0 and resume.duration:
            bar = Gtk.ProgressBar(fraction=min(resume.start / resume.duration, 1.0))
            box.append(bar)
            stopped = Gtk.Label(
                label=f"Parou em {fmt_time(resume.start)} de {fmt_time(resume.duration)}"
            )
            stopped.add_css_class("caption")
            stopped.add_css_class("dim-label")
            box.append(stopped)

        if resume.start > 0:
            play_label = f"Continuar de {fmt_time(resume.start)}"
        elif resume.completed:
            play_label = "Ver de novo"
        else:
            play_label = "Ver"

        dialog = Adw.Dialog()
        dialog.set_title(shown)
        dialog.set_content_width(self.dialog_content_width(380))

        def on_play(_button):
            dialog.close()
            self.play(resume, title=shown, episode_list=self.db.episodes_for_title(title_id))

        def on_restart(_button):
            dialog.close()
            self.play(resume, from_start=True, title=shown, episode_list=self.db.episodes_for_title(title_id))

        def on_edit(_button):
            dialog.close()
            self.open_edit_dialog(title_id)

        play = Gtk.Button(label=play_label)
        play.add_css_class("suggested-action")
        play.add_css_class("pill")
        play.connect("clicked", on_play)
        box.append(play)

        # Botões secundários numa linha só (homogeneous = todos com a mesma largura)
        secondary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        secondary.set_homogeneous(True)
        if resume.start > 0:
            restart = Gtk.Button(label="Recomeçar")
            restart.add_css_class("pill")
            restart.connect("clicked", on_restart)
            secondary.append(restart)

        # Ícone de lápis no canto superior direito da barra do pop-up.
        # tooltip_text = texto que aparece ao passar o mouse por cima.
        edit = Gtk.Button(icon_name="document-edit-symbolic")
        edit.set_tooltip_text("Editar nome e capa")
        set_accessible_label(edit, "Editar nome e capa")
        edit.set_size_request(40, 40)
        edit.connect("clicked", on_edit)
        favorite = Gtk.Button(label=HEART)
        favorite.add_css_class("heart-header")
        set_heart(favorite, bool(row["favorite"]))

        def on_favorite(button):
            new_state = not button.has_css_class("fav-on")
            self.toggle_favorite(title_id, new_state)
            set_heart(button, new_state)

        favorite.connect("clicked", on_favorite)
        header = Adw.HeaderBar()
        header.pack_end(edit)
        header.pack_end(favorite)

        # Adw.NavigationView = pilha de páginas dentro do pop-up. A 1ª é a que
        # já existia; "Ver episódios" empilha a 2ª, com botão de voltar automático.
        nav = Adw.NavigationView()
        if is_series:
            def on_episodes(_button):
                nav.push(self.build_episodes_page(title_id, shown, dialog))

            episodes_button = Gtk.Button(label="Episódios")
            episodes_button.add_css_class("pill")
            episodes_button.connect("clicked", on_episodes)
            secondary.append(episodes_button)
        if secondary.get_first_child() is not None:   # só mostra a linha se tiver botão
            box.append(secondary)

        # Rolável: em telas baixas (ex.: 768 px) o pop-up não cabe inteiro.
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(box)
        scroll.set_propagate_natural_height(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scroll)
        nav.push(Adw.NavigationPage.new(toolbar, shown))
        dialog.set_child(nav)
        dialog.present(self)

    def build_episodes_page(self, title_id: int, shown: str, dialog: Adw.Dialog) -> Adw.NavigationPage:
        """Lista de episódios agrupada por temporada, com o progresso de cada um."""
        episodes = self.db.episodes_for_title(title_id)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)

        def make_handler(index: int):
            # Função fábrica: fixa o `index` deste episódio (o mesmo cuidado
            # com variáveis de laço que tivemos com o lambda do clique direito).
            def on_activated(_row):
                dialog.close()
                self.play(resume_from(episodes, index), title=shown, episode_list=episodes, episode_index=index)
            return on_activated

        listbox = None
        current_season = object()          # valor que nunca é igual a uma temporada real
        for index, ep in enumerate(episodes):
            if listbox is None or ep.season != current_season:
                current_season = ep.season
                heading = Gtk.Label(
                    label=f"Temporada {ep.season}" if ep.season else "Episódios", xalign=0
                )
                heading.add_css_class("heading")
                heading.set_margin_top(12 if listbox is not None else 0)
                listbox = Gtk.ListBox()
                listbox.set_selection_mode(Gtk.SelectionMode.NONE)
                listbox.add_css_class("boxed-list")
                box.append(heading)
                box.append(listbox)

            title = f"Episódio {ep.episode}" if ep.episode else Path(ep.path).stem
            if ep.finished:
                status = "Assistido"
            elif ep.resume_at > 0:
                status = f"Parou em {fmt_time(ep.resume_at)} de {fmt_time(ep.duration)}"
            else:
                status = ""
            row = Adw.ActionRow(title=title, subtitle=status)
            row.set_use_markup(False)       # nomes de arquivo podem ter & ou <
            row.set_activatable(True)
            if ep.finished:
                row.add_suffix(Gtk.Image.new_from_icon_name("object-select-symbolic"))
            row.add_suffix(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
            row.connect("activated", make_handler(index))
            listbox.append(row)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(box)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(480)  # passou disso, aparece a barra de rolagem
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroll)
        return Adw.NavigationPage.new(toolbar, "Episódios")

    # ---------- tocar ----------
    def play(self, resume: Resume, from_start: bool = False, title: str | None = None,
             episode_list=None, episode_index: int | None = None) -> None:
        if self.session is not None:
            # stop() termina a pipeline imediatamente, mas on_exit é entregue
            # pelo loop GTK em idle. Se o utilizador iniciar outro vídeo nesse
            # intervalo, limpa a sessão já encerrada antes de avaliar o novo play.
            if self.session.running:
                self.toast("Já existe uma reprodução em andamento.")
                return
            self.on_session_end()
        if not resume.paths or not Path(resume.paths[0]).is_file():
            self.toast("Arquivo não encontrado. Atualize a biblioteca.")
            return

        start = 0.0 if from_start else resume.start
        shown = title or Path(resume.paths[0]).stem
        playback_paths = [ep.path for ep in episode_list] if episode_list else list(resume.paths)
        if episode_list and episode_index is None:
            episode_index = next((i for i, ep in enumerate(episode_list) if ep.path == resume.paths[0]), 0)
        playback_index = max(0, int(episode_index or 0))
        self._last_pointer_x = None
        self._last_pointer_y = None
        # Cada callback pertence à sessão que o criou. Um on_exit antigo pode
        # chegar ao loop GTK depois de uma nova reprodução já ter começado.
        session_ref = {}
        try:
            session = EmbeddedGStreamerSession(
                playback_paths,
                start,
                on_save=self.save_progress,
                on_exit=lambda: self.on_session_end(session_ref.get("session")),
                on_state=self.update_playback_buttons,
                on_progress=self.update_player_progress,
                on_tracks=self.update_track_menus,
                on_error=lambda message: self.on_player_error(message, session_ref.get("session")),
                start_index=playback_index,
            )
            session_ref["session"] = session
            self.session = session
        except Exception as exc:
            self.session = None
            self.toast(f"Não foi possível iniciar o reprodutor integrado: {exc}")
            return
        # Apresenta primeiro a superfície de vídeo. O GStreamer já pode estar
        # a reproduzir desde o construtor; não deixamos a imagem ficar escondida
        # atrás da biblioteca enquanto os controlos são preparados.
        self._player_episode_paths = list(playback_paths)
        self._player_episode_entries = list(episode_list or [])
        self._player_series_title = shown
        self.player_view = Gtk.Overlay()
        self.player_view.add_css_class("player-surface")
        self.player_view.set_child(self.session)
        self.content_overlay.add_overlay(self.player_view)
        self.player_view.set_visible(True)
        self.header.set_visible(False)
        self.dock.set_visible(False)
        self.playback_bar.set_visible(False)
        self.fullscreen()
        self._is_fullscreen = True

        try:
            self._subtitle_delay_seconds = self.db.subtitle_delay_for(playback_paths[playback_index])
            self.session.set_subtitle_delay(self._subtitle_delay_seconds)
            self._subtitle_font_size = config.get_subtitle_font_size()
            self.session.set_subtitle_font_size(self._subtitle_font_size)

            # Um clique diretamente no vídeo alterna pausa/reprodução. Os controlos
            # GTK sobrepostos continuam a receber os seus próprios cliques.
            video_click = Gtk.GestureClick()
            video_click.set_button(1)
            video_click.connect("released", self._on_video_tap)
            self.session.add_controller(video_click)
            self._video_click_controller = video_click
            self.build_player_controls(self._player_episode_heading(shown, resume))
            self.show_player_controls()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.toast(f"Falha ao preparar o leitor: {type(exc).__name__}: {exc}")
            failed_session = self.session
            if failed_session is not None:
                failed_session.stop()
            self.on_session_end(failed_session)

    @staticmethod
    def _player_episode_heading(series_title: str, resume: Resume) -> str:
        """Identificação curta da série e do episódio atual para o topo do vídeo."""
        if resume.season is None and resume.episode is None:
            return series_title
        stem = Path(resume.paths[0]).stem if resume.paths else ""
        episode_name = re.sub(
            r"(?i)^(?:s\d{1,2}[ ._-]*e\d{1,3}|\d{1,2}x\d{1,3}|ep\d{1,3})[ ._-]*",
            "", stem,
        ).replace("_", " ").replace(".", " ").strip(" -_")
        episode_name = re.sub(r"\s+", " ", episode_name)
        code = f"S{resume.season}:E{resume.episode}" if resume.season is not None else f"E{resume.episode}"
        if episode_name:
            return f'{series_title} · {code} "{episode_name}"'
        return f"{series_title} · {code}"

    def _on_video_tap(self, _gesture, _presses, _x, _y) -> None:
        if self.session and self.session.running:
            self.toggle_playback()
            self.show_player_controls()

    def save_progress(self, entries: dict) -> None:
        for path, (position, duration) in entries.items():
            self.db.save_progress(path, position, duration)
        self.refresh_home()                      # actualiza já se o Início estiver visível

    # ---------- atalhos de teclado ----------
    def is_typing(self) -> bool:
        """True se o foco está num campo de texto (busca, nome na edição, chave da API...).
        Aí as teclas são texto e não podem virar atalhos."""
        return isinstance(self.get_focus(), Gtk.Editable)

    def on_key_pressed(self, controller: Gtk.EventControllerKey, keyval: int, keycode: int, state) -> bool:
        """Atalhos: F = busca, espaço = play/pause, N = próximo, S = parar (maiúscula ou minúscula)."""
        if self.get_visible_dialog() is not None:
            return False                            # diálogo aberto: não são atalhos
        settings_popover = getattr(self, "settings_popover", None)
        if settings_popover is not None and settings_popover.get_visible():
            if keyval == Gdk.KEY_Escape:
                settings_popover.popdown()
                self.settings_button.grab_focus()
                return True
            return False                            # o popover conserva Tab e os atalhos nativos
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.META_MASK):
            return False                            # Ctrl, Alt e Meta continuam livres
        if self.is_typing():
            return False                            # não roubar texto de campos editáveis
        if self.session and self.session.running:
            key = Gdk.keyval_to_lower(keyval)
            if key == Gdk.KEY_Escape:
                self.stop_playback()
                return True
            if key == Gdk.KEY_Left:
                self.session.seek(-10)
                self.show_player_controls()
                return True
            if key == Gdk.KEY_Right:
                self.session.seek(10)
                self.show_player_controls()
                return True
            if key == Gdk.KEY_Up:
                self.volume_scale.set_value(min(150, self.volume_scale.get_value() + 5))
                self.show_player_controls()
                return True
            if key == Gdk.KEY_Down:
                self.volume_scale.set_value(max(0, self.volume_scale.get_value() - 5))
                self.show_player_controls()
                return True
            # Atalhos de acesso direto aos menus do leitor.
            menu_shortcuts = {
                Gdk.KEY_l: getattr(self, "subtitle_menu", None),
                Gdk.KEY_e: getattr(self, "episodes_menu", None),
                Gdk.KEY_a: getattr(self, "audio_menu", None),
                Gdk.KEY_d: getattr(self, "settings_menu", None),
            }
            menu = menu_shortcuts.get(key)
            if menu is not None:
                if menu.get_sensitive():
                    self.show_player_controls()
                    popover = menu.get_popover()
                    if popover is not None and popover.get_visible():
                        popover.popdown()
                    else:
                        menu.set_active(True)
                return True
        key = Gdk.keyval_to_lower(keyval)
        if key == Gdk.KEY_f:
            if self.session and self.session.running:
                self.toggle_player_fullscreen()
            else:
                self.toggle_search()
            return True
        if not (self.session and self.session.running):
            return False                            # sem reprodução, espaço/N/S ficam livres
        if key == Gdk.KEY_space:
            self._close_player_popovers()
            self.toggle_playback()
            return True
        if key == Gdk.KEY_n:
            self.next_in_playlist()
            return True
        if key == Gdk.KEY_s:
            self.stop_playback()
            return True
        return False

    # ---------- reprodução integrada: vídeo, controlos e menus ----------
    def build_player_controls(self, title: str) -> None:
        self.player_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.player_top.add_css_class("player-chrome")
        self.player_top.set_halign(Gtk.Align.FILL)
        self.player_top.set_valign(Gtk.Align.START)
        self.player_top.set_margin_top(8)
        self.player_top.set_margin_start(12)
        self.player_top.set_margin_end(12)

        back = Gtk.Button(icon_name="go-previous-symbolic")
        back.set_tooltip_text("Voltar à biblioteca (Esc)")
        set_accessible_label(back, "Voltar à biblioteca e parar a reprodução")
        back.connect("clicked", lambda _b: self.stop_playback())
        back.set_size_request(40, 40)
        self.player_top.append(back)
        title_label = Gtk.Label(label=title, xalign=0.5)
        title_label.add_css_class("player-title")
        title_label.set_ellipsize(3)
        title_label.set_hexpand(True)
        title_label.set_halign(Gtk.Align.CENTER)
        self.player_top.append(title_label)
        self.player_title_label = title_label

        self.player_bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.player_bottom.add_css_class("player-bottom")
        self.player_bottom.set_halign(Gtk.Align.FILL)
        self.player_bottom.set_valign(Gtk.Align.END)
        self.player_bottom.set_margin_start(12)
        self.player_bottom.set_margin_end(12)
        self.player_bottom.set_margin_bottom(8)

        progress_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.current_time_label = Gtk.Label(label="0:00")
        self.current_time_label.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], ["Tempo decorrido da reprodução"]
        )
        self.current_time_label.add_css_class("player-time")
        self.progress_bar = PlayerProgressBar(self._seek_player_to)
        self.progress_bar.set_hexpand(True)
        # Alias temporário para manter compatibilidade com testes e pontos antigos.
        self.progress_scale = self.progress_bar
        self.total_time_label = Gtk.Label(label="0:00")
        self.total_time_label.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], ["Duração total do vídeo"]
        )
        self.total_time_label.add_css_class("player-time")
        progress_row.append(self.current_time_label)
        progress_row.append(self.progress_scale)
        progress_row.append(self.total_time_label)
        self.player_bottom.append(progress_row)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        controls.set_halign(Gtk.Align.FILL)
        controls.set_valign(Gtk.Align.CENTER)

        # Grupo esquerdo: ações de reprodução, como na interface Netflix.
        left_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.play_pause_button = Gtk.Button(icon_name="media-playback-pause-symbolic")
        self.play_pause_button.set_tooltip_text("Pausar")
        set_accessible_label(self.play_pause_button, "Pausar ou retomar reprodução")
        self.play_pause_button.connect("clicked", lambda _b: self.toggle_playback())
        self.next_button = Gtk.Button(icon_name="media-skip-forward-symbolic")
        self.next_button.set_tooltip_text("Próximo episódio")
        set_accessible_label(self.next_button, "Reproduzir próximo episódio")
        self.next_button.connect("clicked", lambda _b: self.next_in_playlist())
        left_controls.append(self.play_pause_button)
        left_controls.append(self.next_button)
        controls.append(left_controls)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        controls.append(spacer)

        # Grupo direito: idioma, legendas, episódios e opções de visualização.
        right_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.audio_menu, self.audio_menu_box = self.make_track_menu("Áudio", "audio-speakers-symbolic")
        self.subtitle_menu, self.subtitle_menu_box = self.make_track_menu("Legendas", "media-view-subtitles-symbolic")
        self._subtitle_delay_seconds = getattr(self, "_subtitle_delay_seconds", 0.0)
        self.episodes_menu, self.episodes_menu_box = self.make_track_menu("Episódios", "view-list-symbolic")
        right_controls.append(self.audio_menu)
        right_controls.append(self.subtitle_menu)
        right_controls.append(self.episodes_menu)

        self.settings_menu, settings_box = self.make_track_menu("Definições", "emblem-system-symbolic")
        speed_title = Gtk.Label(label="VELOCIDADE")
        speed_title.add_css_class("heading")
        settings_box.append(speed_title)
        self.speed_menu, speed_box = self.make_track_menu("1×")
        for speed in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            label = f"{speed:g}×"
            item = Gtk.Button(label=label)
            item.connect("clicked", lambda _b, value=speed, text=label: self.set_player_speed(value, text))
            speed_box.append(item)
        settings_box.append(self.speed_menu)
        notice_title = Gtk.Label(label="AVISO DO PRÓXIMO EPISÓDIO")
        notice_title.add_css_class("heading")
        notice_title.set_margin_top(8)
        settings_box.append(notice_title)
        self.next_notice_menu, notice_box = self.make_track_menu(
            f"{config.get_next_episode_notice_seconds()} segundos"
        )
        for seconds in (30, 60, 90, 120, 240, 360):
            item = Gtk.Button(label=f"{seconds} segundos")
            item.connect("clicked", lambda _b, value=seconds: self.set_next_episode_notice(value))
            notice_box.append(item)
        settings_box.append(self.next_notice_menu)
        self.mark_credits_button = Gtk.Button(label="Marcar início dos créditos neste ponto")
        self.mark_credits_button.connect("clicked", lambda _b: self._mark_credits_start())
        settings_box.append(self.mark_credits_button)
        self._populate_episode_menu()
        self.player_top.append(self.settings_menu)

        fullscreen = Gtk.Button(icon_name="view-fullscreen-symbolic")
        fullscreen.set_tooltip_text("Sair/entrar no ecrã inteiro")
        set_accessible_label(fullscreen, "Alternar ecrã inteiro")
        fullscreen.connect("clicked", lambda _b: self.toggle_player_fullscreen())
        self.player_top.append(fullscreen)
        controls.append(right_controls)
        self.player_bottom.append(controls)

        # Um único contentor controla a visibilidade de TODOS os elementos da
        # interface. A barra nunca pode ficar fora do grupo que é ocultado.
        self.player_chrome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.player_chrome.set_halign(Gtk.Align.FILL)
        self.player_chrome.set_valign(Gtk.Align.FILL)
        self.player_chrome.set_hexpand(True)
        self.player_chrome.set_vexpand(True)
        middle = Gtk.Overlay()
        middle.set_vexpand(True)
        middle_spacer = Gtk.Box()
        middle_spacer.set_vexpand(True)
        middle.set_child(middle_spacer)

        # Volume vertical, isolado na lateral direita para libertar a faixa inferior.
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, 0, 150, 1)
        self.volume_scale.set_draw_value(False)
        set_accessible_label(self.volume_scale, "Volume de reprodução")
        self.volume_scale.set_value(100)
        self.volume_scale.set_inverted(True)
        self.volume_scale.set_size_request(34, 124)
        self.volume_scale.add_css_class("player-volume")
        self.volume_scale.connect("value-changed", self.on_player_volume)
        volume_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        volume_panel.add_css_class("player-volume-panel")
        volume_panel.set_halign(Gtk.Align.END)
        volume_panel.set_valign(Gtk.Align.CENTER)
        volume_panel.set_margin_end(24)
        volume_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        volume_panel.append(volume_icon)
        volume_panel.append(self.volume_scale)
        middle.add_overlay(volume_panel)

        prompt_stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        prompt_stack.set_halign(Gtk.Align.END)
        prompt_stack.set_valign(Gtk.Align.END)
        prompt_stack.set_margin_end(28)
        prompt_stack.set_margin_bottom(24)
        self.skip_credits_button = Gtk.Button(label="Saltar créditos  ›")
        self.skip_credits_button.add_css_class("skip-credits-prompt")
        self.skip_credits_button.set_visible(False)
        self.skip_credits_button.connect("clicked", lambda _b: self._skip_credits())
        self.next_episode_button = Gtk.Button(label="▶  Próximo episódio")
        self.next_episode_button.add_css_class("next-episode-prompt")
        self.next_episode_button.set_visible(False)
        self.next_episode_button.connect("clicked", lambda _b: self._play_next_episode())
        prompt_stack.append(self.skip_credits_button)
        prompt_stack.append(self.next_episode_button)
        middle.add_overlay(prompt_stack)
        self.player_chrome.append(self.player_top)
        self.player_chrome.append(middle)
        self.player_chrome.append(self.player_bottom)
        self.player_view.add_overlay(self.player_chrome)
        # O controlador pertence ao Overlay raiz, não ao GLArea. Assim, recebe
        # movimento sobre o vídeo E sobre os controlos GTK, sem depender do widget
        # que está por baixo do ponteiro nem do hit-testing do OpenGL.
        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self._on_player_pointer_motion)
        motion.connect("enter", self._on_player_pointer_motion)
        motion.connect("leave", self._on_player_pointer_leave)
        self.player_view.add_controller(motion)
        self._player_motion_controller = motion
        self._player_motion_source = self.player_view
        self._player_debug_log("controller-attached", f"source=player_view mapped={self.player_view.get_mapped()}")

    def make_track_menu(self, label: str, icon_name: str | None = None):
        button = Gtk.MenuButton()
        button.set_tooltip_text(label)
        set_accessible_label(button, label)
        button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
        if icon_name:
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            content.append(Gtk.Image.new_from_icon_name(icon_name))
            content.append(Gtk.Label(label=label))
            button.set_child(content)
        else:
            button.set_label(label)
        popover = Gtk.Popover()
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        items.set_margin_top(8)
        items.set_margin_bottom(8)
        items.set_margin_start(8)
        items.set_margin_end(8)
        popover.set_child(items)
        # O foco GTK pode ficar dentro do popover. Capturar aqui também garante
        # que Espaço/L/E/A/D cheguem ao leitor em vez de ativarem um item.
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self.on_key_pressed)
        popover.add_controller(key_controller)
        button.set_popover(popover)
        return button, items

    def _populate_episode_menu(self) -> None:
        child = self.episodes_menu_box.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.episodes_menu_box.remove(child)
            child = following
        paths = getattr(self, "_player_episode_paths", [])
        if not self.session:
            self.episodes_menu.set_sensitive(False)
            return
        self.episodes_menu.set_sensitive(bool(paths))
        current_index = getattr(self.session, "_index", 0)
        self.mark_credits_button.set_sensitive(
            len(paths) > 1 and isinstance(current_index, int) and current_index < len(paths) - 1
        )
        self._episode_menu_current_index = current_index
        entries = getattr(self, "_player_episode_entries", [])
        for index, path in enumerate(paths):
            stem = Path(path).stem
            match = re.search(r"(?i)(?:s(\d{1,2})[ ._-]*e(\d{1,3})|(?:^|\D)(\d{1,2})x(\d{1,3}))", stem)
            if match:
                season = match.group(1) or match.group(3)
                episode = match.group(2) or match.group(4)
                label = f"T{int(season):02d} · Episódio {int(episode):02d}"
            else:
                label = stem.replace("_", " ").replace(".", " ")
            is_current = index == current_index
            if is_current:
                label = f"▶  A reproduzir  ·  {label}"
            item = Gtk.Button(label=label)
            item.set_halign(Gtk.Align.FILL)
            if is_current:
                item.add_css_class("episode-current")
                item.set_sensitive(False)
            item.connect("clicked", lambda _b, i=index: self._select_player_episode(i))
            self.episodes_menu_box.append(item)

    def _select_player_episode(self, index: int) -> None:
        if not self.session or not hasattr(self.session, "play_index"):
            return
        self.session.play_index(index)
        paths = getattr(self, "_player_episode_paths", [])
        if 0 <= index < len(paths):
            self._subtitle_delay_seconds = self.db.subtitle_delay_for(paths[index])
            self.session.set_subtitle_delay(self._subtitle_delay_seconds)
            if getattr(self, "subtitle_delay_label", None):
                self.subtitle_delay_label.set_label(self._subtitle_delay_text())
        entries = getattr(self, "_player_episode_entries", [])
        if index < len(entries):
            ep = entries[index]
            current = Resume(
                paths=[ep.path], start=0.0, duration=ep.duration, season=ep.season,
                episode=ep.episode, completed=False,
            )
            self.player_title_label.set_label(
                self._player_episode_heading(getattr(self, "_player_series_title", ""), current)
            )
        elif index < len(self._player_episode_paths):
            self.player_title_label.set_label(
                f"{getattr(self, '_player_series_title', '')} · {Path(self._player_episode_paths[index]).stem}"
            )
        self._populate_episode_menu()
        self.show_player_controls()

    def update_track_menus(self, tracks) -> None:
        for box in (self.audio_menu_box, self.subtitle_menu_box):
            child = box.get_first_child()
            while child:
                following = child.get_next_sibling()
                box.remove(child)
                child = following

        audio = [t for t in tracks if t.get("type") == "audio"]
        subtitles = [t for t in tracks if t.get("type") == "sub"]
        self.audio_menu.set_sensitive(bool(audio))
        for track in audio:
            label = " · ".join(str(x) for x in (track.get("lang"), track.get("title")) if x)
            if not label:
                label = f"Faixa {track.get('id')}"
            if track.get("selected"):
                label = "✓ " + label
            item = Gtk.Button(label=label)
            item.connect("clicked", lambda _b, track_id=track.get("id"): self.session and self.session.set_audio_track(track_id))
            self.audio_menu_box.append(item)

        heading = Gtk.Label(label="FAIXA DE LEGENDAS")
        heading.set_halign(Gtk.Align.START)
        heading.add_css_class("caption")
        heading.add_css_class("dim-label")
        self.subtitle_menu_box.append(heading)

        off = Gtk.Button(label="Desativadas" if not any(t.get("selected") for t in subtitles) else "○  Desativadas")
        off.set_halign(Gtk.Align.FILL)
        off.add_css_class("flat")
        off.connect("clicked", lambda _b: self.session and self.session.set_subtitle_track("no"))
        self.subtitle_menu_box.append(off)

        if subtitles:
            for number, track in enumerate(subtitles, 1):
                language = str(track.get("lang") or "Idioma desconhecido").upper()
                title = str(track.get("title") or f"Legenda {number}")
                if title.lower() in ("legenda", "subtitle", "subtitles"):
                    title = f"Faixa {number}"
                label = f"{'✓' if track.get('selected') else '○'}  {language}  ·  {title}"
                item = Gtk.Button(label=label)
                item.set_halign(Gtk.Align.FILL)
                item.add_css_class("flat")
                if track.get("selected"):
                    item.add_css_class("suggested-action")
                item.connect("clicked", lambda _b, track_id=track.get("id"): self.session and self.session.set_subtitle_track(track_id))
                self.subtitle_menu_box.append(item)
        else:
            empty = Gtk.Label(label="Nenhuma faixa embutida disponível")
            empty.set_halign(Gtk.Align.START)
            empty.add_css_class("dim-label")
            self.subtitle_menu_box.append(empty)

        external = Gtk.Button(label="＋  Carregar ficheiro externo…")
        external.set_halign(Gtk.Align.FILL)
        external.add_css_class("flat")
        external.connect("clicked", lambda _b: self.open_external_subtitle())
        self.subtitle_menu_box.append(external)

        self.subtitle_menu_box.append(Gtk.Separator.new(Gtk.Orientation.HORIZONTAL))
        appearance_title = Gtk.Label(label="APARÊNCIA")
        appearance_title.set_halign(Gtk.Align.START)
        appearance_title.add_css_class("caption")
        appearance_title.add_css_class("dim-label")
        self.subtitle_menu_box.append(appearance_title)
        self.subtitle_font_size_label = Gtk.Label(label=f"Tamanho: {self._subtitle_font_size}")
        self.subtitle_font_size_label.set_halign(Gtk.Align.CENTER)
        self.subtitle_font_size_label.add_css_class("title-4")
        self.subtitle_menu_box.append(self.subtitle_font_size_label)
        font_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_smaller = Gtk.Button(label="A−")
        font_smaller.set_tooltip_text("Diminuir o tamanho das legendas")
        font_smaller.connect("clicked", lambda _b: self._adjust_subtitle_font_size(-2))
        font_larger = Gtk.Button(label="A＋")
        font_larger.set_tooltip_text("Aumentar o tamanho das legendas")
        font_larger.connect("clicked", lambda _b: self._adjust_subtitle_font_size(2))
        font_controls.append(font_smaller)
        font_controls.append(font_larger)
        self.subtitle_menu_box.append(font_controls)

        self.subtitle_menu_box.append(Gtk.Separator.new(Gtk.Orientation.HORIZONTAL))
        sync_title = Gtk.Label(label="SINCRONIZAÇÃO")
        sync_title.set_halign(Gtk.Align.START)
        sync_title.add_css_class("caption")
        sync_title.add_css_class("dim-label")
        self.subtitle_menu_box.append(sync_title)
        self.subtitle_delay_label = Gtk.Label(label=self._subtitle_delay_text())
        self.subtitle_delay_label.set_halign(Gtk.Align.CENTER)
        self.subtitle_delay_label.add_css_class("title-3")
        self.subtitle_menu_box.append(self.subtitle_delay_label)
        sync_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        earlier = Gtk.Button(label="− 0,1 s")
        earlier.set_tooltip_text("Adiantar as legendas em 0,1 segundo")
        earlier.connect("clicked", lambda _b: self._adjust_subtitle_delay(-0.1))
        later = Gtk.Button(label="+ 0,1 s")
        later.set_tooltip_text("Atrasar as legendas em 0,1 segundo")
        later.connect("clicked", lambda _b: self._adjust_subtitle_delay(0.1))
        reset = Gtk.Button(label="Repor")
        reset.connect("clicked", lambda _b: self._reset_subtitle_delay())
        sync_controls.append(earlier)
        sync_controls.append(later)
        sync_controls.append(reset)
        self.subtitle_menu_box.append(sync_controls)

    def _adjust_subtitle_font_size(self, delta: int) -> None:
        self._subtitle_font_size = max(16, min(40, self._subtitle_font_size + int(delta)))
        config.set_subtitle_font_size(self._subtitle_font_size)
        if self.session:
            self.session.set_subtitle_font_size(self._subtitle_font_size)
        label = getattr(self, "subtitle_font_size_label", None)
        if label:
            label.set_label(f"Tamanho: {self._subtitle_font_size}")

    def _subtitle_delay_text(self) -> str:
        value = self._subtitle_delay_seconds
        return f"Atraso: {value:+.1f} s".replace(".", ",")

    def _current_player_path(self) -> str | None:
        if not self.session:
            return None
        index = getattr(self.session, "_index", 0)
        paths = getattr(self, "_player_episode_paths", [])
        return paths[index] if isinstance(index, int) and 0 <= index < len(paths) else None

    def _store_subtitle_delay(self) -> None:
        path = self._current_player_path()
        if path:
            self.db.save_subtitle_delay(path, self._subtitle_delay_seconds)

    def _adjust_subtitle_delay(self, delta: float) -> None:
        if not self.session or not self.session.running:
            return
        self._subtitle_delay_seconds = max(-10.0, min(10.0, self._subtitle_delay_seconds + delta))
        self.session.set_subtitle_delay(self._subtitle_delay_seconds)
        self._store_subtitle_delay()
        self.subtitle_delay_label.set_label(self._subtitle_delay_text())

    def _reset_subtitle_delay(self) -> None:
        if not self.session or not self.session.running:
            return
        self._subtitle_delay_seconds = 0.0
        self.session.set_subtitle_delay(0.0)
        self._store_subtitle_delay()
        self.subtitle_delay_label.set_label(self._subtitle_delay_text())

    def open_external_subtitle(self) -> None:
        if not self.session or not self.session.running:
            return
        chooser = Gtk.FileChooserNative.new(
            "Escolher ficheiro de legendas", self, Gtk.FileChooserAction.OPEN, "Abrir", "Cancelar"
        )
        subtitle_filter = Gtk.FileFilter()
        subtitle_filter.set_name("Ficheiros de legendas (*.srt, *.ass, *.ssa, *.vtt)")
        for pattern in ("*.srt", "*.ass", "*.ssa", "*.vtt"):
            subtitle_filter.add_pattern(pattern)
        chooser.add_filter(subtitle_filter)
        def on_response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                selected = dialog.get_file()
                if selected and selected.get_path():
                    try:
                        self.session.load_subtitle(selected.get_path())
                    except Exception as exc:
                        self.toast(f"Não foi possível carregar a legenda: {exc}")
            dialog.destroy()
        chooser.connect("response", on_response)
        chooser.show()

    def update_player_progress(self, position: float, duration: float) -> None:
        if not self.player_view:
            return
        current_index = getattr(self.session, "_index", 0) if self.session else 0
        if current_index != getattr(self, "_episode_menu_current_index", None):
            self._populate_episode_menu()
        self._updating_progress = True
        self.progress_bar.set_range(0, max(duration, 1.0))
        self.progress_bar.set_value(min(position, duration) if duration else position)
        self.current_time_label.set_text(fmt_time(position))
        self.total_time_label.set_text(fmt_time(duration))
        self._updating_progress = False
        self._update_next_episode_prompt(position, duration)
        self._update_skip_credits_prompt(position)

    def _update_skip_credits_prompt(self, position: float) -> None:
        button = getattr(self, "skip_credits_button", None)
        if button is None:
            return
        entries = getattr(self, "_player_episode_entries", [])
        index = getattr(self.session, "_index", 0) if self.session else 0
        paths = getattr(self, "_player_episode_paths", [])
        has_next = index + 1 < len(paths)
        if has_next and index < len(entries):
            marker = entries[index].credits_start
            button.set_visible(marker is not None and position >= marker)
        else:
            button.set_visible(False)

    def _mark_credits_start(self) -> None:
        if not self.session or not self.session.running:
            return
        index = getattr(self.session, "_index", 0)
        paths = getattr(self, "_player_episode_paths", [])
        entries = getattr(self, "_player_episode_entries", [])
        if not isinstance(index, int) or index >= len(paths):
            return
        position, _duration = self.session._query_timeline()
        if position is None:
            return
        path = paths[index]
        self.db.save_credits_start(path, position)
        if index < len(entries):
            entries[index].credits_start = position
        self._update_skip_credits_prompt(position)
        self.toast("Início dos créditos guardado para este episódio.")

    def _skip_credits(self) -> None:
        self._play_next_episode()

    def _update_next_episode_prompt(self, position: float, duration: float) -> None:
        entries = getattr(self, "_player_episode_entries", [])
        paths = getattr(self, "_player_episode_paths", [])
        index = getattr(self.session, "_index", 0) if self.session else 0
        has_next = index + 1 < len(paths)
        button = getattr(self, "next_episode_button", None)
        if button is None:
            return
        notice_seconds = config.get_next_episode_notice_seconds()
        if not has_next or duration <= 0 or duration - position > notice_seconds:
            button.set_visible(False)
            return
        next_label = "Próximo episódio"
        if index + 1 < len(entries):
            ep = entries[index + 1]
            next_label = f"Próximo episódio  ·  T{ep.season:02d} E{ep.episode:02d}"
        button.set_label("▶  " + next_label)
        button.set_visible(True)

    def _play_next_episode(self) -> None:
        if not self.session or not self.session.running:
            return
        current_index = getattr(self.session, "_index", 0)
        if not isinstance(current_index, int):
            return
        index = current_index + 1
        if index < len(getattr(self, "_player_episode_paths", [])):
            self._select_player_episode(index)
        self.next_episode_button.set_visible(False)

    def _seek_player_to(self, position: float) -> None:
        if self.session and self.session.running and not self._updating_progress:
            self.session.seek_absolute(position)

    def on_player_seek(self, scale) -> None:
        # Compatibilidade com chamadas antigas; a barra própria usa _seek_player_to.
        self._seek_player_to(scale.get_value())

    def on_player_volume(self, scale) -> None:
        if self.session and self.session.running:
            self.session.set_volume(scale.get_value())

    def _close_player_popovers(self) -> None:
        """Fecha menus do leitor antes de executar um atalho de reprodução."""
        for name in (
            "speed_menu", "next_notice_menu", "settings_menu",
            "audio_menu", "subtitle_menu", "episodes_menu",
        ):
            menu = getattr(self, name, None)
            if menu is not None:
                popover = menu.get_popover()
                if popover is not None and popover.get_visible():
                    popover.popdown()

    def _close_settings_popovers(self) -> None:
        # Os menus de velocidade/aviso estão aninhados dentro de Definições.
        # Fechar apenas o submenu deixa o popover exterior modal e pode fazer
        # o primeiro clique em Reproduzir ser consumido só para o dispensar.
        for name in ("speed_menu", "next_notice_menu", "settings_menu"):
            menu = getattr(self, name, None)
            if menu is not None:
                popover = menu.get_popover()
                if popover is not None and popover.get_visible():
                    popover.popdown()

    def set_player_speed(self, speed: float, label: str) -> None:
        if self.session and self.session.running:
            self.session.set_speed(speed)
            self.speed_menu.set_label(label)
            self._close_settings_popovers()

    def set_next_episode_notice(self, seconds: int) -> None:
        config.set_next_episode_notice_seconds(seconds)
        self.next_notice_menu.set_label(f"{seconds} segundos")
        self._close_settings_popovers()
        if self.session and self.session.running:
            position, duration = self.session._query_timeline()
            if position is not None and duration is not None:
                self._update_next_episode_prompt(position, duration)
        self.toast(f"Aviso do próximo episódio: {seconds} segundos antes do fim.")

    def toggle_player_fullscreen(self) -> None:
        if self._is_fullscreen:
            self.unfullscreen()
        else:
            self.fullscreen()
        self._is_fullscreen = not self._is_fullscreen

    @staticmethod
    def _player_debug_log(event: str, details: str = "") -> None:
        # Diagnóstico temporário em /tmp: permite distinguir um timeout que não
        # dispara de um widget que volta a ser mostrado depois de oculto.
        try:
            from datetime import datetime
            with open("/tmp/cineprime-player-controls.log", "a", encoding="utf-8") as log:
                log.write(f"{datetime.now().isoformat(timespec='milliseconds')} {event} {details}\n")
        except OSError:
            pass

    def _on_player_pointer_motion(self, _controller, x: float, y: float) -> None:
        if self.player_view is None:
            return

        # GTK/Wayland pode reenviar motion com as mesmas coordenadas enquanto o
        # ponteiro está parado. Só movimento espacial real reinicia os 3 segundos.
        if self._last_pointer_x is not None and self._last_pointer_y is not None:
            if abs(float(x) - self._last_pointer_x) < 1.0 and abs(float(y) - self._last_pointer_y) < 1.0:
                return

        self._last_pointer_x = float(x)
        self._last_pointer_y = float(y)
        self._last_pointer_motion_us = GLib.get_monotonic_time()
        if not self.player_top.get_visible() or not self.player_bottom.get_visible():
            self._player_debug_log("motion-show", f"x={x:.1f} y={y:.1f}")
            self.show_player_controls()

    def _on_player_pointer_leave(self, _controller) -> None:
        # Ao voltar a entrar na superfície, o primeiro evento conta como movimento.
        self._last_pointer_x = None
        self._last_pointer_y = None

    def show_player_controls(self) -> None:
        if self.player_view is None:
            return
        self._last_pointer_motion_us = GLib.get_monotonic_time()
        was_hidden = not self.player_top.get_visible() or not self.player_bottom.get_visible()
        self.player_chrome.set_visible(True)
        self.player_top.set_visible(True)
        self.player_bottom.set_visible(True)
        self.player_top.set_opacity(1.0)
        self.player_bottom.set_opacity(1.0)
        self.player_top.set_can_target(True)
        self.player_bottom.set_can_target(True)
        self.set_cursor_from_name("default")
        if was_hidden:
            self._player_debug_log("controls-shown", f"timeout={self._controls_timeout}")
        # Um único watchdog periódico, sem cancelar/recriar timeout a cada evento.
        if not self._controls_timeout:
            self._controls_timeout = GLib.timeout_add(250, self._check_player_controls_inactivity)
            self._player_debug_log("watchdog-started", f"source={self._controls_timeout}")

    def _player_has_open_popover(self) -> bool:
        """Mantém os controlos visíveis enquanto um menu do leitor está aberto."""
        menu_names = (
            "settings_menu", "speed_menu", "next_notice_menu",
            "audio_menu", "subtitle_menu", "episodes_menu",
        )
        for name in menu_names:
            menu = getattr(self, name, None)
            if menu is None:
                continue
            popover = menu.get_popover()
            if popover is not None and popover.get_visible():
                return True
        return False

    def _check_player_controls_inactivity(self) -> bool:
        if self.player_view is None:
            self._controls_timeout = 0
            self._player_debug_log("watchdog-stopped", "reason=no-player-view")
            return GLib.SOURCE_REMOVE
        # Não ocultar a interface enquanto o utilizador consulta qualquer menu.
        # Reinicia a contagem para conceder 3 segundos após fechar o último menu.
        if self._player_has_open_popover():
            self._last_pointer_motion_us = GLib.get_monotonic_time()
            return GLib.SOURCE_CONTINUE
        idle_us = GLib.get_monotonic_time() - self._last_pointer_motion_us
        if idle_us >= 3_000_000:
            self._player_debug_log(
                "watchdog-hide", f"idle_ms={idle_us / 1000:.0f} top={self.player_top.get_visible()} "
                f"bottom={self.player_bottom.get_visible()} mapped={self.player_bottom.get_mapped()}"
            )
            self.hide_player_controls()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def hide_player_controls(self) -> bool:
        if self._controls_timeout:
            self._controls_timeout = 0
        if self.player_view is not None:
            self.player_top.set_can_target(False)
            self.player_bottom.set_can_target(False)
            # Ocultar o contentor único, não widgets individuais: inclui a barra
            # desenhada pela aplicação e impede qualquer filho de ficar exposto.
            self.player_chrome.set_visible(False)
            self.player_top.set_visible(False)
            self.player_bottom.set_visible(False)
            self.set_cursor_from_name("none")
            self._player_debug_log(
                "controls-hidden", f"top={self.player_top.get_visible()} bottom={self.player_bottom.get_visible()} "
                f"chrome={self.player_chrome.get_visible()} progress={self.progress_bar.get_visible()} "
                f"progress-mapped={self.progress_bar.get_mapped()}"
            )
        return GLib.SOURCE_REMOVE

    def on_player_error(self, message: str, failed_session=None) -> None:
        # Ignora erros atrasados de um leitor que já foi substituído.
        if failed_session is not None and self.session is not failed_session:
            return
        self.toast(f"Não foi possível iniciar o vídeo: {message}")
        self.on_session_end(failed_session)

    def on_session_end(self, ended_session=None) -> None:
        if self._closing_window:
            return
        # Um callback antigo nunca pode desmontar a interface de um leitor novo.
        if ended_session is not None and self.session is not ended_session:
            return
        self.session = None
        self._player_episode_paths = []
        self._player_episode_entries = []
        self._player_series_title = ""
        self.next_episode_button = None
        self.skip_credits_button = None
        self.playback_bar.set_visible(False)
        if self._controls_timeout:
            GLib.source_remove(self._controls_timeout)
            self._controls_timeout = 0
        if self.player_view:
            self.content_overlay.remove_overlay(self.player_view)
            self.player_view = None
        if getattr(self, "_player_motion_controller", None):
            source = getattr(self, "_player_motion_source", None)
            if source is not None:
                source.remove_controller(self._player_motion_controller)
            self._player_motion_controller = None
            self._player_motion_source = None
        self.header.set_visible(True)
        self.dock.set_visible(True)
        if self._is_fullscreen:
            self.unfullscreen()
            self._is_fullscreen = False

    def update_playback_buttons(self) -> None:
        playing = self.session is not None and self.session.running and not self.session.paused
        self.play_pause_button.set_icon_name(
            "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        action = "Pausar reprodução" if playing else "Retomar reprodução"
        self.play_pause_button.set_tooltip_text(action)
        set_accessible_label(self.play_pause_button, action)

    def toggle_playback(self) -> None:
        if self.session and self.session.running:
            self.session.toggle_pause()

    def next_in_playlist(self) -> None:
        if self.session and self.session.running:
            self.session.next_track()

    def stop_playback(self) -> None:
        if self.session:
            self.session.stop()

    def toggle_search(self) -> None:
        """F abre/fecha a busca (atalho de teclado)."""
        if self.query:
            self.query = ""
            self.search_entry.set_text("")
            self._ensure_page_current(self.section)
            self.set_active_dock(self.section)
            self.stack.set_visible_child_name(self.section)
        else:
            self.search_entry.grab_focus()

    # ---------- varredura em segundo plano ----------
    def auto_scan(self) -> bool:
        # O idle pode só executar depois de a janela começar a fechar
        # (por exemplo, no arranque seguido de saída imediata).
        if self._closing_window:
            self._auto_scan_source = 0
            return GLib.SOURCE_REMOVE
        self._auto_scan_source = 0
        self.start_scan(quiet=True)
        return GLib.SOURCE_REMOVE                # roda uma vez só

    def set_scanning(self, flag: bool) -> None:
        self.scanning = flag
        self.update_busy()

    def update_busy(self) -> None:
        """Um só spinner serve para as duas tarefas em segundo plano."""
        busy = self.scanning or self.fetching_covers
        self.spinner.set_visible(busy)
        self.spinner.set_spinning(busy)
        self.refresh_button.set_sensitive(not self.scanning)
        self.covers_button.set_sensitive(not self.fetching_covers)

    def call_main(self, fn, *args) -> None:
        """Roda fn(*args) na thread principal, ignorando resultados após fechar."""
        def invoke():
            if self._closing_window:
                return GLib.SOURCE_REMOVE
            fn(*args)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(invoke)

    def _start_worker(self, target):
        """Regista um worker e protege os ficheiros temporários no encerramento."""
        with self._worker_lock:
            if self._closing_window:
                return None
            self._active_workers += 1

        def run():
            try:
                target()
            finally:
                with self._worker_lock:
                    self._active_workers -= 1
                    tmp_dir = self.tmp_dir if self._closing_window and self._active_workers == 0 else None
                    if tmp_dir is not None:
                        self.tmp_dir = None
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def get_tmp_dir(self) -> str:
        if self.tmp_dir is None:
            self.tmp_dir = tempfile.mkdtemp(prefix="cineprime-")
        return self.tmp_dir

    def start_scan(self, quiet: bool = False) -> None:
        """Procura vídeos nas pastas configuradas, sem travar a janela.

        Divisão de trabalho (importante!):
          - a THREAD só lê o disco (lento) e devolve o resultado;
          - a thread PRINCIPAL grava no banco (rápido), porque o SQLite e
            os widgets do GTK só podem ser usados por ela.
        """
        if self.scanning:
            self.rescan_pending = True           # refaz quando esta terminar
            return
        folders = config.get_folders()
        if not folders:
            if not quiet:
                self.toast("Adicione uma pasta primeiro.")
            return
        self.set_scanning(True)
        known = self.db.known_fingerprints()     # lido aqui, na thread principal
        known_media = self.db.known_media_signatures()
        targets = [
            (Path(f["path"]), None if f["kind"] == "auto" else f["kind"]) for f in folders
        ]

        def worker():
            try:
                result = scan_folders(targets, known, known_media, validate_media=True)
            except Exception as err:  # noqa: BLE001 - qualquer falha precisa soltar o botão
                self.call_main(self.scan_failed, str(err))
                return
            self.call_main(self.finish_scan, result, quiet)

        self._start_worker(worker)

    def scan_failed(self, message: str) -> bool:
        self.set_scanning(False)
        self.toast(f"Falha ao atualizar: {message}")
        return GLib.SOURCE_REMOVE

    def finish_scan(self, result: ScanResult, quiet: bool) -> bool:
        try:
            stats = self.db.save_scan(result.found, result.prune_roots)
        finally:
            self.set_scanning(False)
        for cover in stats.orphan_covers:
            covers.remove_if_managed(cover)
        self.reload()

        changes = []
        if stats.added:
            changes.append(f"{stats.added} novo(s)")
        if stats.moved:
            changes.append(f"{stats.moved} renomeado(s)")
        if stats.removed:
            changes.append(f"{stats.removed} removido(s)")
        if changes:
            self.toast("Biblioteca atualizada: " + ", ".join(changes))
        elif not quiet:
            self.toast("A biblioteca já está atualizada.")
        if result.missing:
            self.toast("Pasta não encontrada: " + ", ".join(p.name for p in result.missing))
        if result.empty:
            self.toast("Nenhum vídeo em: " + ", ".join(p.name for p in result.empty)
                       + ". Mantive o que já estava registrado.")
        if result.errors:
            self.toast(
                f"Leitura incompleta em {len(result.errors)} local(is). "
                "Mantive os registos dessas pastas para evitar perdas."
            )

        if self.rescan_pending:
            self.rescan_pending = False
            self.start_scan(quiet=True)
        return GLib.SOURCE_REMOVE

    # ---------- pastas da biblioteca ----------
    def open_folders_dialog(self) -> Adw.Dialog:
        folders = config.get_folders()
        dialog = Adw.Dialog()
        dialog.set_title("Pastas da biblioteca")
        dialog.set_content_width(self.dialog_content_width(480))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(6)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)

        if folders:
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            for folder in folders:
                row = Adw.ActionRow(
                    title=Path(folder["path"]).name or folder["path"], subtitle=folder["path"]
                )
                # Sem isto, caracteres como & ou < no nome da pasta quebrariam o texto
                # (a mesma armadilha de markup que vimos na página vazia).
                row.set_use_markup(False)
                kind = Gtk.Label(label=KIND_LABELS.get(folder["kind"], folder["kind"]))
                kind.add_css_class("dim-label")
                trash = Gtk.Button(icon_name="user-trash-symbolic")
                trash.add_css_class("flat")
                trash.set_valign(Gtk.Align.CENTER)
                trash.set_tooltip_text("Remover pasta")
                set_accessible_label(trash, f"Remover pasta {Path(folder['path']).name or folder['path']}")
                trash.set_size_request(40, 40)

                def on_remove(_button, path=folder["path"]):
                    dialog.close()
                    self.confirm_remove_folder(path)

                trash.connect("clicked", on_remove)
                row.add_suffix(kind)
                row.add_suffix(trash)
                listbox.append(row)
            box.append(listbox)
        else:
            empty = Gtk.Label(label="Nenhuma pasta adicionada ainda.")
            empty.add_css_class("dim-label")
            box.append(empty)

        def on_add(_button):
            dialog.close()
            self.choose_folder()

        add = Gtk.Button(label="Adicionar pasta…")
        add.add_css_class("suggested-action")
        add.add_css_class("pill")
        add.connect("clicked", on_add)
        box.append(add)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_min_content_height(120)
        scroll.set_max_content_height(560)
        scroll.set_child(box)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroll)
        dialog.set_child(toolbar)
        dialog.present(self)
        return dialog

    def choose_folder(self, _button=None) -> None:
        file_dialog = Gtk.FileDialog(title="Escolher pasta de vídeos")
        file_dialog.select_folder(self, None, self.on_folder_chosen)

    def on_folder_chosen(self, file_dialog, result) -> None:
        try:
            folder = file_dialog.select_folder_finish(result)
        except GLib.Error:
            return                               # usuário cancelou
        self.ask_folder_kind(folder.get_path())

    def ask_folder_kind(self, path: str) -> None:
        dialog = Adw.AlertDialog(
            heading="O que tem nesta pasta?",
            body=(
                f"{path}\n\n"
                "Filmes: tudo nesta pasta vira filme.\n"
                "Séries: cada subpasta é uma série.\n"
                "Automático: usa pastas chamadas Filmes e Series, ou o padrão S01E01 no nome."
            ),
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("movie", "Filmes")
        dialog.add_response("series", "Séries")
        dialog.add_response("auto", "Automático")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response in ("movie", "series", "auto"):
                self.add_folder(path, response)

        dialog.connect("response", on_response)
        dialog.present(self)

    def add_folder(self, path: str, kind: str) -> None:
        config.add_folder(str(Path(path).resolve()), kind)
        self.toast("Pasta adicionada. Procurando vídeos…")
        self.start_scan()

    def confirm_remove_folder(self, path: str) -> None:
        dialog = Adw.AlertDialog(
            heading="Remover pasta da biblioteca?",
            body=(
                f"{path}\n\n"
                "Os títulos desta pasta saem do Cine Prime, junto com nomes e capas "
                "editados e o progresso. Seus vídeos no disco NÃO são apagados."
            ),
        )
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("remove", "Remover")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "remove":
                self.remove_folder(path)

        dialog.connect("response", on_response)
        dialog.present(self)

    def remove_folder(self, path: str) -> None:
        config.remove_folder(path)
        for cover in self.db.remove_files_under(Path(path)):
            covers.remove_if_managed(cover)
        self.reload()
        self.toast("Pasta removida da biblioteca")

    # ---------- capas online (OMDb para filmes, TVmaze para séries) ----------
    def ask_omdb_key(self, then, invalid: bool = False, on_cancel=None) -> None:
        """Pede a chave do OMDb (uma vez só; fica guardada no arquivo de configuração).
        Só os FILMES precisam dela: as séries vêm do TVmaze, que não pede chave."""
        entry = Gtk.PasswordEntry(show_peek_icon=True)   # esconde o texto, com "olhinho"
        # PasswordEntry não tem o método set_activates_default (o Gtk.Entry tem);
        # aqui o jeito é a propriedade. Faz o Enter acionar o botão "Salvar".
        entry.set_property("activates-default", True)
        link = Gtk.LinkButton.new_with_label(
            "https://www.omdbapi.com/apikey.aspx", "Abrir a página para pedir a chave do OMDb"
        )
        credit = attribution_label()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(entry)
        box.append(link)
        box.append(credit)

        body = (
            "Para buscar capas de filmes, o Cine Prime usa o OMDb, que é gratuito e pede "
            "só um email (nenhum documento). Peça a chave na página abaixo, clique no link "
            "de ativação que chega por email e cole a chave aqui. Ela fica guardada só no "
            "seu computador. As séries não precisam de chave."
        )
        if invalid:
            body = "A chave anterior foi recusada.\n\n" + body
        dialog = Adw.AlertDialog(heading="Chave do OMDb", body=body)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("save", "Salvar")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            key = entry.get_text().strip()
            if response == "save" and key:
                config.set_omdb_key(key)
                then()
            elif on_cancel is not None:
                on_cancel()

        dialog.connect("response", on_response)
        dialog.set_focus(entry)                  # já abre com o cursor no campo: é só colar
        dialog.present(self)

    def start_cover_batch(self, ask_key: bool = True) -> None:
        """Busca, em segundo plano, a capa de todos os títulos que ainda não têm."""
        if self.fetching_covers:
            return
        todo = [
            (t["id"], t["kind"], t["display_name"] or t["name"], t["year"])
            for t in self.db.list_titles()
            if not t["cover_path"]
        ]
        if not todo:
            self.toast("Todos os títulos já têm capa.")
            return
        key = config.get_omdb_key()
        self.cover_skipped = 0                   # filmes deixados de fora por falta de chave
        if not key:
            movies = [t for t in todo if t[1] == "movie"]
            if movies and ask_key:
                # Se a pessoa cancelar, as séries ainda podem ser buscadas (sem chave).
                self.ask_omdb_key(
                    then=self.start_cover_batch,
                    on_cancel=lambda: self.start_cover_batch(ask_key=False),
                )
                return
            self.cover_skipped = len(movies)
            todo = [t for t in todo if t[1] != "movie"]
            if not todo:
                self.toast("Filmes precisam da chave do OMDb. Clique em buscar capas de novo para colocá-la.")
                return
        self.fetching_covers = True
        self.update_busy()
        self.progress_toast = Adw.Toast.new(f"Buscando capas de {len(todo)} título(s)…")
        self.progress_toast.set_timeout(0)       # 0 = fica até a gente dispensar
        self.toasts.add_toast(self.progress_toast)
        dest = self.get_tmp_dir()

        def worker():
            try:
                result = posters.fetch_missing_covers(todo, key, dest)
            except Exception as err:  # noqa: BLE001 - qualquer falha precisa soltar o botão
                self.call_main(self.cover_batch_failed, str(err))
                return
            self.call_main(self.finish_cover_batch, result)

        self._start_worker(worker)

    def end_cover_batch(self) -> None:
        self.fetching_covers = False
        self.update_busy()
        if self.progress_toast is not None:
            self.progress_toast.dismiss()        # senão o resumo ficaria na fila atrás dele
            self.progress_toast = None

    def cover_batch_failed(self, message: str) -> None:
        self.end_cover_batch()
        self.toast(f"Falha ao buscar capas: {message}")

    def finish_cover_batch(self, result: "posters.BatchResult") -> None:
        self.end_cover_batch()
        applied = 0
        for title_id, path, _label in result.found:
            row = self.db.get_title(title_id)
            # O título pode ter sumido, ou você pode ter escolhido uma capa à mão
            # enquanto a busca rodava: nesses casos não sobrescrevemos nada.
            if row is not None and not row["cover_path"]:
                try:
                    new = covers.import_cover(path, title_id)
                    self.db.set_cover(title_id, str(new))
                    applied += 1
                except OSError:
                    pass
            path.unlink(missing_ok=True)
        self.reload()

        if result.auth_failed:
            self.ask_omdb_key(then=self.start_cover_batch, invalid=True)
        elif result.error:
            self.toast(result.error)
        manual = len(result.unsure) + len(result.notfound)
        message = f"{applied} capa(s) adicionada(s)"
        if manual:
            message += f" · {manual} para escolher à mão (lápis › Buscar capa online)"
        skipped = getattr(self, "cover_skipped", 0)
        if skipped:
            message += f" · {skipped} filme(s) sem chave do OMDb"
        if not result.error and not result.auth_failed:
            self.toast(message)

    def open_cover_search(self, parent, kind: str, query: str, year, on_chosen) -> None:
        """Pop-up para escolher a capa certa numa lista de resultados online."""
        key = config.get_omdb_key()
        if kind == "movie" and not key:          # séries (TVmaze) dispensam chave
            self.ask_omdb_key(
                then=lambda: self.open_cover_search(parent, kind, query, year, on_chosen)
            )
            return

        dialog = Adw.Dialog()
        dialog.set_title("Buscar capa online")
        dialog.set_content_width(self.dialog_content_width(460))
        holder = Adw.Bin()                       # troca: carregando / lista / erro
        credit = attribution_label()
        credit.set_margin_top(8)
        credit.set_margin_bottom(8)
        credit.set_margin_start(12)
        credit.set_margin_end(12)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(holder)
        toolbar.add_bottom_bar(credit)           # crédito das fontes
        dialog.set_child(toolbar)

        def show_status(title: str, description: str, spinning: bool = False) -> None:
            page = Adw.StatusPage(title=title, description=description)
            if spinning:
                spinner = Gtk.Spinner(spinning=True)
                spinner.set_size_request(32, 32)
                page.set_child(spinner)
            holder.set_child(page)

        def snippet(text: str) -> str:
            return text if len(text) <= 110 else text[:107] + "…"

        def show_results(candidates) -> None:
            if not candidates:
                show_status("Nada encontrado",
                            f"Nenhuma capa para “{query}”. Ajuste o nome no campo e tente de novo.")
                return
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            for cand in candidates:
                label = f"{cand.title} ({cand.year})" if cand.year else cand.title
                detail = snippet(cand.overview) or ("OMDb" if cand.source == "omdb" else "TVmaze")
                if cand.original_title != cand.title:
                    detail = f"{cand.original_title} — {detail}"
                row = Adw.ActionRow(title=label, subtitle=detail)
                row.set_use_markup(False)
                row.set_subtitle_lines(2)
                row.set_activatable(True)
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.connect("activated", make_pick(cand, label))
                listbox.append(row)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.set_margin_top(6)
            box.set_margin_bottom(12)
            box.set_margin_start(18)
            box.set_margin_end(18)
            box.append(listbox)
            scroll = Gtk.ScrolledWindow()
            scroll.set_child(box)
            scroll.set_propagate_natural_height(True)
            scroll.set_max_content_height(440)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            holder.set_child(scroll)

        def make_pick(cand, label):
            def on_activated(_row):
                show_status("Baixando capa…", label, spinning=True)
                dest = self.get_tmp_dir()

                def worker():
                    try:
                        path = posters.download_poster(cand.poster_url, dest, f"pick-{cand.ref}")
                    except posters.PosterError as err:
                        self.call_main(show_status, "Não consegui baixar", str(err))
                        return
                    self.call_main(done, str(path), label)

                self._start_worker(worker)
            return on_activated

        def done(path: str, label: str) -> None:
            dialog.close()
            on_chosen(path, label)

        def search_failed(err: Exception) -> None:
            if isinstance(err, posters.PosterAuthError):
                dialog.close()
                self.ask_omdb_key(
                    then=lambda: self.open_cover_search(parent, kind, query, year, on_chosen),
                    invalid=True,
                )
            else:
                show_status("Não deu certo", str(err))

        def worker():
            try:
                found = [c for c in posters.search(kind, query, key) if c.poster_url]
            except posters.PosterError as err:
                self.call_main(search_failed, err)
                return
            # Os de ano compatível primeiro (a ordenação é estável: o resto mantém a ordem)
            if year:
                found.sort(key=lambda c: not (c.year and abs(c.year - year) <= 1))
            self.call_main(show_results, found[:10])

        show_status("Buscando…", f"“{query}”", spinning=True)
        self._start_worker(worker)
        dialog.present(parent)

    # ---------- edição ----------
    def open_edit_dialog(self, title_id: int) -> None:
        row = self.db.get_title(title_id)
        if row is None:
            return
        chosen = {"cover": None}         # capa escolhida, ainda não salva

        entry = Gtk.Entry()
        entry.set_text(row["display_name"] or "")
        entry.set_placeholder_text(row["name"])   # dica: o nome detectado
        entry.set_activates_default(True)

        cover_label = Gtk.Label(label="Capa: manter a atual")
        cover_label.add_css_class("dim-label")
        cover_label.set_wrap(True)
        cover_button = Gtk.Button(label="Escolher capa…")

        def on_cover_chosen(file_dialog, result):
            try:
                file = file_dialog.open_finish(result)
            except GLib.Error:
                return                    # usuário cancelou
            path = file.get_path()
            try:
                Gdk.Texture.new_from_filename(path)    # só para validar
            except GLib.Error:
                chosen["cover"] = None
                cover_label.set_text("Formato não suportado. Use PNG ou JPG.")
                return
            chosen["cover"] = path
            cover_label.set_text(f"Capa nova: {Path(path).name}")

        def on_choose_clicked(_button):
            img_filter = Gtk.FileFilter(name="Imagens (PNG, JPG)")
            img_filter.add_mime_type("image/png")
            img_filter.add_mime_type("image/jpeg")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(img_filter)
            file_dialog = Gtk.FileDialog(title="Escolher capa", filters=filters)
            file_dialog.open(self, None, on_cover_chosen)

        cover_button.connect("clicked", on_choose_clicked)

        online_button = Gtk.Button(label="Buscar capa online…")

        def on_online_clicked(_button):
            # Busca pelo que está escrito no campo (o nome que você quer), ou pelo
            # nome detectado se o campo estiver vazio.
            query = entry.get_text().strip() or row["name"]

            def on_online_chosen(path: str, label: str) -> None:
                chosen["cover"] = path
                cover_label.set_text(f"Capa nova: {label} (online)")

            self.open_cover_search(online_button, row["kind"], query, row["year"], on_online_chosen)

        online_button.connect("clicked", on_online_clicked)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(entry)
        box.append(cover_button)
        box.append(online_button)
        box.append(cover_label)

        dialog = Adw.AlertDialog(
            heading="Editar título",
            body=f"Nome detectado: {row['name']}\nDeixe o campo vazio para voltar ao nome detectado.",
        )
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("save", "Salvar")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "save":
                self.apply_edit(title_id, entry.get_text(), chosen["cover"])

        dialog.connect("response", on_response)
        dialog.present(self)

    def apply_edit(self, title_id: int, new_name: str, cover_src: str | None) -> None:
        """Grava o nome e (se houver) a nova capa, depois atualiza a tela."""
        self.db.set_display_name(title_id, new_name.strip() or None)
        if cover_src:
            try:
                old = self.db.get_title(title_id)["cover_path"]
                new = covers.import_cover(Path(cover_src), title_id)
                self.db.set_cover(title_id, str(new))
                if old:
                    covers.remove_if_managed(old)
            except OSError as err:
                self.reload()
                self.toast(f"Nome salvo, mas a capa falhou: {err.strerror}")
                return
        self.reload()
        self.toast("Alterações salvas")


class CinePrimeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.helder.cineprime")
        self.db = None

    def do_activate(self):
        self.db = self.db or Database()
        win = self.props.active_window or CinePrimeWindow(self, self.db)
        win.present()


def main() -> int:
    return CinePrimeApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())