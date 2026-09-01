"""占用资金（收益率分母）计算模块

收益率 = 盈亏金额(pnl) / 占用资金(invested_capital)

口径说明：
- 商品期货：占用资金 = 开仓价 × 手数 × 合约乘数 × 保证金率
  - 合约乘数按品种代码前缀 / 中文名（别名表+标准化+唯一性校验）匹配内置表；
    未匹配返回 None（前端提示，退化为名义本金并允许手动修改）
  - 保证金率统一按 10% 估算（FUTURES_DEFAULT_MARGIN），用户可在表单手动修改占用资金覆盖
- A股：手数字段填"手"，占用资金 = 开仓价 × 手数 × 100（1手=100股，全额买入）
- 数字货币：手数字段填 USDT 金额，占用资金 = 开仓价 × 数量（默认 1 倍杠杆）

自动计算仅作为默认值，最终以用户手动填写的 invested_capital 为准。
"""
from __future__ import annotations

import re

# 常见商品期货合约乘数（吨/手 或 克/手、千克/手），按合约代码前缀匹配
FUTURES_MULTIPLIERS: dict[str, tuple[str, float]] = {
    # 上期所（SHFE）
    "RB": ("螺纹钢", 10), "HC": ("热卷", 10), "AL": ("沪铝", 5),
    "CU": ("沪铜", 5), "ZN": ("沪锌", 5), "PB": ("沪铅", 5),
    "NI": ("沪镍", 1), "SN": ("沪锡", 1), "AU": ("沪金", 1000),
    "AG": ("沪银", 15), "SS": ("不锈钢", 5), "AO": ("氧化铝", 20),
    "FU": ("燃油", 10), "BU": ("沥青", 10), "RU": ("橡胶", 10),
    "NR": ("20号胶", 10), "SP": ("纸浆", 10), "BR": ("丁二烯橡胶", 10),
    # 大商所（DCE）
    "JM": ("焦煤", 60), "J": ("焦炭", 100), "I": ("铁矿", 100),
    "L": ("塑料", 5), "PP": ("聚丙烯", 5), "V": ("PVC", 5),
    "EG": ("乙二醇", 10), "EB": ("苯乙烯", 5), "PG": ("液化气", 20),
    "M": ("豆粕", 10), "Y": ("豆油", 10), "A": ("豆一", 10),
    "B": ("豆二", 10), "C": ("玉米", 10), "CS": ("玉米淀粉", 10),
    "JD": ("鸡蛋", 5), "LH": ("生猪", 16), "P": ("棕榈油", 10),
    "RR": ("粳米", 10), "FB": ("纤维板", 10), "BB": ("胶合板", 500),
    # 郑商所（CZCE）
    "MA": ("甲醇", 10), "TA": ("PTA", 5), "PF": ("短纤", 5),
    "SA": ("纯碱", 20), "FG": ("玻璃", 20), "UR": ("尿素", 20),
    "AP": ("苹果", 10), "CJ": ("红枣", 5), "CF": ("棉花", 5),
    "SR": ("白糖", 10), "OI": ("菜油", 10), "RM": ("菜粕", 10),
    "ZC": ("动力煤", 100), "WH": ("强麦", 20), "PM": ("普麦", 50),
    "RS": ("菜籽", 10), "SF": ("硅铁", 5), "SM": ("锰硅", 5),
    "PK": ("花生", 5), "SH": ("烧碱", 30), "PX": ("对二甲苯", 5),
    # 广期所（GFEX）
    "SI": ("工业硅", 5), "LC": ("碳酸锂", 1),
    # 能源中心（INE）
    "SC": ("原油", 1000), "LU": ("低硫燃油", 10),
    "NR": ("20号胶", 10), "BC": ("国际铜", 5), "EC": ("集运欧线", 50),
}

