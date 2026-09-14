import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Divider,
  Empty,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ClearOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FireOutlined,
  HistoryOutlined,
  LineChartOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import ReactECharts from 'echarts-for-react'
import MarkdownView from '../components/MarkdownView'
import { useDraft } from '../utils/draft'
import {
  deleteDailyReview,
  fetchDailySnapshot,
  getDailyReviewByDate,
  getDailyTrend,
  getRebuildStatus,
  getStockKline,
  listDailyReviews,
  runDailyAi,
  saveDailyReview,
  startDailyRebuild,
} from '../api'

const { Text, Title, Paragraph } = Typography

// 涨=红 / 跌=绿（A股习惯）
const RED = '#d9363e'
const GREEN = '#3fa14a'
const pctColor = (v) => (v == null ? '#8c8c8c' : v > 0 ? RED : v < 0 ? GREEN : '#8c8c8c')
const fmtPct = (v) => (v == null ? '-' : `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`)
const fmtYi = (v) => {
  const n = Number(v || 0)
  if (!n) return '-'
  return n >= 1e8 ? `${(n / 1e8).toFixed(2)}亿` : `${(n / 1e4).toFixed(0)}万`
}
const fmtCount = (v) => (v == null || v === '' ? '-' : v)

// 指数点位/涨跌幅的数字字体栈：优先等宽数字字形（DIN/等宽），
// 叠加 fontVariantNumeric:'tabular-nums' 让各位数字等宽，数值变化时不左右抖动。
const NUM_FONT =
  '"DIN Alternate", "Helvetica Neue", "SF Pro Display", -apple-system, "Segoe UI", ' +
  'Arial, "PingFang SC", "Microsoft YaHei", sans-serif'

// 「指数表现」与「情绪指标」两卡共用的三层文字规格 —— 抽成常量让两卡字体/字号结构性一致，
// 以后要调只改这里（L1 标题层 / L2 主数值层 / L3 涨跌幅·单位层）
const TXT_L1 = { fontSize: 14, fontWeight: 500 }
const TXT_L2 = {
  fontFamily: NUM_FONT,
  fontSize: 24,
  fontWeight: 700,
  fontVariantNumeric: 'tabular-nums',
  letterSpacing: 0.3,
}
const TXT_L3 = {
  fontFamily: NUM_FONT,
  fontSize: 16,
  fontWeight: 700,
  fontVariantNumeric: 'tabular-nums',
}
const TXT_L3_DIM = { ...TXT_L3, color: '#8c8c8c' }

// ---------------------------------------------------------------------------
// 可点击的股票名称（V1.009.4）
// 点一下弹出该股日线图（含 EMA20）。样式统一为「蓝色 + 虚线下划线」，
// 让它和纯文本区分开 —— 否则用户不知道哪些名字能点。
// 通过 onOpen 回调显式传入，不做 Context 透传：需要接入的组件只有 5 个，
// 显式传参更好定位（哪张表点了会弹，看 JSX 就知道）。
// ---------------------------------------------------------------------------
function StockName({ code, name, onOpen, strong, fontSize = 14 }) {
  const label = name || code || '-'
  if (!code || !onOpen) {
    return (
      <Text strong={strong} style={{ fontSize }}>
        {label}
      </Text>
    )
  }
  return (
    <a
      onClick={(ev) => {
        ev.preventDefault()
        ev.stopPropagation()
        onOpen(code, name)
      }}
      title={`查看 ${label} 的日线图（含 EMA20）`}
      style={{
        fontSize,
        fontWeight: strong ? 600 : 400,
        color: '#1677ff',
        textDecoration: 'none',
        borderBottom: '1px dashed rgba(22,119,255,.5)',
        cursor: 'pointer',
      }}
    >
      {label}
    </a>
  )
}

// ---------------------------------------------------------------------------
// 指标口径备注（V1.009.2）
// 每个情绪/结构类指标旁的「?」悬停显示：定义 → 公式与口径 → 数据源。
// 文案与后端实现严格对应（backend/app/services/daily_market.py），改口径时必须同步改这里，
// 否则前端会给出错误的解释 —— 比没有备注更糟。
// ---------------------------------------------------------------------------

/** 涨跌家数分档的通用口径（各档共享） */
const BIN_BASE = [
  '涨跌幅 = 当日收盘 ÷ 前收 − 1；前收取交易所「除权参考价」',
  '涨停与跌停不计入任何档位，单独列在首尾两行',
  '无涨跌幅（停牌 / 未上市 / 数据缺失）不计入任何档位，也不计入总家数',
]

const BIN_TIP = (label, rule) => ({
  t: `${label} 家数`,
  d: `当日涨跌幅落在该区间的个股家数：${rule}`,
  f: [...BIN_BASE, '分档边界口径对齐通达信 880005'],
  s: '最近交易日为实时全市场快照；历史日由个股日K聚合',
})

const TIP = {
  limit_up: {
    t: '涨停家数（已剔除 ST/*ST）',
    d: '收盘价等于涨停价的个股家数，已剔除 ST、*ST —— 与东财「涨停板行情」页的涨跌停对比一致。',
    f: [
      '涨停价 = 涨跌停基数 × (1 + 涨跌幅限制)，取整到分',
      '基数取交易所「除权参考价」，不是前复权前收（除权日两者不同）',
      '涨跌幅限制：主板 10%、创业板/科创板 20%、北交所 30%',
      '主板 ST/*ST 常态按 5%，但摘帽 / 复牌等特殊日按 10% 执行 —— 由当日价格反推实际档位',
      '取整：沪深四舍五入（允许略越界）；北交所不越界（涨停向下、跌停向上）',
      '东财涨停池本身不收录 ST，故实时与历史两条路径都按「剔 ST」输出，口径一致',
    ],
    s: '最近交易日取东财涨停池（该池不收录 ST/*ST）；历史日由全市场约 5900 只个股的日K聚合，并按同一口径剔除 ST',
  },
  limit_down: {
    t: '跌停家数（已剔除 ST/*ST）',
    d: '收盘价等于跌停价的个股家数，已剔除 ST、*ST。',
    f: [
      '跌停价 = 涨跌停基数 × (1 − 涨跌幅限制)，取整到分',
      '北交所跌停向上取整（与涨停同向不越界）',
      '基数同涨停：交易所除权参考价',
      '东财跌停池不收录 ST，实时与历史两条路径都按「剔 ST」输出',
    ],
    s: '最近交易日取东财跌停池（不收录 ST/*ST）；历史日由个股日K聚合后按同一口径剔除 ST',
  },
  max_lbc: {
    t: '最高连板',
    d: '当日涨停股中连板数的最大值。1 板即首板。',
    f: [
      '连板数 = 从当日往前逐日回溯连续涨停天数，遇到非涨停日即断',
      '涨停判定与「涨停家数」完全同一口径（除权参考价 + 板块限制 + 取整规则）',
    ],
    s: '历史日为日K聚合；最近交易日取东财涨停池自带连板数',
  },
  broken: {
    t: '炸板家数（已剔除 ST/*ST）',
    d: '当日曾触及涨停价、但收盘未封住涨停的个股家数，已剔除 ST、*ST。',
    f: [
      '历史日口径（日K）：当日最高价 ≥ 涨停价 且 收盘价 < 涨停价',
      '最近交易日口径（东财炸板池）：盘中真的封过板、收盘未封住；不收录 ST',
      '两口径实测差异：日K无分时数据，无法区分「封后开板」与「瞬时摸板」，必然略多计',
      '两条路径均已按「剔 ST」输出，口径一致；含 ST 的家数不在本系统任何指标里出现',
    ],
    s: '最近约 15 个交易日走东财炸板池（不收录 ST/*ST）；更早由个股日K聚合后按同一口径剔除 ST',
  },
  broken_rate: {
    t: '炸板率',
    d: '炸板家数 ÷ (涨停家数 + 炸板家数) × 100%',
    f: [
      '含义：当日尝试封板的个股中最终没封住的比例 —— 越高说明封板意愿越弱、情绪越差',
      '两种炸板口径的差异因分子分母同向偏移被摊薄，实测约 +3 个百分点，小于家数差的 +15%/+26%',
    ],
    s: '随涨停 / 炸板口径自动切换，分子分母同源',
  },
  broken_amount_rate: {
    t: '炸板金额率',
    d: '炸板股成交额 ÷ (涨停股成交额 + 炸板股成交额) × 100%',
    f: [
      '与「炸板率」互补，用于看炸板发生在什么体量的个股上',
      '金额率明显高于家数率 → 大成交额个股更容易炸板；反之 → 炸的多是小票',
    ],
    s: '成交额来自个股日K（优先同花顺精确成交额）',
  },
  amt_delta: {
    t: '成交额较昨日',
    d: '(今日两市成交额 − 昨日两市成交额) ÷ 昨日 × 100%',
    f: [
      '为保证可比，两侧尽量用同一口径：都用个股日K聚合',
      '最近交易日若日K覆盖不足当日实时值的 70%，则改用实时快照的当日值（避免半日数据失真）',
    ],
    s: '个股日K聚合 / 实时快照',
  },
  amt_today: {
    t: '两市成交额',
    d: '当日全部A股个股成交额之和（不含指数、债券、期货）。',
    f: [
      '同花顺源为交易所精确成交额；腾讯源为 成交量 × 成交均价 换算',
      '历史日与「较昨日」取自同一聚合口径',
    ],
    s: '个股日K成交额聚合',
  },
  prev_zt_perf: {
    t: '昨日涨停股今日平均表现',
    d: '上一个交易日的涨停股，在当日的涨跌幅算术平均值。主口径已剔除 ST、*ST。',
    f: [
      '衡量「接力效应」：正值说明昨日封板资金今日仍能获利，情绪延续；负值说明炸板亏钱、情绪转弱',
      '取不到当日价格的（停牌等）不参与均值，括号内为有效家数',
      '昨日涨停名单与上方「涨停」严格同源：实时池与历史日K重建两条路径都按「剔 ST」输出，因此括号内的只数必然等于前一交易日的涨停家数',
      '「再涨停」按同一口径统计，即当日剔 ST 后仍封涨停的家数',
    ],
    s: '历史日由日K聚合回溯并按同一口径剔除 ST；最近交易日优先用实时全市场快照',
  },
  up_count: {
    t: '上涨家数',
    d: '四档上涨区间家数之和（0~3%、3~5%、5~7%、>7%）。',
    f: [...BIN_BASE, '不含涨停股（涨停单独列出）'],
    s: '同「涨跌家数分布」',
  },
  down_count: {
    t: '下跌家数',
    d: '四档下跌区间家数之和（0~-3%、-3~-5%、-5~-7%、<-7%）。',
    f: [...BIN_BASE, '不含跌停股（跌停单独列出）'],
    s: '同「涨跌家数分布」',
  },
  flat: {
    t: '平盘',
    d: '当日涨跌幅恰为 0 的个股家数。',
    f: [
      '停牌 / 停复牌当日无涨跌幅的个股已被剔除，不计入平盘，也不计入总家数',
      '（因此「平盘」不等于「停牌」，停牌股不在本表任何统计口径内）',
    ],
    s: '同「涨跌家数分布」',
  },
  breadth_total: {
    t: '总品种数',
    d: '上涨家数 + 下跌家数 + 平盘家数（含 ST、含北交所）。',
    f: [
      '不含停牌 / 无行情个股，故可能略小于上市总数',
      '历史日重建时覆盖约 5900 只，与全市场清单的差额主要为停牌无成交个股',
    ],
    s: '实时快照 / 个股日K聚合',
  },
  concept_temp: {
    t: '题材情绪温度（0~100）',
    d: '50 + 板块涨幅中位数 × 8 + (强势板块数 − 弱势板块数) ÷ 有效板块数 × 45，结果截断到 0~100。',
    f: [
      '强势板块 = 当日涨幅 > 2% 的概念板块；弱势板块 = 涨幅 < −2%',
      '50 为中性；中位数项反映整体中枢高度，强弱占比项反映分化程度',
      '板块涨跌幅 = 成分股等权平均（非市值加权）',
    ],
    s: '东财概念板块 + 成分股映射',
  },
  // 涨跌家数分布：8 个分档（边界与通达信 880005 对齐）
  bin_up_gt7: BIN_TIP('涨幅 > 7%', '涨跌幅 > 7%'),
  bin_up_5_7: BIN_TIP('涨幅 5~7%', '5% < 涨跌幅 ≤ 7%'),
  bin_up_3_5: BIN_TIP('涨幅 3~5%', '3% < 涨跌幅 ≤ 5%'),
  bin_up_0_3: BIN_TIP('涨幅 0~3%', '0% < 涨跌幅 ≤ 3%'),
  bin_down_0_3: BIN_TIP('跌幅 0~3%', '−3% ≤ 涨跌幅 < 0%'),
  bin_down_3_5: BIN_TIP('跌幅 3~5%', '−5% ≤ 涨跌幅 < −3%'),
  bin_down_5_7: BIN_TIP('跌幅 5~7%', '−7% ≤ 涨跌幅 < −5%'),
  bin_down_gt7: BIN_TIP('跌幅 > 7%', '涨跌幅 < −7%'),
  // 概念板块
  concept_updown: {
    t: '上涨 / 下跌板块',
    d: '当日涨跌幅 > 0 的概念板块数 / < 0 的板块数。',
    f: ['板块涨跌幅 = 成分股等权平均（非市值加权）', '涨跌幅恰为 0 的板块两者都不计'],
    s: '东财概念板块 + 成分股映射',
  },
  concept_strong_weak: {
    t: '强势 / 弱势板块',
    d: '当日涨幅 > 2% 的板块数 / 涨幅 < −2% 的板块数。',
    f: [
      '这两个数正是「题材情绪温度」公式里的得分项：强弱差 ÷ 板块总数 × 45',
      '用于看行情是普遍走强还是只有个别板块扛指数',
    ],
    s: '东财概念板块',
  },
  concept_median: {
    t: '板块涨幅中位数',
    d: '全部概念板块当日涨跌幅的中位数（不是平均值）。',
    f: [
      '取中位数可避免少数暴涨板块把整体中枢拉高',
      '在「题材情绪温度」公式中权重为 × 8',
    ],
    s: '东财概念板块',
  },
  concept_pct: {
    t: '板块涨跌幅',
    d: '板块内全部成分股当日涨跌幅的等权平均。',
    f: [
      '等权 → 小市值个股与权重股同等影响，更贴近题材炒作热度而不是指数涨跌',
      '最近交易日为实时板块行情；历史日由成分股日K聚合（同一算法）',
    ],
    s: '东财板块 + 成分股映射',
  },
  concept_zt: {
    t: '涨停家数',
    d: '该概念板块当日涨停的成分股家数。',
    f: [
      '涨停判定与全局同一口径（除权参考价 + 板块涨跌幅限制 + 取整规则）',
      '实时口径由「涨停股 → 所属概念」映射统计；历史重建用板块行自带的涨停家数',
    ],
    s: '涨停股归属概念 / 板块行',
  },
  concept_ratio: {
    t: '占板块比',
    d: '板块涨停家数 ÷ 板块成分股数 × 100%。',
    f: [
      '衡量资金聚焦度：占比高说明是板块整体性上涨，占比低说明只有个别龙头在涨',
      '成分股数取该板块有行情的成分股数量',
    ],
    s: '东财板块成分股',
  },
  concept_stage: {
    t: '周期阶段',
    d: '「主线题材与周期阶段」表中所属阶段。有近 N 日序列时按序列判定，否则退化为「当日强度」口径。',
    f: [
      '有序列（近 N 日板块涨跌幅与涨停家数）：高潮 = 今日涨停 ≥3 家且为区间峰值；退潮 = 前 1~2 日曾 ≥3 家涨停后今日转跌；发酵 = 连续有涨停；启动 = 近 3 日首次出现涨停；冰点 = 近 N 日无涨停且累计跌幅 ≤ −3%；其余为震荡',
      '无序列（当日强度）：高潮 = 涨停 ≥5 家且板块 >2%；发酵 = 涨停 ≥3 家且板块 >0%；分歧 = 有涨停但板块 ≤0%；活跃 = 涨停 ≥1 家且板块 >0%；补涨 = 无涨停但板块 >1.5%；退潮 = 无涨停且板块 <−2%；其余为平淡',
      '卡片右上角标签会标明用的是哪种判定口径',
    ],
    s: '板块近 N 日序列（hist）+ 当日板块数据',
  },
  concept_roles: {
    t: '板块角色分层（龙头 / 中军 / 跟风 / 补涨）',
    d: '把主线题材的成分股按资金角色分成四类，展开板块行即可看到。四类互斥（一只票只归一类），判定顺序为 龙头 → 中军 → 跟风 → 补涨，且全部剔除 ST/*ST。',
    f: [
      '龙头：板块内涨停股中「连板数 → 当日涨幅 → 成交额」最高者；板块当日无涨停则不输出（龙头未确立）',
      '中军：板块内市值 ≥ 中位数、当日收涨且未涨停的个股里成交额最大者（至多 2 只）。涨停股归龙头/跟风，中军的价值在于「体量大、资金重、涨幅温和」',
      '跟风：其余个股中「涨停或当日涨幅 ≥ 3%」者，按涨幅降序取前 5',
      '补涨：其余个股中「当日涨幅 ≥ 2% 且近 5 日累计涨幅 ≤ 板块中位 − 5pct」者（低位滞涨 + 今日启动），按近 5 日累计涨幅升序取前 5',
      '连板数由日K逐日回溯（口径同涨跌停判定）；近 5 日累计涨幅 = 当日收盘 ÷ 5 个交易日前收盘 − 1',
    ],
    s: '板块成分股（东财，含市值）+ 个股日K（涨跌幅 / 成交额 / 连板 / 近5日涨幅）',
  },
  trend_first_board: {
    t: '首板 / 连板家数',
    d: '把当日涨停股按连板数分档：首板 = 连板数等于 1 的个股数，连板 = 连板数 ≥ 2 的个股数。两者之和即涨停家数。',
    f: [
      '连板数由日K逐日回溯（遇到非涨停日即断），口径与「涨停家数」完全一致',
      '首板代表新增做多力量（新资金进场），连板代表存量赚钱效应的延续',
      '首板占比高 = 情绪刚启动或轮动补涨；连板家数快速萎缩 = 接力意愿转弱，情绪退潮的先行信号',
      '首板 + 连板 = 涨停家数，可与当日看板的涨停家数互相验证',
    ],
    s: '最近交易日取东财涨停池自带的连板数；历史日由全市场个股日K聚合',
  },
  trend_up_ratio: {
    t: '上涨家数占比',
    d: '(上涨家数 + 涨停家数) ÷ (上涨家数 + 下跌家数 + 平盘家数 + 涨停家数 + 跌停家数) × 100%',
    f: [
      '即全部有行情品种中红盘个股的比例，50% 为多空平衡线',
      '「涨跌家数分布」表里的上涨/下跌家数是四档区间家数之和，本身不含涨跌停（涨跌停与平盘单列）；本指标把涨停并入分子、把涨跌停与平盘一并并入分母，因此与分布表可对上账',
      '用于区分「普涨」与「结构行情」：占比高但涨停少 = 普涨但无主线；占比低但涨停多 = 极度分化、只有少数票在涨',
    ],
    s: '同「涨跌家数分布」：最近交易日用实时全市场快照，历史日由全市场个股日K聚合',
  },
  trend_mini: {
    t: '近期情绪趋势（近 N 个交易日）',
    d: '把当日看板上的核心情绪指标拉成时间序列，用来观察「变化的方向与斜率」，而不是只看单日绝对值。',
    f: [
      '每张小图为该指标最近 N 个交易日的走势；右上角为最新值，以及它相对前一个「有数据交易日」的变化',
      '升降标记按指标语义着色：涨停、首板、连板、昨涨停股表现、上涨占比「升高为红（情绪转好）」；跌停、炸板、炸板率「升高为绿（情绪转差）」',
      '折线在中间断开表示该交易日取不到数据（例如尚未做历史数据重建），不是数值为 0',
      '数据点随所选日期变化：以页面当前所选交易日为终点向前取 N 个交易日',
      '涨停 / 跌停 / 炸板三个家数，以及「首板 / 连板 / 昨涨停股表现」的样本名单，均已剔除 ST、*ST，与当日看板、东财「涨停板行情」页口径一致',
    ],
    s: '本地每日快照缓存（历史重建写入，可覆盖约 60 个交易日）；缓存缺失且在回溯窗口内时用东财实时池回补',
  },
  trend_concept_zt: {
    t: '主线题材涨停家数',
    d: '当日主线题材表里「有历史序列」的前 3 条题材，各自最近 N 个交易日的涨停成分股家数。',
    f: [
      '口径与全局「涨停家数」完全一致（除权参考价 + 板块涨跌幅限制 + 取整规则）',
      '数据来自当日快照的 concept.main_lines[].seq（固定约 10 日窗口），按卡片天数取末尾 N 个点，不额外联网',
      '看的是题材的接力节奏：曲线持续上行 = 主线在扩散、资金在同一条线上加注；曲线见顶回落而新题材抬头 = 主线切换',
      '与上面「涨停家数」小图配合看：总涨停家数不变但主线曲线集体走弱，说明是散乱普涨而非主线行情',
    ],
    s: '涨停股归属概念 / 板块近 10 日序列（最近交易日走实时，历史日由板块行重建数据）',
  },
}

