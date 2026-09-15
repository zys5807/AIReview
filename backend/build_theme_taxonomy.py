# -*- coding: utf-8 -*-
"""生成「东财概念板块 → 大类赛道」静态映射表。

为什么是静态 JSON 而不是运行时调 MCP：
    打包后的 exe 没有 MCP 环境，且大类归纳是**低频变动**的知识（东财新增板块才需要重建），
    没必要每天算一遍。生成本地固化，随 exe 打包。

三级归属（按优先级，先命中先用）：
    1. `TRACK_NAME` —— 精确板块名表（人工审校过的兜底，处理语义歧义的少数派）
    2. `RULES`      —— 关键词有序规则（覆盖 ~81%）
    3. 余弦吸附     —— 对规则未命中的概念，吸附到成分股最接近的赛道（~19%）

两层结构：
    tracks 纵向赛道（**互斥**，用于「同一赛道内多概念不重复计数」的聚合）
    cross  横切属性（**多标签**，如央国企改革 —— 它的并集达 2272 只、占半个市场，
           与半导体这种纵向赛道不是同一维度，混在一起会让所有赛道都被它污染）

用法：
    venv/Scripts/python.exe build_theme_taxonomy.py [--db app.db] [--out app/data/theme_taxonomy.json]
"""
import argparse
import collections
import io
import json
import math
import os
import sqlite3
import sys
import datetime