# 品种中文别名表（解决手输名称差异：沪银 vs 白银、螺纹钢 vs 螺纹…）
# key 必须与 FUTURES_MULTIPLIERS 的代码一致；标准名本身无需重复列入
FUTURES_ALIASES: dict[str, list[str]] = {
    # 上期所（SHFE）
    "RB": ["螺纹", "螺", "钢筋", "线材钢"],
    "HC": ["热卷", "卷板", "热轧卷板", "热轧板卷"],
    "AL": ["铝", "铝锭"],
    "CU": ["铜", "电解铜", "阴极铜"],
    "ZN": ["锌", "精炼锌"],
    "PB": ["铅", "铅锭"],
    "NI": ["镍", "电解镍"],
    "SN": ["锡", "锡锭"],
    "AU": ["黄金", "金"],
    "AG": ["白银", "银"],
    "SS": ["不锈钢"],
    "AO": ["氧化铝", "铝土矿"],
    "FU": ["燃料油", "燃油", "燃料"],
    "BU": ["沥青", "石油沥青"],
    "RU": ["天然橡胶", "天胶"],
    "NR": ["20号胶", "20号橡胶", "二十号胶"],
    "SP": ["纸浆", "木浆"],
    "BR": ["丁二烯橡胶", "顺丁橡胶"],
    # 大商所（DCE）
    "JM": ["焦煤"],
    "J": ["焦炭", "冶金焦"],
    "I": ["铁矿石", "铁矿", "铁"],
    "L": ["塑料", "聚乙烯", "lldpe"],
    "PP": ["聚丙烯"],
    "V": ["pvc", "聚氯乙烯"],
    "EG": ["乙二醇", "meg"],
    "EB": ["苯乙烯", "苯乙烯单体"],
    "PG": ["液化气", "lpg"],
    "M": ["豆粕", "粕"],
    "Y": ["豆油", "大豆油"],
    "A": ["豆一", "黄豆一号", "大豆一号", "黄大豆一号"],
    "B": ["豆二", "黄豆二号", "大豆二号", "黄大豆二号"],
    "C": ["玉米"],
    "CS": ["玉米淀粉", "淀粉"],
    "JD": ["鸡蛋", "鲜鸡蛋"],
    "LH": ["生猪"],
    "P": ["棕榈油", "棕油"],
    "RR": ["粳米"],
    "FB": ["纤维板"],
    "BB": ["胶合板"],
    # 郑商所（CZCE）
    "MA": ["甲醇"],
    "TA": ["pta", "精对苯二甲酸"],
    "PF": ["短纤", "涤纶短纤"],
    "SA": ["纯碱", "碳酸钠"],
    "FG": ["玻璃", "平板玻璃"],
    "UR": ["尿素"],
    "AP": ["苹果", "红富士"],
    "CJ": ["红枣"],
    "CF": ["棉花", "皮棉"],
    "SR": ["白糖", "糖"],
    "OI": ["菜油", "菜籽油", "郑油"],
    "RM": ["菜粕", "菜籽粕"],
    "ZC": ["动力煤"],
    "WH": ["强麦", "强筋小麦"],
    "PM": ["普麦", "普通小麦"],
    "RS": ["菜籽", "油菜籽"],
    "SF": ["硅铁"],
    "SM": ["锰硅", "硅锰"],
    "PK": ["花生", "花生仁"],
    "SH": ["烧碱", "氢氧化钠"],
    "PX": ["对二甲苯", "px"],
    # 广期所（GFEX）
    "SI": ["工业硅"],
    "LC": ["碳酸锂", "锂"],
    # 能源中心（INE）
    "SC": ["原油", "石油", "原油期货"],
    "LU": ["低硫燃油", "低硫燃料油"],
    "BC": ["国际铜", "bc铜"],
    "EC": ["集运欧线", "欧线集运", "集运指数"],
}

# 名称标准化时要剥离的尾部修饰词（可重复叠加）
_SUFFIX_WORDS = [
    "期货", "主力", "主连", "次主连", "连续", "当月", "次月",
    "加权", "指数", "合约", "远月", "近月", "新主力", "主力合约",
]
# 标准化时剥离的字符（数字=合约号，点/杠=分隔符，空格=全半角）
_STRIP_RE = re.compile(r"[\s\d.\-—/～~·、,，]+", re.UNICODE)

# 期货保证金率默认估算值（可被手动填写的占用资金覆盖）
FUTURES_DEFAULT_MARGIN = 0.10
# A股每手股数（手数字段按"手"填写，计算占用资金时 ×100 折算股数）
A_SHARE_LOT_SIZE = 100


def normalize_instrument_name(name: str) -> str:
    """名称标准化：去空格/合约号/分隔符 → 去尾部修饰词 → 大写

    "沪银2412 主力" → "沪银"；"PTA主力" → "PTA"
    """
    if not name:
        return ""
    s = _STRIP_RE.sub("", name.strip()).upper()
    for w in _SUFFIX_WORDS:
        wu = w.upper()
        if s.endswith(wu):
            s = s[: -len(wu)]
    return s