/** 指标口径备注图标：悬停显示该字段的计算方法 */
function Hint({ k }) {
  const x = TIP[k]
  if (!x) return null
  return (
    <Tooltip
      placement="top"
      overlayStyle={{ maxWidth: 400 }}
      title={
        <div style={{ fontSize: 12, lineHeight: 1.75 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{x.t}</div>
          {x.d ? <div>{x.d}</div> : null}
          {x.f?.length ? (
            <ul style={{ margin: '6px 0 0', paddingLeft: 16 }}>
              {x.f.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          ) : null}
          {x.s ? <div style={{ marginTop: 6, opacity: 0.7 }}>数据源：{x.s}</div> : null}
        </div>
      }
    >
      <QuestionCircleOutlined
        style={{
          fontSize: 12,
          color: '#bfbfbf',
          marginLeft: 4,
          cursor: 'help',
          verticalAlign: 'text-bottom',
        }}
      />
    </Tooltip>
  )
}

/** 涨跌家数分布（参考通达信 880005 的横向条形样式） */
function BreadthBoard({ snap }) {
  const b = snap?.breadth || {}
  const e = snap?.emotion || {}
  const rows = [
    { label: '其中 涨停', value: e.limit_up || 0, color: RED, strong: true, tip: 'limit_up' },
    { label: '涨幅 > 7%', value: b.up_gt7 || 0, color: RED, tip: 'bin_up_gt7' },
    { label: '涨幅 5-7%', value: b.up_5_7 || 0, color: RED, tip: 'bin_up_5_7' },
    { label: '涨幅 3-5%', value: b.up_3_5 || 0, color: RED, tip: 'bin_up_3_5' },
    { label: '涨幅 0-3%', value: b.up_0_3 || 0, color: RED, tip: 'bin_up_0_3' },
    { label: '跌幅 0-3%', value: b.down_0_3 || 0, color: GREEN, tip: 'bin_down_0_3' },
    { label: '跌幅 3-5%', value: b.down_3_5 || 0, color: GREEN, tip: 'bin_down_3_5' },
    { label: '跌幅 5-7%', value: b.down_5_7 || 0, color: GREEN, tip: 'bin_down_5_7' },
    { label: '跌幅 > 7%', value: b.down_gt7 || 0, color: GREEN, tip: 'bin_down_gt7' },
    { label: '其中 跌停', value: e.limit_down || 0, color: GREEN, strong: true, tip: 'limit_down' },
  ]
  const max = Math.max(1, ...rows.map((r) => r.value))
  if (!b.total) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space direction="vertical" size={2}>
            <Text type="secondary">该日无涨跌家数分布</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              该日为非交易日（周末 / 休市），或尚未执行「历史数据重建」。
              <br />
              执行重建后，历史交易日同样会有完整的涨跌分布。
            </Text>
          </Space>
        }
      />
    )
  }
  return (
    <div>
      {rows.map((r) => (
        <div key={r.label} style={{ display: 'flex', alignItems: 'center', marginBottom: 3 }}>
          <div style={{ width: 92, fontSize: 12, color: r.strong ? '#262626' : '#595959', fontWeight: r.strong ? 600 : 400 }}>
            {r.label}
            {r.tip ? <Hint k={r.tip} /> : null}
          </div>
          <div style={{ flex: 1, background: '#f5f5f5', height: 15, borderRadius: 2, position: 'relative' }}>
            <div
              style={{
                width: `${Math.max(r.value > 0 ? 1.5 : 0, (r.value / max) * 100)}%`,
                height: '100%',
                background: r.color,
                borderRadius: 2,
                transition: 'width .4s',
              }}
            />
          </div>
          <div style={{ width: 68, textAlign: 'right', fontSize: 13, color: r.color, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
            {r.value}
          </div>
        </div>
      ))}
      <Divider style={{ margin: '10px 0 8px' }} />
      <Row gutter={16}>
        <Col span={8}>
          <Text style={{ fontSize: 12, color: '#8c8c8c' }}>
            上涨家数
            <Hint k="up_count" />
          </Text>
          <div style={{ fontSize: 18, color: RED, fontWeight: 700 }}>{b.up_count || 0}</div>
        </Col>
        <Col span={8}>
          <Text style={{ fontSize: 12, color: '#8c8c8c' }}>
            下跌家数
            <Hint k="down_count" />
          </Text>
          <div style={{ fontSize: 18, color: GREEN, fontWeight: 700 }}>{b.down_count || 0}</div>
        </Col>
        <Col span={8}>
          <Text style={{ fontSize: 12, color: '#8c8c8c' }}>
            平盘（0%）
            <Hint k="flat" />
          </Text>
          <div style={{ fontSize: 18, color: '#595959', fontWeight: 700 }}>{b.flat || 0}</div>
        </Col>
        <Col span={8} style={{ marginTop: 8 }}>
          <Text style={{ fontSize: 12, color: '#8c8c8c' }}>
            总品种数
            <Hint k="breadth_total" />
          </Text>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{b.total || 0}</div>
        </Col>
        <Col span={16} style={{ marginTop: 8 }}>
          <Text style={{ fontSize: 12, color: '#8c8c8c' }}>
            两市总成交额（较昨日）
            <Hint k="amt_today" />
          </Text>
          <div style={{ fontSize: 16, fontWeight: 600 }}>
            {fmtYi(b.total_amount)}
            {snap.amount_chg?.available ? (
              <Text
                style={{
                  fontSize: 14,
                  fontWeight: 700,
                  marginLeft: 8,
                  color: snap.amount_chg.delta_pct >= 0 ? RED : GREEN,
                }}
              >
                {snap.amount_chg.delta_pct >= 0 ? '+' : ''}
                {snap.amount_chg.delta_pct}%
              </Text>
            ) : null}
          </div>
          {snap.amount_chg?.available ? (
            <Text type="secondary" style={{ fontSize: 11 }}>
              昨日 {fmtYi(snap.amount_chg.prev)}
              {snap.amount_chg.delta_pct >= 0 ? '　放量' : '　缩量'}
            </Text>
          ) : null}
        </Col>
      </Row>
    </div>
  )
}

// ---------- 板块角色分层（龙头 / 中军 / 跟风 / 补涨）----------
const ROLE_META = [
  { key: 'leader', label: '龙头', color: RED },
  { key: 'main_force', label: '中军', color: '#722ed1' },
  { key: 'follower', label: '跟风', color: '#fa8c16' },
  { key: 'catchup', label: '补涨', color: '#13a8a8' },
]

/** 单只角色股：标签 + 悬停显示判定依据 */
function RoleStock({ x, color, onOpenStock }) {
  return (
    <Tooltip title={<div style={{ fontSize: 12, lineHeight: 1.7 }}>{x.reason || '—'}</div>}>
      <Tag color={color} style={{ fontSize: 11, marginInlineEnd: 0, cursor: 'help' }}>
        {onOpenStock ? (
          <a
            onClick={(ev) => {
              ev.preventDefault()
              ev.stopPropagation()
              onOpenStock(x.code, x.name)
            }}
            title={`查看 ${x.name} 的日线图（含 EMA20）`}
            style={{
              color: '#fff',
              textDecoration: 'none',
              borderBottom: '1px dashed rgba(255,255,255,.65)',
            }}
          >
            {x.name}
          </a>
        ) : (
          x.name
        )}
        <span style={{ marginLeft: 4, fontVariantNumeric: 'tabular-nums' }}>
          {x.pct > 0 ? '+' : ''}
          {Number(x.pct).toFixed(2)}%
        </span>
        {x.lbc ? <b style={{ marginLeft: 3 }}>{x.lbc}板</b> : null}
      </Tag>
    </Tooltip>
  )
}

/** 板块展开行：四类角色（数据由后端 roles 字段提供，缺则整行不可展开） */
function RolePanel({ roles, onOpenStock }) {
  if (!roles) return null
  if (!ROLE_META.some((m) => (roles[m.key] || []).length)) {
    return (
      <Text type="secondary" style={{ fontSize: 12 }}>
        {roles.note || '该板块当日无角色数据'}
      </Text>
    )
  }
  return (
    <div style={{ padding: '2px 4px' }}>
      {ROLE_META.map((m) => {
        const arr = roles[m.key] || []
        if (!arr.length) return null
        return (
          <div key={m.key} style={{ display: 'flex', alignItems: 'flex-start', marginBottom: 4 }}>
            <span
              style={{
                flex: '0 0 40px',
                fontSize: 12,
                fontWeight: 600,
                color: m.color,
                lineHeight: '22px',
              }}
            >
              {m.label}
            </span>
            <Space size={[4, 4]} wrap style={{ flex: 1 }}>
              {arr.map((x) => (
                <RoleStock key={x.code} x={x} color={m.color} onOpenStock={onOpenStock} />
              ))}
            </Space>
          </div>
        )
      })}
      <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 2 }}>
        板块内有效成分 {roles.pool ?? 0} 只
        {roles.note ? `；${roles.note}` : ''}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 近期情绪趋势（V1.009.3）：把核心情绪指标拉成时间序列，多图横向排列
// 目的：看「情绪的变化方向」，而不是静止地看某一天的数据
// ---------------------------------------------------------------------------
const MINI_TRENDS = [
  { key: 'limit_up', label: '涨停家数', unit: '家', color: '#d9363e', goodWhenUp: true, tip: 'limit_up', dec: 0 },
  { key: 'limit_down', label: '跌停家数', unit: '家', color: '#3fa14a', goodWhenUp: false, tip: 'limit_down', dec: 0 },
  { key: 'broken', label: '炸板家数', unit: '家', color: '#d46b08', goodWhenUp: false, tip: 'broken', dec: 0 },
  { key: 'broken_rate', label: '炸板率', unit: '%', color: '#fa8c16', goodWhenUp: false, tip: 'broken_rate', dec: 2 },
  { key: 'first_board', label: '首板家数', unit: '家', color: '#f5222d', goodWhenUp: true, tip: 'trend_first_board', dec: 0 },
  { key: 'multi_board', label: '连板家数', unit: '家', color: '#d4380d', goodWhenUp: true, tip: 'trend_first_board', dec: 0 },
  { key: 'prev_lu_avg', label: '昨涨停股今表现', unit: '%', color: '#fa541c', goodWhenUp: true, tip: 'prev_zt_perf', dec: 2, refLine: 0 },
  { key: 'up_ratio', label: '上涨家数占比', unit: '%', color: '#eb2f96', goodWhenUp: true, tip: 'trend_up_ratio', dec: 2, refLine: 50 },
]

/**
 * 主线题材涨停家数小图（V1.009.3）：与上面 8 个情绪指标共用同一张网格。
 * 数据来自当日快照的 concept.main_lines[].seq（字段 date / zt），与趋势接口无关，
 * 因此不随 miniDays 重新请求，只按天数截取 seq 末尾 —— 与卡片标题的「近 N 日」对齐。
 */
const CONCEPT_MINI_COLORS = ['#d9363e', '#1677ff', '#722ed1']
const conceptMiniMetric = (name, i) => ({
  key: 'zt',
  label: `${name} 涨停`,
  unit: '家',
  color: CONCEPT_MINI_COLORS[i % CONCEPT_MINI_COLORS.length],
  goodWhenUp: true,
  tip: 'trend_concept_zt',
  dec: 0,
})

/** #RRGGBB + alpha → rgba()：折线下方的淡面积填充 */
function hexA(hex, a) {
  const n = parseInt(String(hex).replace('#', ''), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
}

const miniFmt = (v, dec = 0) => (v == null || Number.isNaN(v) ? '-' : Number(v).toFixed(dec))

function MiniTrendCell({ metric, items }) {
  const dates = items.map((x) => String(x.date || '').slice(5))
  const vals = items.map((x) => (x[metric.key] == null ? null : Number(x[metric.key])))

  // 最新值 与 上一个「确实有数据」的值 —— 跳过 null，否则会把「取不到」当成 0 参与变化计算
  let lastIdx = -1
  let prevIdx = -1
  for (let i = vals.length - 1; i >= 0; i -= 1) {
    if (vals[i] == null) continue
    if (lastIdx < 0) lastIdx = i
    else {
      prevIdx = i
      break
    }
  }
  const lastVal = lastIdx >= 0 ? vals[lastIdx] : null
  const prevVal = prevIdx >= 0 ? vals[prevIdx] : null
  const delta = lastVal != null && prevVal != null ? lastVal - prevVal : null

  // 升降标记按指标语义着色：变好=红、变差=绿（A股习惯），持平=灰
  const deltaColor =
    delta == null || delta === 0 ? '#8c8c8c' : (delta > 0) === metric.goodWhenUp ? RED : GREEN

  const option = {
    // 迷你图关掉入场动画：父组件每次 re-render 都会重建 option，ECharts 会重播动画，
    // 既造成整片闪烁，也会让截图/首屏抓到"只画了一小截"的中间态
    animation: false,
    grid: { left: 6, right: 8, top: 8, bottom: 4 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      textStyle: { fontSize: 12 },
      formatter: (ps) => {
        const p = ps && ps[0]
        if (!p) return ''
        const v = p.value
        const sv = v == null ? '无数据' : `${miniFmt(v, metric.dec)}${metric.unit}`
        return `${p.axisValue}<br/>${metric.label}：${sv}`
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#f0f0f0' } },
      axisTick: { show: false },
      axisLabel: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitNumber: 2,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: false },
      splitLine: { lineStyle: { color: '#f5f5f5', type: 'dashed' } },
    },
    series: [
      {
        type: 'line',
        data: vals,
        smooth: true,
        connectNulls: false, // 中间断开表示该日取不到数据，不要连成假线
        showSymbol: false,
        symbolSize: 5,
        lineStyle: { width: 2, color: metric.color },
        itemStyle: { color: metric.color },
        areaStyle: { color: hexA(metric.color, 0.12) },
        // 有天然基准的指标画一条参考线，否则看不出正负/多空分界
        // （昨涨停股今表现 0% = 不赚不亏；上涨家数占比 50% = 多空平衡）
        ...(metric.refLine != null
          ? {
              markLine: {
                silent: true,
                symbol: 'none',
                label: { show: false },
                lineStyle: { color: '#d9d9d9', type: 'dashed', width: 1 },
                data: [{ yAxis: metric.refLine }],
              },
            }
          : {}),
      },
    ],
  }

  return (
    <div
      style={{
        border: '1px solid #f0f0f0',
        borderRadius: 8,
        padding: '8px 10px 2px',
        height: '100%',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <Text style={{ fontSize: 13, fontWeight: 500 }}>
          {metric.label}
          <Hint k={metric.tip} />
        </Text>
        <span style={{ whiteSpace: 'nowrap' }}>
          <span
            style={{
              fontSize: 17,
              fontWeight: 700,
              fontVariantNumeric: 'tabular-nums',
              color: metric.color,
            }}
          >
            {miniFmt(lastVal, metric.dec)}
          </span>
          <span style={{ fontSize: 11, color: '#8c8c8c', marginLeft: 2 }}>{metric.unit}</span>
          {delta != null && (
            <span style={{ fontSize: 12, color: deltaColor, marginLeft: 6 }}>
              {delta === 0
                ? '—'
                : `${delta > 0 ? '↑' : '↓'}${miniFmt(Math.abs(delta), metric.dec)}`}
            </span>
          )}
        </span>
      </div>
      <ReactECharts option={option} style={{ height: 84 }} notMerge />
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 11,
          color: '#bfbfbf',
          marginTop: -4,
        }}
      >
        <span>{dates[0] || ''}</span>
        <span>{dates[dates.length - 1] || ''}</span>
      </div>
    </div>
  )
}

/** 近期情绪趋势网格：大屏 4 列 / 中屏 2 列 / 手机 1 列
 *  第二行为主线题材涨停家数（最多 3 条主线），数据来自当日快照而非趋势接口 */
function MiniTrendGrid({ data, loading, mainLines, days = 10 }) {
  const items = data?.items || []
  const lines = (mainLines || [])
    .filter((m) => (m.seq || []).length >= 2)
    .slice(0, 3)
    .map((m) => ({ name: m.name, seq: (m.seq || []).slice(-days) }))
  return (
    <Spin spinning={!!loading}>
      {items.length ? (
        <>
          <Row gutter={[10, 10]}>
            {MINI_TRENDS.map((m) => (
              <Col key={m.key} xs={24} sm={12} lg={6}>
                <MiniTrendCell metric={m} items={items} />
              </Col>
            ))}
          </Row>
          <div style={{ marginTop: 8, fontSize: 12, color: '#8c8c8c' }}>
            <Space size={12} wrap>
              <span>
                共 {items.length} 个交易日（{items[0]?.date} ~ {items[items.length - 1]?.date}）
              </span>
              {data?.filled ? <span>其中 {data.filled} 日由东财实时池回补</span> : null}
              {data?.missing_dates?.length ? (
                <span style={{ color: '#d46b08' }}>
                  {data.missing_dates.length} 个交易日暂无数据（需执行「历史数据重建」）
                </span>
              ) : null}
            </Space>
          </div>
          {lines.length ? (
            <>
              <Divider orientation="left" style={{ margin: '14px 0 8px' }}>
                <Text strong style={{ fontSize: 13 }}>
                  主线题材涨停家数
                </Text>
                <Hint k="trend_concept_zt" />
                <Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 6 }}>
                  近 {lines[0].seq.length} 日（取当日主线题材前 {lines.length} 条）
                </Text>
              </Divider>
              <Row gutter={[10, 10]}>
                {lines.map((m, i) => (
                  <Col key={m.name} xs={24} sm={12} lg={6}>
                    <MiniTrendCell metric={conceptMiniMetric(m.name, i)} items={m.seq} />
                  </Col>
                ))}
              </Row>
            </>
          ) : null}
        </>
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text type="secondary" style={{ fontSize: 12 }}>
              该日期之前还没有足够的历史快照，执行「历史数据重建」后即可查看
            </Text>
          }
        />
      )}
    </Spin>
  )
}

