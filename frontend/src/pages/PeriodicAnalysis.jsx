import { useEffect, useState, useCallback, useMemo } from 'react'
import dayjs from 'dayjs'
import {
  Card,
  Row,
  Col,
  Statistic,
  Select,
  Space,
  Button,
  DatePicker,
  Table,
  Tag,
  Empty,
  Spin,
  Typography,
  Alert,
  Modal,
  Divider,
  List,
  Tooltip,
} from 'antd'
import {
  CalendarOutlined,
  ReloadOutlined,
  TrophyOutlined,
  RobotOutlined,
  BulbOutlined,
  WarningOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { getPeriodStats, getScoreTrend, periodAiAnalysis } from '../api'

const { RangePicker } = DatePicker

const INSTRUMENT_TYPES = ['A股', '商品期货', '数字货币']

// 预设时间段（返回 dayjs 数组）
const PRESETS = [
  { key: 'this_week', label: '本周', calc: () => calcWeek() },
  { key: 'this_month', label: '本月', calc: () => calcMonth(0) },
  { key: 'last_month', label: '上月', calc: () => calcMonth(-1) },
  { key: 'three_month', label: '近3月', calc: () => calcThreeMonth() },
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

export default function PeriodicAnalysis() {
  const [range, setRange] = useState(() => calcMonth(0))
  const [instrumentType, setInstrumentType] = useState(undefined)
  const [stats, setStats] = useState(null)
  const [scores, setScores] = useState(null)
  const [loading, setLoading] = useState(false)
  const [aiResult, setAiResult] = useState(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiOpen, setAiOpen] = useState(false)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const params = {
        // 用本地时间传参（toISOString 会转 UTC 减 8 小时，导致结束日期提前一天）
        start: dayjs(range[0]).startOf('day').format('YYYY-MM-DD HH:mm:ss'),
        end: dayjs(range[1]).endOf('day').format('YYYY-MM-DD HH:mm:ss'),
      }
      if (instrumentType) params.instrument_type = instrumentType
      const [s, sc] = await Promise.all([getPeriodStats(params), getScoreTrend(params)])
      setStats(s)
      setScores(sc)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }, [range, instrumentType])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  // AI 阶段性复盘分析
  const handleAiAnalyze = async () => {
    setAiLoading(true)
    try {
      const result = await periodAiAnalysis({
        start: dayjs(range[0]).startOf('day').format('YYYY-MM-DD HH:mm:ss'),
        end: dayjs(range[1]).endOf('day').format('YYYY-MM-DD HH:mm:ss'),
        instrument_type: instrumentType,
      })
      setAiResult(result)
      setAiOpen(true)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setAiLoading(false)
    }
  }

  const summary = stats?.summary
  const metrics = stats?.metrics
  const avgScore = scores?.avg_score

  // 累计盈亏折线图
  const cumPnlOption = useMemo(() => {
    const days = stats?.by_day || []
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 20, top: 30, bottom: 30 },
      xAxis: { type: 'category', data: days.map((d) => d.date) },
      yAxis: { type: 'value', name: '盈亏' },
      series: [
        {
          name: '累计盈亏',
          type: 'line',
          data: days.map((d) => d.cumulative_pnl),
          smooth: true,
          areaStyle: { opacity: 0.15 },
          lineStyle: { width: 2 },
          itemStyle: {
            color: days.map((d) => (d.cumulative_pnl >= 0 ? '#cf1322' : '#3f8600')),
          },
        },
      ],
    }
  }, [stats])

  // 评分趋势图
  const scoreTrendOption = useMemo(() => {
    const trend = scores?.score_trend || []
    const withScore = trend.filter((t) => t.score !== null && t.score !== undefined)
    if (withScore.length === 0) return null
    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const p = withScore[params[0]?.dataIndex]
          return `${p?.date} ${p?.instrument_name}<br/>评分: ${p?.score} / 盈亏: ${p?.pnl ?? '-'}`
        },
      },
      grid: { left: 40, right: 20, top: 30, bottom: 30 },
      xAxis: { type: 'category', data: withScore.map((t) => `${t.date} ${t.instrument_name}`) },
      yAxis: { type: 'value', name: '评分', min: 0, max: 100 },
      series: [
        {
          name: '评分',
          type: 'line',
          data: withScore.map((t) => t.score),
          smooth: true,
          markLine: { data: [{ yAxis: 60, name: '及格线' }], lineStyle: { type: 'dashed' } },
          itemStyle: { color: '#534AB7' },
        },
      ],
    }
  }, [scores])

  // 维度雷达图
  const radarOption = useMemo(() => {
    const dims = scores?.dimension_avg || []
    if (dims.length === 0) return null
    return {
      tooltip: {},
      radar: {
        indicator: dims.map((d) => ({ name: d.name, max: 100 })),
        radius: '65%',
      },
      series: [
        {
          type: 'radar',
          data: [{ value: dims.map((d) => d.score), name: '平均评分' }],
          areaStyle: { opacity: 0.2 },
          itemStyle: { color: '#1D9E75' },
        },
      ],
    }
  }, [scores])

  // 品种分布饼图
  const instrumentOption = useMemo(() => {
    const rows = stats?.by_instrument || {}
    const data = Object.entries(rows).map(([name, s]) => ({
      name,
      value: Math.max(0, Math.abs(s.total_pnl)),
    }))
    if (data.length === 0) return null
    return {
      tooltip: { trigger: 'item' },
      series: [
        {
          type: 'pie',
          radius: ['40%', '70%'],
          data,
          label: { formatter: '{b}: {d}%' },
        },
      ],
    }
  }, [stats])

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {/* 筛选区 */}
      <Card>
        <Space wrap size={12}>
          <Space>
            {PRESETS.map((p) => (
              <Button
                key={p.key}
                size="small"
                icon={<CalendarOutlined />}
                onClick={() => setRange(p.calc())}
              >
                {p.label}
              </Button>
            ))}
          </Space>
          <RangePicker
            value={range}
            onChange={(v) => v && setRange(v)}
            allowClear={false}
            format="YYYY-MM-DD"
          />
          <Select
            allowClear
            placeholder="全部品种"
            style={{ width: 140 }}
            value={instrumentType}
            onChange={setInstrumentType}
            options={INSTRUMENT_TYPES.map((t) => ({ value: t, label: t }))}
          />
          <Button icon={<ReloadOutlined />} onClick={loadAll} loading={loading} />
          <Button
            type="primary"
            icon={<RobotOutlined />}
            onClick={handleAiAnalyze}
            loading={aiLoading}
          >
            AI 阶段分析
          </Button>
        </Space>
      </Card>

      {/* 统计卡片 */}
      <Row gutter={16}>
        <Col span={3}>
          <Card size="small">
            <Statistic title="交易笔数" value={summary?.count ?? 0} />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic
              title="胜率"
              value={((summary?.win_rate ?? 0) * 100).toFixed(1)}
              suffix="%"
            />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic
              title="总盈亏"
              value={summary?.total_pnl ?? 0}
              precision={2}
              styles={{
                content: {
                  color: (summary?.total_pnl ?? 0) >= 0 ? '#cf1322' : '#3f8600',
                },
              }}
              prefix={(summary?.total_pnl ?? 0) >= 0 ? '+' : ''}
            />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic
              title="盈亏比"
              value={summary?.profit_factor ?? 0}
              precision={2}
            />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic title="平均评分" value={avgScore ?? '-'} precision={1} />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic
              title="平均每笔盈亏"
              value={summary?.avg_pnl ?? 0}
              precision={2}
              styles={{
                content: {
                  color: (summary?.avg_pnl ?? 0) >= 0 ? '#cf1322' : '#3f8600',
                },
              }}
              prefix={(summary?.avg_pnl ?? 0) >= 0 ? '+' : ''}
            />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic
              title={
                <span>
                  阶段期初资金
                  <Tooltip title="阶段首日交易开始前的账户权益：初始资金 + 出入金 + 此前全部已平仓交易盈亏">
                    <QuestionCircleOutlined
                      style={{ marginLeft: 4, color: '#999', fontSize: 12 }}
                    />
                  </Tooltip>
                </span>
              }
              value={metrics?.start_balance ?? '-'}
              prefix={metrics?.start_balance != null ? '¥' : ''}
              precision={2}
            />
          </Card>
        </Col>
        <Col span={3}>
          <Card size="small">
            <Statistic
              title={
                <span>
                  阶段期末资金
                  <Tooltip title="阶段期初资金 + 该阶段交易盈亏（含手续费按平仓价计，与阶段总收益率口径一致）">
                    <QuestionCircleOutlined
                      style={{ marginLeft: 4, color: '#999', fontSize: 12 }}
                    />
                  </Tooltip>
                </span>
              }
              value={metrics?.end_balance ?? '-'}
              prefix={metrics?.end_balance != null ? '¥' : ''}
              precision={2}
              styles={{
                content: {
                  color:
                    metrics?.start_balance != null &&
                    metrics?.end_balance != null &&
                    metrics.end_balance >= metrics.start_balance
                      ? '#cf1322'
                      : '#3f8600',
                },
              }}
            />
          </Card>
        </Col>
      </Row>

      {/* 阶段高级指标（需求3） */}
      <Card
        size="small"
        title={
          <Space>
            阶段绩效指标
            {stats?.metrics?.has_capital === false && (
              <Typography.Text type="warning" style={{ fontSize: 12, fontWeight: 'normal' }}>
                未设置初始资金，收益率/回撤/夏普等指标不可用（请在交易计划页录入账户资金流水）
              </Typography.Text>
            )}
          </Space>
        }
      >
        <Row gutter={16}>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="平均单笔盈亏比"
                value={metrics?.avg_pl_ratio ?? '-'}
                precision={2}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="日平均仓位"
                value={metrics?.avg_daily_position_pct ?? '-'}
                suffix={metrics?.avg_daily_position_pct != null ? '%' : ''}
                precision={2}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="阶段总收益率"
                value={metrics?.total_return_pct ?? '-'}
                suffix={metrics?.total_return_pct != null ? '%' : ''}
                precision={2}
                styles={{
                  content: {
                    color:
                      metrics?.total_return_pct != null && metrics.total_return_pct >= 0
                        ? '#cf1322'
                        : '#3f8600',
                  },
                }}
                prefix={metrics?.total_return_pct != null && metrics.total_return_pct >= 0 ? '+' : ''}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="阶段最大回撤"
                value={metrics?.max_drawdown_pct ?? '-'}
                suffix={metrics?.max_drawdown_pct != null ? '%' : ''}
                precision={2}
                styles={{
                  content: {
                    color: metrics?.max_drawdown_pct != null ? '#3f8600' : undefined,
                  },
                }}
                prefix={metrics?.max_drawdown_pct != null ? '-' : ''}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="最大周度回撤"
                value={metrics?.max_weekly_drawdown_pct ?? '-'}
                suffix={metrics?.max_weekly_drawdown_pct != null ? '%' : ''}
                precision={2}
                styles={{
                  content: {
                    color: metrics?.max_weekly_drawdown_pct != null ? '#3f8600' : undefined,
                  },
                }}
                prefix={metrics?.max_weekly_drawdown_pct != null ? '-' : ''}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="卡玛比率"
                value={metrics?.calmar_ratio ?? '-'}
                precision={2}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="夏普比率"
                value={metrics?.sharpe_ratio ?? '-'}
                precision={2}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="总手数"
                value={summary?.total_volume ?? 0}
                precision={2}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="手续费汇总"
                value={summary?.total_fee ?? 0}
                precision={2}
              />
            </Card>
          </Col>
        </Row>
      </Card>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : summary?.count === 0 ? (
        <Empty description="该时间段暂无交易记录" />
      ) : (
        <>
          {/* 图表区 */}
          <Row gutter={16}>
            <Col span={12}>
              <Card title="累计盈亏趋势" size="small">
                <ReactECharts option={cumPnlOption} style={{ height: 260 }} />
              </Card>
            </Col>
            <Col span={12}>
              <Card title="评分趋势" size="small">
                {scoreTrendOption ? (
                  <ReactECharts option={scoreTrendOption} style={{ height: 260 }} />
                ) : (
                  <Empty description="该时间段暂无评分数据（先对交易执行AI分析）" />
                )}
              </Card>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Card title="各维度评分对比" size="small">
                {radarOption ? (
                  <ReactECharts option={radarOption} style={{ height: 280 }} />
                ) : (
                  <Empty description="暂无评分数据" />
                )}
              </Card>
            </Col>
            <Col span={12}>
              <Card title="品种盈亏分布" size="small">
                {instrumentOption ? (
                  <ReactECharts option={instrumentOption} style={{ height: 280 }} />
                ) : (
                  <Empty description="暂无数据" />
                )}
              </Card>
            </Col>
          </Row>

          {/* 常见问题 + 交易明细 */}
          <Card title="常见问题提示" size="small">
            {scores?.common_issues?.length > 0 ? (
              <Space wrap>
                {scores.common_issues.map((c) => (
                  <Tag key={c.keyword} color="volcano">
                    <TrophyOutlined /> {c.keyword} × {c.count}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Typography.Text type="secondary">
                暂无问题统计（对交易执行AI分析后自动生成）
              </Typography.Text>
            )}
          </Card>

          <Card title="期间交易明细" size="small">
            <Table
              rowKey="trade_id"
              size="small"
              dataSource={scores?.score_trend || []}
              pagination={{ pageSize: 10, showSizeChanger: false }}
              columns={[
                { title: '日期', dataIndex: 'date', width: 110 },
                {
                  title: '品种',
                  dataIndex: 'instrument_name',
                  render: (v, r) => (
                    <Space>
                      <Tag color={r.instrument_type === 'A股' ? 'blue' : r.instrument_type === '商品期货' ? 'purple' : 'gold'}>
                        {r.instrument_type}
                      </Tag>
                      {v || '-'}
                    </Space>
                  ),
                },
                {
                  title: '评分',
                  dataIndex: 'score',
                  width: 90,
                  render: (v) =>
                    v !== null && v !== undefined ? (
                      <Tag color={v >= 80 ? 'green' : v >= 60 ? 'orange' : 'red'}>{v}</Tag>
                    ) : (
                      '-'
                    ),
                },
                {
                  title: '盈亏',
                  dataIndex: 'pnl',
                  width: 110,
                  render: (v) =>
                    v === null || v === undefined ? (
                      '-'
                    ) : (
                      <Typography.Text type={v >= 0 ? 'danger' : 'success'} strong>
                        {v >= 0 ? '+' : ''}
                        {v}
                      </Typography.Text>
                    ),
                },
              ]}
            />
          </Card>
        </>
      )}

      {/* AI 阶段性复盘结果 */}
      <Modal
        title="AI 阶段性复盘"
        open={aiOpen}
        onCancel={() => setAiOpen(false)}
        footer={
          <Button type="primary" onClick={() => setAiOpen(false)}>
            关闭
          </Button>
        }
        width={760}
      >
        {aiResult && (
          <div style={{ maxHeight: '70vh', overflowY: 'auto', paddingRight: 8 }}>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={
                <Space wrap>
                  本次分析：{aiResult.analyzed_count} 笔
                  {aiResult.stats && (
                    <>
                      <Tag>胜率 {(aiResult.stats.win_rate * 100).toFixed(1)}%</Tag>
                      <Tag color={(aiResult.stats.total_pnl ?? 0) >= 0 ? 'red' : 'green'}>
                        合计盈亏{' '}
                        {(aiResult.stats.total_pnl ?? 0) >= 0 ? '+' : ''}
                        {aiResult.stats.total_pnl ?? 0}
                      </Tag>
                      <Tag>总手续费 {aiResult.stats.total_fee ?? 0}</Tag>
                    </>
                  )}
                </Space>
              }
              description={aiResult.summary || '（AI 未生成总结）'}
            />

            <Divider orientation="left" plain>
              <TrophyOutlined style={{ color: '#f0b90b' }} /> 表现最好的交易
            </Divider>
            <List
              size="small"
              dataSource={aiResult.best_trades || []}
              renderItem={(item) => <List.Item style={{ fontSize: 13 }}>{item}</List.Item>}
              locale={{ emptyText: <Typography.Text type="secondary">暂无</Typography.Text> }}
            />

            <Divider orientation="left" plain>
              <WarningOutlined style={{ color: '#cf1322' }} /> 表现最差的交易
            </Divider>
            <List
              size="small"
              dataSource={aiResult.worst_trades || []}
              renderItem={(item) => <List.Item style={{ fontSize: 13 }}>{item}</List.Item>}
              locale={{ emptyText: <Typography.Text type="secondary">暂无</Typography.Text> }}
            />

            <Divider orientation="left" plain>
              反复出现的行为模式
            </Divider>
            <List
              size="small"
              dataSource={aiResult.patterns || []}
              renderItem={(item) => <List.Item style={{ fontSize: 13 }}>{item}</List.Item>}
              locale={{ emptyText: <Typography.Text type="secondary">暂无</Typography.Text> }}
            />

            <Divider orientation="left" plain>
              <WarningOutlined style={{ color: '#fa8c16' }} /> 反复出现的问题
            </Divider>
            {aiResult.recurring_issues?.length ? (
              aiResult.recurring_issues.map((iss, i) => (
                <div
                  key={i}
                  style={{
                    border: '1px solid #f0f0f0',
                    borderRadius: 6,
                    padding: '8px 12px',
                    marginBottom: 8,
                  }}
                >
                  <Space>
                    <Tag color="volcano">
                      {iss.issue} × {iss.count}
                    </Tag>
                  </Space>
                  <div style={{ fontSize: 13, color: '#595959', marginTop: 4 }}>
                    <Typography.Text type="secondary">改进建议：</Typography.Text>
                    {iss.suggestion}
                  </div>
                </div>
              ))
            ) : (
              <Typography.Text type="secondary">暂无</Typography.Text>
            )}

            <Divider orientation="left" plain>
              <BulbOutlined style={{ color: '#1D9E75' }} /> 系统 / 策略优化建议
            </Divider>
            <List
              size="small"
              dataSource={aiResult.system_feedback || []}
              renderItem={(item) => (
                <List.Item style={{ fontSize: 13 }}>
                  <Typography.Text style={{ color: '#1D9E75' }}>◆ {item}</Typography.Text>
                </List.Item>
              )}
              locale={{ emptyText: <Typography.Text type="secondary">暂无</Typography.Text> }}
            />

            <Divider orientation="left" plain>
              下一周期行动计划
            </Divider>
            <List
              size="small"
              dataSource={aiResult.next_actions || []}
              renderItem={(item, i) => (
                <List.Item style={{ fontSize: 13 }}>
                  <Space>
                    <Tag color="blue">{i + 1}</Tag>
                    {item}
                  </Space>
                </List.Item>
              )}
              locale={{ emptyText: <Typography.Text type="secondary">暂无</Typography.Text> }}
            />
          </div>
        )}
      </Modal>
    </Space>
  )
}
