from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image as PILImage

from image_confusion_core import (
    MAX_IMAGE_PIXELS,
    MAX_INPUT_BYTES,
    gilbert2d_indices,
    transform_image_file,
    transform_rgba_bytes,
    validate_image_dimensions,
)


class ImageConfusionCoreTests(unittest.TestCase):
    def test_gilbert_curve_visits_every_pixel_once(self) -> None:
        for width, height in ((1, 1), (2, 3), (7, 5), (5, 8), (13, 13)):
            with self.subTest(width=width, height=height):
                curve = gilbert2d_indices(width, height)
                self.assertEqual(len(curve), width * height)
                self.assertEqual(sorted(curve), list(range(width * height)))

    def test_scramble_and_descramble_round_trip(self) -> None:
        width, height = 17, 11
        original = bytes((index * 37 + 11) % 256 for index in range(width * height * 4))

        scrambled, _, _ = transform_rgba_bytes(
            original,
            width,
            height,
            "scramble",
            count=7,
        )
        restored, _, _ = transform_rgba_bytes(
            scrambled,
            width,
            height,
            "descramble",
            count=7,
        )

        self.assertNotEqual(scrambled, original)
        self.assertEqual(restored, original)

    def test_file_round_trip_preserves_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            original_path = root / "original.png"
            scrambled_path = root / "scrambled.png"
            restored_path = root / "restored.png"
            image = PILImage.new("RGBA", (19, 13))
            image.putdata(
                [
                    (
                        (x * 17 + y * 3) % 256,
                        (x * 5 + y * 19) % 256,
                        (x * 11 + y * 7) % 256,
                        255,
                    )
                    for y in range(13)
                    for x in range(19)
                ]
            )
            image.save(original_path)

            transform_image_file(
                str(original_path),
                str(scrambled_path),
                "scramble",
                count=3,
            )
            transform_image_file(
                str(scrambled_path),
                str(restored_path),
                "descramble",
                count=3,
            )

            with PILImage.open(original_path) as original_image:
                original_bytes = original_image.convert("RGBA").tobytes()
            with PILImage.open(restored_path) as restored_image:
                restored_bytes = restored_image.convert("RGBA").tobytes()
            self.assertEqual(restored_bytes, original_bytes)

    def test_rejects_unsupported_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持的处理模式"):
            transform_rgba_bytes(bytes(16), 2, 2, "rotate")

    def test_rejects_excessive_pixel_count_before_allocating_curve(self) -> None:
        with self.assertRaisesRegex(ValueError, "图片像素过大"):
            validate_image_dimensions(
                MAX_IMAGE_PIXELS + 1,
                1,
                max_image_pixels=MAX_IMAGE_PIXELS,
            )

    def test_rejects_oversized_input_before_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "oversized.bin"
            output = root / "output.png"
            with source.open("wb") as handle:
                handle.seek(MAX_INPUT_BYTES)
                handle.write(b"\0")

            with self.assertRaisesRegex(ValueError, "图片文件过大"):
                transform_image_file(
                    str(source),
                    str(output),
                    "scramble",
                    count=1,
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
