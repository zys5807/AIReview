import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import {
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Radio,
  DatePicker,
  Button,
  Space,
  Row,
  Col,
  message,
  Divider,
  Tag,
  Descriptions,
  Empty,
  Typography,
} from 'antd'
import { ScanOutlined, ArrowUpOutlined, ArrowDownOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { createTrade, listTradingSystems, recognizeScreenshot } from '../api'
import ScreenshotUploader from '../components/ScreenshotUploader'
import ActualPeriodSelector from '../components/ActualPeriodSelector'

// 品种类型 → 额外字段提示
const INSTRUMENT_META = {
  A股: { placeholder: '如 600519', namePlaceholder: '贵州茅台', contractLabel: '板块/市场', contractPlaceholder: '如 沪市/深市' },
  商品期货: { placeholder: '如 RB2510', namePlaceholder: '螺纹钢2510', contractLabel: '合约类型', contractPlaceholder: '如 2510合约' },
  数字货币: { placeholder: '如 BTCUSDT', namePlaceholder: 'BTC', contractLabel: '合约类型', contractPlaceholder: '如 永续/交割' },
}

export default function TradeUpload() {
  const [form] = Form.useForm()
  const [systems, setSystems] = useState([])
  const [shotLinks, setShotLinks] = useState([])
  const [submitting, setSubmitting] = useState(false)
  const [recognition, setRecognition] = useState(null)
  const [recognizing, setRecognizing] = useState(false)

  const instrumentType = Form.useWatch('instrument_type', form)

  useEffect(() => {
    listTradingSystems()
      .then(setSystems)
      .catch(() => {})
  }, [])

  // AI 识别 K线（用第一张关联截图）
  const handleRecognize = async () => {
    const sid = shotLinks[0]?.screenshot_id
    if (!sid) {
      message.warning('请先上传截图')
      return
    }
    setRecognizing(true)
    try {
      const result = await recognizeScreenshot(sid)
      setRecognition(result)
      // 自动填充识别到的品种/周期到表单（如果有）
      const patch = {}
      if (result.instrument) patch.instrument_code = result.instrument
      if (result.exchange) patch.exchange = result.exchange
      if (result.timeframe) patch.timeframe = result.timeframe
      if (Object.keys(patch).length > 0) {
        form.setFieldsValue(patch)
      }
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setRecognizing(false)
    }
  }

  const handleSubmit = async (values) => {
    setSubmitting(true)
    try {
      // 不传 screenshot_id 单值：主截图由后端从 screenshots 第一张自动派生，避免歧义
      const payload = {
        ...values,
        entry_time: dayjs(values.entry_time).format('YYYY-MM-DD HH:mm:ss'),
        exit_time: dayjs(values.exit_time).format('YYYY-MM-DD HH:mm:ss'),
        screenshots: shotLinks,
        position_actions: (values.position_actions || []).map((a) => ({
          ...a,
          action_time: dayjs(a.action_time).format('YYYY-MM-DD HH:mm:ss'),
        })),
        volume: values.volume ?? 1,
      }
      await createTrade(payload)
      message.success('交易记录已保存')
      form.resetFields()
      setShotLinks([])
      setRecognition(null)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setSubmitting(false)
    }
  }

  const meta = INSTRUMENT_META[instrumentType] || INSTRUMENT_META['A股']

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card title="1. 上传K线截图（可多张，每张指定周期角色）">
        <ScreenshotUploader
          initial={[]}
          onChange={(list) => {
            setShotLinks(list)
            setRecognition(null) // 截图变化后清空旧的识别结果
          }}
        />
        <div style={{ marginTop: 12 }}>
          <Button icon={<ScanOutlined />} loading={recognizing} onClick={handleRecognize}>
            AI 识别第一张K线
          </Button>
          {recognition && (
              <div
                style={{
                  marginTop: 16,
                  padding: 12,
                  background: '#fafafa',
                  borderRadius: 6,
                }}
              >
                <Descriptions
                  size="small"
                  column={3}
                  title={
                    <Space>
                      <ScanOutlined /> 识别结果
                      {recognition.platform && <Tag>{recognition.platform}</Tag>}
                    </Space>
                  }
                  bordered
                >
                  <Descriptions.Item label="品种">
                    {recognition.instrument || <span style={{ color: '#bbb' }}>未识别</span>}
                  </Descriptions.Item>
                  <Descriptions.Item label="交易所">
                    {recognition.exchange || <span style={{ color: '#bbb' }}>未识别</span>}
                  </Descriptions.Item>
                  <Descriptions.Item label="K线周期">
                    {recognition.timeframe_label || recognition.timeframe || (
                      <span style={{ color: '#bbb' }}>未识别</span>
                    )}
                  </Descriptions.Item>
                  <Descriptions.Item label="价格区间">
                    {recognition.price_min && recognition.price_max
                      ? `${recognition.price_min} ~ ${recognition.price_max}`
                      : <span style={{ color: '#bbb' }}>未识别</span>}
                  </Descriptions.Item>
                  <Descriptions.Item label="时间区间">
                    {recognition.time_range || <span style={{ color: '#bbb' }}>未识别</span>}
                  </Descriptions.Item>
                  <Descriptions.Item label="技术指标">
                    {recognition.indicators?.length > 0
                      ? recognition.indicators.map((ind, i) => (
                          <Tag key={i} color="cyan">
                            {ind.name}
                            {ind.params} {ind.value}
                          </Tag>
                        ))
                      : <span style={{ color: '#bbb' }}>未识别</span>}
                  </Descriptions.Item>
                </Descriptions>

                {(recognition.arrows?.length > 0 || recognition.notes?.length > 0) && (
                  <Divider style={{ margin: '12px 0' }} />
                )}

                {recognition.arrows?.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    <strong>检测到的标注：</strong>
                    <Space wrap style={{ marginTop: 4 }}>
                      {recognition.arrows.map((a, i) => (
                        <Tag
                          key={i}
                          color={a.role === 'entry' ? 'green' : a.role === 'exit' ? 'red' : 'default'}
                          icon={
                            a.direction === 'up' ? (
                              <ArrowUpOutlined />
                            ) : a.direction === 'down' ? (
                              <ArrowDownOutlined />
                            ) : null
                          }
                        >
                          {a.role === 'entry' ? '入场' : a.role === 'exit' ? '出场' : '标记'}
                          （位置 ({a.x}, {a.y}））
                        </Tag>
                      ))}
                    </Space>
                  </div>
                )}

                {recognition.notes?.length > 0 && (
                  <div>
                    <strong>用户备注：</strong>
                    <ul style={{ marginTop: 4, marginBottom: 0 }}>
                      {recognition.notes.map((n, i) => (
                        <li key={i} style={{ color: '#cf1322' }}>
                          {n.text}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
      </Card>

      <Card title="2. 录入交易数据">
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          initialValues={{
            instrument_type: 'A股',
            direction: 'long',
            volume: 1,
          }}
        >
          <Divider titlePlacement="left" plain>
            品种信息
          </Divider>
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item name="instrument_type" label="品种类型" rules={[{ required: true }]}>
                <Select
                  options={[
                    { value: 'A股', label: 'A股' },
                    { value: '商品期货', label: '商品期货' },
                    { value: '数字货币', label: '数字货币' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="instrument_code" label="品种代码">
                <Input placeholder={meta.placeholder} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="instrument_name" label="品种名称">
                <Input placeholder={meta.namePlaceholder} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="exchange" label="交易所">
                <Input placeholder="如 上期所/币安" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item name="contract_type" label={meta.contractLabel}>
                <Input placeholder={meta.contractPlaceholder} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="timeframe" label="K线周期">
                <Select
                  allowClear
                  placeholder="如 15m / 1H / 日线"
                  options={['1m', '5m', '15m', '30m', '1H', '4H', '日线', '周线', '月线'].map(
                    (t) => ({ value: t, label: t }),
                  )}
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="direction" label="交易方向" rules={[{ required: true }]}>
                <Radio.Group
                  options={[
                    { value: 'long', label: '做多' },
                    { value: 'short', label: '做空' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="trading_system_id" label="适用交易系统">
                <Select
                  allowClear
                  placeholder="选择交易系统"
                  options={systems.map((s) => ({ value: s.id, label: s.name }))}
                />
              </Form.Item>
            </Col>
          </Row>
          <ActualPeriodSelector systems={systems} />

          <Divider titlePlacement="left" plain>
            交易明细
          </Divider>
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item
                name="entry_time"
                label="入场时间"
                rules={[{ required: true, message: '请选择入场时间' }]}
              >
                <DatePicker showTime style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                name="exit_time"
                label="出场时间"
                rules={[{ required: true, message: '请选择出场时间' }]}
              >
                <DatePicker showTime style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                name="entry_price"
                label="入场价格"
                rules={[{ required: true, message: '请输入入场价格' }]}
              >
                <InputNumber style={{ width: '100%' }} min={0} precision={4} placeholder="0.00" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                name="exit_price"
                label="出场价格"
                rules={[{ required: true, message: '请输入出场价格' }]}
              >
                <InputNumber style={{ width: '100%' }} min={0} precision={4} placeholder="0.00" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item name="volume" label="交易手数/数量">
                <InputNumber style={{ width: '100%' }} min={0} precision={2} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="stop_loss" label="初始止损位">
                <InputNumber style={{ width: '100%' }} precision={4} placeholder="0.00" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="pnl" label="盈亏金额">
                <InputNumber style={{ width: '100%' }} precision={2} placeholder="可留空" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item
                name="fee"
                label="手续费"
                tooltip="仅记录展示，不参与盈亏比等指标计算"
              >
                <InputNumber style={{ width: '100%' }} min={0} precision={2} placeholder="可留空" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={24}>
              <Form.List name="position_actions">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map(({ key, name, ...rest }) => (
                      <div
                        key={key}
                        style={{
                          border: '1px dashed #d9d9d9',
                          padding: 12,
                          borderRadius: 6,
                          marginBottom: 8,
                          background: '#fafafa',
                        }}
                      >
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            marginBottom: 8,
                          }}
                        >
                          <Typography.Text strong style={{ fontSize: 13 }}>
                            操作 {name + 1}（数量填正数=加仓，负数=减仓）
                          </Typography.Text>
                          <Button
                            type="text"
                            danger
                            size="small"
                            icon={<DeleteOutlined />}
                            onClick={() => remove(name)}
                          />
                        </div>
                        <Row gutter={12}>
                          <Col span={8}>
                            <Form.Item
                              {...rest}
                              name={[name, 'action_time']}
                              label="操作时间"
                              rules={[{ required: true, message: '请选择时间' }]}
                              style={{ marginBottom: 0 }}
                            >
                              <DatePicker showTime style={{ width: '100%' }} />
                            </Form.Item>
                          </Col>
                          <Col span={5}>
                            <Form.Item
                              {...rest}
                              name={[name, 'price']}
                              label="成交价格"
                              style={{ marginBottom: 0 }}
                            >
                              <InputNumber
                                style={{ width: '100%' }}
                                min={0}
                                precision={4}
                                placeholder="可留空"
                              />
                            </Form.Item>
                          </Col>
                          <Col span={5}>
                            <Form.Item
                              {...rest}
                              name={[name, 'volume']}
                              label="数量"
                              rules={[{ required: true, message: '填数量' }]}
                              style={{ marginBottom: 0 }}
                            >
                              <InputNumber
                                style={{ width: '100%' }}
                                precision={2}
                                placeholder="正=加仓 负=减仓"
                              />
                            </Form.Item>
                          </Col>
                          <Col span={6}>
                            <Form.Item
                              {...rest}
                              name={[name, 'note']}
                              label="备注"
                              style={{ marginBottom: 0 }}
                            >
                              <Input placeholder="可选" />
                            </Form.Item>
                          </Col>
                        </Row>
                      </div>
                    ))}
                    <Button
                      type="dashed"
                      onClick={() =>
                        add({ action_time: null, price: null, volume: null, note: '' })
                      }
                      block
                      icon={<PlusOutlined />}
                    >
                      添加加仓/减仓操作
                    </Button>
                  </>
                )}
              </Form.List>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={24}>
              <Form.Item
                name="timeframe_notes"
                label="入场理由"
                extra="填你入场时每个周期的信号，如：日线K线在EMA20上方；1小时回踩EMA55不破；15分钟金叉。AI会对照交易系统的多周期规则和入场策略判断是否符合。"
              >
                <Input.TextArea rows={3} placeholder="例：日线K线在EMA20上方；1小时上升；15分钟回踩EMA20不破入场" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={24}>
              <Form.Item
                name="notes"
                label="交易复盘"
                extra="记录这笔交易自己的心得体会（哪里做得好、哪里没执行到位、下次如何改进）。AI会阅读这些内容，并结合你的交易系统给出优化策略的建议。"
              >
                <Input.TextArea
                  rows={4}
                  placeholder="例：这笔突破了前高，但我入场时犹豫了导致成本偏高；下次突破时若1小时方向一致，应果断按策略执行。另外这次没按策略设止损，回撤偏大…"
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={24}>
              <Form.Item
                name="psychology_notes"
                label="持仓过程中的心理状态"
                extra="例：入场后价格回撤时是否焦虑、是否想过提前止损/加仓、情绪如何影响决策。AI分析《情绪控制》维度会参考此内容。"
              >
                <Input.TextArea rows={3} placeholder="记录持仓期间的内心活动和情绪变化（可选，但强烈建议填写，能让AI打分更准）" />
              </Form.Item>
            </Col>
          </Row>

          <Button type="primary" htmlType="submit" loading={submitting} size="large">
            保存交易记录
          </Button>
        </Form>
      </Card>
    </Space>
  )
}