/** 概念板块情绪周期（V1.009.1）：板块全景 + 主线题材周期阶段 + 涨停贡献榜 + 涨跌幅榜 */
function ConceptEmotion({ concept, onOpenStock }) {
  const c = concept || {}
  if (!c.available) {
    return (
      <Alert
        type="info"
        showIcon
        message="概念板块数据不可用"
        description={c.note || '请先抓取盘面数据；历史日期需先执行「历史数据重建」'}
      />
    )
  }
  const temp = c.temperature
  const tempColor =
    temp == null ? '#8c8c8c' : temp >= 65 ? RED : temp >= 45 ? '#d48806' : GREEN
  const stageColor = (s) =>
    ({
      高潮: 'red',
      发酵: 'volcano',
      启动: 'orange',
      活跃: 'gold',
      分歧: 'blue',
      补涨: 'cyan',
      退潮: 'green',
      冰点: 'default',
      震荡: 'default',
      平淡: 'default',
    })[s] || 'default'

  // 主线题材涨停家数走势已移到页面顶部「近期情绪趋势」卡片的第二行（V1.009.3）：
  // 原来是本卡底部一张全宽大图，横向浪费空间，缩成小图后与情绪指标并排更好比较

  return (
    <>
      <Row gutter={[8, 8]} style={{ marginBottom: 8 }}>
        <Col xs={12} sm={6}>
          <Statistic
            title={
              <span>
                题材情绪温度
                <Hint k="concept_temp" />
              </span>
            }
            value={temp ?? '-'}
            suffix="/100"
            valueStyle={{ color: tempColor, fontSize: 20 }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title={
              <span>
                上涨 / 下跌板块
                <Hint k="concept_updown" />
              </span>
            }
            value={`${c.up ?? '-'} / ${c.down ?? '-'}`}
            valueStyle={{ fontSize: 18 }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title={
              <span>
                强势(&gt;2%) / 弱势(&lt;-2%)
                <Hint k="concept_strong_weak" />
              </span>
            }
            value={`${c.strong ?? '-'} / ${c.weak ?? '-'}`}
            valueStyle={{ fontSize: 18 }}
          />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic
            title={
              <span>
                板块涨幅中位数
                <Hint k="concept_median" />
              </span>
            }
            value={fmtPct(c.median_pct)}
            valueStyle={{ color: pctColor(c.median_pct), fontSize: 18 }}
          />
        </Col>
      </Row>

      {c.note && (
        <Alert type="warning" showIcon style={{ marginBottom: 10, fontSize: 12 }} message={c.note} />
      )}

      <Divider orientation="left" style={{ margin: '8px 0' }}>
        <Text strong style={{ fontSize: 13 }}>
          主线题材与周期阶段
        </Text>
        <Hint k="concept_roles" />
        <Tag
          style={{ marginInlineStart: 8 }}
          color={c.source === 'live' ? 'green' : 'blue'}
        >
          {c.source === 'live' ? '实时口径' : '历史重建口径'}
        </Tag>
        <Tag color={c.hist_used ? 'purple' : 'default'}>
          {c.hist_used ? '周期判定：近 N 日序列' : '周期判定：当日强度'}
        </Tag>
      </Divider>
      <Table
        size="small"
        rowKey="code"
        pagination={false}
        dataSource={c.main_lines || []}
        expandable={{
          expandedRowRender: (r) => <RolePanel roles={r.roles} onOpenStock={onOpenStock} />,
          rowExpandable: (r) =>
            !!r.roles &&
            ['leader', 'main_force', 'follower', 'catchup'].some(
              (k) => (r.roles[k] || []).length,
            ),
        }}
        columns={[
          {
            title: '概念板块',
            dataIndex: 'name',
            width: 172,
            render: (v, r) => (
              <Space direction="vertical" size={0}>
                <Space size={4}>
                  <span>{v}</span>
                  {r.max_lbc >= 2 && (
                    <Tag color="red" style={{ marginInlineEnd: 0 }}>
                      {r.max_lbc}板
                    </Tag>
                  )}
                </Space>
                {r.roles?.leader?.[0] ? (
                  <Text style={{ fontSize: 11, color: RED }}>
                    龙头 {r.roles.leader[0].name}
                    {r.roles.leader[0].lbc ? ` ${r.roles.leader[0].lbc}板` : ''}
                  </Text>
                ) : null}
              </Space>
            ),
          },
          {
            title: (
              <span>
                涨跌幅
                <Hint k="concept_pct" />
              </span>
            ),
            dataIndex: 'pct',
            width: 92,
            render: (v) => <span style={{ color: pctColor(v) }}>{fmtPct(v)}</span>,
          },
          {
            title: (
              <span>
                涨停
                <Hint k="concept_zt" />
              </span>
            ),
            dataIndex: 'zt_count',
            width: 66,
          },
          {
            title: (
              <span>
                占板块比
                <Hint k="concept_ratio" />
              </span>
            ),
            dataIndex: 'ratio',
            width: 92,
            render: (v) => (v == null ? '-' : `${v}%`),
          },
          {
            title: (
              <span>
                周期阶段
                <Hint k="concept_stage" />
              </span>
            ),
            dataIndex: 'stage',
            width: 96,
            render: (v) => <Tag color={stageColor(v)}>{v}</Tag>,
          },
          { title: '判定依据', dataIndex: 'reason', ellipsis: true },
        ]}
      />

      {(c.zt_contrib || []).length > 0 && (
        <>
          <Divider orientation="left" style={{ margin: '12px 0 8px' }}>
            <Text strong style={{ fontSize: 13 }}>
              涨停贡献榜（题材热度）
            </Text>
          </Divider>
          <Space size={[6, 6]} wrap>
            {(c.zt_contrib || []).slice(0, 14).map((b, i) => (
              <Tooltip
                key={b.code}
                title={
                  (b.codes || []).length
                    ? (b.codes || []).map((x) => x.name).join('、')
                    : `${b.count} 只涨停`
                }
              >
                <Tag color={i < 3 ? 'red' : i < 7 ? 'volcano' : 'default'}>
                  {b.name} {b.count}只
                </Tag>
              </Tooltip>
            ))}
          </Space>
        </>
      )}

      <Row gutter={12} style={{ marginTop: 12 }}>
        <Col xs={24} md={12}>
          <Text strong style={{ fontSize: 13 }}>
            概念涨幅榜 TOP10
          </Text>
          <Table
            size="small"
            rowKey="code"
            pagination={false}
            style={{ marginTop: 6 }}
            dataSource={(c.top_up || []).slice(0, 10)}
            columns={[
              { title: '概念', dataIndex: 'name', ellipsis: true },
              {
                title: '涨跌幅',
                dataIndex: 'pct',
                width: 76,
                render: (v) => <span style={{ color: pctColor(v) }}>{fmtPct(v)}</span>,
              },
              { title: '领涨股', dataIndex: 'lead_name', width: 92, ellipsis: true },
            ]}
          />
        </Col>
        <Col xs={24} md={12}>
          <Text strong style={{ fontSize: 13 }}>
            概念跌幅榜 TOP10
          </Text>
          <Table
            size="small"
            rowKey="code"
            pagination={false}
            style={{ marginTop: 6 }}
            dataSource={(c.top_down || []).slice(0, 10)}
            columns={[
              { title: '概念', dataIndex: 'name', ellipsis: true },
              {
                title: '涨跌幅',
                dataIndex: 'pct',
                width: 76,
                render: (v) => <span style={{ color: pctColor(v) }}>{fmtPct(v)}</span>,
              },
              { title: '领跌股', dataIndex: 'lag_name', width: 92, ellipsis: true },
            ]}
          />
        </Col>
      </Row>

    </>
  )
}

/** 严重异动提醒（V1.009.2）：偏离值口径的异常波动 / 严重异常波动 / 连板风险 */
function AbnormalWatch({ abnormal, onOpenStock }) {
  const a = abnormal || {}
  if (!a.available) {
    // ok=True 表示检测已成功执行、只是当日没有满足条件的标的 —— 与「数据缺失」必须区分开
    const done = !!a.ok
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {done ? '检测已执行，当日无满足阈值或龙虎榜异动条件的标的' : (a.note || '异动数据不可用')}
          </Text>
        }
      />
    )
  }
  const levelColor = (lv) =>
    lv === '严重异动'
      ? '#d9363e'
      : String(lv || '').startsWith('异常波动')
        ? '#fa8c16'
        : lv === '龙虎榜异动'
          ? '#1677ff'
          : '#722ed1'

  const columns = [
    {
      title: '个股',
      dataIndex: 'name',
      width: 132,
      render: (v, r) => (
        <Space size={4}>
          <StockName code={r.code} name={v} onOpen={onOpenStock} strong fontSize={12} />
          <Text type="secondary" style={{ fontSize: 11 }}>{r.code}</Text>
        </Space>
      ),
    },
    {
      title: '当日',
      dataIndex: 'pct',
      width: 66,
      render: (v) => <Text style={{ color: pctColor(v), fontSize: 12 }}>{fmtPct(v)}</Text>,
    },
    {
      title: '连板',
      dataIndex: 'lbc',
      width: 56,
      render: (v) => (v ? <Text style={{ fontSize: 12, color: '#722ed1' }}>{v}板</Text> : '-'),
    },
    {
      title: '级别',
      dataIndex: 'level',
      width: 92,
      render: (v) => (
        <Tag color={levelColor(v)} style={{ fontSize: 11, marginInlineEnd: 0 }}>{v}</Tag>
      ),
    },
    {
      title: '判定依据',
      dataIndex: 'reasons',
      render: (v, r) => (
        <Tooltip
          title={
            <div style={{ fontSize: 12 }}>
              {[...(v || []), `基准：${r.base}`, `3日偏离 ${r.dev3 ?? '-'} / 10日 ${r.dev10 ?? '-'} / 30日 ${r.dev30 ?? '-'}`].map(
                (t, i) => <div key={i}>{t}</div>,
              )}
            </div>
          }
        >
          <Text style={{ fontSize: 12, color: '#595959', cursor: 'help' }}>
            {(v || []).join('；')}
          </Text>
        </Tooltip>
      ),
    },
  ]

  return (
    <>
      <Space size={[6, 6]} style={{ marginBottom: 8 }}>
        {(Object.entries(a.counts || {}) || []).map(([k, v]) => (
          <Tag key={k} color={levelColor(k)}>{k} {v}</Tag>
        ))}
        <Text type="secondary" style={{ fontSize: 12 }}>共 {a.total || 0} 只</Text>
      </Space>
      <Table
        size="small"
        rowKey="code"
        columns={columns}
        dataSource={a.items || []}
        pagination={{ pageSize: 10, size: 'small', showSizeChanger: false }}
      />
      {a.note ? (
        <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 6 }}>{a.note}</Text>
      ) : null}
    </>
  )
}

