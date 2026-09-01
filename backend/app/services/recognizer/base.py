"""K线识别服务：基类、结果数据结构、OCR 可插拔接口"""
from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArrowAnnotation:
    """用户在图上标注的箭头"""

    direction: str  # up / down
    role: str  # entry / exit / unknown
    x: int  # 像素坐标
    y: int
    estimated_time: str = ""  # 对应的时间（从坐标推断）
    estimated_price: str = ""  # 对应的价格
    confidence: float = 0.0


@dataclass
class NoteAnnotation:
    """用户在图上的文字备注"""

    text: str
    color: str = ""  # 文字颜色，如 red
    x: int = 0
    y: int = 0


@dataclass
class KlineRecognitionResult:
    """K线截图识别结果"""

    platform: str = ""  # TradingView / 同花顺 / 富途 / 币安 ...
    instrument: str = ""  # 品种代码 AGV2026
    exchange: str = ""  # SHFE
    timeframe: str = ""  # 3 / 15 / 1H / 1D
    timeframe_label: str = ""  # 3分钟 / 15分钟 / 1小时 / 日线

    price_min: str = ""
    price_max: str = ""
    time_range: str = ""  # 时间区间描述

    indicators: list[dict[str, str]] = field(default_factory=list)
    # 例: [{"name": "EMA", "params": "20", "value": "16,647"}]

    arrows: list[ArrowAnnotation] = field(default_factory=list)
    notes: list[NoteAnnotation] = field(default_factory=list)

    raw_text_blocks: list[dict[str, Any]] = field(default_factory=list)
    # OCR 识别出的所有文本块（坐标+文本），调试用

    image_width: int = 0
    image_height: int = 0
    recognized_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class OCRBackend(abc.ABC):
    """OCR 后端抽象接口（可插拔 pytesseract / EasyOCR / PaddleOCR）"""

    @abc.abstractmethod
    def extract(self, image_path: Path, regions: list[dict] | None = None) -> list[dict]:
        """提取图像中的文字，返回 [{text, x, y, w, h, confidence}, ...]"""
        raise NotImplementedError


class NullOCRBackend(OCRBackend):
    """无 OCR 时的占位实现（仅供未配置 OCR 时的降级，避免服务启动失败）"""

    def extract(self, image_path: Path, regions: list[dict] | None = None) -> list[dict]:
        return []


class KlineRecognizer(abc.ABC):
    """K线识别器抽象基类。各平台（TradingView/同花顺/币安等）实现自己的识别策略"""

    platform_name: str = "unknown"

    def __init__(self, ocr: OCRBackend):
        self.ocr = ocr

    @abc.abstractmethod
    def recognize(self, image_path: Path) -> KlineRecognitionResult:
        raise NotImplementedError

    # 通用工具：颜色过滤
    @staticmethod
    def color_mask(img_b, lower_hsv, upper_hsv):
        """HSV 颜色空间内的掩码"""
        import cv2
        import numpy as np

        hsv = cv2.cvtColor(img_b, cv2.COLOR_BGR2HSV)
        lower = np.array(lower_hsv, dtype=np.uint8)
        upper = np.array(upper_hsv, dtype=np.uint8)
        return cv2.inRange(hsv, lower, upper)