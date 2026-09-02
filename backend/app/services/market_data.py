# -*- coding: utf-8 -*-
"""盘面行情采集器（V1.008.2 功能1/功能2）

架构前提：本应用是单机 exe，NeoData/腾讯元宝等外部 AI 工具无法进入 exe，
因此盘面数据由后端在请求时直接联网抓取公开行情接口。全部请求【直连】，
不经过系统/环境代理（代理会造成部分行情源 RemoteDisconnected）。

数据源（均已实测）：
- A股指数 / 个股日K：腾讯 web.ifzq.gtimg.cn fqkline（快、稳、含北交所）
- A股板块快照榜 / 个股实时榜：东财 push2delay.eastmoney.com clist
- A股板块历史K线：东财 push2his（时间窗限流：只对候选板块尽力而为，失败降级标注）
- 商品期货主连日K：新浪 InnerFuturesNewService.getDailyKLine（2005 至今全历史）
- 商品期货当日报价：新浪 hq.sinajs.cn（GBK）
- 数字货币日K：Gate.io spot candlesticks

采集失败一律静默降级并在 snapshot["notes"] 记录，绝不阻塞报告生成。
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 静态代码表（板块代码来自东财真实 BK 代码，2026-09 实测枚举）
# ---------------------------------------------------------------------------

# A股大盘指数：腾讯K线代码（个股/指数共用）
A_SHARE_INDICES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
]

# 行业板块（东财行业 t:2 一级，代码真实存在）
INDUSTRY_BOARDS = [
    ("BK1201", "电子"), ("BK1036", "半导体"), ("BK0459", "元件"), ("BK1038", "光学光电子"),
    ("BK1037", "消费电子"), ("BK1330", "模拟芯片设计"), ("BK1328", "集成电路封测"),
    ("BK1326", "半导体设备"), ("BK1207", "计算机"), ("BK0737", "软件开发"), ("BK0735", "计算机设备"),
    ("BK1215", "通信"), ("BK0448", "通信设备"), ("BK0736", "通信服务"), ("BK1591", "通信网络设备及器件"),
    ("BK0486", "传媒"), ("BK1222", "影视院线"), ("BK1220", "广告营销"), ("BK1221", "数字媒体"),
    ("BK1200", "电力设备"), ("BK1033", "电池"), ("BK1031", "光伏设备"), ("BK1032", "风电设备"),
    ("BK0457", "电网设备"), ("BK1315", "光伏电池组件"), ("BK1303", "锂电池"), ("BK1204", "国防军工"),
    ("BK1205", "机械设备"), ("BK0739", "工程机械"), ("BK0545", "通用设备"), ("BK0910", "专用设备"),
    ("BK1237", "自动化设备"), ("BK1408", "机器人"), ("BK0458", "仪器仪表"), ("BK1409", "激光设备"),
    ("BK1211", "汽车"), ("BK1262", "乘用车"), ("BK1264", "商用车"), ("BK0481", "汽车零部件"),
    ("BK1016", "汽车服务"), ("BK1529", "汽车电子电气系统"), ("BK0456", "家用电器"), ("BK1239", "白色家电"),
    ("BK1241", "黑色家电"), ("BK1244", "小家电"), ("BK1240", "厨卫电器"), ("BK0438", "食品饮料"),
    ("BK1280", "食品加工"), ("BK1282", "饮料乳品"), ("BK1281", "休闲食品"), ("BK1216", "医药生物"),
    ("BK0465", "化学制药"), ("BK1044", "生物制品"), ("BK1041", "医疗器械"), ("BK0727", "医疗服务"),
    ("BK1042", "医药商业"), ("BK1595", "原料药"), ("BK1598", "疫苗"), ("BK0433", "农林牧渔"),
    ("BK1261", "种植业"), ("BK1259", "养殖业"), ("BK1258", "饲料"), ("BK1256", "农产品加工"),
    ("BK1283", "银行"), ("BK1203", "非银金融"), ("BK0738", "多元金融"), ("BK1202", "房地产"),
    ("BK0451", "房地产开发"), ("BK1343", "物业管理"), ("BK1208", "建筑材料"), ("BK0424", "水泥"),
    ("BK0546", "玻璃玻纤"), ("BK0476", "装修建材"), ("BK1209", "建筑装饰"), ("BK1247", "基础建设"),
    ("BK1248", "专业工程"), ("BK1210", "交通运输"), ("BK0422", "物流"), ("BK0420", "航空机场"),
    ("BK0450", "航运港口"), ("BK0421", "铁路公路"), ("BK1489", "快递"), ("BK0427", "公用事业"),
    ("BK0428", "电力"), ("BK1390", "水务及水治理"), ("BK0728", "环保"), ("BK0437", "煤炭"),
    ("BK1250", "煤炭开采"), ("BK0464", "石油石化"), ("BK1274", "炼化及贸易"), ("BK1275", "油服工程"),
    ("BK1206", "基础化工"), ("BK1019", "化学原料"), ("BK0538", "化学制品"), ("BK0471", "化学纤维"),
    ("BK0454", "塑料"), ("BK1018", "橡胶"), ("BK0731", "农化制品"), ("BK1424", "氟化工"),
    ("BK0479", "钢铁"), ("BK1226", "普钢"), ("BK0478", "有色金属"), ("BK1287", "工业金属"),
    ("BK1615", "铜"), ("BK1613", "铝"), ("BK1617", "黄金"), ("BK0732", "贵金属"),
    ("BK1027", "小金属"), ("BK1015", "能源金属"), ("BK1621", "锂"), ("BK1626", "稀土"),
    ("BK1288", "金属新材料"), ("BK0436", "纺织服饰"), ("BK1225", "服装家纺"), ("BK1224", "纺织制造"),
    ("BK1212", "轻工制造"), ("BK1267", "造纸"), ("BK1265", "包装印刷"), ("BK0440", "家居用品"),
    ("BK1213", "商贸零售"), ("BK0482", "一般零售"), ("BK1268", "互联网电商"), ("BK1214", "社会服务"),
    ("BK1272", "旅游及景区"), ("BK1271", "酒店餐饮"), ("BK0740", "教育"), ("BK1035", "美容护理"),
]

# 热门概念板块（东财概念 t:3，2026-08 时代主线，代码真实存在）
CONCEPT_BOARDS = [
    ("BK1134", "算力概念"), ("BK1128", "CPO概念"), ("BK1138", "液冷服务器"),
    ("BK1188", "DeepSeek概念"), ("BK0800", "人工智能"), ("BK1153", "多模态AI"),
    ("BK1127", "AI芯片"), ("BK1126", "ChatGPT概念"), ("BK0891", "国产芯片"),
    ("BK0917", "半导体概念"), ("BK1184", "人形机器人"), ("BK1090", "机器人概念"),
    ("BK1166", "低空经济"), ("BK1157", "飞行汽车(eVTOL)"), ("BK1139", "中特估"),
    ("BK0683", "央国企改革"), ("BK1135", "数据要素"), ("BK1104", "信创"),
    ("BK0854", "华为概念"), ("BK0995", "华为昇腾"), ("BK0968", "固态电池"),
    ("BK0989", "储能概念"), ("BK0588", "光伏概念"), ("BK0802", "智能驾驶"),
    ("BK0547", "黄金概念"), ("BK1193", "海南自贸"), ("BK0454", "壳资源"),
]

# 期货主连（新浪日K symbol + 展示名 + 板块）；symbol 不存在会被自动跳过并在 notes 标注
FUTURES = [
    # 贵金属
    ("AU0", "沪金", "贵金属"), ("AG0", "沪银", "贵金属"),
    # 有色金属
    ("CU0", "沪铜", "有色金属"), ("AL0", "沪铝", "有色金属"), ("ZN0", "沪锌", "有色金属"),
    ("PB0", "沪铅", "有色金属"), ("NI0", "沪镍", "有色金属"), ("SN0", "沪锡", "有色金属"),
    ("AO0", "氧化铝", "有色金属"), ("BC0", "国际铜", "有色金属"),
    # 黑色系
    ("RB0", "螺纹钢", "黑色系"), ("HC0", "热卷", "黑色系"), ("I0", "铁矿石", "黑色系"),
    ("J0", "焦炭", "黑色系"), ("JM0", "焦煤", "黑色系"), ("SS0", "不锈钢", "黑色系"),
    ("SF0", "硅铁", "黑色系"), ("SM0", "锰硅", "黑色系"),
    # 能源化工
    ("SC0", "原油", "能源化工"), ("FU0", "燃油", "能源化工"), ("LU0", "低硫燃油", "能源化工"),
    ("BU0", "沥青", "能源化工"), ("RU0", "橡胶", "能源化工"), ("NR0", "20号胶", "能源化工"),
    ("L0", "塑料", "能源化工"), ("PP0", "聚丙烯", "能源化工"), ("V0", "PVC", "能源化工"),
    ("EG0", "乙二醇", "能源化工"), ("EB0", "苯乙烯", "能源化工"), ("PG0", "液化气", "能源化工"),
    ("TA0", "PTA", "能源化工"), ("MA0", "甲醇", "能源化工"), ("PF0", "短纤", "能源化工"),
    ("SH0", "烧碱", "能源化工"), ("UR0", "尿素", "能源化工"), ("SA0", "纯碱", "能源化工"),
    ("FG0", "玻璃", "能源化工"), ("SP0", "纸浆", "能源化工"),
    # 油脂油料
    ("M0", "豆粕", "油脂油料"), ("RM0", "菜粕", "油脂油料"), ("Y0", "豆油", "油脂油料"),
    ("OI0", "菜油", "油脂油料"), ("P0", "棕榈油", "油脂油料"),
    # 谷物
    ("C0", "玉米", "谷物"), ("CS0", "玉米淀粉", "谷物"), ("A0", "豆一", "谷物"),
    # 农副产品
    ("CF0", "棉花", "农副产品"), ("SR0", "白糖", "农副产品"), ("AP0", "苹果", "农副产品"),
    ("CJ0", "红枣", "农副产品"), ("PK0", "花生", "农副产品"), ("JD0", "鸡蛋", "农副产品"),
    ("LH0", "生猪", "农副产品"),
    # 新能源与航运
    ("SI0", "工业硅", "新能源与航运"), ("LC0", "碳酸锂", "新能源与航运"), ("EC0", "集运欧线", "新能源与航运"),
]

# 数字货币（Gate.io spot）
CRYPTO_PAIRS = [
    ("BTC_USDT", "BTC 比特币"),
    ("ETH_USDT", "ETH 以太坊"),
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
EM_HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"
SINA_HEADERS = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}

# 全局直连 opener（绕过任何系统/环境代理 —— 实测代理会让 push2his 等行情源断连）
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# 基础网络工具
# ---------------------------------------------------------------------------

def _fetch(url: str, headers=None, timeout: float = 12, retries: int = 2,
           decode: str = "utf-8") -> str | None:
    """直连 GET；失败重试 retries 次；始终返回文本或 None（不抛异常）"""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    last = None
    for i in range(retries + 1):
        try:
            with _DIRECT_OPENER.open(req, timeout=timeout) as r:
                return r.read().decode(decode, errors="replace")
        except Exception as e:  # noqa: BLE001 单源失败静默降级
            last = e
            if i < retries:
                time.sleep(0.4 * (i + 1))
    return None


def _fetch_json(url: str, headers=None, timeout: float = 12, retries: int = 2):
    text = _fetch(url, headers=headers, timeout=timeout, retries=retries)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _fetch_curl(url: str, timeout: float = 12) -> str | None:
    """curl.exe 直连兜底（Windows 10+ 自带；push2his 对部分 TLS 指纹/限流敏感时用）"""
    try:
        cp = subprocess.run(
            ["curl", "-s", "--max-time", str(int(timeout)), "--noproxy", "*",
             "-A", UA, "-e", "https://quote.eastmoney.com/", url],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return cp.stdout or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 各数据源原始接口
# ---------------------------------------------------------------------------

def _tencent_bars(code: str, start: str, end: str, max_n: int = 400) -> list | None:
    """腾讯日K：返回 [[date, open, close, high, low, volume?], ...]（qfq）

    注意：腾讯 WAF 封禁时快速失败（timeout 8/retries 1），由 _astock_bars 降级新浪源。
    """
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,%s,%s,%d,qfq"
           % (code, start, end, max_n))
    j = _fetch_json(url, timeout=8, retries=1)
    if not j:
        return None
    try:
        d = j["data"].get(code, {})
        bars = d.get("qfqday") or d.get("day") or []
        return bars if bars else None
    except Exception:
        return None


def _astock_bars(code: str, start: str, end: str) -> list | None:
    """A股区间日K：腾讯主源 → 新浪兜底。

    腾讯 web.ifzq 对高并发突发会触发 WAF（返回 501 页），批量拉取被临时封 IP 时自动降级新浪
    CN_MarketDataService（scale=240 日线，仅最近 400 根，需窗口过滤）。
    code 形如 sh000001 / sz000001 / bj832xxx（两源同格式）。
    返回与 _tencent_bars 相同的 [date,open,close,high,low,volume] 结构。
    """
    bars = _tencent_bars(code, start, end)
    if bars:
        return bars
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
           "symbol=" + code + "&scale=240&ma=no&datalen=400")
    j = _fetch_json(url, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"},
                    timeout=12, retries=2)
    if not isinstance(j, list) or not j:
        return None
    rows = []
    for x in j:
        try:
            d = (x or {}).get("day", "")
            if start <= d <= end:
                rows.append([d, x.get("open"), x.get("close"), x.get("high"),
                             x.get("low"), x.get("volume")])
        except Exception:
            continue
    return rows or None


def _em_kline_rows(secid: str, beg: str, end: str, lmt: int = 400) -> list | None:
    """东财 push2his 日K（个股/板块/期货统一）：返回 "date,open,close,high,low,volume" 字符串列表"""
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s&ut=%s"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
           "&klt=101&fqt=1&beg=%s&end=%s&lmt=%d") % (secid, EM_UT, beg, end, lmt)
    j = _fetch_json(url, headers=EM_HEADERS, timeout=10, retries=1)
    if not j:
        # curl 兜底（部分网络环境 urllib TLS 指纹被拒）
        text = _fetch_curl(url)
        if text:
            try:
                j = json.loads(text)
            except Exception:
                j = None
    if not j:
        return None
    try:
        d = j.get("data") or {}
        kl = d.get("klines") or []
        return kl if kl else None
    except Exception:
        return None


def _em_clist(fs: str, fields: str, sort_fid: str = "f3", po: int = 1,
              pn: int = 1, pz: int = 100) -> list | None:
    """东财 push2delay 列表（个股/板块快照）。fields 形如 "f2,f3,f12,f14,f100" """
    q = urllib.parse.urlencode({
        "pn": pn, "pz": pz, "po": po, "np": 1, "fltt": 2, "invt": 2,
        "fid": sort_fid, "fs": fs, "fields": fields,
    })
    j = _fetch_json("https://push2delay.eastmoney.com/api/qt/clist/get?" + q,
                    headers=EM_HEADERS, timeout=12, retries=2)
    if not j:
        return None
    try:
        diff = (j.get("data") or {}).get("diff") or []
        return diff
    except Exception:
        return None


def _sina_futures_daily(symbol: str) -> list | None:
    """新浪期货主连日K（全历史，返回 dict 列表）"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/"
           "InnerFuturesNewService.getDailyKLine?symbol=" + symbol)
    text = _fetch(url, headers=SINA_HEADERS, timeout=15, retries=1)
    if not text:
        return None
    import re as _re
    m = _re.search(r"\((\[.*\])\)", text, _re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
        return arr if isinstance(arr, list) else None
    except Exception:
        return None


def _sina_futures_quotes(symbols: list[str]) -> dict:
    """新浪期货主连实时报价（GBK）：返回 {symbol: {name, price, prev_settle, change_pct, date}}"""
    if not symbols:
        return {}
    url = "https://hq.sinajs.cn/list=" + ",".join("nf_" + s for s in symbols)
    text = _fetch(url, headers=SINA_HEADERS, timeout=10, retries=1, decode="gbk")
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        if "=" not in line or "nf_" not in line:
            continue
        try:
            sym = line.split("hq_str_nf_")[1].split("=")[0].strip()
            body = line.split('="', 1)[1].rsplit('"', 1)[0]
            f = body.split(",")
            if len(f) < 18 or sym not in symbols:
                continue
            prev_settle = float(f[10]) if f[10] else 0.0
            price = float(f[8]) if f[8] else 0.0
            change_pct = (price - prev_settle) / prev_settle * 100 if prev_settle else None
            out[sym] = {
                "name": f[0], "price": price, "prev_settle": prev_settle,
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "date": f[17], "open": float(f[2]) if f[2] else None,
                "high": float(f[3]) if f[3] else None, "low": float(f[4]) if f[4] else None,
            }
        except Exception:
            continue
    return out


def _gate_klines(pair: str, start: str, end: str) -> list | None:
    """Gate.io 日K：start/end 为 'YYYY-MM-DD'；返回 [ts, quote_vol, close, high, low, open, base_vol, closed]"""
    ts_from = int(_dt.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc).timestamp())
    ts_to = int(_dt.datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc).timestamp()) + 86400
    url = ("https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=%s&interval=1d"
           "&from=%d&to=%d&limit=1000") % (pair, ts_from, ts_to)
    j = _fetch_json(url, timeout=15, retries=2)
    if not isinstance(j, list):
        return None
    return j


