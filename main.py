from __future__ import annotations

import math
import re
import time
import uuid
from array import array
from pathlib import Path
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image as CompImage
from astrbot.api.message_components import Plain, Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from PIL import Image as PILImage
from PIL import ImageOps


GOLDEN_RATIO_CONJUGATE = (math.sqrt(5.0) - 1.0) / 2.0


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _js_round(value: float) -> int:
    if not math.isfinite(value):
        raise ValueError("offset 计算结果不是有限数字。")
    return int(math.floor(value + 0.5))


def compute_offset(width: int, height: int) -> int:
    pixels = width * height
    if pixels <= 0:
        raise ValueError("图片宽高必须为正数。")
    return _js_round(GOLDEN_RATIO_CONJUGATE * pixels) % pixels


def gilbert2d_indices(width: int, height: int) -> array:
    """Return a deterministic Gilbert curve order for image pixel shuffling."""
    if width <= 0 or height <= 0:
        raise ValueError("图片宽高必须为正数。")

    total = width * height
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
) -> tuple[bytes, int, int]:
    if count < 0:
        raise ValueError("次数必须是非负整数。")

    total = width * height
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
) -> tuple[int, int, int, int]:
    with PILImage.open(input_path) as opened:
        image = ImageOps.exif_transpose(opened)
        rgba = image.convert("RGBA")

    width, height = rgba.size
    out_bytes, offset, shift = transform_rgba_bytes(
        rgba.tobytes(),
        width,
        height,
        mode,
        count=count,
    )
    out_image = PILImage.frombytes("RGBA", (width, height), out_bytes)
    out_image.save(output_path, "PNG", optimize=True)
    return width, height, offset, shift


def _extract_image_components(event: AstrMessageEvent) -> list[CompImage]:
    images: list[CompImage] = []
    for comp in event.get_messages():
        if isinstance(comp, CompImage):
            images.append(comp)
        elif isinstance(comp, Reply) and comp.chain:
            for reply_comp in comp.chain:
                if isinstance(reply_comp, CompImage):
                    images.append(reply_comp)
    return images


def _parse_image_command(text: str) -> tuple[str, int] | None:
    compact = re.sub(r"\s+", "", text or "").removeprefix("/")
    match = re.fullmatch(r"(解混淆|混淆)(\d*)", compact)
    if not match:
        return None

    action, raw_count = match.groups()
    count = int(raw_count) if raw_count else 1
    mode = "descramble" if action == "解混淆" else "scramble"
    return mode, max(1, min(count, 50))


class ImageCommandFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg) -> bool:
        if not getattr(event, "is_at_or_wake_command", False):
            return False
        return _parse_image_command(event.get_message_str() or "") is not None


@register(
    "astrbot_plugin_image_confusion",
    "luo",
    "图片混淆/解混淆插件：基于 Gilbert Curve 的图片混淆与还原",
    "0.1.3",
)
class ImageConfusionPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        super().__init__(context)
        self.config = config or {}
        self.output_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_image_confusion" / "outputs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_outputs()

    @filter.custom_filter(ImageCommandFilter, False)
    async def image_command(self, event: AstrMessageEvent):
        """处理图片混淆/解混淆。用法：/混淆2 或 /解混淆2，可直接带图或引用图片。"""
        parsed = _parse_image_command(event.get_message_str() or "")
        if not parsed:
            return
        mode, count = parsed
        await self._handle(event, mode=mode, count=count)

    async def _handle(self, event: AstrMessageEvent, mode: str, count: int) -> None:
        event.should_call_llm(False)
        event.stop_event()
        self._cleanup_old_outputs()

        images = _extract_image_components(event)
        if not images:
            await event.send(MessageChain().message("请在消息里带一张图片，或引用图片后发送 /混淆 或 /解混淆。"))
            return

        image = images[0]
        action = "混淆" if mode == "scramble" else "解混淆"
        started = time.time()
        tmp_input: Optional[str] = None

        try:
            tmp_input = await image.convert_to_file_path()
            filename = f"confusion_{mode}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
            output_path = self.output_dir / filename
            width, height, offset, shift = transform_image_file(
                tmp_input,
                str(output_path),
                mode=mode,
                count=count,
            )
            elapsed = time.time() - started
            logger.info(
                "[图片混淆/解混淆插件] %s完成 size=%sx%s count=%s offset=%s shift=%s elapsed=%.2fs output=%s",
                action,
                width,
                height,
                count,
                offset,
                shift,
                elapsed,
                output_path,
            )

            await event.send(
                MessageChain(
                    chain=[
                        Plain(f"{action}完成，次数={count}，尺寸={width}x{height}\n"),
                        CompImage.fromFileSystem(str(output_path)),
                    ],
                ),
            )
        except Exception as exc:
            logger.error("[图片混淆/解混淆插件] %s失败: %s", action, exc, exc_info=True)
            await event.send(MessageChain().message(f"{action}失败：{exc}"))

    def _retention_hours(self) -> float:
        cleanup_config = self.config.get("cleanup", {}) if isinstance(self.config, dict) else {}
        if not isinstance(cleanup_config, dict):
            return 24.0
        raw_value = cleanup_config.get("retention_hours", 24)
        try:
            return max(0.0, float(raw_value))
        except (TypeError, ValueError):
            return 24.0

    def _cleanup_old_outputs(self) -> None:
        retention_hours = self._retention_hours()
        if retention_hours <= 0:
            return

        cutoff = time.time() - retention_hours * 3600
        removed = 0
        try:
            for path in self.output_dir.glob("*.png"):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
                except Exception:
                    logger.warning("[图片混淆/解混淆插件] 清理过期输出失败: %s", path, exc_info=True)
        except Exception:
            logger.warning("[图片混淆/解混淆插件] 扫描输出目录失败: %s", self.output_dir, exc_info=True)
            return

        if removed:
            logger.info(
                "[图片混淆/解混淆插件] 已清理 %s 个过期输出文件 retention_hours=%s",
                removed,
                retention_hours,
            )
