from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image as CompImage
from astrbot.api.message_components import Plain, Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .image_confusion_core import transform_image_file


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
    "0.1.4",
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
