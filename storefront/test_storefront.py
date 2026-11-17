import copy
import hashlib
import unittest

import numpy as np
import PIL
from PIL import Image

from probes import scene_for
from shared_renderer import draw_display, render


class DisplayTests(unittest.TestCase):
    @unittest.skipUnless((PIL.__version__, np.__version__) == ("13.0.0", "2.5.4"),
                         "native RGB baseline pinned to the recorded Pillow/NumPy versions")
    def test_default_matches_original_native_bytes(self):
        scene = scene_for("legacy")
        image = render(scene)
        self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(),
                         "fe9d6dc42013012c4757ae356c01422692c7b6075b6433714deb5e43c2a8ce58")

    def test_geometry_and_tint_are_clipped_and_apply_to_merchandise(self):
        image = Image.new("RGB", (24, 30), (1, 2, 3))
        fixture = {"box": [0, .25, .5, .75], "shapes": [
            {"kind": "polygon", "points": [(0, 0), (1, 0), (1, 1)], "color": [180, 80, 40]}]}
        display = {"objects": [fixture], "glass": {"color": [20, 40, 60], "opacity": .5}}
        draw_display(image, (3, 5, 20, 24), display, [220, 180, 140], frame_width=2)
        self.assertEqual(image.getpixel((2, 14)), (1, 2, 3))
        self.assertEqual(image.getpixel((4, 14)), (1, 2, 3))
        self.assertEqual(image.getpixel((9, 12)), (100, 60, 50))
        self.assertEqual(image.getpixel((14, 18)), (120, 110, 100))

    def test_reject_missing_panes_and_bad_geometry(self):
        bad = scene_for("florist")
        bad["window_displays"].pop()
        with self.assertRaisesRegex(ValueError, "for each window"):
            render(bad)
        fixture = {"box": [0, 0, 1, 1], "shapes": [
            {"kind": "line", "points": [(0, 0), (2, 1)], "color": [0, 0, 0], "width": .2}]}
        with self.assertRaisesRegex(ValueError, "in \\[0, 1\\]"):
            draw_display(Image.new("RGB", (10, 10)), (0, 0, 9, 9), {"objects": [fixture]},
                         [0, 0, 0], frame_width=1)

    def test_native_development_images(self):
        for task, expected_goods_color in [("florist", (166, 62, 103)), ("bookshop", (43, 94, 108))]:
            with self.subTest(task=task):
                scene = scene_for(task)
                # Disable global tone remapping and blur only in this locality test.
                # The two inputs share the same seed, all other content and weather.
                scene.update(soften=0, contrast=1, saturation=1, grain=0)
                bare = copy.deepcopy(scene)
                for window in bare["window_displays"]:
                    window["objects"] = []
                displayed = np.asarray(render(scene), dtype=np.int16)
                empty = np.asarray(render(bare), dtype=np.int16)
                changed = np.any(displayed != empty, axis=2)
                outside = changed.copy()
                for win in scene["windows"]:
                    left, top, right, bottom = [int(t * scene["size"]) for t in win]
                    pane = changed[top+16:bottom-15, left+16:right-15]
                    self.assertGreater(np.count_nonzero(pane), 1500)
                    outside[top+16:bottom-15, left+16:right-15] = False
                self.assertFalse(outside.any(), "display changed a protected exterior pixel")
                x0, y0, x1, y1 = [int(t*scene["size"]) for t in scene["windows"][0]]
                target = np.array(expected_goods_color)
                glass = scene["window_displays"][0].get("glass")
                if glass:
                    target = (1-glass["opacity"])*target + glass["opacity"]*np.array(glass["color"])
                # Detect solid petal/spine pixels *introduced by* the object program,
                # so daylight or a magically empty pane cannot satisfy the assertion.
                roi = displayed[y0:y1, x0:x1]
                colored = np.max(np.abs(roi-target), axis=2) < 9
                self.assertGreater(np.count_nonzero(colored & changed[y0:y1, x0:x1]), 170)


if __name__ == "__main__":
    unittest.main()