/** 游资动向（V1.009.2）：龙虎榜席位聚合 + 知名游资识别 */
function YouziFlow({ youzi, onOpenStock }) {
  const y = youzi || {}
  if (!y.available) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={<Text type="secondary" style={{ fontSize: 12 }}>{y.note || '无游资数据'}</Text>}
      />
    )
  }
  const stockTags = (stocks, max = 6) => (
    <Space size={[4, 4]} wrap>
      {(stocks || []).slice(0, max).map((s) => (
        <Tag key={s.code} style={{ fontSize: 11, marginInlineEnd: 0 }}>
          <StockName code={s.code} name={s.name} onOpen={onOpenStock} fontSize={11} />
          <Text style={{ color: pctColor(s.pct), marginLeft: 3, fontSize: 11 }}>
            {s.pct == null ? '' : `${s.pct > 0 ? '+' : ''}${Number(s.pct).toFixed(1)}%`}
          </Text>
        </Tag>
      ))}
      {(stocks || []).length > max ? (
        <Text type="secondary" style={{ fontSize: 11 }}>等 {stocks.length} 只</Text>
      ) : null}
    </Space>
  )

  return (
    <>
      <Space size={[12, 4]} wrap style={{ marginBottom: 8 }}>
        <Text style={{ fontSize: 12 }}>
          龙虎榜个股 <Text strong>{y.lhb_count || 0}</Text> 只
        </Text>
        <Text style={{ fontSize: 12 }}>
          游资营业部 <Text strong>{y.seat_count || 0}</Text> 家
        </Text>
        <Text style={{ fontSize: 12, color: RED }}>买入合计 {fmtYi(y.total_buy)}</Text>
        <Text style={{ fontSize: 12, color: GREEN }}>卖出合计 {fmtYi(y.total_sell)}</Text>
      </Space>

      {(y.hots || []).length > 0 ? (
        <>
          <Divider orientation="left" plain style={{ margin: '8px 0' }}>
            <Text strong style={{ fontSize: 13 }}>知名游资席位</Text>
          </Divider>
          <Row gutter={[8, 8]}>
            {(y.hots || []).map((h, i) => (
              <Col span={12} key={`${h.alias}-${i}`}>
                <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: '6px 8px' }}>
                  <Space size={6} style={{ marginBottom: 4 }} wrap>
                    <Tag color="#722ed1" style={{ fontSize: 11, marginInlineEnd: 0 }}>{h.alias}</Tag>
                    <Text style={{ fontSize: 12, color: RED }}>买 {fmtYi(h.buy)}</Text>
                    <Text style={{ fontSize: 12, color: pctColor(h.net) }}>净 {fmtYi(h.net)}</Text>
                  </Space>
                  <div style={{ marginBottom: 4 }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>{h.name}</Text>
                  </div>
                  {stockTags(h.stocks, 5)}
                </div>
              </Col>
            ))}
          </Row>
        </>
      ) : null}

      <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>
        <Text strong style={{ fontSize: 13 }}>营业部买入榜</Text>
      </Divider>
      <Table
        size="small"
        rowKey="name"
        pagination={false}
        dataSource={y.seats || []}
        columns={[
          {
            title: '营业部',
            dataIndex: 'name',
            width: 260,
            render: (v, r) => (
              <Tooltip title={v}>
                <div>
                  <Text style={{ fontSize: 12 }}>{v.replace(/证券(股份)?(有限)?公司/, '')}</Text>
                  {r.alias ? (
                    <Tag color="#722ed1" style={{ fontSize: 10, marginLeft: 4, marginInlineEnd: 0 }}>
                      {r.alias}
                    </Tag>
                  ) : null}
                </div>
              </Tooltip>
            ),
          },
          { title: '买入', dataIndex: 'buy', width: 78, render: (v) => <Text style={{ fontSize: 12, color: RED }}>{fmtYi(v)}</Text> },
          { title: '净额', dataIndex: 'net', width: 78, render: (v) => <Text style={{ fontSize: 12, color: pctColor(v) }}>{fmtYi(v)}</Text> },
          { title: '个股数', dataIndex: 'count', width: 60, render: (v) => <Text style={{ fontSize: 12 }}>{v}</Text> },
          {
            title: '涉及个股',
            dataIndex: 'stocks',
            render: (v) => stockTags(v, 4),
          },
        ]}
      />

      {(y.inst || []).length > 0 ? (
        <>
          <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>
            <Text strong style={{ fontSize: 13 }}>机构 / 北向通道（非游资）</Text>
          </Divider>
          <Space size={[8, 4]} wrap>
            {(y.inst || []).map((s) => (
              <Tag key={s.name} style={{ fontSize: 11 }}>
                {s.name}　买 {fmtYi(s.buy)}　净
                <Text style={{ color: pctColor(s.net), marginLeft: 2 }}>{fmtYi(s.net)}</Text>
              </Tag>
            ))}
          </Space>
        </>
      ) : null}

      {y.note ? (
        <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 8 }}>{y.note}</Text>
      ) : null}
    </>
  )
}

