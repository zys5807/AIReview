import { useEffect } from 'react'
import { Form, Select, Space, Button, Row, Col, Typography, Divider } from 'antd'
import { MinusOutlined, PlusOutlined } from '@ant-design/icons'

// 周期等级（用于整体缩放）
export const TIMEFRAME_LEVELS = [
  '1分钟', '3分钟', '5分钟', '15分钟', '30分钟', '1小时', '4小时', '日线', '周线', '月线',
]

const FIELDS = [
  { key: 'trend_timeframe_used', label: '多空判断周期' },
  { key: 'direction_timeframe_used', label: '方向判断周期' },
  { key: 'entry_timeframe_used', label: '交易周期' },
]

const options = TIMEFRAME_LEVELS.map((t) => ({ value: t, label: t }))

/**
 * 本笔实际周期选择器：
 * - 选了交易系统后自动带出系统默认周期
 * - 可整体缩放（-2/-1/+1/+2档）或单独修改
 * - 留空 = 使用交易系统的默认周期
 */
export default function ActualPeriodSelector({ systems = [] }) {
  const form = Form.useFormInstance()
  const systemId = Form.useWatch('trading_system_id', form)

  // 切换/首次选择系统时，若未填写则带出系统默认周期
  useEffect(() => {
    if (!systemId) return
    const sys = systems.find((s) => s.id === systemId)
    if (!sys) return
    const patch = {}
    const cur = form.getFieldsValue(FIELDS.map((f) => f.key))
    if (!cur.trend_timeframe_used && sys.trend_timeframe)
      patch.trend_timeframe_used = sys.trend_timeframe
    if (!cur.direction_timeframe_used && sys.direction_timeframe)
      patch.direction_timeframe_used = sys.direction_timeframe
    if (!cur.entry_timeframe_used && sys.entry_timeframe)
      patch.entry_timeframe_used = sys.entry_timeframe
    if (Object.keys(patch).length) form.setFieldsValue(patch)
  }, [systemId, systems, form])

  // 整体缩放（按周期等级同步加减）
  const scale = (delta) => {
    const patch = {}
    for (const f of FIELDS) {
      const v = form.getFieldValue(f.key)
      if (!v) continue
      const idx = TIMEFRAME_LEVELS.indexOf(v)
      if (idx === -1) continue
      const ni = Math.max(0, Math.min(TIMEFRAME_LEVELS.length - 1, idx + delta))
      patch[f.key] = TIMEFRAME_LEVELS[ni]
    }
    if (Object.keys(patch).length) form.setFieldsValue(patch)
  }

  return (
    <div
      style={{
        marginTop: 8,
        padding: '8px 12px',
        background: '#fafafa',
        borderRadius: 6,
        border: '1px dashed #d9d9d9',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <Typography.Text strong style={{ fontSize: 13 }}>
          本笔实际周期
        </Typography.Text>
        <Space size={4}>
          <Button size="small" icon={<MinusOutlined />} onClick={() => scale(-2)}>
            缩小2档
          </Button>
          <Button size="small" icon={<MinusOutlined />} onClick={() => scale(-1)}>
            缩小1档
          </Button>
          <Button size="small" icon={<PlusOutlined />} onClick={() => scale(1)}>
            放大1档
          </Button>
          <Button size="small" icon={<PlusOutlined />} onClick={() => scale(2)}>
            放大2档
          </Button>
        </Space>
      </div>
      <Row gutter={12}>
        {FIELDS.map((f) => (
          <Col span={8} key={f.key}>
            <Form.Item name={f.key} label={f.label} style={{ marginBottom: 0 }}>
              <Select allowClear placeholder="= 系统默认" options={options} />
            </Form.Item>
          </Col>
        ))}
      </Row>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        短线交易可整体缩小档位（如日线/1小时/15分钟 → 1小时/15分钟/3分钟）；留空则使用交易系统默认周期
      </Typography.Text>
    </div>
  )
}