# ---------------- 纵向赛道关键词规则（顺序敏感：先命中先用，特例要排在通例前面）----
RULES = [
 ('半导体/芯片', ['半导体', '芯片', '光刻', '晶圆', '封装', '存储', 'IC设计', '硅片', '靶材',
                  '集成电路', '第三代', '第四代', '碳化硅', '氮化镓', 'IGBT', 'MLCC',
                  '被动元件', '蓝宝石', 'PCB']),
 ('消费电子', ['消费电子', '耳机', '智能穿戴', '苹果', '折叠屏', '手机', '摄像头', '屏下',
              '电子纸', '无线充电', '智能电视', '智能家居', '智能音箱', '超清视频', '裸眼',
              '显示技术', 'LED', 'OLED', 'MiniLED', 'MicroLED', '柔性屏', '全息', '电子烟',
              '荣耀', '小米']),
 ('通信/算力基建', ['通信', '5G', '6G', 'F5G', '光通信', '光模块', 'CPO', '光纤', '算力',
                    '数据中心', '东数西算', '液冷', 'IPv6', '物联网', 'WiFi', '卫星互联网',
                    '北斗', 'UWB', '车联网', '智能驾驶', '毫米波', 'ETC', '灯杆', '传感器',
                    '激光雷达', '卫星导航']),
 ('AI软件应用', ['人工智能', 'AI', 'AIGC', 'ChatGPT', 'DeepSeek', 'Kimi', '多模态', '大模型',
                 '智能体', '机器视觉', '语料', '英伟达', '智谱', 'MLOps', '昇腾']),
 ('计算机/信创', ['信创', '国产软件', '软件', '网络安全', '大数据', '云计算', '数字经济', '数据要素',
                  '数据安全', '数据确权', '区块链', '数字货币', '财税数字化', 'ERP', 'EDA', 'SaaS',
                  '智慧政务', '国资云', 'Web3', '元宇宙', '数字孪生', '边缘计算', '工业互联网',
                  '数字水印', '时空大数据', '量子科技', 'VPN', 'DRG', 'EDR', '安防', '空间计算',
                  '电子身份证', '欧拉', '鸿蒙', '腾讯云', '阿里', '互联网服务']),
 ('传媒/游戏', ['游戏', '影视', '短剧', '传媒', '文娱', '虚拟数字人', '虚拟现实', '增强现实',
                '混合现实', '数字阅读', '出版', '网红', '直播', '谷子经济', '盲盒', '体育',
                '电竞', '知识产权', '小红书', '抖音', '快手']),
 ('医药生物', ['医药', '医疗', '药', '生物', '疫苗', '诊断', 'CRO', '免疫', '单抗', '基因',
               '细胞', '器械', '中药', '肝素', '青蒿素', '维生素', '阿兹海默', '长寿', '医美',
               'SPD', '幽门', '流感', '病毒', '肝炎', '辅助生殖', '血制品', '脑机接口']),
 ('军工/航天', ['军工', '军民融合', '航天', '航空', '大飞机', '卫星', '航母', '空间站', '船舶',
                '兵装', '无人机', '低空经济', '商业航天', '通用航空', 'C919', '民船']),
 ('新能源电池', ['电池', '锂电', '钠离子', '固态', '正极', '负极', '隔膜', '电解液', '麒麟',
                 '钒', '储能', '氢', '燃料电池', '充电桩', '换电', '超级电容', '复合集流体',
                 '高压快充', '新能源']),
 ('光伏/风电', ['光伏', '风电', '风能', 'HJT', 'TOPCon', 'BC电池', '钙钛矿', '太阳能', '硅料',
                '逆变器']),
 ('电网/电力', ['电网', '电力', '特高压', '虚拟电厂', '抽水蓄能', '可控核聚变', '核电', '核能',
                '水电', '发电机', '超超临界']),
 ('汽车', ['汽车', '整车', '特斯拉', '小米汽车', '华为汽车', '智能座舱', '车轮', '轮毂',
           '一体化压铸', '拆解', '后视镜', '车灯', '燃油', '胎压']),
 ('机器人/自动化', ['机器人', '人形', '执行器', '减速器', '自动化', '工业母机', '机床', '伺服',
                    '谐波', 'PLC', '新型工业化']),
 ('机械/工程', ['工程机械', '挖掘机', '机械', '电梯', '轨道交通', '高铁', '铁路', '叉车',
                '油气设服', '海工', '3D打印', '磁悬浮', '交运设备', '磁材', '同步磁阻']),
 ('化工', ['化工', '氟化', '磷化', '有机硅', '环氧', '钛白', '纯碱', '聚氨酯', '橡胶', '塑料',
           '降解', '农药', '兽药', '颜料', '涂料', '添加剂', '草甘膦', '民爆', 'PEEK',
           '工业气体', '氦气', '新材料', 'PVDF']),
 ('有色/贵金属', ['稀土', '黄金', '白银', '小金属', '铜', '铝', '锌', '镍', '铅', '钨', '钼',
                  '锡', '钛', '镁', '锑', '锂矿', '金属回收', '高温合金', '粉末冶金', '培育钻石',
                  '石墨烯', '碳纤维', '碳基', '超导', '纳米银', '资源开采']),
 ('煤炭石油', ['煤炭', '石油', '油气', '天然气', '页岩气', '煤化工', '可燃冰', '地热', '油服']),
 ('钢铁/建材', ['钢铁', '冶金', '水泥', '玻璃', '建材', '装配式', '管材', '特钢']),
 ('农林牧渔', ['农业', '种植', '种子', '转基因', '粮', '猪肉', '鸡', '水产', '养殖', '饲料',
               '化肥', '土地流转', '供销社', '人造肉', '大豆', '玉米', '宠物', '林业', '渔业',
               '乡村振兴', '工业大麻']),
 ('食品饮料', ['白酒', '酿酒', '食品', '饮料', '调味', '乳业', '啤酒', '预制菜', '味蕾', '膳食',
               '代糖', '植物奶', '零食', '烘焙']),
 ('零售/消费', ['零售', '电商', '消费', '拼多多', '淘宝', '京东', '跨境', '免税', '退税', '百货',
                '超市', '社区团购', '化妆品', '纺织', '服装', '面料', '家纺', '家电', '五金',
                '包装', '造纸', '印刷', '户外', '露营', '珠宝', '美容', '快递', '冷链', 'C2M',
                '首发经济', '地摊', '内贸流通']),
 ('旅游/服务', ['旅游', '酒店', '餐饮', '教育', '培训', '职教', '人力资源', '劳务', '养老',
                '托育', '婴童', '殡葬', '彩票', '共享经济', '物业', '冰雪经济']),
 ('金融', ['券商', '银行', '保险', '金融', '证券', '信托', '期货', '支付', '汇率', '贬值',
           'REITs', '创投', '蚂蚁', 'AMC']),
 ('地产/建筑', ['房地产', '地产', '建筑', '工程', '基建', '园林', '装修', '装饰', '水利', '管网',
                '自贸', '新区', '雄安', '海南', '一带一路', 'PPP', '安置', '租售', '房屋检测']),
 ('环保/公用', ['环保', '节能', '污染', '垃圾', '净水', '水务', '治理', '海绵', '土壤', '空气能',
                '碳中和', '碳交易', '碳捕捉', '循环', '噪声']),
]