/** 股票热度榜（V1.009.2）：同花顺热榜 + 东财人气榜 */
function HotList({ hot, onOpenStock }) {
  const h = hot || {}
  // 历史日热度榜由「东财人气榜个股逐日排名」回溯组装：名次与排名变化是真实历史数据，
  // 但同花顺的「热度值」「上榜原因」只在实时榜里有（任何历史源都不提供），故隐藏这两列。
  const isHist = String(h.source || '').includes('回溯')
  if (!h.available) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={<Text type="secondary" style={{ fontSize: 12 }}>{h.note || '无热度榜数据'}</Text>}
      />
    )
  }
  return (
    <>
      {h.source ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          数据源：{h.source}（共 {h.count || 0} 只）
        </Text>
      ) : null}
      {(h.top_concepts || []).length > 0 ? (
        <div style={{ marginTop: 8, marginBottom: 4 }}>
          <Text type="secondary" style={{ fontSize: 12, marginRight: 6 }}>热榜概念分布：</Text>
          <Space size={[4, 4]} wrap>
            {(h.top_concepts || []).map((c) => (
              <Tag key={c.name} color="#1677ff" style={{ fontSize: 11, marginInlineEnd: 0 }}>
                {c.name} {c.count}
              </Tag>
            ))}
          </Space>
        </div>
      ) : null}
      <Table
        size="small"
        rowKey="code"
        style={{ marginTop: 8 }}
        dataSource={h.items || []}
        pagination={{ pageSize: 15, size: 'small', showSizeChanger: false }}
        columns={[
          {
            title: '排名',
            dataIndex: 'rank',
            width: 62,
            render: (v, r) => (
              <Text strong style={{ fontSize: 12 }}>{v || r.em_rank || '-'}</Text>
            ),
          },
          {
            title: '个股',
            dataIndex: 'name',
            width: 132,
            render: (v, r) => (
              <Space size={4}>
                <StockName code={r.code} name={v} onOpen={onOpenStock} strong fontSize={12} />
                <Text type="secondary" style={{ fontSize: 11 }}>{r.code}</Text>
              </Space>
            ),
          },
          {
            title: '涨跌幅',
            dataIndex: 'pct',
            width: 70,
            render: (v) => <Text style={{ color: pctColor(v), fontSize: 12 }}>{fmtPct(v)}</Text>,
          },
          ...(isHist
            ? []
            : [
                {
                  title: '热度值',
                  dataIndex: 'rate',
                  width: 84,
                  render: (v) =>
                    v == null ? '-' : <Text style={{ fontSize: 12 }}>{Number(v).toLocaleString()}</Text>,
                },
              ]),
          {
            title: '排名变化',
            dataIndex: 'rank_chg',
            width: 80,
            render: (v) =>
              v == null ? (
                <Text type="secondary" style={{ fontSize: 12 }}>-</Text>
              ) : v > 0 ? (
                <Text style={{ fontSize: 12, color: RED }}>↑{v}</Text>
              ) : v < 0 ? (
                <Text style={{ fontSize: 12, color: GREEN }}>↓{Math.abs(v)}</Text>
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>持平</Text>
              ),
          },
          ...(isHist
            ? []
            : [
                {
                  title: '东财人气',
                  dataIndex: 'em_rank',
                  width: 84,
                  render: (v, r) =>
                    v ? (
                      <Text style={{ fontSize: 12 }}>
                        #{v}
                        {r.em_rank_chg ? (
                          <Text style={{ fontSize: 11, marginLeft: 3, color: r.em_rank_chg > 0 ? RED : GREEN }}>
                            {r.em_rank_chg > 0 ? `↑${r.em_rank_chg}` : `↓${Math.abs(r.em_rank_chg)}`}
                          </Text>
                        ) : null}
                      </Text>
                    ) : (
                      '-'
                    ),
                },
              ]),
          {
            title: isHist ? '热门概念' : '标签 / 热门概念',
            dataIndex: 'concepts',
            render: (v, r) => (
              <Space size={[4, 4]} wrap>
                {r.tag ? <Tag color="#fa8c16" style={{ fontSize: 10, marginInlineEnd: 0 }}>{r.tag}</Tag> : null}
                {(v || []).slice(0, 3).map((c) => (
                  <Tag key={c} style={{ fontSize: 10, marginInlineEnd: 0 }}>{c}</Tag>
                ))}
              </Space>
            ),
          },
          ...(isHist
            ? []
            : [
                {
                  title: '上榜原因',
                  dataIndex: 'reason',
                  width: 60,
                  render: (v) =>
                    v ? (
                      <Tooltip title={<div style={{ maxHeight: 260, overflow: 'auto', fontSize: 12, whiteSpace: 'pre-wrap' }}>{v}</div>}>
                        <Text style={{ fontSize: 12, color: '#1677ff', cursor: 'help' }}>查看</Text>
                      </Tooltip>
                    ) : (
                      '-'
                    ),
                },
              ]),
        ]}
      />
      {h.note ? (
        <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 8 }}>{h.note}</Text>
      ) : null}
    </>
  )
}

const fmt2 = (v) => (v == null || Number.isNaN(Number(v)) ? '-' : Number(v).toFixed(2))

