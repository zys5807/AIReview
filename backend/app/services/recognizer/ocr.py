"""OCR 后端实现：可插拔设计，未安装时降级为 NullOCRBackend"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import NullOCRBackend, OCRBackend


def get_ocr_backend() -> OCRBackend:
    """工厂：选择当前可用的 OCR 后端，优先使用 Tesseract"""
    try:
        import pytesseract  # noqa: F401

        from .tesseract_backend import TesseractBackend

        return TesseractBackend()
    except ImportError:
        return NullOCRBackend()


__all__ = ["get_ocr_backend", "OCRBackend", "NullOCRBackend"]