def resolve_multiplier(code: str = "", name: str = "") -> tuple[float, str | None]:
    """解析合约乘数，返回 (乘数, 匹配到的标准品种名)

    匹配规则（三级）：
    1. 代码前缀匹配：字母部分 ≥2 位时优先 2-3 位前缀（RB2510 -> RB）；
       代码本身是单字母（大商所 A/B/I/L/M/V/P/Y/C/J 等）才允许 1 位匹配，
       避免 BZ(纯苯)/PR(瓶片) 误匹配到 B(豆二)/P(棕榈油)
    2. 中文名：标准化后查别名表（含标准名），精确命中即返回
    3. 包含匹配兜底 + 唯一性校验：同时命中多个品种（如"豆"）→ 放弃，返回 (1.0, None)
    未匹配返回 (1.0, None)，matched_name=None 表示未识别（前端提示，可手动改）
    """
    if code:
        c = code.strip().upper()
        alpha = "".join(ch for ch in c if ch.isalpha())
        if len(alpha) >= 2:
            for i in range(min(len(alpha), 3), 1, -1):
                key = alpha[:i]
                if key in FUTURES_MULTIPLIERS:
                    std_name, mult = FUTURES_MULTIPLIERS[key]
                    return mult, std_name
        elif len(alpha) == 1 and alpha in FUTURES_MULTIPLIERS:
            std_name, mult = FUTURES_MULTIPLIERS[alpha]
            return mult, std_name
    if name:
        n = normalize_instrument_name(name)
        if not n:
            return 1.0, None
        # 2) 别名表精确匹配（标准名 + 别名）
        for code_key, aliases in FUTURES_ALIASES.items():
            std_name, mult = FUTURES_MULTIPLIERS[code_key][0], FUTURES_MULTIPLIERS[code_key][1]
            if n == std_name or n in aliases:
                return mult, std_name
        # 3) 包含匹配 + 唯一性校验
        candidates: set[str] = set()
        for code_key, aliases in FUTURES_ALIASES.items():
            std_name = FUTURES_MULTIPLIERS[code_key][0]
            if (
                n in std_name
                or std_name in n
                or any(n in a or a in n for a in aliases)
            ):
                candidates.add(code_key)
        if len(candidates) == 1:
            code_key = candidates.pop()
            return FUTURES_MULTIPLIERS[code_key][1], FUTURES_MULTIPLIERS[code_key][0]
    return 1.0, None


def compute_invested_capital(
    instrument_type: str = "",
    instrument_code: str = "",
    instrument_name: str = "",
    entry_price: float | None = None,
    volume: float = 1.0,
) -> float | None:
    """计算一笔交易的占用资金（自动默认值）

    返回 None 表示无法计算（缺价格/数量）
    """
    if not entry_price or entry_price <= 0 or not volume or volume <= 0:
        return None
    base = entry_price * volume  # 名义本金（volume=手数/数量口径）
    if instrument_type == "商品期货":
        mult, _ = resolve_multiplier(instrument_code, instrument_name)
        return round(base * mult * FUTURES_DEFAULT_MARGIN, 2)
    if instrument_type == "A股":
        # 手数字段按"手"填写（1手=100股），折算股数后全额买入
        return round(entry_price * volume * A_SHARE_LOT_SIZE, 2)
    # 数字货币等：默认 1 倍杠杆
    return round(base, 2)


def compute_return_rate(pnl: float | None, invested_capital: float | None) -> float | None:
    """收益率 = 盈亏 / 占用资金（百分比）。缺任一返回 None"""
    if pnl is None or not invested_capital:
        return None
    try:
        return round(pnl / invested_capital * 100, 2)
    except (ZeroDivisionError, TypeError):
        return None


def backfill_invested_capital(db, force: bool = False) -> tuple[int, int]:
    """跑批回填历史交易的 invested_capital

    force=True 时重算所有记录（算法变更后使用）；否则仅填 None 的记录
    返回 (已回填数, 扫描数)
    """
    from ..models import Trade

    query = db.query(Trade)
    if not force:
        query = query.filter(Trade.invested_capital.is_(None))
    trades = query.all()
    filled = 0
    for t in trades:
        val = compute_invested_capital(
            t.instrument_type, t.instrument_code, t.instrument_name,
            t.entry_price, t.volume,
        )
        if val is not None:
            t.invested_capital = val
            filled += 1
    db.commit()
    return filled, len(trades)