# ---------------------------------------------------------------------------
# 区间统计工具
# ---------------------------------------------------------------------------

def _bars_pct(bars) -> dict | None:
    """腾讯 bars（[date,open,close,high,low,vol]）→ 区间统计：取窗口内首末交易日收盘计算涨跌幅"""
    if not bars or len(bars) < 2:
        return None
    try:
        first = bars[0]
        last = bars[-1]
        c0 = float(first[2])
        c1 = float(last[2])
        if not c0:
            return None
        highs = [float(b[3]) for b in bars]
        lows = [float(b[4]) for b in bars]
        hi = max(highs)
        lo = min(lows)
        vol = None
        if len(last) > 5:
            try:
                vol = float(last[5])
            except Exception:
                vol = None
        return {
            "start_date": first[0], "end_date": last[0],
            "start_close": c0, "end_close": c1,
            "change_pct": round((c1 - c0) / c0 * 100, 2),
            "high": hi, "low": lo,
            "amplitude_pct": round((hi - lo) / c0 * 100, 2),
            "end_volume": vol,
        }
    except Exception:
        return None


def _em_rows_pct(rows: list[str]) -> dict | None:
    """东财K线 rows（"date,open,close,high,low,volume"）→ 同 _bars_pct"""
    bars = []
    for line in rows:
        parts = line.split(",")
        if len(parts) >= 6:
            bars.append([parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]])
    return _bars_pct(bars)


