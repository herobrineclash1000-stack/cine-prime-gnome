"""Testes da janela (dock de navegação e busca). DESLIGADOS por padrão.

Precisam de um ambiente gráfico e não abrem nenhuma janela no ecrã, mas ligam o GTK.
Para rodar, dentro da pasta do projeto:

    CINEPRIME_GUI_TESTS=1 python3 -m unittest discover -s tests -v

Usam bancos e pastas temporários: a sua biblioteca real não é tocada.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENABLED = os.environ.get("CINEPRIME_GUI_TESTS") == "1"


def walk(widget, kind, found=None):
    """Percorre a árvore de widgets e junta todos do tipo `kind`."""
    found = [] if found is None else found
    child = widget.get_first_child()
    while child:
        if isinstance(child, kind):
            found.append(child)
        walk(child, kind, found)
        child = child.get_next_sibling()
    return found


def iter_listbox_rows(listbox):
    index = 0
    while True:
        row = listbox.get_row_at_index(index)
        if row is None:
            return
        yield row
        index += 1


@unittest.skipUnless(ENABLED, "defina CINEPRIME_GUI_TESTS=1 para rodar os testes da janela")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Uma única aplicação para todos os testes, com um nome só dos testes.

        Registrar o mesmo nome várias vezes falha num computador com D-Bus de sessão real
        ("já foi exportado"), e o nome de teste evita disputar com o Cine Prime de verdade
        caso ele esteja aberto enquanto os testes rodam.
        """
        import app as A
        cls.application = A.CinePrimeApp()
        cls.application.set_application_id("io.github.helder.cineprime.Testes")
        cls.application.register()

    def setUp(self):
        import app as A
        from database import Database
        from gi.repository import Adw, Gdk, Gtk
        from scanner import scan_folders

        self.A, self.Adw, self.Gdk, self.Gtk = A, Adw, Gdk, Gtk
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = tmp.name
        root = Path(tmp.name) / "videos"
        self.root = root
        self.scan_folders = scan_folders
        for rel in ("Filmes/Tenet (2020).mp4", "Filmes/Spider-Man Brand New Day (2026).mp4",
                    "Filmes/Cidade de Deus (2002).mp4", "Series/Spider Noir/T1/Spider-Noir-EP01.mp4", "Series/Spider Noir/T1/Spider-Noir-EP02.mp4"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(rel.encode() * 100)
        self.db = Database(Path(tmp.name) / "t.db")
        self.addCleanup(self.db.conn.close)
        result = scan_folders([(root, None)], {})
        self.db.save_scan(result.found, result.prune_roots)

        self.application = type(self).application       # criada uma vez só (setUpClass)
        self.application.db = self.db
        self.win = A.CinePrimeWindow(self.application, self.db)
        # A varredura automática é coberta separadamente; não a deixar correr
        # contra a base temporária depois de cada teste fechar a ligação SQLite.
        source = getattr(self.win, "_auto_scan_source", 0)
        if source:
            from gi.repository import GLib
            GLib.source_remove(source)
            self.win._auto_scan_source = 0
        self.addCleanup(self.win.destroy)
        self.addCleanup(self.win.on_close, self.win)

    def search(self, text):
        self.win.search_entry.set_text(text)
        self.win.apply_search()

    def active_pages(self):
        """Páginas do dock que estão pintadas de vermelho (deve haver no máximo uma)."""
        return sorted(n for n, b in self.win.dock_buttons.items() if b.has_css_class("active"))

    def shown_titles(self):
        """Nomes dos cartões que aparecem na página visível."""
        page = self.win.stack.get_visible_child()
        labels = []
        for grid in walk(page, self.Gtk.GridView):
            selection = grid.get_model()
            model = selection.get_model()
            for index in range(model.get_n_items()):
                labels.append(int(model.get_item(index).get_string()))
        return sorted(self.db.get_title(i)["name"] for i in labels)

    def test_cover_texture_is_cached_and_scaled_to_card_size(self):
        from gi.repository import GdkPixbuf

        path = Path(self.tmp_dir) / "large-cover.png"
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1200, 1800)
        pixbuf.fill(0x336699ff)
        pixbuf.savev(str(path), "png", [], [])

        self.A._cover_texture_cache.clear()
        first = self.A._load_cover_texture(str(path), 160, 240)
        second = self.A._load_cover_texture(str(path), 160, 240)

        self.assertIsNotNone(first)
        self.assertIs(first, second)
        self.assertLessEqual(first.get_width(), 160)
        self.assertLessEqual(first.get_height(), 240)
        self.assertEqual(len(self.A._cover_texture_cache), 1)

    def test_cover_texture_supports_rgb_without_alpha(self):
        from gi.repository import GdkPixbuf

        path = Path(self.tmp_dir) / "rgb-cover.png"
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 320, 480)
        pixbuf.fill(0x336699ff)
        pixbuf.savev(str(path), "png", [], [])

        texture = self.A._load_cover_texture(str(path), 160, 240)
        self.assertIsNotNone(texture)
        self.assertLessEqual(texture.get_width(), 160)
        self.assertLessEqual(texture.get_height(), 240)

    def test_cover_texture_cache_invalidates_when_file_changes(self):
        import os
        from gi.repository import GdkPixbuf

        path = Path(self.tmp_dir) / "replace-cover.png"
        first_pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 320, 480)
        first_pixbuf.fill(0x336699ff)
        first_pixbuf.savev(str(path), "png", [], [])

        self.A._cover_texture_cache.clear()
        first = self.A._load_cover_texture(str(path), 160, 240)
        second_pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 640, 960)
        second_pixbuf.fill(0x993366ff)
        second_pixbuf.savev(str(path), "png", [], [])
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 2_000_000_000))
        second = self.A._load_cover_texture(str(path), 160, 240)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
        self.assertEqual(len(self.A._cover_texture_cache), 2)

    def test_cover_is_decoded_off_main_thread_and_applied_on_main_loop(self):
        from gi.repository import GdkPixbuf, GLib

        path = Path(self.tmp_dir) / "async-cover.png"
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1200, 1800)
        pixbuf.fill(0x336699ff)
        pixbuf.savev(str(path), "png", [], [])
        row = {"cover_path": str(path), "display_name": "Capa", "name": "Capa"}

        frame = self.A.build_cover(row, 160, 240)
        overlay = frame.get_child()
        placeholder = overlay.get_child()
        picture = overlay.get_last_child()
        self.assertFalse(picture.get_visible())
        self.assertTrue(placeholder.get_visible())

        loop = GLib.MainLoop()
        GLib.timeout_add(3000, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
        GLib.timeout_add(20, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1] if picture.get_visible() else GLib.SOURCE_CONTINUE)
        loop.run()

        self.assertTrue(picture.get_visible())
        self.assertFalse(placeholder.get_visible())
        self.assertIsNotNone(picture.get_paintable())
        self.assertLessEqual(picture.get_paintable().get_width(), 160)
        self.assertLessEqual(picture.get_paintable().get_height(), 240)

    def test_concurrent_cards_share_one_cover_decode_and_both_update(self):
        from gi.repository import GdkPixbuf, GLib

        path = Path(self.tmp_dir) / "shared-async-cover.png"
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1000, 1500)
        pixbuf.fill(0x663399ff)
        pixbuf.savev(str(path), "png", [], [])
        row = {"cover_path": str(path), "display_name": "Partilhada", "name": "Partilhada"}

        first = self.A.build_cover(row, 160, 240)
        second = self.A.build_cover(row, 160, 240)
        first_picture = first.get_child().get_last_child()
        second_picture = second.get_child().get_last_child()
        self.assertFalse(first_picture.get_visible())
        self.assertFalse(second_picture.get_visible())

        loop = GLib.MainLoop()
        GLib.timeout_add(3000, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
        GLib.timeout_add(20, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1] if first_picture.get_visible() and second_picture.get_visible() else GLib.SOURCE_CONTINUE)
        loop.run()

        self.assertTrue(first_picture.get_visible())
        self.assertTrue(second_picture.get_visible())
        self.assertIs(first_picture.get_paintable(), second_picture.get_paintable())

    def test_cover_texture_cache_is_bounded(self):
        from gi.repository import GdkPixbuf

        self.A._cover_texture_cache.clear()
        for index in range(self.A._COVER_CACHE_MAX + 1):
            path = Path(self.tmp_dir) / f"cover-{index}.png"
            pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 80, 120)
            pixbuf.fill((index + 1) * 0x000100ff)
            pixbuf.savev(str(path), "png", [], [])
            self.assertIsNotNone(self.A._load_cover_texture(str(path), 80, 120))

        self.assertEqual(len(self.A._cover_texture_cache), self.A._COVER_CACHE_MAX)

    def test_starts_on_home_with_its_icon_active(self):
        self.assertEqual(self.win.stack.get_visible_child_name(), "home")
        self.assertEqual(self.active_pages(), ["home"])

    def test_movies_page_lists_every_movie(self):
        self.win.show_section("movie")
        self.assertEqual(self.shown_titles(), ["Cidade de Deus", "Spider-Man Brand New Day", "Tenet"])

    def test_library_grids_use_a_virtualized_grid_view_model(self):
        self.win.show_section("movie")
        grids = walk(self.win.pages["movie"], self.Gtk.GridView)
        self.assertEqual(len(grids), 1)
        self.assertEqual(grids[0].get_model().get_model().get_n_items(), 3)
        self.assertEqual(walk(self.win.pages["movie"], self.Gtk.FlowBox), [])
        self.assertEqual(set(grids[0]._cineprime_rows), {t["id"] for t in self.db.list_titles("movie")})

    def test_favorite_changes_update_unmaterialized_grid_data(self):
        self.win.show_section("movie")
        grid = walk(self.win.pages["movie"], self.Gtk.GridView)[0]
        tenet = next(t for t in self.db.list_titles("movie") if t["name"] == "Tenet")
        self.assertEqual(grid._cineprime_rows[tenet["id"]]["favorite"], 0)
        self.win.toggle_favorite(tenet["id"], True)
        self.assertEqual(grid._cineprime_rows[tenet["id"]]["favorite"], 1)
        self.win.toggle_favorite(tenet["id"], False)
        self.assertEqual(grid._cineprime_rows[tenet["id"]]["favorite"], 0)

    def test_dock_has_the_four_pages_plus_the_add_button(self):
        self.assertEqual(list(self.win.dock_buttons), ["home", "movie", "series", "favorites"])
        self.assertEqual(self.win.add_button.get_tooltip_text(), "Adicionar pasta")

    def test_duplicate_diagnostic_dialog_displays_real_duplicate_group(self):
        original = self.root / "Filmes/Tenet (2020).mp4"
        duplicate = self.root / "Filmes/Tenet copy (2020).mp4"
        duplicate.write_bytes(original.read_bytes())
        result = self.scan_folders([(self.root, None)], {})
        self.db.save_scan(result.found, result.prune_roots)

        dialog = self.win.open_duplicates_dialog()
        labels = [label.get_text() for label in walk(dialog.get_child(), self.Gtk.Label)]
        rendered = "\\n".join(labels)

        self.assertIn("1 grupos", rendered)
        self.assertIn("2 ficheiros", rendered)
        self.assertIn("Tenet (2020)", rendered)
        self.assertIn(str(original), rendered)
        self.assertIn(str(duplicate), rendered)
        self.assertEqual(len(self.db.list_titles()), 5)  # não funde os dois títulos

    def test_clicking_each_dock_icon_opens_its_own_page(self):
        """Protege contra o defeito clássico de lambda em laço: todos abrindo a última página."""
        for name in ("home", "series", "favorites", "movie"):
            with self.subTest(name):
                self.win.dock_buttons[name].emit("clicked")
                self.assertEqual(self.win.stack.get_visible_child_name(), name)
                self.assertEqual(self.active_pages(), [name])

    def test_series_page_lists_only_series(self):
        self.win.show_section("series")
        self.assertEqual(self.shown_titles(), ["Spider Noir"])

    def card_for(self, name, page=None):
        """Cartão do título `name`; olha a página `page` (ou a que está visível)."""
        root = self.win.pages[page] if page else self.win.stack.get_visible_child()
        for card in self.A.iter_cards(root):
            if self.db.get_title(card.title_id)["name"] == name:
                return card
        self.fail(f"cartão '{name}' não encontrado")

    def test_favorites_page_starts_with_a_friendly_message(self):
        self.win.show_section("favorites")
        page = self.win.pages["favorites"].get_child()
        self.assertIsInstance(page, self.Adw.StatusPage)
        self.assertEqual(page.get_title(), "Nenhum favorito ainda")

    def test_clicking_a_cards_heart_favorites_it_and_fills_the_favorites_page(self):
        self.win.show_section("movie")
        heart = self.card_for("Tenet").heart
        heart.emit("clicked")
        self.assertEqual([t["name"] for t in self.db.list_titles(favorites_only=True)], ["Tenet"])
        self.assertTrue(heart.has_css_class("fav-on"))
        self.win.show_section("favorites")
        self.assertEqual(self.shown_titles(), ["Tenet"])

    def test_unfavoriting_from_the_favorites_page_removes_the_card(self):
        tenet = next(t for t in self.db.list_titles() if t["name"] == "Tenet")
        self.db.set_favorite(tenet["id"], True)
        self.win.reload()
        self.win.show_section("favorites")
        self.card_for("Tenet").heart.emit("clicked")
        self.assertEqual(self.db.list_titles(favorites_only=True), [])
        self.assertEqual(self.win.pages["favorites"].get_child().get_title(), "Nenhum favorito ainda")

    def test_a_heart_clicked_on_one_page_updates_the_other_pages_in_place(self):
        """Não refazemos as páginas (a lista voltaria ao topo): os corações mudam onde estão."""
        self.win.show_section("movie")
        movie_card = self.card_for("Spider-Man Brand New Day", "movie")
        self.assertFalse(movie_card.heart.has_css_class("fav-on"))
        self.search("spider")                                        # outra página, outro cartão do mesmo título
        self.card_for("Spider-Man Brand New Day", self.A.SEARCH_PAGE).heart.emit("clicked")
        self.assertTrue(movie_card.heart.has_css_class("fav-on"))    # o cartão antigo, sem ter sido refeito

    def test_card_hearts_are_drawn_as_text_so_the_icon_theme_cannot_change_them(self):
        """Temas de ícones de terceiros (ex.: só contorno, cor fixa) impediam o vermelho."""
        self.win.show_section("movie")
        heart = self.card_for("Tenet").heart
        self.assertEqual(heart.get_label(), self.A.HEART)
        self.assertIsNone(heart.get_icon_name())

    def test_hearts_reflect_the_database_when_pages_are_built(self):
        tenet = next(t for t in self.db.list_titles() if t["name"] == "Tenet")
        self.db.set_favorite(tenet["id"], True)
        self.win.reload()
        self.win.show_section("movie")  # página adiada: deve reflectir o estado actual da BD
        self.assertTrue(self.card_for("Tenet", "movie").heart.has_css_class("fav-on"))
        self.assertFalse(self.card_for("Cidade de Deus", "movie").heart.has_css_class("fav-on"))

    def test_the_heart_in_the_detail_popup_toggles_the_same_favorite(self):
        from unittest import mock
        tenet = next(t for t in self.db.list_titles() if t["name"] == "Tenet")
        shown = []
        original = self.Adw.Dialog.present

        def record(dialog, parent):
            shown.append(dialog)
            return original(dialog, parent)

        with mock.patch.object(self.Adw.Dialog, "present", record):
            self.win.open_detail(tenet["id"])
        heart = next(b for b in walk(shown[0], self.Gtk.Button) if b.has_css_class("heart-header"))
        self.assertEqual(heart.get_label(), self.A.HEART)             # texto, não ícone do tema
        self.assertIsNone(heart.get_icon_name())
        self.assertFalse(heart.has_css_class("fav-on"))
        self.win.show_section("movie")
        heart.emit("clicked")
        self.assertTrue(heart.has_css_class("fav-on"))
        self.assertEqual(self.db.get_title(tenet["id"])["favorite"], 1)
        self.assertTrue(self.card_for("Tenet", "movie").heart.has_css_class("fav-on"))
        heart.emit("clicked")                                        # e desfaz
        self.assertEqual(self.db.get_title(tenet["id"])["favorite"], 0)

    def watch(self, name_part, position, duration):
        path = next(p for (p,) in self.db.conn.execute("SELECT path FROM files") if name_part in p)
        self.db.save_progress(path, position, duration)

    def test_home_without_progress_says_there_is_nothing_to_continue(self):
        page = self.win.pages["home"].get_child()
        self.assertIsInstance(page, self.Adw.StatusPage)
        self.assertEqual(page.get_title(), "Nada para continuar")

    def test_home_of_an_empty_library_points_to_the_plus_button(self):
        from database import Database
        empty = Database(Path(self.tmp_dir) / "vazio.db")
        self.addCleanup(empty.conn.close)
        win = self.A.CinePrimeWindow(self.application, empty)
        self.addCleanup(win.destroy)
        self.addCleanup(win.on_close, win)
        self.assertEqual(win.pages["home"].get_child().get_title(), "Sua biblioteca está vazia")

    def test_home_mixes_movies_and_series_in_progress(self):
        self.watch("Tenet", 1800, 7200)
        self.watch("Spider-Noir-EP01", 30, 100)
        self.win.reload()
        self.assertEqual(self.shown_titles(), ["Spider Noir", "Tenet"])

    def test_home_leaves_out_what_was_finished_or_barely_opened(self):
        self.watch("Tenet", 7100, 7200)
        self.watch("Cidade de Deus", 2, 7200)
        self.win.reload()
        self.assertEqual(self.shown_titles(), [])

    def test_home_cards_show_the_bar_over_the_cover_only_once_started(self):
        self.watch("Tenet", 1800, 7200)                  # filme a meio: barra
        self.watch("Spider-Noir-EP01", 98, 100)          # próximo episódio ainda não começado: sem barra
        self.win.reload()
        bars = {}
        for card in self.A.iter_cards(self.win.pages["home"]):
            bars[self.db.get_title(card.title_id)["name"]] = walk(card, self.Gtk.ProgressBar)
        self.assertEqual({n: len(b) for n, b in bars.items()}, {"Tenet": 1, "Spider Noir": 0})
        self.assertIsInstance(bars["Tenet"][0].get_parent(), self.Gtk.Overlay)    # por cima da capa

    def test_saving_progress_refreshes_home_without_a_full_reload(self):
        """É o que acontece quando o backend guarda o progresso: o Início se atualiza na hora."""
        path = next(p for (p,) in self.db.conn.execute("SELECT path FROM files") if "Tenet" in p)
        self.win.save_progress({path: (1800, 7200)})
        self.assertEqual(self.shown_titles(), ["Tenet"])

    def test_clicking_a_home_card_goes_through_the_same_handler_as_the_grids(self):
        self.watch("Tenet", 1800, 7200)
        self.win.reload()
        grid = walk(self.win.stack.get_visible_child(), self.Gtk.GridView)[0]
        model = grid.get_model().get_model()
        self.assertEqual(self.db.get_title(int(model.get_item(0).get_string()))["name"], "Tenet")
        from unittest import mock
        with mock.patch.object(self.win, "open_detail") as open_detail:
            self.win.on_card_activated(grid, 0)
        open_detail.assert_called_once_with(int(model.get_item(0).get_string()))

    def test_add_button_opens_the_folders_dialog_and_changes_no_page(self):
        from unittest import mock
        self.win.open_folders_dialog = mock.Mock()
        self.win.add_button.emit("clicked")
        self.win.open_folders_dialog.assert_called_once_with()
        self.assertEqual(self.active_pages(), ["home"])

    def test_search_covers_movies_and_series_together(self):
        self.search("spider")
        self.assertEqual(self.win.stack.get_visible_child_name(), self.A.SEARCH_PAGE)
        self.assertEqual(self.shown_titles(), ["Spider Noir", "Spider-Man Brand New Day"])

    def test_search_ignores_case_accents_and_punctuation(self):
        self.search("SPIDER man")                       # vs. "Spider-Man Brand New Day"
        self.assertEqual(self.shown_titles(), ["Spider-Man Brand New Day"])
        self.search("cidáde de deus")                   # acento a mais: também acha (ignora nos dois lados)
        self.assertEqual(self.shown_titles(), ["Cidade de Deus"])
        self.search("ciDADE de DEUS")
        self.assertEqual(self.shown_titles(), ["Cidade de Deus"])

    def test_search_without_results_shows_a_message(self):
        self.search("zzzz")
        self.assertEqual(self.win.stack.get_visible_child_name(), self.A.SEARCH_PAGE)
        self.assertIsInstance(self.win.pages[self.A.SEARCH_PAGE].get_child(), self.Adw.StatusPage)

    def test_search_deactivates_the_dock_and_clearing_restores_it(self):
        self.win.show_section("series")
        self.search("spider")
        self.assertEqual(self.active_pages(), [])
        self.search("")
        self.assertEqual(self.win.stack.get_visible_child_name(), "series")
        self.assertEqual(self.active_pages(), ["series"])

    def test_choosing_a_section_ends_the_search(self):
        self.search("spider")
        self.win.show_section("movie")
        self.assertEqual(self.win.search_entry.get_text(), "")
        self.assertEqual(self.win.query, "")
        self.assertEqual(self.win.stack.get_visible_child_name(), "movie")

    def test_close_defers_temp_cleanup_until_background_worker_finishes(self):
        import threading

        temp = Path(self.tmp_dir) / "worker-temp"
        temp.mkdir()
        self.win.tmp_dir = str(temp)
        started = threading.Event()
        release = threading.Event()

        def worker():
            started.set()
            release.wait(3)
            (temp / "finished.txt").write_text("complete")

        thread = self.win._start_worker(worker)
        self.assertIsNotNone(thread)
        self.assertTrue(started.wait(1))
        self.win.on_close(self.win)
        self.assertTrue(temp.exists(), "a pasta não pode ser removida durante a escrita")
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(temp.exists(), "a pasta deve ser removida depois do último worker")
        self.assertIsNone(self.win.tmp_dir)

    def test_reload_rebuilds_only_the_visible_page(self):
        from unittest import mock

        self.win.show_section("movie")
        with mock.patch.object(self.win, "build_page", wraps=self.win.build_page) as build_page, \
             mock.patch.object(self.win, "build_home_page", wraps=self.win.build_home_page) as build_home, \
             mock.patch.object(self.win, "build_favorites_page", wraps=self.win.build_favorites_page) as build_favorites:
            self.win.reload()

        self.assertEqual(build_page.call_count, 1)
        self.assertEqual(build_page.call_args.args, ("movie",))
        build_home.assert_not_called()
        build_favorites.assert_not_called()
        self.assertIn("home", self.win._dirty_pages)
        self.assertIn("series", self.win._dirty_pages)
        self.assertIn("favorites", self.win._dirty_pages)
        self.assertNotIn("movie", self.win._dirty_pages)

    def test_dirty_page_is_rebuilt_only_when_opened(self):
        from unittest import mock

        self.win.show_section("movie")
        self.win.reload()
        with mock.patch.object(self.win, "build_page", wraps=self.win.build_page) as build_page:
            self.win.show_section("series")
            self.win.show_section("series")
        self.assertEqual(build_page.call_count, 1)
        self.assertEqual(build_page.call_args.args, ("series",))
        self.assertNotIn("series", self.win._dirty_pages)

    def test_hidden_home_is_refreshed_when_navigated_to(self):
        from unittest import mock

        self.win.show_section("movie")
        self.win.save_progress({next(p for (p,) in self.db.conn.execute("SELECT path FROM files") if "Tenet" in p): (1800, 7200)})
        self.assertIn("home", self.win._dirty_pages)
        with mock.patch.object(self.win, "build_home_page", wraps=self.win.build_home_page) as build_home:
            self.win.show_section("home")
            self.assertEqual(self.shown_titles(), ["Tenet"])
        self.assertEqual(build_home.call_count, 1)
        self.assertNotIn("home", self.win._dirty_pages)

    def test_reload_keeps_the_search_results_up_to_date(self):
        self.search("spider")
        tenet = next(t for t in self.db.list_titles() if t["name"] == "Tenet")
        self.db.set_display_name(tenet["id"], "Spider Teste")      # renomeado enquanto há busca
        self.win.reload()
        self.assertEqual(self.win.stack.get_visible_child_name(), self.A.SEARCH_PAGE)
        self.assertEqual(self.shown_titles(), ["Spider Noir", "Spider-Man Brand New Day", "Tenet"])


    # ---------- atalhos de teclado ----------
    def fake_session(self):
        from unittest import mock
        session = mock.Mock()
        session.running = True
        session.paused = False
        self.win.session = session
        return session

    def press(self, keyval, state=0):
        return self.win.on_key_pressed(None, keyval, 0, self.Gdk.ModifierType(state))

    def test_space_toggles_pause_while_something_plays(self):
        session = self.fake_session()
        self.assertTrue(self.press(self.Gdk.KEY_space))
        session.toggle_pause.assert_called_once_with()

    def test_n_and_s_work_with_or_without_shift(self):
        session = self.fake_session()
        for key in (self.Gdk.KEY_n, self.Gdk.KEY_N):
            self.assertTrue(self.press(key))
        for key in (self.Gdk.KEY_s, self.Gdk.KEY_S):
            self.assertTrue(self.press(key))
        self.assertEqual(session.next_track.call_count, 2)
        self.assertEqual(session.stop.call_count, 2)

    def test_f_focuses_the_search_with_or_without_shift(self):
        from unittest import mock
        with mock.patch.object(self.win.search_entry, "grab_focus") as grab:
            self.assertTrue(self.press(self.Gdk.KEY_f))
            self.assertTrue(self.press(self.Gdk.KEY_F))
        self.assertEqual(grab.call_count, 2)

    def test_nothing_is_a_shortcut_while_typing(self):
        """Digitar "Spider" na busca ou no nome de um título não pode acionar o "parar"."""
        from unittest import mock
        session = self.fake_session()
        with mock.patch.object(self.win, "get_focus", return_value=self.win.search_entry.get_delegate()):
            for key in (self.Gdk.KEY_space, self.Gdk.KEY_n, self.Gdk.KEY_s, self.Gdk.KEY_f):
                self.assertFalse(self.press(key))
        session.toggle_pause.assert_not_called()
        session.next_track.assert_not_called()
        session.stop.assert_not_called()

    def test_ctrl_and_alt_combinations_are_left_alone(self):
        session = self.fake_session()
        self.assertFalse(self.press(self.Gdk.KEY_s, self.Gdk.ModifierType.CONTROL_MASK))
        self.assertFalse(self.press(self.Gdk.KEY_n, self.Gdk.ModifierType.ALT_MASK))
        session.stop.assert_not_called()
        session.next_track.assert_not_called()

    def test_without_playback_space_n_s_are_not_consumed(self):
        self.win.session = None
        for key in (self.Gdk.KEY_space, self.Gdk.KEY_n, self.Gdk.KEY_s):
            self.assertFalse(self.press(key))

    def test_open_dialog_turns_the_shortcuts_off(self):
        from unittest import mock
        session = self.fake_session()
        with mock.patch.object(self.win, "get_visible_dialog", return_value=object()):
            self.assertFalse(self.press(self.Gdk.KEY_space))
        session.toggle_pause.assert_not_called()

    # ---------- barra de reprodução ----------
    def play_with_fake_backend(self, **factory_kwargs):
        from unittest import mock
        tenet = next(t for t in self.db.list_titles() if t["name"] == "Tenet")
        resume = self.db.resume_plan(tenet["id"])
        fake = factory_kwargs.get("return_value")
        if fake is not None:
            # A GLArea é a superfície falsa usada pelos testes; a sessão real
            # também expõe estas operações de controlo ao método play().
            fake.set_subtitle_delay = mock.Mock()
            fake.set_subtitle_font_size = mock.Mock()
            fake.save_now = mock.Mock()
            fake.close = mock.Mock(side_effect=lambda: setattr(fake, "running", False))
            fake.stop = mock.Mock(side_effect=lambda: setattr(fake, "running", False))
        with mock.patch.object(self.A, "EmbeddedGStreamerSession", **factory_kwargs) as factory:
            self.win.play(resume)
        return factory

    def test_play_pause_button_shows_the_action_not_the_state(self):
        from unittest import mock
        session = mock.Mock(running=True, paused=False)
        self.win.session = session
        self.win.update_playback_buttons()
        self.assertEqual(self.win.play_pause_button.get_icon_name(), "media-playback-pause-symbolic")
        self.assertEqual(self.win.play_pause_button.get_tooltip_text(), "Pausar reprodução")
        session.paused = True
        self.win.update_playback_buttons()
        self.assertEqual(self.win.play_pause_button.get_icon_name(), "media-playback-start-symbolic")
        self.assertEqual(self.win.play_pause_button.get_tooltip_text(), "Retomar reprodução")

    def test_bar_appears_when_gstreamer_starts_and_follows_its_real_state(self):
        from unittest import mock
        fake = self.Gtk.GLArea()
        fake.running = True
        fake.paused = False
        factory = self.play_with_fake_backend(return_value=fake)
        self.assertTrue(self.win.player_view.get_visible())
        self.assertFalse(self.win.header.get_visible())
        self.assertFalse(self.win.dock.get_visible())
        self.assertEqual(factory.call_args.kwargs["on_state"], self.win.update_playback_buttons)
        self.assertEqual(self.win.play_pause_button.get_icon_name(), "media-playback-pause-symbolic")

    def test_player_controls_hide_together_and_show_again(self):
        fake = self.Gtk.GLArea()
        fake.running = True
        fake.paused = False
        self.play_with_fake_backend(return_value=fake)

        self.assertTrue(self.win.player_top.get_visible())
        self.assertTrue(self.win.player_bottom.get_visible())
        self.win.hide_player_controls()
        self.assertFalse(self.win.player_top.get_visible())
        self.assertFalse(self.win.player_bottom.get_visible())

        self.win.show_player_controls()
        self.assertTrue(self.win.player_top.get_visible())
        self.assertTrue(self.win.player_bottom.get_visible())
        self.assertGreater(self.win._controls_timeout, 0)

    def test_stationary_pointer_events_do_not_restart_the_hide_timer(self):
        fake = self.Gtk.GLArea()
        fake.running = True
        fake.paused = False
        self.play_with_fake_backend(return_value=fake)

        self.win._on_player_pointer_motion(None, 100.0, 200.0)
        self.win._last_pointer_motion_us -= 2_000_000
        old_timestamp = self.win._last_pointer_motion_us

        # O GTK pode enviar o mesmo ponto várias vezes sem movimento físico.
        self.win._on_player_pointer_motion(None, 100.0, 200.0)
        self.assertEqual(self.win._last_pointer_motion_us, old_timestamp)

        # Uma mudança real de posição deve reiniciar o período de inatividade.
        self.win._on_player_pointer_motion(None, 104.0, 200.0)
        self.assertGreater(self.win._last_pointer_motion_us, old_timestamp)

    def test_player_controls_really_hide_after_three_seconds(self):
        from gi.repository import GLib
        fake = self.Gtk.GLArea()
        fake.running = True
        fake.paused = False
        self.play_with_fake_backend(return_value=fake)

        loop = GLib.MainLoop()
        GLib.timeout_add(3200, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
        loop.run()

        self.assertFalse(self.win.player_top.get_visible())
        self.assertFalse(self.win.player_bottom.get_visible())
        self.assertEqual(self.win._controls_timeout, 0)

    def test_player_view_is_not_created_when_embedded_backend_cannot_be_constructed(self):
        self.play_with_fake_backend(side_effect=RuntimeError("backend unavailable"))
        self.assertIsNone(self.win.player_view)
        self.assertIsNone(self.win.session)

    def test_toggle_playback_waits_for_backend_before_changing_the_icon(self):
        from unittest import mock
        session = mock.Mock(running=True, paused=False)
        self.win.session = session
        with mock.patch.object(self.win, "update_playback_buttons") as update:
            self.win.toggle_playback()
        session.toggle_pause.assert_called_once_with()
        update.assert_not_called()                       # o ícone muda quando o backend confirma (on_state)

    def test_embedded_player_exposes_seek_track_speed_and_volume_controls(self):
        from unittest import mock
        fake = self.Gtk.GLArea()
        fake.running = True
        fake.paused = False
        self.play_with_fake_backend(return_value=fake)
        self.assertIsNotNone(self.win.progress_scale)
        self.assertIsNotNone(self.win.audio_menu)
        self.assertIsNotNone(self.win.subtitle_menu)
        self.assertIsNotNone(self.win.speed_menu)
        self.assertIsNotNone(self.win.volume_scale)
        self.assertTrue(self.win.player_view.get_visible())
        self.assertEqual(
            self.win.progress_scale._cineprime_accessible_label,
            "Progresso e posição da reprodução",
        )
        self.assertEqual(
            self.win.volume_scale._cineprime_accessible_label,
            "Volume de reprodução",
        )
        self.assertTrue(self.win.play_pause_button._cineprime_accessible_label)
        self.assertTrue(self.win.next_button._cineprime_accessible_label)

    def test_leaving_embedded_playback_restores_the_library_interface(self):
        fake = self.Gtk.GLArea()
        fake.running = True
        fake.paused = False
        self.play_with_fake_backend(return_value=fake)
        self.win.on_session_end()
        self.assertIsNone(self.win.session)
        self.assertIsNone(self.win.player_view)
        self.assertTrue(self.win.header.get_visible())
        self.assertTrue(self.win.dock.get_visible())
        self.assertFalse(self.win._is_fullscreen)

    # ---------- visual (estilo streaming) ----------
    def test_cards_show_the_file_format_as_a_badge_over_the_cover(self):
        self.win.show_section("movie")
        badges = [l for l in walk(self.card_for("Tenet"), self.Gtk.Label) if l.has_css_class("badge")]
        self.assertEqual([b.get_label() for b in badges], ["MP4"])
        self.assertIsInstance(badges[0].get_parent(), self.Gtk.Overlay)

    def test_the_app_always_uses_the_dark_theme(self):
        self.assertEqual(self.Adw.StyleManager.get_default().get_color_scheme(), self.Adw.ColorScheme.FORCE_DARK)
        self.assertTrue(self.win.has_css_class("cineprime"))

    def test_the_stylesheet_has_no_parse_errors_on_this_gtk(self):
        """Propriedades que o GTK não conhece (ex.: overflow) só dão erro em tempo de execução."""
        errors = []
        provider = self.Gtk.CssProvider()
        provider.connect("parsing-error", lambda _p, section, error: errors.append(error.message))
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(self.A.ALL_CSS)
        else:
            provider.load_from_data(self.A.ALL_CSS.encode())
        self.assertEqual(errors, [])

    # ---------- efeito do cursor nos cartões ----------
    def a_card(self):
        self.win.show_section("movie")
        return self.card_for("Tenet")

    def sized(self, card, width=160, height=280):
        """Os cartões de teste não são desenhados na tela (tamanho 0): damos um tamanho fixo."""
        from unittest import mock
        stack = mock.patch.multiple(card, get_width=lambda: width, get_height=lambda: height)
        stack.start()
        self.addCleanup(stack.stop)

    def test_cards_are_card_boxes_that_know_their_cover(self):
        import effects
        card = self.a_card()
        self.assertIsInstance(card, effects.CardBox)
        self.assertIsNotNone(card.cover)

    def test_the_card_leans_toward_the_pointer_and_the_glare_turns_on(self):
        card = self.a_card()
        self.sized(card)
        card._pointer = (4, 4)                           # canto superior esquerdo
        for _ in range(90):
            card.step(1 / 60)
        self.assertGreater(card._rx, 5)                  # topo afunda
        self.assertLess(card._ry, -5)                    # esquerda afunda
        self.assertGreater(card._glare, 0.95)
        self.assertFalse(card.is_idle())

    def test_leaving_brings_the_card_back_to_rest(self):
        card = self.a_card()
        self.sized(card)
        card._pointer = (4, 4)
        for _ in range(60):
            card.step(1 / 60)
        card._pointer = None
        for _ in range(240):
            card.step(1 / 60)
        self.assertTrue(card.is_idle())
        self.assertAlmostEqual(card._rx, 0.0, places=1)

    def test_with_animations_off_in_the_system_the_card_stays_still(self):
        from unittest import mock
        card = self.a_card()
        with mock.patch("effects.animations_enabled", return_value=False):
            card._on_pointer(None, 20, 20)
        self.assertIsNone(card._pointer)
        self.assertEqual(card._tick_id, 0)

    def test_the_pointer_starts_the_animation_and_leaving_keeps_it_running_until_rest(self):
        card = self.a_card()
        card._on_pointer(None, 20, 20)
        self.assertNotEqual(card._tick_id, 0)
        self.assertEqual(card._gx, 20)                   # o brilho nasce debaixo do cursor
        card._on_leave(None)
        self.assertIsNone(card._pointer)

    def test_drawing_works_at_rest_tilted_and_with_the_glare(self):
        """do_snapshot não pode falhar em nenhum dos três estados (só roda com a janela na tela)."""
        from unittest import mock
        from gi.repository import Graphene
        card = self.a_card()
        self.sized(card)
        rect = Graphene.Rect().init(0, 0, 160, 240)
        card.cover = mock.Mock(compute_bounds=lambda target: (True, rect))
        for rx, ry, glare in ((0.0, 0.0, 0.0), (5.0, -4.0, 0.0), (5.0, -4.0, 1.0)):
            with self.subTest(rx=rx, ry=ry, glare=glare):
                card._rx, card._ry, card._glare, card._gx, card._gy = rx, ry, glare, 30.0, 40.0
                snapshot = self.Gtk.Snapshot()
                card.do_snapshot(snapshot)
                if glare:
                    self.assertIsNotNone(snapshot.to_node())     # o brilho foi mesmo desenhado


    def test_backup_button_is_present_and_connected_to_backup_menu(self):
        from unittest.mock import patch
        self.assertIsNotNone(self.win.backup_button)
        self.assertEqual(self.win.backup_button.get_tooltip_text(), "Backup e restauro da biblioteca")
        with patch.object(self.win, "open_backup_menu") as open_menu:
            self.win.backup_button.emit("clicked")
        open_menu.assert_called_once_with()

    def test_settings_button_opens_anchored_autohide_popover(self):
        self.assertEqual(self.win.settings_button.get_tooltip_text(), "Definições e manutenção da biblioteca")
        self.win.settings_button.emit("clicked")
        popover = self.win.settings_popover
        self.assertIsInstance(popover, self.Gtk.Popover)
        self.assertTrue(popover.get_autohide())
        self.assertFalse(popover.get_has_arrow())
        self.assertEqual(popover.get_position(), self.Gtk.PositionType.BOTTOM)
        self.assertIs(popover.get_parent(), self.win.settings_button)
        labels = [label.get_text() for label in walk(popover, self.Gtk.Label)]
        for expected in (
            "Biblioteca", "Actualizar biblioteca", "Buscar capas em falta",
            "Verificar ficheiros duplicados", "Integridade e cópias de segurança",
            "Verificação profunda da integridade", "Backup e restauro",
        ):
            self.assertIn(expected, labels)

    def test_primary_icon_controls_have_accessible_names_and_keyboard_focus(self):
        controls = [self.win.settings_button, self.win.add_button, *self.win.dock_buttons.values()]
        expected = {
            "Abrir definições e manutenção", "Adicionar pasta à biblioteca",
            "Início", "Filmes", "Séries", "Favoritos",
        }
        labels = {getattr(widget, "_cineprime_accessible_label", None) for widget in controls}
        self.assertTrue(expected.issubset(labels))
        for widget in controls:
            with self.subTest(label=getattr(widget, "_cineprime_accessible_label", None)):
                self.assertTrue(widget.get_focusable())
                self.assertEqual(widget.get_accessible_role(), self.Gtk.AccessibleRole.BUTTON)
                self.assertTrue(widget._cineprime_accessible_label)
        self.assertEqual(
            self.win.search_entry._cineprime_accessible_label,
            "Pesquisar filmes e séries na biblioteca",
        )
        self.assertTrue(self.win.search_entry.get_focusable())

    def test_settings_popover_blocks_global_shortcuts_and_escape_returns_focus(self):
        from unittest.mock import patch
        self.win.open_settings_dialog()
        popover = self.win.settings_popover
        self.assertTrue(popover.get_visible())
        original_query = self.win.query
        self.assertFalse(self.press(self.Gdk.KEY_f))
        self.assertEqual(self.win.query, original_query)
        with patch.object(self.win.settings_button, "grab_focus") as grab:
            self.assertTrue(self.press(self.Gdk.KEY_Escape))
        self.assertFalse(popover.get_visible())
        grab.assert_called_once_with()

    def test_settings_popover_width_adapts_to_a_compact_window(self):
        self.win.set_default_size(360, 360)
        self.win.open_settings_dialog()
        self.assertEqual(self.win.settings_popover.get_size_request()[0], 336)

    def test_dialog_widths_respect_small_window_bounds(self):
        self.win.set_default_size(360, 360)
        self.assertEqual(self.win.dialog_content_width(680), 328)
        self.assertEqual(self.win.dialog_content_width(480), 328)
        self.win.set_default_size(1000, 700)
        self.assertEqual(self.win.dialog_content_width(680), 680)

    def test_empty_movie_and_series_categories_explain_how_to_add_content(self):
        from unittest.mock import patch
        with patch.object(self.db, "list_titles", return_value=[]), \
             patch("app.config.get_folders", return_value=[]):
            for kind in ("movie", "series"):
                with self.subTest(kind=kind):
                    page = self.win.build_page(kind)
                    self.assertIsInstance(page, self.Adw.StatusPage)
                    self.assertEqual(page.get_title(), "Nada por aqui ainda")
                    actions = walk(page, self.Gtk.Button)
                    self.assertEqual(len(actions), 1)
                    self.assertEqual(actions[0].get_label(), "Adicionar pasta…")
                    self.assertTrue(actions[0].get_focusable())

    def test_duplicate_diagnostic_has_a_clear_empty_state(self):
        with __import__("unittest").mock.patch.object(self.db, "duplicate_groups", return_value=[]):
            dialog = self.win.open_duplicates_dialog()
        pages = walk(dialog, self.Adw.StatusPage)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].get_title(), "Nenhum duplicado encontrado")

    def test_folder_manager_is_scrollable_with_many_folders(self):
        from unittest.mock import patch
        folders = [
            {"path": f"/tmp/Cine Prime Library Folder {index:02d}", "kind": "auto"}
            for index in range(24)
        ]
        with patch("app.config.get_folders", return_value=folders):
            dialog = self.win.open_folders_dialog()
        scrolls = walk(dialog, self.Gtk.ScrolledWindow)
        self.assertEqual(len(scrolls), 1)
        self.assertEqual(scrolls[0].get_policy(), (self.Gtk.PolicyType.NEVER, self.Gtk.PolicyType.AUTOMATIC))
        self.assertEqual(scrolls[0].get_max_content_height(), 560)
        listboxes = walk(scrolls[0], self.Gtk.ListBox)
        self.assertEqual(len(listboxes), 1)
        self.assertEqual(sum(1 for _ in iter_listbox_rows(listboxes[0])), 24)

    def test_folder_manager_empty_state_explains_how_to_add_a_folder(self):
        from unittest.mock import patch
        with patch("app.config.get_folders", return_value=[]):
            dialog = self.win.open_folders_dialog()
        labels = [label.get_text() for label in walk(dialog, self.Gtk.Label)]
        buttons = [button.get_label() for button in walk(dialog, self.Gtk.Button)]
        self.assertIn("Nenhuma pasta adicionada ainda.", labels)
        self.assertIn("Adicionar pasta…", buttons)

    def test_scan_failure_restores_controls_and_reports_the_error(self):
        from unittest.mock import patch
        self.win.scanning = True
        with patch.object(self.win, "toast") as toast:
            result = self.win.scan_failed("Permissão negada")
        self.assertEqual(result, self.A.GLib.SOURCE_REMOVE)
        self.assertFalse(self.win.scanning)
        self.assertTrue(self.win.refresh_button.get_sensitive())
        toast.assert_called_once_with("Falha ao atualizar: Permissão negada")

    def test_cover_batch_failure_clears_busy_state_and_reports_the_error(self):
        from unittest.mock import patch
        self.win.fetching_covers = True
        with patch.object(self.win, "toast") as toast:
            self.win.cover_batch_failed("Sem ligação à rede")
        self.assertFalse(self.win.fetching_covers)
        toast.assert_called_once_with("Falha ao buscar capas: Sem ligação à rede")

    def test_player_failure_reports_error_and_returns_to_library(self):
        from unittest.mock import patch
        with patch.object(self.win, "toast") as toast, \
             patch.object(self.win, "on_session_end") as end_session:
            self.win.on_player_error("descodificador indisponível")
        toast.assert_called_once_with(
            "Não foi possível iniciar o vídeo: descodificador indisponível"
        )
        end_session.assert_called_once_with(None)

    def test_integrity_failure_dialog_identifies_the_problem_file(self):
        from unittest.mock import patch
        from media_integrity import IntegrityResult
        from pathlib import Path
        shown = []
        with patch.object(self.Adw.Dialog, "present", lambda dialog, parent: shown.append(dialog)):
            self.win.integrity_running = True
            self.win.finish_integrity_check([
                IntegrityResult(Path("/tmp/filme-danificado.mp4"), False, "Invalid frame data")
            ])
        self.assertFalse(self.win.integrity_running)
        self.assertTrue(self.win.integrity_button.get_sensitive())
        self.assertEqual(len(shown), 1)
        labels = [label.get_text() for label in walk(shown[0].get_child(), self.Gtk.Label)]
        self.assertTrue(any("filme-danificado.mp4" in label for label in labels))
        self.assertTrue(any("Invalid frame data" in label for label in labels))

    def test_integrity_check_requires_explicit_confirmation(self):
        from unittest.mock import patch

        with patch("app.shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch.object(self.win, "_run_integrity_check") as run_check:
            self.win.start_integrity_check()
            dialog = self.win._integrity_confirmation
            self.assertIsNotNone(dialog)
            self.assertFalse(self.win.integrity_running)
            self.assertTrue(self.win.integrity_button.get_sensitive())
            dialog.emit("response", "cancel")
            self.assertIsNone(self.win._integrity_confirmation)
            run_check.assert_not_called()

            self.win.start_integrity_check()
            dialog = self.win._integrity_confirmation
            dialog.emit("response", "start")
            run_check.assert_called_once()



if __name__ == "__main__":
    unittest.main()