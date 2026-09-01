"""TradingView 模板的 K线识别实现

优先适配：
- 顶部品种/周期标题（如 "AGV2026 · 3 · SHFE"）
- 右侧价格刻度区域
- 底部时间刻度
- EMA / MACD 等指标标签
- 用户标注的白色箭头（入场/出场）
- 用户标注的彩色文字（红色/黄色等）

OCR 文字提取通过可插拔的 OCRBackend 完成；颜色/形状识别全部用 OpenCV。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .base import ArrowAnnotation, KlineRecognitionResult, KlineRecognizer, NoteAnnotation


class TradingViewRecognizer(KlineRecognizer):
    """TradingView 截图识别器"""

    platform_name = "TradingView"

    def __init__(self, ocr):
        super().__init__(ocr)
        # 关键区域比例（相对图片宽高）—— TradingView 暗色主题布局相对固定
        # 顶部标题栏高度 ~6%
        self.title_band_ratio = (0.0, 0.06)
        # 右侧价格刻度宽度 ~7%
        self.price_band_ratio = (0.93, 1.0)
        # 底部时间刻度高度 ~5%
        self.time_band_ratio = (0.95, 1.0)
        # 主图区域（K线）：扣除标题和最下方时间刻度 + 预留成交量/MACD
        # 主图实际占比约 18%~75%
        self.chart_band_ratio = (0.0, 0.55)

    def recognize(self, image_path: Path) -> KlineRecognitionResult:
        img = cv2.imread(str(image_path))
        if img is None:
            raise RuntimeError(f"无法读取图片: {image_path}")

        h, w = img.shape[:2]
        result = KlineRecognitionResult(
            platform=self.platform_name,
            image_width=w,
            image_height=h,
            recognized_at=datetime.now().isoformat(timespec="seconds"),
        )

        # 1. OCR 提取顶部/右侧/底部文字
        text_blocks = self._extract_text_regions(img, w, h)
        result.raw_text_blocks = text_blocks
        self._parse_text(text_blocks, result)

        # 2. 检测用户标注的白色箭头
        result.arrows = self._detect_white_arrows(img)

        # 3. 检测用户标注的彩色文字（红色等）
        result.notes = self._detect_colored_notes(img, w, h, text_blocks)

        return result

    # ---------- 文字区域 ----------
    def _extract_text_regions(self, img, w, h):
        """从关键区域提取文字，返回 [{text, x, y, w, h, region}]"""
        blocks = []
        regions = [
            ("title", 0, 0, w, int(h * self.title_band_ratio[1])),
            ("price", int(w * self.price_band_ratio[0]), int(h * self.chart_band_ratio[0]),
             w, int(h * self.chart_band_ratio[1])),
            ("time", 0, int(h * self.time_band_ratio[0]), w, h),
            ("left", 0, int(h * self.chart_band_ratio[0]),
             int(w * 0.05), int(h * self.chart_band_ratio[1])),
        ]
        for name, x0, y0, x1, y1 in regions:
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            crop = img[y0:y1, x0:x1]
            extracted = self.ocr.extract(crop)
            for blk in extracted:
                blocks.append(
                    {
                        "text": blk.get("text", ""),
                        "x": x0 + blk.get("x", 0),
                        "y": y0 + blk.get("y", 0),
                        "w": blk.get("w", 0),
                        "h": blk.get("h", 0),
                        "region": name,
                    }
                )
        return blocks

    def _parse_text(self, blocks, result: KlineRecognitionResult):
        """从文字块中解析出品种/周期/指标/价格"""
        import re

        title_blocks = [b for b in blocks if b["region"] == "title"]
        price_blocks = [b for b in blocks if b["region"] == "price"]
        time_blocks = [b for b in blocks if b["region"] == "time"]

        # ---- 标题区域：OCR 会把一行拆成多个块，先按位置聚合再匹配 ----
        if title_blocks:
            title_blocks.sort(key=lambda b: (b["y"], b["x"]))
            line = " ".join(b["text"] for b in title_blocks)
            # 例： "@ AGv2026 . 3-SHFE @ 开 -16,639 高 =16,655 ..."
            m_inst = re.search(r"\b([A-Za-z]{2,10}\d{0,10})\b", line)
            if m_inst:
                result.instrument = m_inst.group(1)
            m_ex = re.search(r"\b([A-Z]{2,6})\b", line)
            if m_ex:
                result.exchange = m_ex.group(1)
            # 周期：独立数字 或 1H/2H/4H/1D/日/周/月
            m_tf = re.search(
                r"\b(\d{1,3}|1H|2H|4H|6H|1D|1W|1M|日|周|月)\b", line, re.IGNORECASE
            )
            if m_tf:
                tf = m_tf.group(1)
                result.timeframe = tf
                tf_map = {
                    "1": "1分钟", "5": "5分钟", "15": "15分钟", "30": "30分钟",
                    "60": "1小时", "120": "2小时", "240": "4小时", "360": "6小时",
                    "1h": "1小时", "4h": "4小时", "1d": "日线", "1w": "周线", "1m": "月线",
                }
                result.timeframe_label = tf_map.get(tf.lower(), f"{tf}周期")
            # 当日开/高/低/收数值
            m_ohlc = re.findall(r"(开|高|低|收|今开|最新)[-=]?\s*([\d,\.\+\-%]+)", line)
            for label, val in m_ohlc:
                result.indicators.append({"name": "OHLC", "params": label, "value": val})

        # ---- EMA/MA 指标（"EMA 20 close 16,647" / "EMA 55 16,565"）----
        for b in title_blocks + [bb for bb in blocks if bb["region"] == "left"]:
            text = b["text"].strip()
            m = re.match(
                r"(EMA|SMA|MA|BOLL|VOL)\s+(\d+)\s+.*?([\d,\.\+\-]+)", text, re.IGNORECASE
            )
            if m:
                result.indicators.append(
                    {
                        "name": m.group(1).upper(),
                        "params": m.group(2),
                        "value": m.group(3),
                    }
                )

        # ---- 右侧价格刻度 ----
        prices = []
        for b in price_blocks:
            t = b["text"].replace(",", "").strip()
            if re.match(r"^-?\d+(\.\d+)?$", t):
                prices.append(float(t))
        if prices:
            result.price_min = f"{min(prices):,.0f}"
            result.price_max = f"{max(prices):,.0f}"

        # ---- 底部时间刻度 ----
        times = [b["text"].strip() for b in time_blocks if b["text"].strip()]
        if times:
            result.time_range = f"{times[0]} → {times[-1]}"

    # ---------- 箭头识别 ----------
    def _detect_white_arrows(self, img):
        """检测白色箭头（用户在 K线上方/下方画的方向标记）

        限制只在主图区域内寻找，避免底部时间刻度/成交量/MACD区域的白色数字误检。
        """
        arrows = []
        h, w = img.shape[:2]
        # 主图区域：6%~55% 高度（避开顶部标题和底部时间刻度/指标）
        y0_main = int(h * 0.06)
        y1_main = int(h * 0.55)

        main = img[y0_main:y1_main, :]
        hsv = cv2.cvtColor(main, cv2.COLOR_BGR2HSV)
        white_mask = cv2.inRange(hsv, (0, 0, 220), (180, 60, 255))

        # 形态学：去噪 + 连通域
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            # 箭头比文字明显大，过滤太小的
            if area < 200 or area > 5000:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)

            # 箭头尺寸约束：宽度和高度都在合理范围
            if not (15 <= cw <= 60 and 20 <= ch <= 80):
                continue

            # 用 approxPolyDP 把轮廓近似成多边形，判断尖端
            epsilon = 0.05 * cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, epsilon, True)
            if len(approx) < 3:
                continue

            # 找最上方和最下方的点
            pts = [p[0] for p in approx]
            top_pt = min(pts, key=lambda p: p[1])
            bot_pt = max(pts, key=lambda p: p[1])
            vertical_span = bot_pt[1] - top_pt[1]
            if vertical_span < 15:
                continue

            # 通过顶端 vs 底端的水平宽度判断方向：
            # 向上箭头(↑)尖端在上 → 顶部窄、底部宽
            # 向下箭头(↓)尖端在下 → 顶部宽、底部窄
            top_band_pts = [p for p in pts if p[1] < top_pt[1] + vertical_span * 0.25]
            bot_band_pts = [p for p in pts if p[1] > bot_pt[1] - vertical_span * 0.25]
            top_w = (
                max(p[0] for p in top_band_pts) - min(p[0] for p in top_band_pts)
                if top_band_pts
                else cw
            )
            bot_w = (
                max(p[0] for p in bot_band_pts) - min(p[0] for p in bot_band_pts)
                if bot_band_pts
                else cw
            )

            if top_w < bot_w * 0.75:
                direction = "up"
            elif bot_w < top_w * 0.75:
                direction = "down"
            else:
                direction = "unknown"

            role = {"up": "entry", "down": "exit", "unknown": "unknown"}.get(direction)

            arrows.append(
                ArrowAnnotation(
                    direction=direction,
                    role=role,
                    x=int(x + cw / 2),
                    y=int(y + y0_main + ch / 2),
                    confidence=0.7 if direction != "unknown" else 0.3,
                )
            )

        # 按 y 从小到大（上→下）排序，再按 x
        arrows.sort(key=lambda a: (a.y, a.x))
        return arrows

    # ---------- 文字标注识别 ----------
    def _detect_colored_notes(self, img, w, h, text_blocks):
        """检测用户写的彩色文字（红色等），从对应区域提取文字"""
        notes = []
        # 红色文字掩码
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # 红色在 HSV 跨 0/180 度，用两个范围
        red1 = cv2.inRange(hsv, (0, 100, 100), (10, 255, 255))
        red2 = cv2.inRange(hsv, (170, 100, 100), (180, 255, 255))
        red_mask = cv2.bitwise_or(red1, red2)

        # 找红色文字的连通区域：用较大 kernel 膨胀，把相邻文字合并成块
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)
        merge_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        red_mask = cv2.dilate(red_mask, merge_kernel, iterations=2)

        contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 200:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            # 过滤过大的（可能是非文字区域）
            if cw > w * 0.5 or ch > h * 0.3:
                continue
            # 切出这块区域，把红色文字转成白底黑字再 OCR（Tesseract 对彩色文字识别差）
            pad = 6
            y0 = max(0, y - pad)
            y1 = min(h, y + ch + pad)
            x0 = max(0, x - pad)
            x1 = min(w, x + cw + pad)
            crop = img[y0:y1, x0:x1]

            crop_hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            cr1 = cv2.inRange(crop_hsv, (0, 80, 80), (12, 255, 255))
            cr2 = cv2.inRange(crop_hsv, (168, 80, 80), (180, 255, 255))
            red = cv2.bitwise_or(cr1, cr2)
            # 白底黑字
            text_img = np.full_like(crop, 255)
            text_img[red > 0] = 0

            extracted = self.ocr.extract(text_img)
            for blk in extracted:
                text = blk.get("text", "").strip()
                if not text:
                    continue
                # 与已有 text_blocks 比对坐标，避免重复
                if any(
                    abs(b["x"] - (x0 + blk.get("x", 0))) < 20
                    and abs(b["y"] - (y0 + blk.get("y", 0))) < 20
                    and text in b.get("text", "")
                    for b in text_blocks
                ):
                    continue
                notes.append(
                    NoteAnnotation(
                        text=text,
                        color="red",
                        x=x0 + blk.get("x", 0),
                        y=y0 + blk.get("y", 0),
                    )
                )

        # 按位置排序
        notes.sort(key=lambda n: (n.y, n.x))

        # 把同一行的碎片合并成完整句子
        merged_lines: list[NoteAnnotation] = []
        current_line: list[NoteAnnotation] = []
        current_y: int | None = None
        for n in notes:
            if current_y is None or abs(n.y - current_y) <= 30:
                current_line.append(n)
            else:
                merged_lines.append(self._merge_note_line(current_line))
                current_line = [n]
            current_y = n.y
        if current_line:
            merged_lines.append(self._merge_note_line(current_line))

        # 过滤空行和无意义内容
        import re

        result = []
        for n in merged_lines:
            text = n.text.strip()
            if not text:
                continue
            # 保留包含中文或字母的句子，过滤纯符号
            if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
                continue
            # 过滤噪声：中文备注应以汉字为主（K线图上的红色价格/柱子会被误检为碎片）
            cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
            meaningful = sum(1 for c in text if not c.isspace())
            if meaningful > 0 and cn_chars < max(3, meaningful * 0.5):
                continue
            result.append(n)
        return result

    @staticmethod
    def _merge_note_line(line: list[NoteAnnotation]) -> NoteAnnotation:
        """把一行内的碎片按 x 排序拼接成一个完整句子"""
        line = sorted(line, key=lambda n: n.x)
        text = "".join(n.text for n in line)
        # 清理 OCR 产生的孤立符号
        text = (
            text.replace(" ' ", "")
            .replace("  ", " ")
            .strip(" ,，。.|'\"")
        )
        return NoteAnnotation(
            text=text,
            color=line[0].color,
            x=line[0].x,
            y=line[0].y,
        )