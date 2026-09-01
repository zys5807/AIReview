import { useEffect, useRef, useState } from 'react'
import {
  Modal,
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
  Typography,
} from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { listTradingSystems, updateTrade, calcTradeCapital } from '../api'
import ScreenshotUploader from './ScreenshotUploader'
import ActualPeriodSelector from './ActualPeriodSelector'

/**
 * 编辑交易弹窗（支持修改字段 + 多张截图/角色管理）
 * @param trade 当前交易
 */
export default function TradeEditModal({ trade, open, onClose, onSuccess }) {
  const [form] = Form.useForm()
  const [systems, setSystems] = useState([])
  const [shotLinks, setShotLinks] = useState([])
  const [saving, setSaving] = useState(false)
  // 自动计算的占用资金（用户手动改过后不再覆盖）
  const manualCapitalRef = useRef(false)
  // 品种识别结果（用于显式反馈：已识别/未识别）
  const [matchInfo, setMatchInfo] = useState(null)
  // 实时监听盈亏金额与占用资金，计算收益率
  const pnl = Form.useWatch('pnl', form)
  const investedCapital = Form.useWatch('invested_capital', form)
  const returnRate =
    pnl != null && investedCapital
      ? (pnl / investedCapital) * 100
      : null

  // 自动计算占用资金（品种/价格/数量变化时）
  const refreshAutoCapital = (values) => {
    const { instrument_type, instrument_code, instrument_name, entry_price, volume } = values
    // 数字货币：volume 即 USDT 持仓规模，占用资金=volume，不依赖价格
    const needPrice = instrument_type !== '数字货币'
    if (!volume || (needPrice && !entry_price) || manualCapitalRef.current) return
    calcTradeCapital({
      instrument_type,
      instrument_code,
      instrument_name,
      entry_price,
      volume,
    })
      .then((r) => {
        setMatchInfo({ matched: r.matched_name, multiplier: r.multiplier })
        if (r.invested_capital != null && !manualCapitalRef.current) {
          form.setFieldValue('invested_capital', r.invested_capital)
        }
      })
      .catch(() => {})
  }

  // 打开时预填表单 + 恢复截图
  useEffect(() => {
    if (open && trade) {
      manualCapitalRef.current = false
      form.setFieldsValue({
        ...trade,
        entry_time: dayjs(trade.entry_time),
        exit_time: dayjs(trade.exit_time),
        position_actions: (trade.position_actions || []).map((a) => ({
          ...a,
          action_time: a.action_time ? dayjs(a.action_time) : null,
        })),
      })
      // 多截图：优先用 screenshots 列表（兼容旧 screenshot_id）
      const initial =
        trade.screenshots?.length > 0
          ? trade.screenshots
          : trade.screenshot_id
            ? [{ screenshot_id: trade.screenshot_id, role: '后续' }]
            : []
      setShotLinks(initial)
    }
  }, [open, trade, form])

  useEffect(() => {
    listTradingSystems()
      .then(setSystems)
      .catch(() => {})
  }, [])

  const handleSubmit = async (values) => {
    setSaving(true)
    try {
      const payload = {
        ...values,
        entry_time: dayjs(values.entry_time).format('YYYY-MM-DD HH:mm:ss'),
        exit_time: dayjs(values.exit_time).format('YYYY-MM-DD HH:mm:ss'),
        screenshots: shotLinks,
        position_actions: (values.position_actions || []).map((a) => ({
          ...a,
          action_time: dayjs(a.action_time).format('YYYY-MM-DD HH:mm:ss'),
        })),
      }
      await updateTrade(trade.id, payload)
      message.success('交易已更新')
      onSuccess?.()
      onClose()
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={`编辑交易 #${trade?.id}`}
      open={open}
      onCancel={onClose}
      width={760}
      footer={null}
      destroyOnClose
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        onValuesChange={(changed, all) => {
          if ('invested_capital' in changed) manualCapitalRef.current = true
          if (
            ['instrument_type', 'instrument_code', 'instrument_name', 'entry_price', 'volume'].some(
              (k) => k in changed,
            )
          ) {
            refreshAutoCapital(all)
          }
        }}
      >
        <Divider orientation="left" plain style={{ margin: '8px 0' }}>
          品种信息
        </Divider>
        <Row gutter={16}>
          <Col span={8}>
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
          <Col span={8}>
            <Form.Item name="instrument_code" label="品种代码">
              <Input />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="instrument_name" label="品种名称">
              <Input />
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item name="exchange" label="交易所">
              <Input />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="contract_type" label="合约类型">
              <Input />
            </Form.Item>
          </Col>
          <Col span={8}>
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
        </Row>

        <Divider orientation="left" plain style={{ margin: '8px 0' }}>
          交易明细
        </Divider>
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item
              name="direction"
              label="交易方向"
              rules={[{ required: true }]}
            >
              <Radio.Group
                options={[
                  { value: 'long', label: '做多' },
                  { value: 'short', label: '做空' },
                ]}
              />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="trading_system_id" label="适用交易系统">
              <Select
                allowClear
                placeholder="选择交易系统"
                options={systems.map((s) => ({ value: s.id, label: s.name }))}
              />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              name="volume"
              label="交易手数/数量"
              tooltip="A股：1手=100股；商品期货：1手按合约乘数计算；数字货币：填写USDT持仓规模金额（如开仓5万USDT填50000，占用资金=该金额）"
            >
              <InputNumber style={{ width: '100%' }} min={0} precision={2} />
            </Form.Item>
          </Col>
        </Row>
        <ActualPeriodSelector systems={systems} />
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item name="entry_time" label="入场时间" rules={[{ required: true }]}>
              <DatePicker showTime style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              name="exit_time"
              label="出场时间"
              tooltip="未完全平仓时留空"
            >
              <DatePicker showTime style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="pnl" label="盈亏金额">
              <InputNumber style={{ width: '100%' }} precision={2} placeholder="可留空" />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item name="fee" label="手续费" tooltip="仅记录展示，不参与指标计算">
              <InputNumber style={{ width: '100%' }} min={0} precision={2} placeholder="可留空" />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item
              name="remaining_volume"
              label="当前持仓量"
              tooltip="0=已平仓；大于0表示该交易尚未完全平仓（交割单导入自动维护，可手动修改）"
            >
              <InputNumber style={{ width: '100%' }} min={0} precision={4} placeholder="0=已平仓" />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item
              name="invested_capital"
              label="占用资金"
              tooltip="收益率分母。自动按品种计算（期货=开仓价×手数×合约乘数×10%保证金；A股=价×手数×100；数字货币=USDT持仓规模金额），也可手动修改"
            >
              <InputNumber style={{ width: '100%' }} min={0} precision={2} placeholder="自动计算" />
            </Form.Item>
            {matchInfo && (
              <div style={{ fontSize: 12, marginTop: -18, marginBottom: 8 }}>
                {matchInfo.matched ? (
                  <span style={{ color: '#52c41a' }}>
                    ✅ 已识别品种：{matchInfo.matched}（乘数 {matchInfo.multiplier}）
                  </span>
                ) : (
                  <span style={{ color: '#fa8c16' }}>
                    ⚠️ 未识别品种，已按名义本金估算，建议手动核对
                  </span>
                )}
              </div>
            )}
          </Col>
          <Col span={6}>
            <Form.Item label="收益率">
              <div
                style={{
                  lineHeight: '32px',
                  fontWeight: 500,
                  fontSize: 14,
                  color:
                    returnRate == null
                      ? '#999'
                      : returnRate >= 0
                        ? '#cf1322'
                        : '#3f8600',
                }}
              >
                {returnRate != null ? `${returnRate >= 0 ? '+' : ''}${returnRate.toFixed(2)}%` : '—'}
              </div>
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item name="entry_price" label="入场价格" rules={[{ required: true }]}>
              <InputNumber style={{ width: '100%' }} min={0} precision={4} placeholder="0.00" />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              name="exit_price"
              label="出场价格"
              tooltip="未完全平仓时留空"
            >
              <InputNumber style={{ width: '100%' }} min={0} precision={4} placeholder="可留空" />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="stop_loss" label="初始止损位">
              <InputNumber style={{ width: '100%' }} precision={4} placeholder="0.00" />
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
                    onClick={() => add({ action_time: null, price: null, volume: null, note: '' })}
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
              extra="AI会对照交易系统的多周期规则和入场策略判断是否符合。"
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
              extra="记录这笔交易的心得体会（哪里做得好、哪里没执行到位）。AI会阅读并据此给出优化交易系统和策略的建议。"
            >
              <Input.TextArea rows={3} placeholder="例：这次没按策略设止损，回撤偏大；下次应严格执行…" />
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={16}>
          <Col span={24}>
            <Form.Item
              name="psychology_notes"
              label="持仓过程中的心理状态"
              extra="AI分析《情绪控制》维度会参考此内容。"
            >
              <Input.TextArea rows={3} placeholder="记录持仓期间的内心活动和情绪变化（可选）" />
            </Form.Item>
          </Col>
        </Row>

        <Divider orientation="left" plain style={{ margin: '8px 0' }}>
          K线截图（多张，带角色）
        </Divider>
        <ScreenshotUploader
          key={trade?.id}
          initial={shotLinks}
          onChange={setShotLinks}
        />

        <div style={{ textAlign: 'right', marginTop: 16 }}>
          <Space>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" htmlType="submit" loading={saving}>
              保存修改
            </Button>
          </Space>
        </div>
      </Form>
    </Modal>
  )
}
