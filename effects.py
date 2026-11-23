"""Efeito de cursor nos cartões: o cartão inclina para o lado do mouse e um brilho segue o ponteiro.

A matemática (ângulos, suavização) fica em funções simples, testáveis sem janela.
O desenho fica em CardBox: um Gtk.Box comum com o desenho (snapshot) sobrescrito.
"""
import math

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

MAX_TILT_DEG = 9.0        # inclinação máxima (com o mouse na borda do cartão)
PERSPECTIVE = 700.0       # distância da "câmera", em pixels: menor = 3D mais forte
GLARE_ALPHA = 0.26        # força do brilho no ponto do cursor
GLARE_RADIUS = 0.95       # raio do brilho, como fração da largura da capa
CORNER_RADIUS = 12.0      # o brilho é recortado nos cantos arredondados da capa
TILT_SPEED = 12.0         # suavização da inclinação (maior = mais rápido)
GLARE_SPEED = 22.0        # o brilho acompanha o cursor mais depressa que a inclinação
EPSILON = 0.02            # abaixo disto consideramos que a animação terminou


def tilt_target(x: float, y: float, width: float, height: float, max_deg: float = MAX_TILT_DEG):
    """Ângulos-alvo (rot_x, rot_y), em graus, para o ponteiro em (x, y) dentro do cartão.

    O lado do cursor "afunde": no centro = (0, 0); nas bordas = ±max_deg.
    Os sinais foram MEDIDOS no GTK: rotate_3d positivo no eixo Y afasta o lado direito,
    e no eixo X afasta o topo.
    """
    if width <= 0 or height <= 0:
        return 0.0, 0.0
    nx = max(-1.0, min(1.0, 2.0 * x / width - 1.0))     # -1 = borda esquerda, +1 = direita
    ny = max(-1.0, min(1.0, 2.0 * y / height - 1.0))    # -1 = topo, +1 = base
    return -ny * max_deg, nx * max_deg


def ease(current: float, target: float, dt: float, speed: float) -> float:
    """Aproxima `current` de `target`. Usa o tempo (dt, em segundos), então a velocidade
    é a mesma em telas de 60 Hz ou de 144 Hz."""
    return target + (current - target) * math.exp(-speed * max(dt, 0.0))


def glare_stops(alpha: float) -> list:
    """Do branco semitransparente (centro) ao transparente (borda).

    ATENÇÃO: Gdk.RGBA(1, 1, 1, 0.3) IGNORA os argumentos no PyGObject (a cor ficaria 0,0,0,0 e o
    brilho invisível, sem nenhum erro). Por isso a cor é montada com parse().
    """
    stops = []
    for offset, a in ((0.0, alpha), (1.0, 0.0)):
        stop = Gsk.ColorStop()
        stop.offset = offset
        color = Gdk.RGBA()
        color.parse(f"rgba(255,255,255,{a:.3f})")
        stop.color = color
        stops.append(stop)
    return stops


def animations_enabled() -> bool:
    """Respeita a configuração do sistema (Ajustes > Acessibilidade > Reduzir animações)."""
    settings = Gtk.Settings.get_default()
    return True if settings is None else bool(settings.get_property("gtk-enable-animations"))


class CardBox(Gtk.Box):
    """Cartão que inclina para o lado do mouse, com um brilho que segue o ponteiro."""

    __gtype_name__ = "CinePrimeCardBox"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cover = None                     # widget da capa: o brilho fica recortado nela
        self._pointer = None                  # (x, y) enquanto o mouse está em cima
        self._rx = self._ry = 0.0             # inclinação atual, em graus
        self._glare = 0.0                     # intensidade atual do brilho (0 a 1)
        self._gx = self._gy = 0.0             # posição atual do brilho
        self._tick_id = 0
        self._last = None
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_pointer)
        motion.connect("motion", self._on_pointer)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    # ---------- eventos ----------
    def _on_pointer(self, _controller, x, y):
        if not animations_enabled():
            return
        if self._pointer is None:             # o brilho nasce debaixo do cursor, sem "voar" de longe
            self._gx, self._gy = x, y
        self._pointer = (x, y)
        self._start()

    def _on_leave(self, _controller):
        self._pointer = None
        self._start()

    def _start(self):
        if not self._tick_id:
            self._last = None
            self._tick_id = self.add_tick_callback(self._tick)

    # ---------- animação (um passo por quadro) ----------
    def _tick(self, _widget, clock):
        now = clock.get_frame_time() / 1_000_000
        dt = 1 / 60 if self._last is None else now - self._last
        self._last = now
        self.step(dt)
        self.queue_draw()
        if self._pointer is None and self.is_idle():
            self._rx = self._ry = self._glare = 0.0
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def step(self, dt: float) -> None:
        """Avança a animação `dt` segundos em direção ao alvo (cursor em cima, ou repouso)."""
        if self._pointer is not None:
            target_rx, target_ry = tilt_target(*self._pointer, self.get_width(), self.get_height())
            target_glare = 1.0
            self._gx = ease(self._gx, self._pointer[0], dt, GLARE_SPEED)
            self._gy = ease(self._gy, self._pointer[1], dt, GLARE_SPEED)
        else:
            target_rx = target_ry = target_glare = 0.0
        self._rx = ease(self._rx, target_rx, dt, TILT_SPEED)
        self._ry = ease(self._ry, target_ry, dt, TILT_SPEED)
        self._glare = ease(self._glare, target_glare, dt, TILT_SPEED)

    def is_idle(self) -> bool:
        return max(abs(self._rx), abs(self._ry)) < EPSILON and self._glare < EPSILON / 10

    # ---------- desenho ----------
    def do_snapshot(self, snapshot):
        if max(abs(self._rx), abs(self._ry)) < EPSILON and self._glare < 0.001:
            Gtk.Widget.do_snapshot(self, snapshot)           # em repouso: desenho normal
            return
        w, h = self.get_width(), self.get_height()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(w / 2, h / 2))      # gira em torno do centro
        snapshot.perspective(PERSPECTIVE)
        snapshot.rotate_3d(self._rx, Graphene.Vec3().init(1, 0, 0))
        snapshot.rotate_3d(self._ry, Graphene.Vec3().init(0, 1, 0))
        snapshot.translate(Graphene.Point().init(-w / 2, -h / 2))
        Gtk.Widget.do_snapshot(self, snapshot)               # os filhos (capa, nome...) já inclinados
        self._snapshot_glare(snapshot)
        snapshot.restore()

    def _snapshot_glare(self, snapshot) -> None:
        if self._glare < 0.001 or self.cover is None:
            return
        found, bounds = self.cover.compute_bounds(self)
        if not found:
            return
        clip = Gsk.RoundedRect()
        clip.init_from_rect(bounds, CORNER_RADIUS)
        radius = bounds.get_width() * GLARE_RADIUS
        snapshot.push_rounded_clip(clip)
        snapshot.append_radial_gradient(
            bounds, Graphene.Point().init(self._gx, self._gy), radius, radius,
            0.0, 1.0, glare_stops(GLARE_ALPHA * self._glare),
        )
        snapshot.pop()