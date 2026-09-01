"""Tesseract OCR 后端（基于 pytesseract）

需要系统已安装 tesseract-ocr 程序。
自动探测常见安装路径；也可通过环境变量 TESSERACT_CMD 显式指定。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ...config import TESSERACT_CMD
from .base import OCRBackend

# 常见 Windows 安装路径
_COMMON_PATHS = [
    Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
    Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    Path("D:/ProgramFiles/Tesseract-OCR/tesseract.exe"),
    Path("D:/Program Files/Tesseract-OCR/tesseract.exe"),
    Path("E:/ProgramFiles/Tesseract-OCR/tesseract.exe"),
]


def _locate_tesseract() -> str | None:
    """定位 tesseract.exe，找不到返回 None"""
    if TESSERACT_CMD:
        p = Path(TESSERACT_CMD)
        if p.exists():
            return str(p)

    # PATH 中查找
    found = shutil.which("tesseract")
    if found:
        return found

    # 常见安装路径
    for p in _COMMON_PATHS:
        if p.exists():
            return str(p)
    return None


class TesseractBackend(OCRBackend):
    """pytesseract 包装；识别中英文+数字"""

    def __init__(self, lang: str = "chi_sim+eng"):
        import pytesseract

        exe = _locate_tesseract()
        if not exe:
            raise RuntimeError(
                "未找到 tesseract.exe，请安装 Tesseract OCR（https://github.com/UB-Mannheim/tesseract）"
                "或通过环境变量 TESSERACT_CMD 指定路径"
            )
        pytesseract.pytesseract.tesseract_cmd = exe
        self.pytesseract = pytesseract
        self.lang = lang

    def extract(self, image, regions: list[dict] | None = None) -> list[dict]:
        """
        image 可以是文件路径或 numpy 数组（BGR）。
        返回 [{text, x, y, w, h, confidence}, ...]
        """
        import cv2

        if isinstance(image, (str, Path)):
            img = cv2.imread(str(image))
        else:
            img = image
        if img is None:
            return []

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 放大提升小字识别率
        gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)

        try:
            data = self.pytesseract.image_to_data(
                gray, lang=self.lang, output_type=self.pytesseract.Output.DICT
            )
        except Exception:
            # 切 eng 重试
            try:
                data = self.pytesseract.image_to_data(
                    gray, lang="eng", output_type=self.pytesseract.Output.DICT
                )
            except Exception:
                return []

        blocks = []
        n = len(data.get("text", []))
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            try:
                conf = float(data.get("conf", [0])[i])
            except (ValueError, TypeError):
                conf = 0
            if conf < 30:
                continue
            blocks.append(
                {
                    "text": txt,
                    "x": int(data["left"][i] / 1.5),  # 缩放回原坐标
                    "y": int(data["top"][i] / 1.5),
                    "w": int(data["width"][i] / 1.5),
                    "h": int(data["height"][i] / 1.5),
                    "confidence": conf / 100.0,
                }
            )
        return blocks