# ---------------- 横切属性（多标签，不算赛道）----------------
CROSS_RULES = {
 '国企改革': ['央国企改革', '中特估', '沪企改革', '国资', '国企'],
 '政策主题': ['统一大市场', '反内卷', '中俄贸易', '内循环', '共同富裕', '新型城镇化'],
 '属性标签': ['专精特新', '行业龙头', '超级品牌', '高成长股', '微盘', '宁组合', '创业成份',
              '股权', '密集调研', 'IPO受益', '证金持股', '破净', '红利', '重组', '并购',
              '独角兽', '周期股', '贬值受益', '稀缺资源'],
}

# ---------------- 人工兜底：精确板块名 → 赛道（处理余弦吸附判错的那批）----------------
# ⚠️ 这里只能填「赛道名」或「横切属性名」，**不能填 None**。
# 早先版本把政策主题映射成 None 表示"不归赛道"，结果它们既没进 tracks 也没进 cross，
# 而是掉进 unassigned —— 中特估 / 反内卷概念 / 统一大市场 / 中俄贸易概念 全部丢失。
# 正确做法是直接删掉条目，让 CROSS_RULES 里的"政策主题"规则接住。
TRACK_NAME = {
    '新能源': '新能源电池',
    '新材料': '化工',
    '资源开采概念': '有色/贵金属',
    # 国资云 = 国资背景的云计算基础设施，是**计算机**而非国企改革主题。
    # 但 CROSS_RULES 先于 RULES 匹配，「国资」子串会把它抓走 —— 必须在此处显式纠正。
    '国资云概念': '计算机/信创',
    # 转基因是**农业**技术，不是医药。医药生物规则里的「基因」原本是为「基因测序」设的，
    # 但它的赛道排在农林牧渔之前，会把转基因先抓走（实测 09-01 主线里转基因显示成医药生物）。
    '转基因': '农林牧渔',
    # ── 以下 4 条是全量审计（383 个概念逐条过）后发现的明确错判 ──
    # 小米汽车是**整车**题材，被「小米」关键词抓进了消费电子（小米概念/小米汽车同前缀）
    '小米汽车': '汽车',
    # 「药」字命中太宽：农药兽药属化工。医药规则排在化工之前，必须显式纠正
    '农药兽药': '化工',
    # 「生物」字命中太宽：生物识别是计算机（人脸/指纹识别）
    '生物识别': '计算机/信创',
    # 「生物」字命中太宽：生物质能发电是公用事业，不是医药
    '生物质能发电': '环保/公用',
}


