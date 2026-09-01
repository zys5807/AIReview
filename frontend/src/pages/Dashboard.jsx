import { useEffect, useState, useCallback } from 'react'
import dayjs from 'dayjs'
import {
  Card,
  Table,
  Tag,
  Space,
  Button,
  Popconfirm,
  message,
  Typography,
  Tabs,
  Modal,
  Spin,
  Image,
  DatePicker,
} from 'antd'
import {
  DeleteOutlined,
  ReloadOutlined,
  RobotOutlined,
  EditOutlined,
  PictureOutlined,
  DownloadOutlined,
  ClearOutlined,
  ImportOutlined,
} from '@ant-design/icons'
import {
  listTrades,
  deleteTrade,
  analyzeTrade,
  listReports,
  listScreenshots,
  cleanupOrphanScreenshots,
} from '../api'
import ReportView from '../components/ReportView'
import TradeEditModal from '../components/TradeEditModal'
import ImportModal from '../components/ImportModal'
import { API_BASE } from '../api/client'

const { RangePicker } = DatePicker

// 时间段筛选（本周/本月/三个月）
const TIME_PRESETS = [
  { key: 'this_week', label: '本周', calc: () => calcWeek() },
  { key: 'this_month', label: '本月', calc: () => calcMonth(0) },
  { key: 'three_month', label: '三个月', calc: () => calcThreeMonth() },
]
function calcWeek() {
  const now = dayjs()
  const day = now.day() || 7
  return [now.startOf('day').subtract(day - 1, 'day'), now]
}
function calcMonth(offset) {
  const now = dayjs()
  if (offset === 0) return [now.startOf('month'), now]
  const start = now.startOf('month').add(offset, 'month')
  const end = now.startOf('month').add(offset + 1, 'month').subtract(1, 'day').endOf('day')
  return [start, end]
}
function calcThreeMonth() {
  const now = dayjs()
  return [now.subtract(3, 'month').startOf('day'), now]
}

const shotUrl = (shot) =>
  shot ? `${API_BASE}/uploads/${shot.stored_path.replace('uploads/', '')}` : null

const DIRECTION_TAG = {
  long: { color: 'red', label: '做多' },
  short: { color: 'green', label: '做空' },
}

const TYPE_COLOR = {
  'A股': 'blue',
  '商品期货': 'purple',
  '数字货币': 'gold',
}

const TABS = [
  { key: 'all', label: '全部', type: undefined },
  { key: 'A股', label: 'A股', type: 'A股' },
  { key: '商品期货', label: '商品期货', type: '商品期货' },
  { key: '数字货币', label: '数字货币', type: '数字货币' },
]

