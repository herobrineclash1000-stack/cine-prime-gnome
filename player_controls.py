"""Controlos de reprodução próprios do Cine Prime."""
from __future__ import annotations

from gi.repository import Gtk


class PlayerProgressBar(Gtk.Scale):
    """Barra de procura com thumb circular branco, arrastável e clicável."""

    def __init__(self, on_seek):
        adjustment = Gtk.Adjustment.new(0.0, 0.0, 1.0, 1.0, 10.0, 0.0)
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
        self._on_seek = on_seek
        self._duration = 1.0
        self._syncing = False
        self.set_draw_value(False)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Progresso e posição da reprodução"]
        )
        self._cineprime_accessible_label = "Progresso e posição da reprodução"
        self.set_hexpand(True)
        self.set_size_request(-1, 20)
        self.add_css_class("player-progress-track")
        self.set_can_target(True)
        self.set_value(0.0)
        self.connect("value-changed", self._on_value_changed)

    def _on_value_changed(self, _scale):
        if not self._syncing and self._on_seek:
            self._on_seek(self.get_value())

    def set_range(self, lower, upper):
        self._duration = max(0.0, float(upper) - float(lower)) or 1.0
        self._syncing = True
        Gtk.Range.set_range(self, float(lower), float(upper))
        Gtk.Range.set_value(self, min(float(upper), max(float(lower), self.get_value())))
        self._syncing = False

    def set_value(self, value):
        self._syncing = True
        Gtk.Range.set_value(self, min(self._duration, max(0.0, float(value))))
        self._syncing = False

    def get_value(self):
        return Gtk.Range.get_value(self)