def _load_boards(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT board_code, board_name, members_json FROM board_member_cache").fetchall()
    con.close()
    out = {}
    for code, name, mj in rows:
        try:
            m = json.loads(mj or '[]')
        except Exception:
            m = []
        mem = set()
        for x in m:
            c = x.get('code') if isinstance(x, dict) else (x if isinstance(x, str) else None)
            if c:
                mem.add(str(c))
        out[code] = {'name': name or '', 'mem': mem}
    return out


def _rule_match(name):
    for cat, kws in CROSS_RULES.items():
        for k in kws:
            if k in name:
                return cat
    for cat, kws in RULES:
        for k in kws:
            if k in name:
                return cat
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.path.join(os.path.dirname(__file__), 'app.db'))
    ap.add_argument('--out', default=os.path.join(os.path.dirname(__file__), 'app', 'data', 'theme_taxonomy.json'))
    ap.add_argument('--min-members', type=int, default=3)
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(__file__))
    from app.services.daily_market import _is_noise_board

    boards = _load_boards(args.db)
    clean, noise = {}, []
    for code, v in boards.items():
        nm = v['name']
        if _is_noise_board(nm):
            noise.append(nm)
            continue
        if len(v['mem']) < args.min_members:
            continue
        clean[code] = v

    # ---- 1) 精确名表 2) 关键词规则 ----
    conf, low = {}, []
    for code, v in clean.items():
        ov = TRACK_NAME.get(v['name'], '__NONE__')
        if ov != '__NONE__':
            if ov:
                conf[code] = {'track': ov, 'src': 'name'}
            else:
                conf[code] = {'track': None, 'src': 'name'}   # 显式判为横切/无赛道
            continue
        cat = _rule_match(v['name'])
        if cat:
            conf[code] = {'track': cat, 'src': 'kw'}
        else:
            low.append(code)

    # ---- 3) 余弦吸附 ----
    anchor_mem = {}
    for cat, _ in RULES:
        m = set()
        for code, c in conf.items():
            if c['track'] == cat and c['src'] != 'name':
                m |= clean[code]['mem']
        anchor_mem[cat] = m
    for code in low:
        mem = clean[code]['mem']
        best, bestcat = 0.0, None
        for cat, m in anchor_mem.items():
            if not m:
                continue
            c = len(mem & m) / math.sqrt(len(mem) * len(m))
            if c > best:
                best, bestcat = c, cat
        conf[code] = {'track': bestcat, 'src': 'cos', 'conf': round(best, 3)}

    # ---- 把吸附结果回写进 name_override，让「规则 + 名表」能完整复现整张映射 ----
    # 动机：吸附是按 **code** 落在 JSON 的 tracks 里的。如果东财改了板块代码，
    # 或者有人只拿着板块名去查（code=None），这条归属就会丢掉 ——
    # 实测 `track_of(None,'华为概念')` 原本返回 None，因为规则里没有「华为」二字
    # （华为概念当初是吸附进来的）。
    # 回写后 name_override 就成为"规则兜底不到的那批"的精确补丁，与 code 表互为备份。
    # 顺序：自动提升在前，人工 TRACK_NAME 在后 —— 人工优先。
    auto_override = {}
    for code, c in conf.items():
        if c['src'] == 'cos' and c['track']:
            auto_override[clean[code]['name']] = c['track']

    # ---- 汇总 ----
    tracks = collections.OrderedDict((cat, []) for cat, _ in RULES)
    cross = collections.OrderedDict((k, []) for k in CROSS_RULES)
    unassigned = []
    for code in sorted(clean):
        t = conf[code]['track']
        if t in tracks:
            tracks[t].append(code)
        elif t in cross:
            cross[t].append(code)
        elif t is None:
            unassigned.append(clean[code]['name'])

    tracks = collections.OrderedDict((k, v) for k, v in tracks.items() if v)
    cross = collections.OrderedDict((k, v) for k, v in cross.items() if v)

    src_cnt = collections.Counter(c['src'] for c in conf.values() if c['track'])
    doc = {
        'schema': 1,
        'generated_at': datetime.date.today().isoformat(),
        'source': 'Eastmoney concept boards x local board_member_cache',
        'board_count': len(boards),
        'noise_removed': len(noise),
        'tracks': tracks,
        'cross': cross,
        'assigned': {c: [v['track'], v['src'], v.get('conf')] for c, v in conf.items() if v['track']},
        'rules': [[cat, kws] for cat, kws in RULES],
        'cross_rules': CROSS_RULES,
        'name_override': {**auto_override, **TRACK_NAME},
        'unassigned': sorted(unassigned),
        'stats': {
            'clean': len(clean),
            'by_source': dict(src_cnt),
            'track_total': sum(len(v) for v in tracks.values()),
            'cross_total': sum(len(v) for v in cross.values()),
            'unassigned': len(unassigned),
        },
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with io.open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=False)

    st = doc['stats']
    print('概念板块 %d → 剔伪板块 %d → 有效 %d' % (len(boards), len(noise), st['clean']))
    print('归属来源: ' + '  '.join('%s=%d' % kv for kv in sorted(st['by_source'].items())))
    print('纵向赛道 %d 个概念 / 横切属性 %d 个 / 无归属 %d' % (
        st['track_total'], st['cross_total'], st['unassigned']))
    print()
    for k, v in tracks.items():
        print('  [赛道] %-16s %3d' % (k, len(v)))
    for k, v in cross.items():
        print('  [横切] %-16s %3d' % (k, len(v)))
    if unassigned:
        print('\n无归属: ' + '、'.join(sorted(unassigned)))
    print('\n写入 %s (%.1f KB)' % (args.out, os.path.getsize(args.out) / 1024))


if __name__ == '__main__':
    main()
