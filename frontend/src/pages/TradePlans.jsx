import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import {
  Card,
  Button,
  Space,
  Modal,
  Form,
  Input,
  InputNumber,
  Select,
  Radio,
  DatePicker,
  Tag,
  Popconfirm,
  message,
  Typography,
  Empty,
  Row,
  Col,
  List,
  Divider,
  Alert,
  Spin,
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  RobotOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CalendarOutlined,
} from '@ant-design/icons'
import {
  listTradePlans,
  createTradePlan,
  updateTradePlan,
  deleteTradePlan,
  executeTradePlan,
  cancelTradePlan,
  reviewTradePlan,
  compareTradePlan,
  listTradingSystems,
  listTrades,
} from '../api'

const { TextArea } = Input

const INSTRUMENT_TYPES = ['A股', '商品期货', '数字货币']
const ENTRY_METHODS = ['突破', '回踩', '挂单', '条件单', '其他']
const STATUS_META = {
  pending: { color: 'blue', label: '待执行' },
  executed: { color: 'green', label: '已执行' },
  cancelled: { color: 'default', label: '已取消' },
}
const STATUS_FILTERS = [
  { key: '', label: '全部' },
  { key: 'pending', label: '待执行' },
  { key: 'executed', label: '已执行' },
  { key: 'cancelled', label: '已取消' },
]

const DIRECTION_LABEL = { long: '做多', short: '做空' }