def _em_secid_of_stock(code: str) -> str:
    """A股代码 → 东财 secid（sh/sz/bj 前缀推断）"""
    if code.startswith(("6", "9", "5")):  # 沪市 6 开头 A股（68 科创）；沪基金 5
        return "1." + code
    if code.startswith(("0", "3")):  # 深市 0 主板 / 3 创业板
        return "0." + code
    if code.startswith(("4", "8", "92")):  # 北交所
        return "0." + code
    return "0." + code


def _tencent_code_of_stock(code: str) -> str:
    if code.startswith(("6", "9", "5")):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    return "sz" + code


def _fmt_price(v) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:,.1f}"
    return f"{v:,.2f}"


def _fmt_pct(v) -> str:
    if v is None:
        return "-"
    return f"{v:+.2f}%"


def _fmt_vol(v) -> str:
    """成交量(手/股) → 亿/万 缩写"""
    if not v:
        return "-"
    try:
        v = float(v)
    except Exception:
        return "-"
    if v >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if v >= 1e4:
        return f"{v / 1e4:.1f}万"
    return f"{v:.0f}"


# ---------------------------------------------------------------------------
# 采集器：A股
# ---------------------------------------------------------------------------

def collect_astock(start: str, end: str, quick: bool = False) -> dict:
    """A股盘面快照。

    quick=True（AI 阶段分析注入用）：指数区间 + 当日板块/个股榜单，数秒内完成。
    quick=False（盘面综述用）：指数区间 + 板块区间（候选尽力而为）+ 领涨个股区间精确重算。
    """
    notes: list[str] = []
    indices = []
    tencent_failed = False
    for code, name in A_SHARE_INDICES:
        bars = _astock_bars(code, start, end)
        if not bars and not tencent_failed:
            tencent_failed = True
            notes.append("腾讯行情受限（WAF/限流），指数/个股已降级新浪源重试")
        st = _bars_pct(bars) if bars else None
        if st:
            st["name"] = name
            st["code"] = code
            indices.append(st)
        else:
            notes.append(f"指数 {name}({code}) 数据缺失")
        time.sleep(0.08)

    # 当日板块快照（行业 top / 概念 top 混合，供候选与快照口径展示）
    board_snapshot = []
    board_fs = "m:90+t:2,m:90+t:3"
    for po in (1, 0):
        diff = _em_clist(board_fs, "f2,f3,f12,f14,f128,f100", sort_fid="f3", po=po, pz=30)
        for x in (diff or []):
            if x.get("f3") in (None, "-"):
                continue
            board_snapshot.append({
                "code": x.get("f12"), "name": x.get("f14"),
                "pct": x.get("f3"), "leader": x.get("f128"), "leader_pct": x.get("f100"),
            })
        time.sleep(0.2)
    # 去重保序（部分板块同时在 t:2/t:3？几乎不会，但保险）
    seen = set()
    board_snapshot = [b for b in board_snapshot if not (b["code"] in seen or seen.add(b["code"]))]

    # 板块区间（仅非 quick）：候选 = 静态表里出现过的快照榜前 16 + 榜尾 4（push2his 限流，尽力而为）
    boards_interval = []
    if not quick:
        cands = []
        static = {c: n for c, n in INDUSTRY_BOARDS + CONCEPT_BOARDS}
        for b in board_snapshot:
            if b["code"] in static and len(cands) < 16 and b["pct"] is not None and float(b["pct"]) >= 0:
                cands.append(b)
        for b in reversed(board_snapshot):
            if b["code"] in static and len(cands) < 20 and b["pct"] is not None and float(b["pct"]) < 0:
                cands.append(b)
        t_board = time.time()
        board_budget = 75.0  # 板块区间拉取总耗时预算（秒），超时即停避免拖死生成
        consec_fail = 0
        for b in cands:
            if time.time() - t_board > board_budget:
                notes.append("板块区间K线受行情源限流，仅部分完成（预算内共 %d 个）" % len(boards_interval))
                break
            rows = _em_kline_rows("90." + b["code"], start.replace("-", ""), end.replace("-", ""), lmt=300)
            st = _em_rows_pct(rows) if rows else None
            if st:
                st["name"] = static.get(b["code"], b["name"])
                st["code"] = b["code"]
                st["day_pct"] = b["pct"]
                boards_interval.append(st)
                consec_fail = 0
                time.sleep(0.4)
            else:
                consec_fail += 1
                time.sleep(1.0 + 1.5 * min(consec_fail, 4))  # 失败退避：1s→2.5s→4s→...
        if cands and not boards_interval:
            notes.append("板块区间K线未取到（东财 push2his 限流），板块表现以榜单快照口径展示")

    # 领涨个股候选池：阶段末日（= end，若未来则用今天）当日涨幅榜前 200（翻 2 页）
    stocks = []
    pool = []
    for pn in (1, 2):
        diff = _em_clist("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                         "f2,f3,f12,f14,f100", sort_fid="f3", po=1, pn=pn, pz=100)
        pool += (diff or [])
        time.sleep(0.2)
    # 去重（按代码）
    uniq = {}
    for x in pool:
        if x.get("f12") and x["f12"] not in uniq:
            uniq[x["f12"]] = x
    pool = list(uniq.values())[:200]

    if quick:
        # 快速模式只取前 12 做展示（不做区间重算，避免 200 次K线）
        for x in pool[:12]:
            stocks.append({
                "code": x.get("f12"), "name": x.get("f14"),
                "day_pct": x.get("f3"), "industry": x.get("f100"),
            })
    else:
        # 逐个拉区间K线精确重算区间涨幅（并发 6 + 微抖动，避免腾讯 WAF 突发触发）
        import concurrent.futures as _cf
        import random as _rnd

        def _stk(x):
            time.sleep(_rnd.uniform(0.02, 0.12))
            bars = _astock_bars(_tencent_code_of_stock(x["f12"]), start, end)
            st = _bars_pct(bars) if bars else None
            if not st:
                return None
            return {
                "code": x.get("f12"), "name": x.get("f14"),
                "industry": x.get("f100"), "interval_pct": st["change_pct"],
                "end_close": st["end_close"], "start_close": st["start_close"],
                "day_pct": x.get("f3"),
            }

        with _cf.ThreadPoolExecutor(max_workers=6) as ex:
            res = list(ex.map(_stk, pool))
        changed = [r for r in res if r]
        changed.sort(key=lambda s: s["interval_pct"], reverse=True)
        stocks = changed[:12]
        if len(changed) < 12:
            notes.append(f"领涨候选池重算仅成功 {len(changed)}/200（腾讯限流时降级新浪源）")
        notes.append("领涨个股口径：取阶段末日全市场涨幅榜前 200 为候选，逐个拉区间日K重算区间涨幅，取前 12 名")

    return {
        "indices": indices,
        "board_snapshot": board_snapshot,
        "boards_interval": boards_interval,
        "stocks": stocks,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 采集器：商品期货
# ---------------------------------------------------------------------------

def collect_futures(start: str, end: str, quick: bool = False) -> dict:
    notes: list[str] = []
    varieties = []
    missing = []
    # 主连 daily（新浪全历史，逐个拉太重：并发 6）
    import concurrent.futures as _cf

    def _one(item):
        sym, name, sector = item
        bars = _sina_futures_daily(sym)
        return (sym, name, sector, bars)

    items = [(s, n, sec) for (s, n, sec) in FUTURES]
    with _cf.ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(_one, items))
    for sym, name, sector, bars in res:
        if not bars:
            missing.append(f"{name}({sym})")
            continue
        try:
            win = [b for b in bars if start <= b["d"] <= end]
        except Exception:
            win = []
        if len(win) < 2:
            missing.append(f"{name}({sym})")
            continue
        c0 = float(win[0]["c"])
        c1 = float(win[-1]["c"])
        s0 = float(win[0].get("s") or 0) or c0
        s1 = float(win[-1].get("s") or 0) or c1
        highs = [float(b["h"]) for b in win]
        lows = [float(b["l"]) for b in win]
        if not c0:
            missing.append(f"{name}({sym})")
            continue
        varieties.append({
            "symbol": sym, "name": name, "sector": sector,
            "start_date": win[0]["d"], "end_date": win[-1]["d"],
            "start_close": c0, "end_close": c1,
            "close_pct": round((c1 - c0) / c0 * 100, 2),
            "settle_pct": round((s1 - s0) / s0 * 100, 2) if s0 else None,
            "high": max(highs), "low": min(lows),
            "amp_pct": round((max(highs) - min(lows)) / c0 * 100, 2),
        })
        time.sleep(0)

    # 板块聚合（按 close_pct 均值）
    sectors = {}
    for v in varieties:
        s = sectors.setdefault(v["sector"], {"sector": v["sector"], "items": []})
        s["items"].append(v)
    sector_rows = []
    for s in sectors.values():
        pcts = [i["close_pct"] for i in s["items"]]
        sector_rows.append({
            "sector": s["sector"],
            "count": len(pcts),
            "avg_pct": round(sum(pcts) / len(pcts), 2),
            "varieties": sorted(s["items"], key=lambda i: i["close_pct"], reverse=True),
        })
    sector_rows.sort(key=lambda s: s["avg_pct"], reverse=True)

    if missing:
        notes.append("以下品种未取到区间日K（已跳过）：" + "、".join(missing[:12]))

    # 当日快照报价（供报告末尾"截至阶段末日收盘"参考）
    quotes = _sina_futures_quotes([v["symbol"] for v in varieties[:18]]) if not quick else {}

    return {"sectors": sector_rows, "varieties": varieties, "quotes": quotes, "notes": notes}


# ---------------------------------------------------------------------------
# 采集器：数字货币
# ---------------------------------------------------------------------------

def collect_crypto(start: str, end: str, quick: bool = False) -> dict:
    notes: list[str] = []
    coins = []
    for pair, label in CRYPTO_PAIRS:
        kl = _gate_klines(pair, start, end)
        if not kl or len(kl) < 2:
            notes.append(f"{label} 区间日K缺失")
            continue
        c0 = float(kl[0][5])   # open
        c1 = float(kl[-1][2])  # close
        highs = [float(k[3]) for k in kl]
        lows = [float(k[4]) for k in kl]
        vols = [float(k[6]) for k in kl]
        coins.append({
            "pair": pair, "label": label,
            "start_date": _dt.datetime.utcfromtimestamp(int(kl[0][0])).strftime("%Y-%m-%d"),
            "end_date": _dt.datetime.utcfromtimestamp(int(kl[-1][0])).strftime("%Y-%m-%d"),
            "start_price": c0, "end_price": c1,
            "change_pct": round((c1 - c0) / c0 * 100, 2),
            "high": max(highs), "low": min(lows),
            "amplitude_pct": round((max(highs) - min(lows)) / c0 * 100, 2),
            "avg_vol": round(sum(vols) / len(vols), 0),
        })
        time.sleep(0.2)
    return {"coins": coins, "notes": notes}


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------

def collect_market(instrument_type: str, start: str, end: str, quick: bool = False) -> dict:
    """按品种类型采集盘面快照。start/end: 'YYYY-MM-DD'"""
    snap = {
        "instrument_type": instrument_type,
        "period": {"start": start, "end": end},
        "collected_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "notes": [],
    }
    t0 = time.time()
    if instrument_type == "A股":
        part = collect_astock(start, end, quick=quick)
    elif instrument_type == "商品期货":
        part = collect_futures(start, end, quick=quick)
    elif instrument_type == "数字货币":
        part = collect_crypto(start, end, quick=quick)
    else:  # ''/通用 → 三大市场轻量都取（仅指数级别概览）
        part = {
            "A股": collect_astock(start, end, quick=True),
            "商品期货": collect_futures(start, end, quick=True),
            "数字货币": collect_crypto(start, end, quick=True),
        }
    if isinstance(part, dict) and "notes" in part:
        snap["notes"] += part["notes"]
    snap["data"] = part
    snap["elapsed_sec"] = round(time.time() - t0, 1)
    return snap