// ---------------------------------------------------------------------------
// 个股日线弹窗（V1.009.4）：日线 K + EMA20
// 数据来自后端 /api/daily-reviews/stock-kline（腾讯 / 新浪日K直连，前复权）。
// 颜色遵循 A 股习惯：红 = 阳线（收 ≥ 开），绿 = 阴线。
// ---------------------------------------------------------------------------
function StockKlineModal({ open, code, name, days, loading, data, onDays, onClose }) {
  const d = data || {}
  const n = (d.dates || []).length
  // 默认只展示最近约 130 根，更早的靠 dataZoom 拖回来（一次画 500 根 K 线会挤成一团）
  const zoomStart = n > 130 ? Math.round((1 - 130 / n) * 100) : 0

  const option = useMemo(() => {
    if (!d.available) return null
    const kl = d.klines || []
    const closes = d.closes || []
    const ema20 = d.ema20 || []
    const dates = d.dates || []
    return {
      animation: false,
      grid: { left: 66, right: 20, top: 36, bottom: 58 },
      legend: {
        data: ['日K', 'EMA20'],
        right: 10,
        top: 2,
        itemWidth: 14,
        itemHeight: 8,
        textStyle: { fontSize: 11 },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { fontSize: 11 } },
        formatter: (ps) => {
          if (!ps || !ps.length) return ''
          const i = ps[0].dataIndex
          const k = kl[i] || []
          const prev = i > 0 ? closes[i - 1] : null
          const pct = prev ? (closes[i] / prev - 1) * 100 : null
          const cc = (v) => (v == null ? '#8c8c8c' : v >= 0 ? RED : GREEN)
          return [
            `<b>${dates[i] || ''}</b>`,
            `开 ${fmt2(k[0])}　高 ${fmt2(k[3])}`,
            `低 ${fmt2(k[2])}　收 <b>${fmt2(k[1])}</b>`,
            pct == null
              ? '涨跌幅 -'
              : `涨跌幅 <span style="color:${cc(pct)}">${pct > 0 ? '+' : ''}${pct.toFixed(2)}%</span>`,
            `EMA20 ${fmt2(ema20[i])}`,
          ].join('<br/>')
        },
      },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: '#d9d9d9' } },
        axisLabel: { fontSize: 11, hideOverlap: true },
      },
      yAxis: {
        scale: true,
        splitLine: { lineStyle: { color: '#f0f0f0' } },
        axisLabel: { fontSize: 11, formatter: (v) => Number(v).toFixed(2) },
      },
      dataZoom: [
        { type: 'inside', start: zoomStart, end: 100 },
        { type: 'slider', height: 16, bottom: 16, start: zoomStart, end: 100, showDetail: false },
      ],
      series: [
        {
          name: '日K',
          type: 'candlestick',
          data: kl,
          itemStyle: { color: RED, color0: GREEN, borderColor: RED, borderColor0: GREEN },
        },
        {
          name: 'EMA20',
          type: 'line',
          data: ema20,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 1.8, color: '#fa8c16' },
          itemStyle: { color: '#fa8c16' },
          connectNulls: false,
          z: 3,
        },
      ],
    }
  }, [d, zoomStart])

  const L = d.latest || {}

  return (
    <Modal
      title={
        <Space size={8} wrap>
          <span>{name || code || '个股'}</span>
          <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>{code}</Text>
          {d.available && L.close != null ? (
            <>
              <Text style={{ fontSize: 13, fontWeight: 400 }}>
                最新 <b style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt2(L.close)}</b>
              </Text>
              <Text style={{ fontSize: 13, fontWeight: 400, color: pctColor(L.pct) }}>
                {fmtPct(L.pct)}
              </Text>
              <Text style={{ fontSize: 12, fontWeight: 400, color: '#fa8c16' }}>
                EMA20 {fmt2(L.ema20)}
              </Text>
              <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>{L.date}</Text>
            </>
          ) : null}
        </Space>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={920}
      destroyOnClose
    >
      <Space direction="vertical" style={{ width: '100%' }} size={10}>
        <Space size={8} wrap>
          {[60, 120, 250].map((x) => (
            <Button
              key={x}
              size="small"
              type={days === x ? 'primary' : 'default'}
              disabled={loading}
              onClick={() => onDays(x)}
            >
              近 {x} 日
            </Button>
          ))}
          <Text type="secondary" style={{ fontSize: 12 }}>
            日线（前复权）+ EMA20
          </Text>
          {d.available && d.source ? (
            <Text type="secondary" style={{ fontSize: 12 }}>· {d.source}</Text>
          ) : null}
        </Space>
        {d.available && d.note ? <Alert type="warning" showIcon message={d.note} /> : null}
        {loading ? (
          <div style={{ height: 420, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Spin />
          </div>
        ) : d.available ? (
          <ReactECharts option={option} style={{ height: 420 }} notMerge />
        ) : (
          <Empty description={d.note || '未取到该股行情数据'} />
        )}
        <Text type="secondary" style={{ fontSize: 11 }}>
          数据源：腾讯 / 新浪日K直连（前复权）；EMA20 首值取前 20 根收盘均价作种子，与通达信、东财一致。
          红为阳线、绿为阴线；默认展示最近约 130 根，可拖动下方滑块回看更早。
        </Text>
      </Space>
    </Modal>
  )
}

export default function DailyReview() {
  // 默认定位最近交易日（周末自动回退到周五，节假日需手动选择）
  const [date, setDate] = useState(() => {
    const d = dayjs()
    const dow = d.day()
    if (dow === 6) return d.subtract(1, 'day')
    if (dow === 0) return d.subtract(2, 'day')
    return d
  })
  const [snap, setSnap] = useState(null)
  const [recordId, setRecordId] = useState(null)
  const [manual, setManual] = useState('')
  const [aiContent, setAiContent] = useState('')
  const [aiAt, setAiAt] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [aiLoading, setAiLoading] = useState(false)

  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyList, setHistoryList] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)

  const [trendOpen, setTrendOpen] = useState(false)
  const [trendData, setTrendData] = useState(null)
  const [trendLoading, setTrendLoading] = useState(false)
  const [trendDays, setTrendDays] = useState(20)

  // 近期情绪趋势小图（V1.009.3）：随所选日期自动加载，不需要手动点击
  const [miniTrend, setMiniTrend] = useState(null)
  const [miniLoading, setMiniLoading] = useState(false)
  const [miniDays, setMiniDays] = useState(10)

  // 个股日线弹窗（V1.009.4）：点股票名称 → 日线图 + EMA20
  const [stockModal, setStockModal] = useState({
    open: false,
    code: '',
    name: '',
    days: 120,
    loading: false,
    data: null,
  })

  const loadStock = useCallback(async (code, nm, days) => {
    setStockModal({ open: true, code, name: nm || '', days, loading: true, data: null })
    try {
      const r = await getStockKline(code, days)
      setStockModal((st) => (st.code === code ? { ...st, loading: false, data: r } : st))
    } catch (e) {
      // 取数失败只在弹窗里提示，不弹全局 message（弹窗本身就是这次交互的载体）
      setStockModal((st) =>
        st.code === code
          ? {
              ...st,
              loading: false,
              data: { available: false, note: `行情获取失败：${e?.message || e}` },
            }
          : st,
      )
    }
  }, [])

  const openStock = useCallback((code, nm) => loadStock(code, nm, 120), [loadStock])
  const changeStockDays = useCallback(
    (n) => loadStock(stockModal.code, stockModal.name, n),
    [loadStock, stockModal.code, stockModal.name],
  )

  // 快照来源与历史重建（V1.009.1）
  const [snapLoading, setSnapLoading] = useState(false)
  const [snapSource, setSnapSource] = useState('')   // cache / fresh
  const [needRebuild, setNeedRebuild] = useState(false)
  const [rebuildOpen, setRebuildOpen] = useState(false)
  const [rebuild, setRebuild] = useState(null)
  const [rebuildDays, setRebuildDays] = useState(60)
  const rebuildTimer = useRef(null)

  const dateStr = date.format('YYYY-MM-DD')
  const draftKey = `daily_review:${dateStr}`

  // 近期情绪趋势：以当前所选交易日为终点，自动拉取近 N 个交易日（独立请求，不阻塞主数据）
  useEffect(() => {
    let alive = true
    setMiniLoading(true)
    getDailyTrend(dateStr, miniDays)
      .then((d) => {
        if (alive) setMiniTrend(d)
      })
      .catch(() => {
        if (alive) setMiniTrend(null)
      })
      .finally(() => {
        if (alive) setMiniLoading(false)
      })
    return () => {
      alive = false
    }
  }, [dateStr, miniDays])

  // 草稿缓存：手写内容防丢（切换日期 / 误关页面都能恢复）
  const draft = useDraft(draftKey, {
    formData: manual ? { manual } : null,
  })

  // 载入某交易日：本地记录（手写 / AI）+ 盘面快照（缓存优先）
  const loadByDate = useCallback(async (d) => {
    const ds = d.format('YYYY-MM-DD')
    setSnap(null)
    setSnapSource('')
    setNeedRebuild(false)
    setManual('')
    setAiContent('')
    setAiAt('')
    setRecordId(null)
    try {
      const r = await getDailyReviewByDate(ds)
      setRecordId(r.id)
      setManual(r.manual_content || '')
      setAiContent(r.ai_content || '')
      setAiAt(r.ai_created_at || '')
      if (r.market_json) {
        try {
          setSnap(JSON.parse(r.market_json))
        } catch (e) {
          /* 快照损坏则忽略 */
        }
      }
    } catch (e) {
      // 404 = 该交易日还没记录，属正常
    }
    // 盘面数据：命中本地缓存秒回；未命中才联网回溯（约 3~10s）
    setSnapLoading(true)
    try {
      const res = await fetchDailySnapshot(ds)
      setSnap(res.snapshot)
      setSnapSource(res.source || '')
      setNeedRebuild(!!res.snapshot?.need_rebuild)
    } catch (e) {
      /* 联网失败不阻塞复盘编辑 */
    } finally {
      setSnapLoading(false)
    }
  }, [])

  useEffect(() => {
    loadByDate(date)
    // 切换日期时检测草稿（有则提示恢复）
    const d = draft.checkDraft(`daily_review:${date.format('YYYY-MM-DD')}`)
    if (d?.values?.manual) {
      Modal.confirm({
        title: '检测到未保存的草稿',
        content: '该日期有上次未保存的手写复盘草稿，是否恢复？',
        okText: '恢复草稿',
        cancelText: '放弃草稿',
        onOk: () => setManual(d.values.manual),
        onCancel: () => draft.clear(),
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, loadByDate])

  // 重新获取盘面数据：最近交易日强制联网重抓；历史日只能由「历史数据重建」刷新
  // （实时接口无法按历史日期取涨跌家数与概念板块，强抓只会得到残缺快照）
  const handleFetch = async () => {
    setLoading(true)
    try {
      const res = await fetchDailySnapshot(dateStr, true)
      const s = res.snapshot || {}
      setSnap(s)
      setSnapSource(res.source || '')
      setNeedRebuild(!!s.need_rebuild)
      if (s.force_ignored) {
        message.info(
          `${dateStr} 为历史日期，已直接读取本地数据（重建口径）。` +
            '历史日不支持重新抓取——实时接口取不到历史日的涨跌家数与概念板块，' +
            '如需更新请用「历史数据重建」。',
          6
        )
      } else {
        message.success(
          `已获取 ${dateStr} 盘面数据（${
            res.source === 'cache' ? '本地缓存' : `联网 ${s.elapsed_sec ?? '-'}s`
          }）`
        )
      }
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!snap && !manual.trim()) {
      message.warning('请先抓取盘面数据或填写复盘内容')
      return
    }
    setSaving(true)
    try {
      const r = await saveDailyReview({
        trade_date: dateStr,
        manual_content: manual,
        market_json: snap ? JSON.stringify(snap) : '',
      })
      setRecordId(r.id)
      draft.clear()
      message.success('复盘已保存')
    } catch (e) {
      /* ignore */
    } finally {
      setSaving(false)
    }
  }

  const handleAi = async () => {
    if (!snap) {
      message.warning('请先抓取当日盘面数据，AI 点评需要结合数据')
      return
    }
    setAiLoading(true)
    try {
      const r = await runDailyAi({
        trade_date: dateStr,
        manual_content: manual,
        market_json: JSON.stringify(snap),
      })
      setAiContent(r.ai_content || '')
      setAiAt(r.ai_created_at || '')
      setRecordId(r.id)
      message.success('AI 点评完成')
    } catch (e) {
      /* ignore */
    } finally {
      setAiLoading(false)
    }
  }

  const openHistory = async () => {
    setHistoryOpen(true)
    setHistoryLoading(true)
    try {
      setHistoryList(await listDailyReviews({ limit: 200 }))
    } catch (e) {
      /* ignore */
    } finally {
      setHistoryLoading(false)
    }
  }

  const openTrend = async (days = trendDays) => {
    setTrendOpen(true)
    setTrendLoading(true)
    setTrendDays(days)
    try {
      setTrendData(await getDailyTrend(dateStr, days))
    } catch (e) {
      /* ignore */
    } finally {
      setTrendLoading(false)
    }
  }

  const handleDelete = async (id) => {
    try {
      await deleteDailyReview(id)
      message.success('已删除')
      setHistoryList((prev) => prev.filter((x) => x.id !== id))
      if (recordId === id) {
        setRecordId(null)
        setManual('')
        setAiContent('')
      }
    } catch (e) {
      /* ignore */
    }
  }

  // ---------------- 历史数据重建（V1.009.1） ----------------
  // 东财实时快照类接口无法回溯历史，靠全市场个股日K重建后落入本地缓存
  const pollRebuild = useCallback(() => {
    if (rebuildTimer.current) clearInterval(rebuildTimer.current)
    rebuildTimer.current = setInterval(async () => {
      try {
        const st = await getRebuildStatus()
        setRebuild(st)
        if (st.status === 'running') return
        clearInterval(rebuildTimer.current)
        rebuildTimer.current = null
        if (st.status === 'done') {
          message.success(st.message || '历史数据重建完成')
          loadByDate(date)
        } else if (st.status === 'error') {
          message.error(st.error || '重建失败')
        }
      } catch (e) {
        clearInterval(rebuildTimer.current)
        rebuildTimer.current = null
      }
    }, 1500)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, loadByDate])

  const handleRebuild = async () => {
    setRebuildOpen(true)
    try {
      const r = await startDailyRebuild(rebuildDays)
      if (r.ok === false) message.warning(r.error || '已有重建任务正在进行')
      setRebuild(await getRebuildStatus())
      pollRebuild()
    } catch (e) {
      /* ignore */
    }
  }

  // 页面挂载时：若后台仍在重建则自动接管进度显示
  useEffect(() => {
    getRebuildStatus()
      .then((st) => {
        if (st?.status === 'running') {
          setRebuild(st)
          setRebuildOpen(true)
          pollRebuild()
        }
      })
      .catch(() => {})
    return () => {
      if (rebuildTimer.current) clearInterval(rebuildTimer.current)
      rebuildTimer.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---------------- 表格列 ----------------
  const ztColumns = [
    { title: '代码', dataIndex: 'code', width: 78 },
    {
      title: '名称',
      dataIndex: 'name',
      width: 110,
      render: (v, r) => (
        <Space size={4}>
          <StockName code={r.code} name={v} onOpen={openStock} strong />
          {r.is_st && <Tag color="default">ST</Tag>}
        </Space>
      ),
    },
    { title: '现价', dataIndex: 'price', width: 70, render: (v) => (v ?? '-') },
    {
      title: '涨跌幅',
      dataIndex: 'pct',
      width: 80,
      render: (v) => <Text style={{ color: pctColor(v) }}>{fmtPct(v)}</Text>,
    },
    {
      title: '连板',
      dataIndex: 'lbc',
      width: 58,
      render: (v) => (v > 1 ? <Tag color="red">{v}板</Tag> : <Text type="secondary">首板</Text>),
    },
    { title: '涨停统计', dataIndex: 'stat', width: 88, render: (v) => v || '-' },
    { title: '首封', dataIndex: 'fbt', width: 84 },
    { title: '最后封板', dataIndex: 'lbt', width: 92 },
    { title: '炸板次数', dataIndex: 'zbc', width: 84 },
    { title: '成交额', dataIndex: 'amount', width: 88, render: (v) => fmtYi(v) },
    { title: '换手%', dataIndex: 'hs', width: 74 },
    { title: '流通市值', dataIndex: 'ltsz', width: 92, render: (v) => fmtYi(v) },
    { title: '行业', dataIndex: 'industry', width: 100, render: (v) => v || '-' },
  ]

  const zbColumns = [
    { title: '代码', dataIndex: 'code', width: 78 },
    { title: '名称', dataIndex: 'name', width: 118, render: (v, r) => <StockName code={r.code} name={v} onOpen={openStock} /> },
    {
      title: '涨跌幅',
      dataIndex: 'pct',
      width: 80,
      render: (v) => <Text style={{ color: pctColor(v) }}>{fmtPct(v)}</Text>,
    },
    { title: '涨停价', dataIndex: 'zt_price', width: 74 },
    { title: '炸板次数', dataIndex: 'zbc', width: 84, render: (v) => <Tag color="orange">{v}</Tag> },
    { title: '振幅%', dataIndex: 'amplitude', width: 74 },
    { title: '成交额', dataIndex: 'amount', width: 90, render: (v) => fmtYi(v) },
    { title: '首封', dataIndex: 'fbt', width: 84 },
    { title: '行业', dataIndex: 'industry', width: 100, render: (v) => v || '-' },
  ]

  const dtColumns = [
    { title: '代码', dataIndex: 'code', width: 78 },
    { title: '名称', dataIndex: 'name', width: 118, render: (v, r) => <StockName code={r.code} name={v} onOpen={openStock} /> },
    {
      title: '涨跌幅',
      dataIndex: 'pct',
      width: 80,
      render: (v) => <Text style={{ color: pctColor(v) }}>{fmtPct(v)}</Text>,
    },
    { title: '成交额', dataIndex: 'amount', width: 90, render: (v) => fmtYi(v) },
    { title: '换手%', dataIndex: 'hs', width: 74 },
    { title: '行业', dataIndex: 'industry', width: 110, render: (v) => v || '-' },
  ]

  const ppColumns = [
    { title: '代码', dataIndex: 'code', width: 78 },
    { title: '名称', dataIndex: 'name', width: 118, render: (v, r) => <StockName code={r.code} name={v} onOpen={openStock} /> },
    { title: '昨日', dataIndex: 'prev_stat', width: 88, render: (v, r) => v || (r.prev_lbc > 1 ? `${r.prev_lbc}板` : '首板') },
    {
      title: '今日涨跌幅',
      dataIndex: 'pct',
      width: 96,
      render: (v) => <Text style={{ color: pctColor(v) }}>{fmtPct(v)}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'again_limit_up',
      width: 90,
      render: (v, r) =>
        v ? <Tag color="red">再涨停</Tag> : r.pct != null && r.pct > 0 ? <Tag color="volcano">红盘</Tag> : <Tag>走弱</Tag>,
    },
  ]

  // ---------------- 趋势图（参考截图3 的四张图） ----------------
  const trendOptions = useMemo(() => {
    const items = trendData?.items || []
    const xs = items.map((x) => x.date?.slice(5))
    const base = {
      grid: { left: 52, right: 18, top: 34, bottom: 26 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0, textStyle: { fontSize: 11 } },
      xAxis: { type: 'category', data: xs, axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#f0f0f0' } } },
    }
    const line = (name, data, color) => ({
      name,
      type: 'line',
      data,
      smooth: false,
      symbolSize: 5,
      lineStyle: { width: 2, color },
      itemStyle: { color },
    })
    return {
      amount: {
        ...base,
        yAxis: { ...base.yAxis, name: '亿元', nameTextStyle: { fontSize: 10 } },
        series: [
          line('涨停金额/亿', items.map((x) => +(x.zt_amount / 1e8).toFixed(1)), RED),
          line('炸板金额/亿', items.map((x) => +(x.zb_amount / 1e8).toFixed(1)), '#8c8c8c'),
          line(
            '涨停总额/亿',
            items.map((x) => +((x.zt_amount + x.zb_amount) / 1e8).toFixed(1)),
            '#52a550',
          ),
        ],
      },
      count: {
        ...base,
        yAxis: { ...base.yAxis, name: '家数', nameTextStyle: { fontSize: 10 } },
        series: [
          line('涨停家数', items.map((x) => x.limit_up), RED),
          line('炸板家数', items.map((x) => x.broken), '#1677ff'),
          // 「涨停总数（涨停 + 炸板）」已按用户要求去掉：与涨停/炸板两条线信息重复，
          // 且三条线里两条是它的加数，视觉上互相干扰
          line('跌停家数', items.map((x) => x.limit_down), GREEN),
        ],
      },
      rate: {
        ...base,
        yAxis: {
          ...base.yAxis,
          name: '%',
          max: 100,
          nameTextStyle: { fontSize: 10 },
          axisLabel: { fontSize: 10, formatter: '{value}%' },
        },
        series: [
          line('炸板金额率', items.map((x) => x.broken_amount_rate), '#1677ff'),
          line('炸板率', items.map((x) => x.broken_rate), RED),
        ],
      },
      ladder: {
        ...base,
        yAxis: { ...base.yAxis, name: '板', nameTextStyle: { fontSize: 10 } },
        series: [line('最高连板', items.map((x) => x.max_lbc), '#722ed1')],
      },
      // 首板 / 连板：两条线之和恒等于「涨停家数」，看的是涨停的内部结构 ——
      // 连板萎缩而首板顶上 = 存量接力断了、靠新面孔维持；两者同时萎缩 = 情绪整体退潮
      structure: {
        ...base,
        yAxis: { ...base.yAxis, name: '家数', nameTextStyle: { fontSize: 10 } },
        series: [
          line('首板家数', items.map((x) => x.first_board), '#d9363e'),
          line('连板家数', items.map((x) => x.multi_board), '#722ed1'),
        ],
      },
      // 昨涨停股今日表现：平均涨幅看「赚钱效应强弱」，晋级率看「接力意愿」。
      // 两者量纲差一个数量级（涨幅 ±5%、晋级率 0~50%），必须分双 Y 轴，否则平均涨幅会被压平
      prevlu: {
        ...base,
        yAxis: [
          {
            ...base.yAxis,
            name: '平均涨幅%',
            nameTextStyle: { fontSize: 10 },
            axisLabel: { fontSize: 10, formatter: '{value}%' },
          },
          {
            ...base.yAxis,
            name: '晋级率%',
            nameTextStyle: { fontSize: 10 },
            position: 'right',
            splitLine: { show: false },
            axisLabel: { fontSize: 10, formatter: '{value}%' },
          },
        ],
        series: [
          line('平均涨幅', items.map((x) => x.prev_lu_avg), RED),
          { ...line('晋级率', items.map((x) => x.prev_lu_rate), '#722ed1'), yAxisIndex: 1 },
        ],
      },
    }
  }, [trendData])

  // 趋势数据来源统计：本地重建缓存 / 东财实时池回补
  const trendSrc = useMemo(() => {
    const its = trendData?.items || []
    const em = its.filter((x) => x.source === 'em').length
    return { total: its.length, em, cache: its.length - em }
  }, [trendData])

  const zt = snap?.limit_up || {}
  const zb = snap?.broken || {}
  const dtp = snap?.limit_down || {}
  const pp = snap?.prev_limit_up || {}
  const e = snap?.emotion || {}
  const ac = snap?.amount_chg || {}

  return (
    <div>
      {/* 顶部操作栏 */}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap>
          <Text strong>A股每日复盘</Text>
          <DatePicker
            value={date}
            onChange={(d) => d && setDate(d)}
            allowClear={false}
            format="YYYY-MM-DD"
          />
          <Button type="primary" icon={<SyncOutlined />} loading={loading} onClick={handleFetch}>
            重新抓取数据
          </Button>
          <Button icon={<LineChartOutlined />} onClick={() => openTrend()}>
            情绪趋势
          </Button>
          <Button icon={<HistoryOutlined />} onClick={openHistory}>
            历史复盘
          </Button>
          <Button icon={<DatabaseOutlined />} onClick={handleRebuild}>
            历史数据重建
          </Button>
          {snap?.collected_at && (
            <Space size={4}>
              <Tag color={snapSource === 'cache' ? 'blue' : snap.is_latest ? 'green' : 'orange'}>
                {snapSource === 'cache'
                  ? '本地缓存'
                  : snap.is_latest
                    ? '实时采集'
                    : snap.cache_kind === 'rebuild'
                      ? '历史重建'
                      : '历史回补'}
              </Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>
                采集于 {snap.collected_at}
                {snap.elapsed_sec ? `｜耗时 ${snap.elapsed_sec}s` : ''}
                {snap.partial ? '｜数据不完整' : ''}
                {snap.coverage?.universe
                  ? `｜有效个股 ${snap.coverage.stocks}/${snap.coverage.universe}`
                  : ''}
              </Text>
            </Space>
          )}
        </Space>
      </Card>

      {/* 我的复盘 + AI 点评（置于最上方：先写/看自己的判断，再往下看盘面数据） */}
      <Card
        size="small"
        style={{ marginBottom: 12 }}
        title={
          <Space>
            <FireOutlined style={{ color: '#fa8c16' }} />
            <span>我的复盘</span>
            {recordId && <Tag color="green">已保存</Tag>}
            {draft.hasDraft && <Tag color="orange">有草稿</Tag>}
          </Space>
        }
        extra={
          <Space>
            <Button
              icon={<RobotOutlined />}
              loading={aiLoading}
              onClick={handleAi}
              disabled={!snap}
            >
              AI 点评
            </Button>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
              保存复盘
            </Button>
          </Space>
        }
      >
        <Input.TextArea
          value={manual}
          onChange={(ev) => setManual(ev.target.value)}
          rows={8}
          placeholder={`记录 ${dateStr} 的复盘思考：今日情绪周期位置、主线板块与龙头、自己的操作与得失、明日计划…\n（输入内容自动存草稿，不怕误关）`}
          style={{ marginBottom: 12 }}
        />

        {aiContent && (
          <>
            <Divider orientation="left" plain style={{ marginTop: 4 }}>
              <Space size={6}>
                <RobotOutlined style={{ color: '#534AB7' }} />
                <span>AI 点评</span>
                {aiAt && <Text type="secondary" style={{ fontSize: 12 }}>{aiAt}</Text>}
              </Space>
            </Divider>
            <div
              style={{
                background: '#fafafa',
                border: '1px solid #f0f0f0',
                borderRadius: 6,
                padding: '10px 14px',
                maxHeight: 520,
                overflow: 'auto',
              }}
            >
              <MarkdownView text={aiContent} />
            </div>
          </>
        )}
      </Card>

      {snap?.note && (
        <Alert
          type="warning"
          showIcon
          message="数据提示"
          description={snap.note}
          style={{ marginBottom: 12 }}
        />
      )}

      {needRebuild && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="该交易日缺少涨跌家数与概念板块数据"
          description={
            <Space wrap>
              <Text style={{ fontSize: 12 }}>
                东财的涨跌家数分布 / 概念板块排行只能实时获取，无法按历史日期查询。
                执行「历史数据重建」可由全市场个股日K回溯补齐（约 1~3 分钟，一次重建多日，之后随时可查）。
              </Text>
              <Button size="small" type="primary" icon={<DatabaseOutlined />} onClick={handleRebuild}>
                立即重建
              </Button>
            </Space>
          }
        />
      )}

      {!snap && !loading && !snapLoading && (
        <Card>
          <Empty
            description={
              <Space direction="vertical" size={2}>
                <Text>还没有 {dateStr} 的盘面数据</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  切换日期会自动读取（本地缓存优先）；也可点「重新抓取数据」强制联网采集
                </Text>
              </Space>
            }
          />
        </Card>
      )}

      <Spin
        spinning={loading || snapLoading}
        tip={snapLoading ? '正在读取盘面数据…' : '正在抓取盘面数据（约 3~10 秒）…'}
      >
        {snap && (
          <>
            {/* 指数 + 情绪指标（两卡等高、横向对齐）
                注：alignItems 同时写在内联 style 里 —— antd 的 align 是运行时拼 class 名，
                若对应 CSS 未注入则不生效，内联可确保 Col 被拉伸、Card 的 height:100% 才拿得到高度 */}
            <Row gutter={12} align="stretch" style={{ marginBottom: 12, alignItems: 'stretch' }}>
              <Col span={14} style={{ display: 'flex' }}>
                <Card
                  size="small"
                  title="指数表现"
                  style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}
                  bodyStyle={{ padding: 12, flex: 1, display: 'flex', alignItems: 'stretch' }}
                >
                  {/* 指数有 8 个（上证/深证/创业板/科创50/沪深300/中证500/中证1000/北证50），
                     单行均分会让每块只剩 ~90px，22px + 8 位数字必然挤在一起。
                      此处固定 4 列自动换行（两行），列宽 25% 减去 gap，行高由内容决定、
                     整体 alignContent 居中；外层 body 的 stretch 仍保证与「情绪指标」卡等高 */}
                  <div
                    style={{
                      width: '100%',
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 10,
                      alignContent: 'center',
                    }}
                  >
                    {(snap.indices || []).map((i) => (
                      <div
                        key={i.name}
                        style={{
                          flex: '0 0 calc(25% - 7.5px)',
                          minWidth: 0,
                          border: '1px solid #f0f0f0',
                          borderRadius: 6,
                          padding: '8px 10px',
                          display: 'flex',
                          flexDirection: 'column',
                          justifyContent: 'center',
                          gap: 3,
                        }}
                      >
                        <Text type="secondary" style={{ ...TXT_L1, whiteSpace: 'nowrap' }}>
                          {i.name}
                        </Text>
                        <div style={{ ...TXT_L2, lineHeight: 1.15, whiteSpace: 'nowrap' }}>
                          {i.close == null ? '-' : i.close.toFixed(2)}
                        </div>
                        <div
                          style={{
                            ...TXT_L3,
                            lineHeight: 1.2,
                            whiteSpace: 'nowrap',
                            color: pctColor(i.pct),
                          }}
                        >
                          {fmtPct(i.pct)}
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              </Col>
              <Col span={10} style={{ display: 'flex' }}>
                <Card
                  size="small"
                  title="情绪指标"
                  style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}
                  bodyStyle={{ padding: 12, flex: 1 }}
                >
                  <Row gutter={[8, 10]}>
                    <Col span={8}>
                      <Statistic
                        title={
                          <span style={TXT_L1}>
                            涨停家数
                            <Hint k="limit_up" />
                          </span>
                        }
                        value={fmtCount(e.limit_up)}
                        valueStyle={{ ...TXT_L2, color: RED }}
                      />
                    </Col>
                    <Col span={8}>
                      <Statistic
                        title={
                          <span style={TXT_L1}>
                            跌停家数
                            <Hint k="limit_down" />
                          </span>
                        }
                        value={fmtCount(e.limit_down)}
                        valueStyle={{ ...TXT_L2, color: GREEN }}
                      />
                    </Col>
                    <Col span={8}>
                      <Statistic
                        title={
                          <span style={TXT_L1}>
                            最高连板
                            <Hint k="max_lbc" />
                          </span>
                        }
                        value={fmtCount(e.max_lbc)}
                        suffix={<span style={TXT_L3_DIM}>板</span>}
                        valueStyle={{ ...TXT_L2, color: '#722ed1' }}
                      />
                    </Col>
                    <Col span={8}>
                      <Statistic
                        title={
                          <span style={TXT_L1}>
                            炸板家数
                            <Hint k="broken" />
                          </span>
                        }
                        value={fmtCount(e.broken)}
                        valueStyle={{ ...TXT_L2 }}
                      />
                    </Col>
                    <Col span={8}>
                      <Statistic
                        title={
                          <span style={TXT_L1}>
                            炸板率
                            <Hint k="broken_rate" />
                          </span>
                        }
                        value={e.broken_rate == null ? '-' : e.broken_rate}
                        suffix={<span style={TXT_L3_DIM}>%</span>}
                        valueStyle={{ ...TXT_L2 }}
                      />
                    </Col>
                    <Col span={8}>
                      <Statistic
                        title={
                          <span style={TXT_L1}>
                            炸板金额率
                            <Hint k="broken_amount_rate" />
                          </span>
                        }
                        value={e.broken_amount_rate == null ? '-' : e.broken_amount_rate}
                        suffix={<span style={TXT_L3_DIM}>%</span>}
                        valueStyle={{ ...TXT_L2 }}
                      />
                    </Col>
                    <Col span={12}>
                      <Statistic
                        title={
                          <span style={TXT_L1}>
                            成交额较昨日
                            <Hint k="amt_delta" />
                          </span>
                        }
                        value={ac.available ? `${ac.delta_pct >= 0 ? '+' : ''}${ac.delta_pct}` : '-'}
                        suffix={<span style={TXT_L3_DIM}>%</span>}
                        valueStyle={{
                          ...TXT_L2,
                          color: ac.available ? (ac.delta_pct >= 0 ? RED : GREEN) : '#8c8c8c',
                        }}
                      />
                    </Col>
                    <Col span={12}>
                      <Statistic
                        title={
                          <span style={TXT_L1}>
                            两市成交额
                            <Hint k="amt_today" />
                          </span>
                        }
                        value={ac.today ? Math.round(ac.today / 1e8) : '-'}
                        suffix={<span style={TXT_L3_DIM}>亿</span>}
                        valueStyle={{ ...TXT_L2 }}
                      />
                    </Col>
                    <Col span={24}>
                      {/* 脚注属于说明文字，比指标低一档（13px）以便整行不折行 */}
                      <Text type="secondary" style={{ fontSize: 13 }}>
                        昨日涨停股今日平均表现：
                        <Hint k="prev_zt_perf" />
                      </Text>
                      <Text style={{ ...TXT_L3, color: pctColor(pp.avg_pct) }}>
                        {fmtPct(pp.avg_pct)}
                      </Text>
                      <Text type="secondary" style={{ fontSize: 13, marginLeft: 8 }}>
                        （{pp.prev_date || '-'} 的 {pp.count || 0} 只：涨 {pp.up_count || 0} / 跌 {pp.down_count || 0} / 再涨停 {pp.limit_up_again || 0}）
                      </Text>
                    </Col>
                  </Row>
                </Card>
              </Col>
            </Row>

            {/* 涨跌家数分布 + 连板梯队 */}
            <Row gutter={12} style={{ marginBottom: 12 }}>
              <Col span={11}>
                <Card size="small" title="涨跌家数分布" bodyStyle={{ padding: 12 }}>
                  <BreadthBoard snap={snap} />
                </Card>
              </Col>
              <Col span={13}>
                <Card
                  size="small"
                  title="连板梯队"
                  extra={<Text type="secondary" style={{ fontSize: 12 }}>涨停 {zt.count || 0} 只 · 成交 {fmtYi(zt.amount)}</Text>}
                  bodyStyle={{ padding: 12 }}
                >
                  {(zt.ladder || []).length === 0 ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当日无涨停" />
                  ) : (
                    <Table
                      size="small"
                      rowKey="lbc"
                      pagination={false}
                      dataSource={zt.ladder}
                      columns={[
                        {
                          title: '连板高度',
                          dataIndex: 'lbc',
                          width: 92,
                          render: (v) => <Tag color={v >= 3 ? 'red' : 'volcano'}>{v} 板</Tag>,
                        },
                        { title: '家数', dataIndex: 'count', width: 60 },
                        { title: '成交额', dataIndex: 'amount', width: 88, render: (v) => fmtYi(v) },
                        {
                          title: '个股',
                          dataIndex: 'stocks',
                          render: (v) => (
                            <Space size={[4, 4]} wrap>
                              {v.map((s) => (
                                <Tooltip key={s.code} title={`${s.code} ${s.industry} ${s.fbt}封板`}>
                                  <Tag style={{ margin: 0 }}>
                                    <StockName code={s.code} name={s.name} onOpen={openStock} fontSize={12} />
                                  </Tag>
                                </Tooltip>
                              ))}
                            </Space>
                          ),
                        },
                      ]}
                    />
                  )}
                  <Divider style={{ margin: '10px 0 8px' }} />
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      涨停行业分布（前 10）：
                    </Text>
                    <div style={{ marginTop: 6 }}>
                      <Space size={[6, 6]} wrap>
                        {(zt.industries || []).slice(0, 10).map((i) => (
                          <Tag key={i.name} color="blue">
                            {i.name} {i.count}
                          </Tag>
                        ))}
                        {(zt.industries || []).length === 0 && <Text type="secondary">-</Text>}
                      </Space>
                    </div>
                  </div>
                </Card>
              </Col>
            </Row>

            {/* 近期情绪趋势（V1.009.3）：把核心情绪指标拉成时间序列，先看"变化方向"再看单日数据 */}
            <Card
              size="small"
              style={{ marginBottom: 12 }}
              title={
                <Space size={4}>
                  <LineChartOutlined />
                  <span>近期情绪趋势</span>
                  <Hint k="trend_mini" />
                  <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                    近 {miniDays} 个交易日
                  </Text>
                </Space>
              }
              extra={
                <Space size={8}>
                  {[5, 10, 20].map((d) => (
                    <Button
                      key={d}
                      size="small"
                      type={miniDays === d ? 'primary' : 'default'}
                      onClick={() => setMiniDays(d)}
                    >
                      {d} 日
                    </Button>
                  ))}
                  <Button
                    size="small"
                    icon={<LineChartOutlined />}
                    onClick={() => openTrend(miniDays)}
                  >
                    放大查看
                  </Button>
                </Space>
              }
              bodyStyle={{ padding: 12 }}
            >
              <MiniTrendGrid
                data={miniTrend}
                loading={miniLoading}
                mainLines={snap?.concept?.main_lines}
                days={miniDays}
              />
            </Card>

            {/* 概念板块情绪周期（V1.009.1） */}
            <Card
              size="small"
              style={{ marginBottom: 12 }}
              bodyStyle={{ padding: 12 }}
              title="概念板块情绪周期"
              extra={
                snap.concept?.total ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    概念板块 {snap.concept.total} 个
                    {snap.concept.source === 'rebuild' ? '（历史重建口径）' : ''}
                  </Text>
                ) : null
              }
            >
              <ConceptEmotion concept={snap.concept} onOpenStock={openStock} />
            </Card>

            {/* 严重异动提醒（V1.009.2） */}
            <Card
              size="small"
              style={{ marginBottom: 12 }}
              bodyStyle={{ padding: 12 }}
              title={
                <Space size={6}>
                  <span>严重异动提醒</span>
                  {snap.abnormal?.total ? (
                    <Tag color="#d9363e" style={{ marginInlineEnd: 0 }}>
                      {snap.abnormal.total} 只
                    </Tag>
                  ) : null}
                  {/* 检测异常要显性化：这类错误曾被 try/except 静默吞掉，报告里只显示"无数据" */}
                  {snap.abnormal && snap.abnormal.ok === false && !snap.abnormal.available ? (
                    <Tooltip title={snap.abnormal.note || '检测异常'}>
                      <Tag color="error" style={{ marginInlineEnd: 0 }}>
                        检测未能执行
                      </Tag>
                    </Tooltip>
                  ) : null}
                </Space>
              }
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  偏离值口径 · 3/10/30 日窗口
                </Text>
              }
            >
              <AbnormalWatch abnormal={snap.abnormal} onOpenStock={openStock} />
            </Card>

            {/* 游资动向（V1.009.2） */}
            <Card
              size="small"
              style={{ marginBottom: 12 }}
              bodyStyle={{ padding: 12 }}
              title="游资动向（龙虎榜席位）"
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  东财龙虎榜买卖席位明细
                </Text>
              }
            >
              <YouziFlow youzi={snap.youzi} onOpenStock={openStock} />
            </Card>

            {/* 股票热度榜（V1.009.2） */}
            <Card
              size="small"
              style={{ marginBottom: 12 }}
              bodyStyle={{ padding: 12 }}
              title="股票热度榜"
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {snap.hot?.available ? snap.hot.source : '需先完成历史数据重建'}
                </Text>
              }
            >
              <HotList hot={snap.hot} onOpenStock={openStock} />
            </Card>

            {/* 个股明细 */}
            <Card size="small" style={{ marginBottom: 12 }} bodyStyle={{ paddingTop: 0 }}>
              <Tabs
                items={[
                  {
                    key: 'zt',
                    label: `涨停 ${zt.count || 0}`,
                    children: (
                      <Table
                        size="small"
                        rowKey="code"
                        dataSource={zt.stocks || []}
                        columns={ztColumns}
                        pagination={{ pageSize: 15, size: 'small', showSizeChanger: false }}
                        scroll={{ x: 1160 }}
                      />
                    ),
                  },
                  {
                    key: 'zb',
                    label: `炸板 ${zb.count || 0}`,
                    children: (
                      <Table
                        size="small"
                        rowKey="code"
                        dataSource={zb.stocks || []}
                        columns={zbColumns}
                        pagination={{ pageSize: 15, size: 'small', showSizeChanger: false }}
                        scroll={{ x: 800 }}
                      />
                    ),
                  },
                  {
                    key: 'dt',
                    label: `跌停 ${dtp.count || 0}`,
                    children: (
                      <Table
                        size="small"
                        rowKey="code"
                        dataSource={dtp.stocks || []}
                        columns={dtColumns}
                        pagination={{ pageSize: 15, size: 'small', showSizeChanger: false }}
                        scroll={{ x: 560 }}
                      />
                    ),
                  },
                  {
                    key: 'pp',
                    label: `昨日涨停今日表现 ${pp.count || 0}`,
                    children: (
                      <Table
                        size="small"
                        rowKey="code"
                        dataSource={pp.items || []}
                        columns={ppColumns}
                        pagination={{ pageSize: 15, size: 'small', showSizeChanger: false }}
                        scroll={{ x: 480 }}
                      />
                    ),
                  },
                ]}
              />
            </Card>
          </>
        )}
      </Spin>

      {/* 历史复盘弹窗 */}
      <Modal
        title={`历史每日复盘（${historyList.length}）`}
        open={historyOpen}
        onCancel={() => setHistoryOpen(false)}
        footer={null}
        width={840}
      >
        <Spin spinning={historyLoading}>
          <List
            size="small"
            dataSource={historyList}
            locale={{ emptyText: '暂无每日复盘记录' }}
            renderItem={(r) => (
              <List.Item
                actions={[
                  <Button
                    key="open"
                    type="link"
                    size="small"
                    onClick={() => {
                      setDate(dayjs(r.trade_date))
                      setHistoryOpen(false)
                    }}
                  >
                    查看
                  </Button>,
                  <Popconfirm
                    key="del"
                    title="删除该日复盘？"
                    onConfirm={() => handleDelete(r.id)}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Button type="link" size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space wrap>
                      <Text strong>{r.trade_date}</Text>
                      {r.has_manual && <Tag color="blue">手写 {r.manual_len}字</Tag>}
                      {r.has_ai && <Tag color="purple">AI 点评</Tag>}
                      {!r.has_manual && !r.has_ai && <Tag>仅数据</Tag>}
                    </Space>
                  }
                  description={
                    <Space size={12} wrap style={{ fontSize: 12 }}>
                      <span>
                        涨停 <b style={{ color: RED }}>{r.limit_up ?? '-'}</b>
                      </span>
                      <span>
                        跌停 <b style={{ color: GREEN }}>{r.limit_down ?? '-'}</b>
                      </span>
                      <span>
                        炸板 <b>{r.broken ?? '-'}</b>
                      </span>
                      <span>
                        炸板率 <b>{r.broken_rate == null ? '-' : `${r.broken_rate}%`}</b>
                      </span>
                      <span>
                        最高连板 <b>{r.max_lbc ?? '-'}</b>
                      </span>
                      <Text type="secondary">{r.updated_at}</Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        </Spin>
      </Modal>

      {/* 情绪趋势弹窗 */}
      <Modal
        title={`情绪趋势（近 ${trendData?.days || trendDays} 个交易日，截至 ${dateStr}）`}
        open={trendOpen}
        onCancel={() => setTrendOpen(false)}
        footer={null}
        width={880}
      >
        <Space style={{ marginBottom: 10 }} wrap>
          {[10, 20, 30, 60].map((d) => (
            <Button
              key={d}
              size="small"
              type={trendDays === d ? 'primary' : 'default'}
              onClick={() => openTrend(d)}
            >
              近 {d} 日
            </Button>
          ))}
          <Text type="secondary" style={{ fontSize: 12 }}>
            本地重建缓存 {trendSrc.cache} 日
            {trendSrc.em ? `｜东财实时池 ${trendSrc.em} 日` : ''}
          </Text>
        </Space>
        {trendData?.note && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 10 }}
            message={trendData.note}
          />
        )}
        <Spin spinning={trendLoading}>
          {trendData?.items?.length ? (
            <>
              <Card size="small" title="涨停-炸板成交金额分析图" style={{ marginBottom: 10 }}>
                <ReactECharts option={trendOptions.amount} style={{ height: 240 }} notMerge />
              </Card>
              <Card size="small" title="涨停-炸板-跌停家数分析图" style={{ marginBottom: 10 }}>
                <ReactECharts option={trendOptions.count} style={{ height: 240 }} notMerge />
              </Card>
              <Card size="small" title="炸板金额率-炸板率分析图" style={{ marginBottom: 10 }}>
                <ReactECharts option={trendOptions.rate} style={{ height: 240 }} notMerge />
              </Card>
              <Card size="small" title="首板-连板家数分析图" style={{ marginBottom: 10 }}>
                <ReactECharts option={trendOptions.structure} style={{ height: 240 }} notMerge />
              </Card>
              <Card size="small" title="连板高度分析图" style={{ marginBottom: 10 }}>
                <ReactECharts option={trendOptions.ladder} style={{ height: 220 }} notMerge />
              </Card>
              <Card size="small" title="昨日涨停股今日表现分析图">
                <ReactECharts option={trendOptions.prevlu} style={{ height: 240 }} notMerge />
              </Card>
            </>
          ) : (
            <Empty description="暂无趋势数据" />
          )}
        </Spin>
      </Modal>

      {/* 历史数据重建（V1.009.1）：把"只能实时获取"的数据补成历史 */}
      <Modal
        title="历史数据重建"
        open={rebuildOpen}
        onCancel={() => setRebuildOpen(false)}
        footer={
          <Space>
            <Button onClick={() => setRebuildOpen(false)}>关闭</Button>
            <Button
              type="primary"
              icon={<DatabaseOutlined />}
              loading={rebuild?.status === 'running'}
              disabled={rebuild?.status === 'running'}
              onClick={handleRebuild}
            >
              开始重建
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          <Alert
            type="info"
            showIcon
            message="把「只能实时获取」的数据补成历史"
            description={
              <span style={{ fontSize: 12 }}>
                涨跌家数分布、两市成交额、概念板块排行只反映当下，无法按历史日期查询；
                涨停池回溯窗口也只有约 15 个交易日。重建会用全市场约 5900 只个股的日K回溯聚合，
                生成每个交易日的涨跌分布、涨停跌停结构、连板梯队、概念板块表现与周期阶段，
                并由日K计算严重异动提醒、抓取当日龙虎榜生成游资动向，全部写入本地缓存，
                之后查看历史日期即为秒级读取。热度榜的「榜单」接口同样只有当前值，但东财人气榜
                的「个股」接口有逐日全市场名次 —— 重建时会对候选股批量抓取（约 1 分钟），
                历史日热度榜由此离线组装；仅同花顺的热度值与上榜原因无历史源。
              </span>
            }
          />
          <Space wrap>
            <Text>重建范围</Text>
            <InputNumber
              min={5}
              max={250}
              value={rebuildDays}
              onChange={(v) => setRebuildDays(v || 60)}
              addonAfter="个交易日"
              disabled={rebuild?.status === 'running'}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              约 1~3 分钟
            </Text>
          </Space>
          {rebuild && rebuild.status !== 'idle' && (
            <>
              <Progress
                percent={rebuild.progress || 0}
                status={
                  rebuild.status === 'error'
                    ? 'exception'
                    : rebuild.status === 'done'
                      ? 'success'
                      : 'active'
                }
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {rebuild.message || ''}
                {rebuild.status === 'done' ? `｜已写入 ${rebuild.saved} 个交易日` : ''}
              </Text>
              {rebuild.status === 'error' && (
                <Text type="danger" style={{ fontSize: 12 }}>
                  {rebuild.error}
                </Text>
              )}
            </>
          )}
        </Space>
      </Modal>

      {/* 个股日线弹窗（V1.009.4） */}
      <StockKlineModal
        open={stockModal.open}
        code={stockModal.code}
        name={stockModal.name}
        days={stockModal.days}
        loading={stockModal.loading}
        data={stockModal.data}
        onDays={changeStockDays}
        onClose={() => setStockModal((st) => ({ ...st, open: false }))}
      />
    </div>
  )
}
