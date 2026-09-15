# -*- coding: utf-8 -*-
"""大类赛道映射：东财概念板块 → 纵向赛道 / 横切属性。

数据来自 `app/data/theme_taxonomy.json`（由 `backend/build_theme_taxonomy.py` 离线生成）。
**为什么离线固化而不是运行时算**：
    1. 打包后的 exe 没有 MCP 环境，拉不到申万/产业链骨架；
    2. 赛道归类是低频知识 —— 只有东财新增概念板块时才需要重建，没必要每天算。

**运行时自愈**：JSON 里同时内嵌了 `rules` / `cross_rules` / `name_override`。
东财若在两次发版之间新增了概念板块，`tracks` 里查不到 code，就退到名表、再退到关键词规则，
不必为了一个新板块重新打包发版。

查找优先级（先命中先用）：
    code → tracks/cross 反查表      （精确，最可靠）
    name → name_override 覆盖表      （处理语义歧义，如「国资云概念」）
    name → cross_rules               （横切属性，**必须先于赛道规则**，否则「国资」会把
                                      「国资云概念」抓走）
    name → rules                     （纵向赛道关键词）
    都没有 → None                    （不显示赛道列，不硬套）

⚠️ **重新生成映射表后必须重启后端**：`_load()` 把 JSON 缓存进模块级 `_LOADED`，
只在进程首次调用时读盘。改了 `theme_taxonomy.json` 却不重启，页面上仍是旧映射
（实测：`转基因` 已修正为农林牧渔，但页面继续显示医药生物）。
打包后的 exe 不存在这个问题（映射文件随程序分发，运行期不变）。
"""
from __future__ import annotations

import json
import os
import sys

_LOADED: dict | None = None
_CODE2CAT: dict[str, str] = {}
_NAME2CAT: dict[str, str] = {}
_RULES: list = []
_CROSS_RULES: dict = {}
_NAME_OVERRIDE: dict = {}
_ERROR = ''


def _data_dir() -> str:
    """定位打包后的数据目录。

    onedir 模式下 PyInstaller 把 `datas` 放到 `_internal/`，运行时 `_MEIPASS` 指向它；
    开发环境直接用 `backend/app/data/`。
    """
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', None) or os.path.dirname(sys.executable)
        return os.path.join(base, 'app', 'data')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')


def _load() -> dict:
    global _LOADED, _CODE2CAT, _NAME2CAT, _RULES, _CROSS_RULES, _NAME_OVERRIDE, _ERROR
    if _LOADED is not None:
        return _LOADED
    path = os.path.join(_data_dir(), 'theme_taxonomy.json')
    doc: dict = {}
    try:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except Exception as e:  # noqa: BLE001
        # 映射文件缺失/损坏时**不能抛** —— 赛道只是展示增强项，
        # 让它把整个复盘页带崩是得不偿失的。
        _ERROR = '%s: %s' % (type(e).__name__, e)
        doc = {}
    _CODE2CAT = {}
    for cat, codes in (doc.get('tracks') or {}).items():
        for c in codes or []:
            _CODE2CAT[c] = cat
    for cat, codes in (doc.get('cross') or {}).items():
        for c in codes or []:
            _CODE2CAT.setdefault(c, cat)
    _NAME2CAT = {}
    _NAME_OVERRIDE = dict(doc.get('name_override') or {})
    _RULES = list(doc.get('rules') or [])
    _CROSS_RULES = dict(doc.get('cross_rules') or {})
    _LOADED = doc
    return doc


_TRACK_ABSENT = object()


def track_of(code: str | None, name: str | None) -> str | None:
    """返回板块所属赛道/横切属性名；无法归纳时返回 None（前端显示 `-`）。"""
    _load()
    nm = (name or '').strip()
    # 1) 覆盖表（最高优先级：人工纠正过语义歧义的那几个）
    if nm and nm in _NAME_OVERRIDE:
        return _NAME_OVERRIDE[nm] or None
    # 2) code 反查
    if code:
        t = _CODE2CAT.get(code)
        if t:
            return t
    if not nm:
        return None
    # 3) 横切属性规则（先于赛道规则）
    for cat, kws in _CROSS_RULES.items():
        for k in kws:
            if k in nm:
                return cat
    # 4) 纵向赛道规则
    for row in _RULES:
        if len(row) < 2:
            continue
        cat, kws = row[0], row[1]
        for k in kws:
            if k in nm:
                return cat
    return None


def track_meta() -> dict:
    """诊断用：映射表状态（供 /api/health 或排障脚本查看）。"""
    doc = _load()
    return {
        'available': bool(doc and not _ERROR),
        'error': _ERROR,
        'path': os.path.join(_data_dir(), 'theme_taxonomy.json'),
        'schema': doc.get('schema'),
        'generated_at': doc.get('generated_at'),
        'tracks': len(doc.get('tracks') or {}),
        'cross': len(doc.get('cross') or {}),
        'stats': doc.get('stats') or {},
    }


def track_names() -> list[str]:
    """全部纵向赛道名（前端下拉/图例用）。"""
    doc = _load()
    return list((doc.get('tracks') or {}).keys())


def is_track(name: str | None) -> bool:
    """是否为纵向赛道（区别于横切属性 —— 横切不参与赛道聚合，避免「央国企改革」这种
    并集 2272 只、65 天里 64 天在榜的属性把所有赛道都污染掉）。"""
    doc = _load()
    return bool(name) and name in (doc.get('tracks') or {})


def attach_tracks(snap: dict | None) -> int:
    """给快照 `concept.main_lines` 补齐/刷新 `track` 字段，返回被写入的行数。

    **为什么放在读取时而不是只放在构建时**：
        赛道映射表是离线生成的。库里那 65 行历史快照重建时还没有这个字段；
        `live` 行更是只在开盘日才刷新一次。与其为两个展示字段重跑 1.5 分钟全量重建，
        不如在 `cache_get` 这个唯一读口增量补齐 —— 新老数据一视同仁。

    **为什么每次都覆盖而不是「缺了才填」**：
        映射表重生成后（东财新增板块、规则调整），旧快照里缓存的值就是错的。
        覆盖才能保证展示与当前映射一致。单次成本就是几十次 dict 查询，可以忽略。
    """
    if not isinstance(snap, dict):
        return 0
    concept = snap.get('concept')
    if not isinstance(concept, dict):
        return 0
    lines = concept.get('main_lines')
    if not isinstance(lines, list):
        return 0
    n = 0
    for m in lines:
        if not isinstance(m, dict):
            continue
        t = track_of(m.get('code'), m.get('name'))
        m['track'] = t
        m['track_cross'] = bool(t) and not is_track(t)
        n += 1
    return n
