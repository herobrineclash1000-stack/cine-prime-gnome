"""Testes do efeito de cursor nos cartões (effects.py): a matemática, sem precisar de janela.

Rodar (na pasta do projeto):   python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gi  # noqa: E402

gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Graphene, Gsk  # noqa: E402

import effects  # noqa: E402

W, H = 160.0, 280.0


def project(rx, ry, px, py):
    """Onde o ponto (px, py), relativo ao centro do cartão, cai no ecrã depois da inclinação.
    Devolve (x, y) já com a divisão da perspectiva: quanto mais LONGE, mais perto do centro."""
    t = Gsk.Transform().perspective(effects.PERSPECTIVE)
    t = t.rotate_3d(rx, Graphene.Vec3().init(1, 0, 0))
    t = t.rotate_3d(ry, Graphene.Vec3().init(0, 1, 0))
    v = t.to_matrix().transform_vec4(Graphene.Vec4().init(px, py, 0, 1))
    return v.get_x() / v.get_w(), v.get_y() / v.get_w()


class TiltTargetTests(unittest.TestCase):
    def test_pointer_at_the_center_keeps_the_card_flat(self):
        self.assertEqual(effects.tilt_target(W / 2, H / 2, W, H), (0.0, 0.0))

    def test_the_corners_reach_the_maximum_in_each_direction(self):
        m = effects.MAX_TILT_DEG
        self.assertEqual(effects.tilt_target(0, 0, W, H), (m, -m))         # topo-esquerda
        self.assertEqual(effects.tilt_target(W, H, W, H), (-m, m))         # base-direita

    def test_a_pointer_outside_the_card_is_clamped(self):
        m = effects.MAX_TILT_DEG
        self.assertEqual(effects.tilt_target(-500, 9999, W, H), (-m, -m))

    def test_a_card_without_size_is_safe(self):
        self.assertEqual(effects.tilt_target(10, 10, 0, 0), (0.0, 0.0))

    def test_a_custom_maximum_is_respected(self):
        self.assertEqual(effects.tilt_target(W, H / 2, W, H, max_deg=4.0), (0.0, 4.0))


class DirectionTests(unittest.TestCase):
    """Os sinais foram medidos no GTK. Aqui MEDIMOS de novo o resultado: o lado do cursor afunda."""

    def test_pointer_on_the_right_pushes_the_right_side_away(self):
        rx, ry = effects.tilt_target(W, H / 2, W, H)
        right_x, _ = project(rx, ry, +80, 0)
        left_x, _ = project(rx, ry, -80, 0)
        self.assertLess(abs(right_x), abs(left_x))          # mais longe = mais perto do centro

    def test_pointer_on_the_left_pushes_the_left_side_away(self):
        rx, ry = effects.tilt_target(0, H / 2, W, H)
        right_x, _ = project(rx, ry, +80, 0)
        left_x, _ = project(rx, ry, -80, 0)
        self.assertLess(abs(left_x), abs(right_x))

    def test_pointer_at_the_top_pushes_the_top_away(self):
        rx, ry = effects.tilt_target(W / 2, 0, W, H)
        _, top_y = project(rx, ry, 0, -120)
        _, bottom_y = project(rx, ry, 0, +120)
        self.assertLess(abs(top_y), abs(bottom_y))

    def test_pointer_at_the_bottom_pushes_the_bottom_away(self):
        rx, ry = effects.tilt_target(W / 2, H, W, H)
        _, top_y = project(rx, ry, 0, -120)
        _, bottom_y = project(rx, ry, 0, +120)
        self.assertLess(abs(bottom_y), abs(top_y))


class EaseTests(unittest.TestCase):
    def test_no_time_no_movement(self):
        self.assertEqual(effects.ease(3.0, 9.0, 0.0, 12.0), 3.0)
        self.assertEqual(effects.ease(3.0, 9.0, -1.0, 12.0), 3.0)      # tempo negativo = como zero

    def test_a_long_time_reaches_the_target(self):
        self.assertAlmostEqual(effects.ease(0.0, 9.0, 5.0, 12.0), 9.0, places=6)

    def test_it_moves_toward_the_target_without_overshooting(self):
        for start, target in ((0.0, 9.0), (9.0, 0.0), (-4.0, 6.0)):
            value = effects.ease(start, target, 1 / 60, 12.0)
            self.assertTrue(min(start, target) <= value <= max(start, target))
            self.assertNotEqual(value, start)

    def test_the_speed_does_not_depend_on_the_frame_rate(self):
        """Duas passadas de meio quadro valem uma de quadro inteiro (60 Hz = 144 Hz)."""
        once = effects.ease(0.0, 9.0, 1 / 30, 12.0)
        twice = effects.ease(effects.ease(0.0, 9.0, 1 / 60, 12.0), 9.0, 1 / 60, 12.0)
        self.assertAlmostEqual(once, twice, places=9)


class GlareStopsTests(unittest.TestCase):
    def test_the_glare_color_is_really_white_not_the_silent_black_of_rgba_args(self):
        """Gdk.RGBA(1,1,1,0.3) ignora os argumentos no PyGObject: o brilho ficaria invisível."""
        first, last = effects.glare_stops(0.3)
        self.assertEqual((first.offset, last.offset), (0.0, 1.0))
        self.assertEqual((first.color.red, first.color.green, first.color.blue), (1.0, 1.0, 1.0))
        self.assertAlmostEqual(first.color.alpha, 0.3, places=2)
        self.assertEqual(last.color.alpha, 0.0)

    def test_a_weaker_glare_has_a_lower_alpha(self):
        strong = effects.glare_stops(0.26)[0].color.alpha
        weak = effects.glare_stops(0.26 * 0.25)[0].color.alpha
        self.assertLess(weak, strong)


if __name__ == "__main__":
    unittest.main()