export default function TradePlans() {
  const [plans, setPlans] = useState([])
  const [systems, setSystems] = useState([])
  const [trades, setTrades] = useState([])
  const [filter, setFilter] = useState('')
  const [dateFilter, setDateFilter] = useState(null) // 按计划日期筛选（dayjs）
  const [loading, setLoading] = useState(false)

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form] = Form.useForm()

  const [reviewPlan, setReviewPlan] = useState(null) // AI 预评审结果
  const [reviewing, setReviewing] = useState(false)
  const [comparePlan, setComparePlan] = useState(null) // 执行对照结果
  const [comparing, setComparing] = useState(false)
  const [execOpen, setExecOpen] = useState(null) // 当前执行关联的计划

  const fetchData = async () => {
    setLoading(true)
    try {
      const params = {}
      if (filter) params.status = filter
      if (dateFilter) params.plan_date = dateFilter.format('YYYY-MM-DD')
      setPlans(await listTradePlans(params))
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [filter, dateFilter])

  useEffect(() => {
    listTradingSystems().then(setSystems).catch(() => {})
    listTrades({ page: 1, page_size: 200 })
      .then((r) => setTrades(r.items || []))
      .catch(() => {})
  }, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ direction: 'long', status: 'pending', plan_date: dayjs() })
    setModalOpen(true)
  }

  const openEdit = (p) => {
    setEditing(p)
    form.setFieldsValue({
      ...p,
      plan_date: p.plan_date ? dayjs(p.plan_date) : dayjs(),
      status: p.status === 'executed' ? 'pending' : p.status,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    const payload = {
      ...values,
      plan_date: values.plan_date ? dayjs(values.plan_date).format('YYYY-MM-DD') : null,
    }
    if (editing) {
      await updateTradePlan(editing.id, payload)
      message.success('交易计划已更新')
    } else {
      await createTradePlan(payload)
      message.success('交易计划已创建')
    }
    setModalOpen(false)
    fetchData()
  }

  const handleDelete = async (id) => {
    await deleteTradePlan(id)
    message.success('已删除')
    fetchData()
  }

  const handleCancel = async (p) => {
    await cancelTradePlan(p.id)
    message.success('计划已取消')
    fetchData()
  }

  const handleExecute = async () => {
    if (!execOpen) return
    await executeTradePlan(execOpen.id, execOpen.linked_trade_id ?? null)
    message.success('计划已标记执行')
    setExecOpen(null)
    fetchData()
  }

  const handleReview = async (p) => {
    setReviewing(true)
    setReviewPlan({ ...p, result: null })
    try {
      const result = await reviewTradePlan(p.id)
      setReviewPlan({ ...p, result })
    } catch (e) {
      setReviewPlan(null)
    } finally {
      setReviewing(false)
    }
  }

  const handleCompare = async (p) => {
    setComparing(true)
    setComparePlan({ ...p, result: null })
    try {
      const result = await compareTradePlan(p.id)
      setComparePlan({ ...p, result })
    } catch (e) {
      setComparePlan(null)
    } finally {
      setComparing(false)
    }
  }

  const renderStatus = (p) => (
    <Tag color={STATUS_META[p.status]?.color}>{STATUS_META[p.status]?.label}</Tag>
  )

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="交易计划"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建计划
          </Button>
        }
      >
        <Space wrap size={8} style={{ marginBottom: 16 }}>
          {STATUS_FILTERS.map((s) => (
            <Button
              key={s.key}
              size="small"
              type={filter === s.key ? 'primary' : 'default'}
              onClick={() => setFilter(s.key)}
            >
              {s.label}
            </Button>
          ))}
          <DatePicker
            size="small"
            value={dateFilter}
            onChange={(v) => setDateFilter(v || null)}
            placeholder="按计划日期筛选"
            allowClear
            format="YYYY-MM-DD"
          />
          {dateFilter && (
            <Button size="small" onClick={() => setDateFilter(null)}>
              清除日期
            </Button>
          )}
        </Space>

        {loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : plans.length === 0 ? (
          <Empty description="暂无交易计划，点击右上角创建" />
        ) : (
          (() => {
            // 按计划日期分组（后端已按日期倒序）
            const groups = []
            for (const p of plans) {
              const key = p.plan_date || '未设置日期'
              const last = groups[groups.length - 1]
              if (last && last.date === key) last.plans.push(p)
              else groups.push({ date: key, plans: [p] })
            }
            return groups.map((g) => (
              <div key={g.date} style={{ marginBottom: 8 }}>
                <Divider orientation="left" plain style={{ margin: '8px 0' }}>
                  <Space>
                    <CalendarOutlined style={{ color: '#534AB7' }} />
                    <Typography.Text strong>{g.date}</Typography.Text>
                    <Tag>{g.plans.length} 个计划</Tag>
                  </Space>
                </Divider>
                <List
                  grid={{ gutter: 16, column: 2 }}
                  dataSource={g.plans}
                  renderItem={(p) => (
                    <List.Item>
                      <Card
                        size="small"
                        title={
                          <Space>
                            <Typography.Text strong>{p.name}</Typography.Text>
                            {renderStatus(p)}
                          </Space>
                        }
                        extra={
                          <Space>
                            <Button
                              type="text"
                              size="small"
                              icon={<EditOutlined />}
                              onClick={() => openEdit(p)}
                            />
                            <Button
                              type="text"
                              size="small"
                              icon={<RobotOutlined />}
                              title="AI 评审计划"
                              onClick={() => handleReview(p)}
                            />
                            {p.status === 'executed' && p.linked_trade_id && (
                              <Button
                                type="text"
                                size="small"
                                icon={<CheckCircleOutlined />}
                                title="执行对照分析"
                                onClick={() => handleCompare(p)}
                              />
                            )}
                            <Popconfirm
                              title="确定删除该计划？"
                              onConfirm={() => handleDelete(p.id)}
                            >
                              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                            </Popconfirm>
                          </Space>
                        }
                      >
                        <div style={{ fontSize: 13 }}>
                          <Space wrap style={{ marginBottom: 6 }}>
                            <Tag color={p.instrument_type === 'A股' ? 'blue' : p.instrument_type === '商品期货' ? 'purple' : 'gold'}>
                              {p.instrument_type}
                            </Tag>
                            <Tag>{p.instrument_name || p.instrument_code || '-'}</Tag>
                            <Tag color={p.direction === 'long' ? 'red' : 'green'}>
                              {DIRECTION_LABEL[p.direction]}
                            </Tag>
                            {p.entry_method && <Tag color="cyan">{p.entry_method}</Tag>}
                          </Space>
                          <div>
                            <Typography.Text type="secondary">入场价：</Typography.Text>
                            {p.planned_entry_price ?? '-'}
                            <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
                              止损：
                            </Typography.Text>
                            {p.stop_loss ?? '-'}
                            <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
                              目标1：
                            </Typography.Text>
                            {p.target1 ?? '-'}
                            {p.target2 ? (
                              <>
                                <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                                  目标2：
                                </Typography.Text>
                                {p.target2}
                              </>
                            ) : null}
                          </div>
                          <div>
                            <Typography.Text type="secondary">手数：</Typography.Text>
                            {p.planned_volume ?? '-'}
                            <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
                              盈亏比：
                            </Typography.Text>
                            {p.risk_reward || '-'}
                            {p.trading_system_name && (
                              <>
                                <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
                                  系统：
                                </Typography.Text>
                                {p.trading_system_name}
                              </>
                            )}
                          </div>
                          {p.entry_reason && (
                            <div style={{ color: '#595959', marginTop: 4 }}>入场理由：{p.entry_reason}</div>
                          )}
                          {p.linked_trade_name && (
                            <div style={{ marginTop: 4 }}>
                              <Tag color="green">已关联：{p.linked_trade_name}</Tag>
                            </div>
                          )}
                          {p.review_result?.verdict && (
                            <div style={{ marginTop: 4 }}>
                              <Tag color={p.review_result.verdict === '可执行' ? 'green' : p.review_result.verdict === '需调整' ? 'orange' : 'red'}>
                                AI评审：{p.review_result.verdict}
                              </Tag>
                            </div>
                          )}
                          {p.comparison_result?.discipline_score !== undefined && (
                            <div style={{ marginTop: 4 }}>
                              <Tag color={p.comparison_result.discipline_score >= 60 ? 'green' : 'volcano'}>
                                纪律评分：{p.comparison_result.discipline_score}
                              </Tag>
                            </div>
                          )}
                        </div>
                        <div style={{ marginTop: 10 }}>
                          <Space wrap>
                            {p.status === 'pending' && (
                              <>
                                <Button
                                  size="small"
                                  type="primary"
                                  icon={<CheckCircleOutlined />}
                                  onClick={() => setExecOpen(p)}
                                >
                                  标记已执行
                                </Button>
                                <Popconfirm title="取消该计划？" onConfirm={() => handleCancel(p)}>
                                  <Button size="small" icon={<CloseCircleOutlined />}>
                                    取消计划
                                  </Button>
                                </Popconfirm>
                              </>
                            )}
                          </Space>
                        </div>
                      </Card>
                    </List.Item>
                  )}
                />
              </div>
            ))
          })()
        )}
      </Card>

      {/* 新建/编辑弹窗 */}
      <Modal
        title={editing ? '编辑交易计划' : '新建交易计划'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        width={720}
      >
        <Form form={form} layout="vertical">
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item
                name="name"
                label="计划名称"
                rules={[{ required: true, message: '请输入计划名称' }]}
              >
                <Input placeholder="如：PTA突破计划" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="trading_system_id" label="关联交易系统（可选）">
                <Select
                  allowClear
                  placeholder="选择计划基于的交易系统"
                  options={systems.map((s) => ({ value: s.id, label: s.name }))}
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={8}>
              <Form.Item
                name="plan_date"
                label="计划日期"
                tooltip="收盘后制定的计划，精确到日期即可"
              >
                <DatePicker style={{ width: '100%' }} placeholder="默认今天" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={12}>
            <Col span={6}>
              <Form.Item name="instrument_type" label="品种类型">
                <Select
                  options={INSTRUMENT_TYPES.map((t) => ({ value: t, label: t }))}
                  placeholder="选择"
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="instrument_code" label="代码">
                <Input placeholder="如 600000 / 2手 等" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="instrument_name" label="品种名称">
                <Input placeholder="如 PTA" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="direction" label="方向">
                <Radio.Group>
                  <Radio.Button value="long">做多</Radio.Button>
                  <Radio.Button value="short">做空</Radio.Button>
                </Radio.Group>
              </Form.Item>
            </Col>
          </Row>

          <Divider orientation="left" plain style={{ margin: '8px 0' }}>
            入场计划
          </Divider>
          <Row gutter={12}>
            <Col span={6}>
              <Form.Item name="entry_method" label="入场方式">
                <Select
                  options={ENTRY_METHODS.map((m) => ({ value: m, label: m }))}
                  placeholder="突破/回踩/挂单"
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="planned_entry_price" label="计划入场价">
                <InputNumber style={{ width: '100%' }} precision={4} placeholder="触发价" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="planned_volume" label="计划手数">
                <InputNumber style={{ width: '100%' }} min={0} precision={2} placeholder="可留空" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="risk_reward" label="预期盈亏比">
                <Input placeholder="如 1:2" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="entry_reason" label="入场理由">
            <TextArea
              rows={2}
              placeholder="如：1小时突破前高，且日线多头时入场"
            />
          </Form.Item>

          <Divider orientation="left" plain style={{ margin: '8px 0' }}>
            风险与目标
          </Divider>
          <Row gutter={12}>
            <Col span={6}>
              <Form.Item name="stop_loss" label="初始止损位">
                <InputNumber style={{ width: '100%' }} precision={4} placeholder="0.00" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="max_loss_amount" label="最大亏损金额">
                <InputNumber style={{ width: '100%' }} precision={2} placeholder="仓位预算" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="target1" label="目标价1">
                <InputNumber style={{ width: '100%' }} precision={4} placeholder="止盈1" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="target2" label="目标价2">
                <InputNumber style={{ width: '100%' }} precision={4} placeholder="止盈2(可选)" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="market_context" label="市场背景 / 计划逻辑">
            <TextArea rows={2} placeholder="如：原油走强带动化工板块，PTA 跟随反弹" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 标记执行：选择关联交易 */}
      <Modal
        title="标记为已执行"
        open={!!execOpen}
        onOk={handleExecute}
        onCancel={() => setExecOpen(null)}
        okText="确认执行"
        cancelText="取消"
      >
        <Typography.Paragraph>
          计划「{execOpen?.name}」已按计划入场，选择对应的实际交易记录进行关联：
        </Typography.Paragraph>
        <Form layout="vertical">
          <Form.Item label="关联交易">
            <Select
              style={{ width: '100%' }}
              placeholder="选择一笔实际交易"
              value={execOpen?.linked_trade_id}
              onChange={(v) => setExecOpen({ ...execOpen, linked_trade_id: v })}
              options={(trades || []).map((t) => ({
                value: t.id,
                label: `${t.entry_time?.slice?.(0, 10) || ''} ${t.instrument_name || t.instrument_code} ${t.direction === 'long' ? '做多' : '做空'} 盈亏:${t.pnl ?? '-'}`,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* AI 预评审 */}
      <Modal
        title="AI 计划评审"
        open={!!reviewPlan}
        onCancel={() => setReviewPlan(null)}
        footer={<Button onClick={() => setReviewPlan(null)}>关闭</Button>}
        width={640}
      >
        {reviewing ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin tip="AI 正在评审计划..." />
          </div>
        ) : reviewPlan?.result ? (
          <div>
            <Alert
              type={
                reviewPlan.result.verdict === '可执行'
                  ? 'success'
                  : reviewPlan.result.verdict === '需调整'
                    ? 'warning'
                    : 'error'
              }
              showIcon
              message={`评审结论：${reviewPlan.result.verdict}`}
              description={reviewPlan.result.assessment}
              style={{ marginBottom: 12 }}
            />
            {reviewPlan.result.strengths?.length > 0 && (
              <>
                <Typography.Text strong style={{ color: '#1D9E75' }}>
                  做得好的地方
                </Typography.Text>
                <List
                  size="small"
                  dataSource={reviewPlan.result.strengths}
                  renderItem={(item) => (
                    <List.Item style={{ fontSize: 13 }}>✅ {item}</List.Item>
                  )}
                />
              </>
            )}
            {reviewPlan.result.risks?.length > 0 && (
              <>
                <Typography.Text strong type="danger">风险与漏洞</Typography.Text>
                <List
                  size="small"
                  dataSource={reviewPlan.result.risks}
                  renderItem={(item) => (
                    <List.Item style={{ fontSize: 13 }}>⚠️ {item}</List.Item>
                  )}
                />
              </>
            )}
            {reviewPlan.result.suggestions?.length > 0 && (
              <>
                <Typography.Text strong style={{ color: '#534AB7' }}>调整建议</Typography.Text>
                <List
                  size="small"
                  dataSource={reviewPlan.result.suggestions}
                  renderItem={(item, i) => (
                    <List.Item style={{ fontSize: 13 }}>
                      <Tag color="blue">{i + 1}</Tag> {item}
                    </List.Item>
                  )}
                />
              </>
            )}
          </div>
        ) : (
          <Empty description="评审失败或无结果" />
        )}
      </Modal>

      {/* 执行对照 */}
      <Modal
        title="计划 vs 实际执行对照"
        open={!!comparePlan}
        onCancel={() => setComparePlan(null)}
        footer={<Button onClick={() => setComparePlan(null)}>关闭</Button>}
        width={680}
      >
        {comparing ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin tip="AI 正在对照分析..." />
          </div>
        ) : comparePlan?.result ? (
          <div>
            <Alert
              type={comparePlan.result.discipline_score >= 60 ? 'success' : 'error'}
              showIcon
              message={`纪律执行评分：${comparePlan.result.discipline_score} 分`}
              description={comparePlan.result.execution_summary}
              style={{ marginBottom: 12 }}
            />
            {comparePlan.result.deviations?.length > 0 && (
              <>
                <Typography.Text strong type="warning">与计划的偏离</Typography.Text>
                {comparePlan.result.deviations.map((d, i) => (
                  <div
                    key={i}
                    style={{
                      border: '1px solid #f0f0f0',
                      borderRadius: 6,
                      padding: '8px 12px',
                      marginTop: 8,
                    }}
                  >
                    <Tag color="volcano">{d.item}</Tag>
                    <div style={{ fontSize: 13 }}>
                      <Typography.Text type="secondary">计划：</Typography.Text>
                      {d.planned || '-'}
                    </div>
                    <div style={{ fontSize: 13 }}>
                      <Typography.Text type="secondary">实际：</Typography.Text>
                      {d.actual || '-'}
                    </div>
                    {d.impact && (
                      <div style={{ fontSize: 13, color: '#595959' }}>影响：{d.impact}</div>
                    )}
                  </div>
                ))}
              </>
            )}
            {comparePlan.result.comments?.length > 0 && (
              <>
                <Typography.Text strong style={{ color: '#534AB7' }}>改进建议</Typography.Text>
                <List
                  size="small"
                  dataSource={comparePlan.result.comments}
                  renderItem={(item) => (
                    <List.Item style={{ fontSize: 13 }}>◆ {item}</List.Item>
                  )}
                />
              </>
            )}
          </div>
        ) : (
          <Empty description="对照分析失败（需先标记执行并关联交易）" />
        )}
      </Modal>
    </Space>
  )
}