export default function Dashboard() {
  const [data, setData] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [activeTab, setActiveTab] = useState('all')
  const [loading, setLoading] = useState(false)

  // AI 分析弹窗状态
  const [reportOpen, setReportOpen] = useState(false)
  const [report, setReport] = useState(null)
  const [reportLoading, setReportLoading] = useState(false)
  const [currentTradeId, setCurrentTradeId] = useState(null)

  // 编辑弹窗 + 截图查看状态
  const [editingTrade, setEditingTrade] = useState(null)
  const [shotView, setShotView] = useState(null) // { url, filename }
  const [screenshotMap, setScreenshotMap] = useState({})

  // 时间段筛选（仅过滤交易列表，不做统计）
  const [timeKey, setTimeKey] = useState('all') // all/this_week/this_month/three_month/custom
  const [customRange, setCustomRange] = useState(null)

  // 交割单导入
  const [importOpen, setImportOpen] = useState(false)

  const activeType = TABS.find((t) => t.key === activeTab)?.type

  // 加载全部截图，用于 id -> url 映射
  useEffect(() => {
    listScreenshots()
      .then((list) => {
        const m = {}
        list.forEach((s) => {
          m[s.id] = s
        })
        setScreenshotMap(m)
      })
      .catch(() => {})
  }, [])

  // 交易系统名称映射（表格里显示）
  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, page_size: pageSize }
      if (activeType) params.instrument_type = activeType
      // 时间段筛选：只过滤交易列表展示
      if (timeKey === 'custom' && customRange) {
        params.start = dayjs(customRange[0]).startOf('day').format('YYYY-MM-DD HH:mm:ss')
        params.end = dayjs(customRange[1]).endOf('day').format('YYYY-MM-DD HH:mm:ss')
      } else if (timeKey !== 'all') {
        const preset = TIME_PRESETS.find((p) => p.key === timeKey)
        if (preset) {
          const [s, e] = preset.calc()
          params.start = dayjs(s).startOf('day').format('YYYY-MM-DD HH:mm:ss')
          params.end = dayjs(e).endOf('day').format('YYYY-MM-DD HH:mm:ss')
        }
      }
      const res = await listTrades(params)
      setData(res.items)
      setTotal(res.total)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, activeType, timeKey, customRange])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleDelete = async (id) => {
    try {
      const res = await deleteTrade(id)
      message.success(res?.message || '已删除')
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  // 清理无用截图（孤儿截图）
  const handleCleanupOrphans = async () => {
    try {
      const res = await cleanupOrphanScreenshots(7)
      message.success(res?.message || '清理完成')
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  // 打开 AI 分析：有历史报告直接展示，没有则发起分析
  const handleAnalyze = async (trade) => {
    setCurrentTradeId(trade.id)
    setReport(null)
    setReportOpen(true)
    setReportLoading(true)
    try {
      const reports = await listReports(trade.id)
      if (reports && reports.length > 0) {
        setReport(reports[0])
      } else {
        const newReport = await analyzeTrade(trade.id)
        setReport(newReport)
      }
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setReportLoading(false)
    }
  }

  const handleReanalyze = async () => {
    if (!currentTradeId) return
    setReportLoading(true)
    try {
      const newReport = await analyzeTrade(currentTradeId)
      setReport(newReport)
      message.success('分析完成')
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setReportLoading(false)
    }
  }

  const columns = [
    {
      title: '品种',
      key: 'instrument',
      render: (_, r) => (
        <Space orientation="vertical" size={0}>
          <Space size={6}>
            <Tag color={TYPE_COLOR[r.instrument_type]}>{r.instrument_type}</Tag>
            <Typography.Text strong>
              {r.instrument_name || r.instrument_code || '-'}
            </Typography.Text>
            {(r.remaining_volume || 0) > 0 && (
              <Tag color="processing">持仓中·{r.remaining_volume}</Tag>
            )}
          </Space>
          {r.instrument_code && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.instrument_code} {r.exchange && `· ${r.exchange}`}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '方向',
      dataIndex: 'direction',
      render: (v) => <Tag color={DIRECTION_TAG[v].color}>{DIRECTION_TAG[v].label}</Tag>,
    },
    {
      title: '入场',
      key: 'entry',
      render: (_, r) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text>{new Date(r.entry_time).toLocaleString()}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            @ {r.entry_price}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '出场',
      key: 'exit',
      render: (_, r) =>
        r.exit_time ? (
          <Space orientation="vertical" size={0}>
            <Typography.Text>{new Date(r.exit_time).toLocaleString()}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              @ {r.exit_price}
            </Typography.Text>
          </Space>
        ) : (
          <Typography.Text type="secondary">— 未平仓 —</Typography.Text>
        ),
    },
    { title: '手数', dataIndex: 'volume' },
    { title: '止损', dataIndex: 'stop_loss', render: (v) => v ?? '-' },
    {
      title: '盈亏',
      dataIndex: 'pnl',
      render: (v, r) => {
        const net = v === null || v === undefined ? null : v - (r.fee || 0)
        if (net === null) return '-'
        return (
          <Typography.Text type={net >= 0 ? 'danger' : 'success'} strong>
            {net >= 0 ? '+' : ''}
            {net}
          </Typography.Text>
        )
      },
    },
    {
      title: '收益率',
      key: 'returnRate',
      width: 90,
      render: (_, r) => {
        const net = r.pnl === null || r.pnl === undefined ? null : r.pnl - (r.fee || 0)
        if (net === null || !r.invested_capital) return '-'
        const rate = (net / r.invested_capital) * 100
        return (
          <Typography.Text type={rate >= 0 ? 'danger' : 'success'} strong>
            {rate >= 0 ? '+' : ''}
            {rate.toFixed(2)}%
          </Typography.Text>
        )
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_, r) => {
        // 收集该交易的所有截图（优先多图关联 screenshots，兼容旧 screenshot_id）
        const shots = (r.screenshots?.length
          ? r.screenshots
          : r.screenshot_id
            ? [{ screenshot_id: r.screenshot_id }]
            : []
        )
          .map((s) => {
            const shot = s.stored_path ? s : screenshotMap[s.screenshot_id]
            if (!shot) return null
            return {
              url: shotUrl(shot),
              role: s.role || '后续',
              filename: s.filename || shot.filename || '',
            }
          })
          .filter(Boolean)
        return (
          <Space>
            <Button
              type="link"
              size="small"
              icon={<RobotOutlined />}
              onClick={() => handleAnalyze(r)}
            >
              分析
            </Button>
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => setEditingTrade(r)}
            >
              编辑
            </Button>
            {shots.length > 0 && (
              <Button
                type="link"
                size="small"
                icon={<PictureOutlined />}
                onClick={() => setShotView({ shots })}
              >
                截图({shots.length})
              </Button>
            )}
            <Popconfirm title="确定删除该记录？" onConfirm={() => handleDelete(r.id)}>
              <Button type="text" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  return (
    <Card
      title="交易记录"
      extra={
        <Space>
          <Popconfirm
            title="清理未被任何交易引用的截图（保留近7天内的）？"
            onConfirm={handleCleanupOrphans}
            okText="清理"
            cancelText="取消"
          >
            <Button icon={<ClearOutlined />}>清理无用截图</Button>
          </Popconfirm>
          <Button
            icon={<DownloadOutlined />}
            onClick={() => {
              // 备份全部数据（数据库+截图）
              const url = `${API_BASE}/api/backup/download`
              if (url.startsWith('http')) {
                window.open(url, '_blank')
              } else {
                const a = document.createElement('a')
                a.href = url
                a.download = 'backup.zip'
                a.click()
              }
            }}
          >
            备份数据
          </Button>
          <Button icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>
            导入交割单
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              fetchData()
            }}
          />
        </Space>
      }
    >
      {/* 时间段筛选：仅过滤交易列表展示，不做统计 */}
      <Space wrap size={8} style={{ marginBottom: 12 }}>
        <Button
          size="small"
          type={timeKey === 'all' ? 'primary' : 'default'}
          onClick={() => {
            setTimeKey('all')
            setPage(1)
          }}
        >
          全部
        </Button>
        {TIME_PRESETS.map((p) => (
          <Button
            key={p.key}
            size="small"
            type={timeKey === p.key ? 'primary' : 'default'}
            onClick={() => {
              setTimeKey(p.key)
              setPage(1)
            }}
          >
            {p.label}
          </Button>
        ))}
        <RangePicker
          size="small"
          value={timeKey === 'custom' ? customRange : null}
          onChange={(v) => {
            if (v) {
              setCustomRange(v)
              setTimeKey('custom')
              setPage(1)
            }
          }}
          format="YYYY-MM-DD"
          placeholder={['开始日期', '结束日期']}
        />
        {timeKey !== 'all' && (
          <Button
            size="small"
            type="link"
            onClick={() => {
              setTimeKey('all')
              setCustomRange(null)
              setPage(1)
            }}
          >
            清除筛选
          </Button>
        )}
      </Space>

      <Tabs
        activeKey={activeTab}
        onChange={(key) => {
          setActiveTab(key)
          setPage(1)
        }}
        items={TABS.map((t) => ({ key: t.key, label: t.label }))}
      />

      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={data}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
      />

      <Modal
        title="AI 复盘分析"
        open={reportOpen}
        onCancel={() => setReportOpen(false)}
        footer={
          <Space>
            <Button onClick={() => setReportOpen(false)}>关闭</Button>
            <Button
              type="primary"
              icon={<RobotOutlined />}
              loading={reportLoading}
              onClick={handleReanalyze}
            >
              重新分析
            </Button>
          </Space>
        }
        width={640}
      >
        {reportLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin size="large" />
            <div style={{ marginTop: 12, color: '#888' }}>
              正在调用 AI 分析交易，请稍候…
            </div>
          </div>
        ) : (
          <ReportView report={report} />
        )}
      </Modal>

      {/* 编辑交易弹窗 */}
      <TradeEditModal
        trade={editingTrade}
        screenshotMap={screenshotMap}
        open={!!editingTrade}
        onClose={() => setEditingTrade(null)}
        onSuccess={() => {
          fetchData()
        }}
      />

      {/* 截图预览弹窗（支持多张） */}
      <Modal
        title="交易截图"
        open={!!shotView}
        onCancel={() => setShotView(null)}
        footer={null}
        width={720}
      >
        {shotView?.shots?.map((s, i) => (
          <div key={i} style={{ marginBottom: 16 }}>
            <Tag color="blue" style={{ marginBottom: 6 }}>
              {s.role || `截图${i + 1}`}
            </Tag>
            <Image src={s.url} alt={`交易截图-${s.role}`} style={{ width: '100%' }} />
          </div>
        ))}
      </Modal>

      {/* 交割单导入 */}
      <ImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onSuccess={() => {
          fetchData()
        }}
      />
    </Card>
  )
}
