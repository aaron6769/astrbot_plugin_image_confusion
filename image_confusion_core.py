from __future__ import annotations

import math
from array import array
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageOps


GOLDEN_RATIO_CONJUGATE = (math.sqrt(5.0) - 1.0) / 2.0
MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
VALID_MODES = frozenset({"scramble", "descramble"})


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _js_round(value: float) -> int:
    if not math.isfinite(value):
        raise ValueError("offset 计算结果不是有限数字。")
    return int(math.floor(value + 0.5))


def validate_image_dimensions(
    width: int,
    height: int,
    *,
    max_image_pixels: int = MAX_IMAGE_PIXELS,
) -> int:
    if width <= 0 or height <= 0:
        raise ValueError("图片宽高必须为正数。")
    pixels = width * height
    if pixels > max_image_pixels:
        raise ValueError(
            f"图片像素过大：{width}x{height}={pixels:,}，"
            f"上限为 {max_image_pixels:,} 像素。"
        )
    return pixels


def compute_offset(width: int, height: int) -> int:
    pixels = width * height
    if pixels <= 0:
        raise ValueError("图片宽高必须为正数。")
    return _js_round(GOLDEN_RATIO_CONJUGATE * pixels) % pixels


def gilbert2d_indices(width: int, height: int) -> array:
    """Return a deterministic Gilbert curve order for image pixel shuffling."""
    total = validate_image_dimensions(width, height)
    typecode = "I" if total <= 0xFFFFFFFF else "Q"
    coords = array(typecode)

    def push(x: int, y: int) -> None:
        if not (0 <= x < width and 0 <= y < height):
            raise RuntimeError(f"Gilbert 曲线越界：({x}, {y}) not in {width}x{height}")
        coords.append(y * width + x)

    def generate2d(x: int, y: int, ax: int, ay: int, bx: int, by: int) -> None:
        w = abs(ax + ay)
        h = abs(bx + by)
        dax, day = _sign(ax), _sign(ay)
        dbx, dby = _sign(bx), _sign(by)

        if h == 1:
            for _ in range(w):
                push(x, y)
                x += dax
                y += day
            return

        if w == 1:
            for _ in range(h):
                push(x, y)
                x += dbx
                y += dby
            return

        ax2, ay2 = ax // 2, ay // 2
        bx2, by2 = bx // 2, by // 2
        w2 = abs(ax2 + ay2)
        h2 = abs(bx2 + by2)

        if 2 * w > 3 * h:
            if (w2 % 2) and (w > 2):
                ax2 += dax
                ay2 += day
            generate2d(x, y, ax2, ay2, bx, by)
            generate2d(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by)
        else:
            if (h2 % 2) and (h > 2):
                bx2 += dbx
                by2 += dby
            generate2d(x, y, bx2, by2, ax2, ay2)
            generate2d(x + bx2, y + by2, ax, ay, bx - bx2, by - by2)
            generate2d(
                x + (ax - dax) + (bx2 - dbx),
                y + (ay - day) + (by2 - dby),
                -bx2,
                -by2,
                -(ax - ax2),
                -(ay - ay2),
            )

    if width >= height:
        generate2d(0, 0, width, 0, 0, height)
    else:
        generate2d(0, 0, 0, height, width, 0)

    if len(coords) != total:
        raise RuntimeError(f"Gilbert 曲线长度错误：got {len(coords)}, expected {total}")
    return coords


def transform_rgba_bytes(
    rgba_bytes: bytes,
    width: int,
    height: int,
    mode: str,
    count: int = 1,
    *,
    max_image_pixels: int = MAX_IMAGE_PIXELS,
) -> tuple[bytes, int, int]:
    if mode not in VALID_MODES:
        raise ValueError(f"不支持的处理模式：{mode}")
    if count < 0:
        raise ValueError("次数必须是非负整数。")

    total = validate_image_dimensions(
        width,
        height,
        max_image_pixels=max_image_pixels,
    )
    expected_len = total * 4
    if len(rgba_bytes) != expected_len:
        raise ValueError(f"RGBA 数据长度错误：got {len(rgba_bytes)}, expected {expected_len}")

    offset = compute_offset(width, height)
    shift = (offset * count) % total
    if mode == "descramble":
        shift = (-shift) % total

    if count == 0 or shift == 0:
        return bytes(rgba_bytes), offset, shift

    curve = gilbert2d_indices(width, height)
    src = memoryview(rgba_bytes)
    dst = bytearray(expected_len)

    for i, src_pixel in enumerate(curve):
        dst_pixel = curve[(i + shift) % total]
        src_byte = src_pixel << 2
        dst_byte = dst_pixel << 2
        dst[dst_byte : dst_byte + 4] = src[src_byte : src_byte + 4]

    return bytes(dst), offset, shift


def transform_image_file(
    input_path: str,
    output_path: str,
    mode: str,
    count: int,
    *,
    max_input_bytes: int = MAX_INPUT_BYTES,
    max_image_pixels: int = MAX_IMAGE_PIXELS,
) -> tuple[int, int, int, int]:
    source = Path(input_path)
    input_bytes = source.stat().st_size
    if input_bytes > max_input_bytes:
        raise ValueError(
            f"图片文件过大：{input_bytes / (1024 * 1024):.1f} MiB，"
            f"上限为 {max_input_bytes / (1024 * 1024):.0f} MiB。"
        )

    try:
        with PILImage.open(source) as opened:
            width, height = opened.size
            validate_image_dimensions(
                width,
                height,
                max_image_pixels=max_image_pixels,
            )
            image = ImageOps.exif_transpose(opened)
            rgba = image.convert("RGBA")
    except PILImage.DecompressionBombError as exc:
        raise ValueError("图片像素数量超过 Pillow 安全上限。") from exc

    width, height = rgba.size
    out_bytes, offset, shift = transform_rgba_bytes(
        rgba.tobytes(),
        width,
        height,
        mode,
        count=count,
        max_image_pixels=max_image_pixels,
    )
    target = Path(output_path)
    try:
        with PILImage.frombytes("RGBA", (width, height), out_bytes) as out_image:
            out_image.save(target, "PNG", optimize=True)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return width, height, offset, shift
