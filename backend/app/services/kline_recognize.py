"""K线识别服务统一入口"""
from __future__ import annotations

from pathlib import Path

from .recognizer.base import KlineRecognitionResult, KlineRecognizer
from .recognizer.ocr import get_ocr_backend
from .recognizer.tradingview import TradingViewRecognizer

# 平台识别器注册表（后续可加 同花顺Recognizer、币安Recognizer 等）
_RECOGNIZERS: list[KlineRecognizer] = []


def _build_recognizers() -> list[KlineRecognizer]:
    ocr = get_ocr_backend()
    return [TradingViewRecognizer(ocr)]


def recognize_screenshot(image_path: Path | str) -> KlineRecognitionResult:
    """对一张截图依次用各平台识别器识别，返回第一个非空结果。

    目前只实现了 TradingView 模板；后续可加同花顺/币安等。
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {path}")

    if not _RECOGNIZERS:
        _RECOGNIZERS.extend(_build_recognizers())

    # 简单策略：直接用第一个识别器（TradingView），后续可按图片特征切换
    recognizer = _RECOGNIZERS[0]
    return recognizer.recognize(path)