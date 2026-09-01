import { useEffect, useState } from 'react'
import {
  Card,
  Button,
  Space,
  List,
  Modal,
  Form,
  Input,
  Select,
  Tag,
  Popconfirm,
  message,
  Typography,
  Empty,
  Row,
  Col,
  Divider,
} from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, MinusCircleOutlined } from '@ant-design/icons'
import {
  listTradingSystems,
  createTradingSystem,
  updateTradingSystem,
  deleteTradingSystem,
} from '../api'

const { TextArea } = Input

const TIMEFRAME_OPTIONS = ['1分钟', '5分钟', '15分钟', '30分钟', '1小时', '2小时', '4小时', '日线', '周线', '月线']

// 共用规则（所有交易策略共用；入场/出场已并入"交易策略"）
const RULE_FIELDS = [
  { key: 'trend_rule', label: '趋势判断规则（共用）', placeholder: '如：MA20上穿MA60视为上升趋势' },
  { key: 'position_rule', label: '仓位管理（共用）', placeholder: '如：单笔不超过总资金10%' },
  { key: 'risk_rule', label: '风险控制（共用）', placeholder: '如：初始止损不超过2%' },
]

// 多周期职责标签（用于卡片展示）
const TIMEFRAME_LABELS = [
  { key: 'trend_timeframe', label: '多空判断' },
  { key: 'direction_timeframe', label: '方向判断' },
  { key: 'entry_timeframe', label: '入场/离场' },
]

export default function TradingSystems() {
  const [systems, setSystems] = useState([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form] = Form.useForm()

  const fetchData = async () => {
    try {
      setSystems(await listTradingSystems())
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (item) => {
    setEditing(item)
    form.setFieldsValue(item)
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    if (editing) {
      await updateTradingSystem(editing.id, values)
      message.success('交易系统已更新')
    } else {
      await createTradingSystem(values)
      message.success('交易系统已创建')
    }
    setModalOpen(false)
    fetchData()
  }

  const handleDelete = async (id) => {
    try {
      await deleteTradingSystem(id)
      message.success('已删除')
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  return (
    <Card
      title="交易系统"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建系统
        </Button>
      }
    >
      {systems.length === 0 ? (
        <Empty description="暂无交易系统，点击右上角创建" />
      ) : (
        <List
          grid={{ gutter: 16, column: 2 }}
          dataSource={systems}
          renderItem={(item) => (
            <List.Item>
              <Card
                size="small"
                title={
                  <Space>
                    <Typography.Text strong>{item.name}</Typography.Text>
                    {item.is_active && <Tag color="green">启用中</Tag>}
                  </Space>
                }
                extra={
                  <Space>
                    <Button
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      onClick={() => openEdit(item)}
                    />
                    <Popconfirm
                      title="确定删除该系统？"
                      onConfirm={() => handleDelete(item.id)}
                    >
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                  </Space>
                }
              >
                {item.description && (
                  <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
                    {item.description}
                  </Typography.Paragraph>
                )}
                <Space wrap style={{ marginBottom: 8 }}>
                  {TIMEFRAME_LABELS.map((tf) =>
                    item[tf.key] ? (
                      <Tag key={tf.key} color="blue">
                        {tf.label}: {item[tf.key]}
                      </Tag>
                    ) : null,
                  )}
                </Space>
                <div style={{ fontSize: 13 }}>
                  {RULE_FIELDS.map((f) => (
                    <div key={f.key} style={{ marginTop: 4 }}>
                      <Typography.Text type="secondary">{f.label}：</Typography.Text>
                      {item[f.key] || <Typography.Text type="secondary">未设置</Typography.Text>}
                    </div>
                  ))}
                  <div style={{ marginTop: 8 }}>
                    <Typography.Text type="secondary">交易策略（或关系，符合任一即可入场）：</Typography.Text>
                    {item.trade_strategies?.length ? (
                      item.trade_strategies.map((s) => (
                        <div
                          key={s.id}
                          style={{
                            marginTop: 6,
                            paddingLeft: 8,
                            borderLeft: '2px solid #d9d9d9',
                            fontSize: 12,
                          }}
                        >
                          <Tag color={s.is_active ? 'green' : 'default'} style={{ marginRight: 4 }}>
                            {s.name}
                          </Tag>
                          <div>入场：{s.entry_rule || '未设置'}</div>
                          <div>止损：{s.stop_loss_rule || '未设置'}</div>
                          <div>止盈：{s.take_profit_rule || '未设置'}</div>
                        </div>
                      ))
                    ) : (
                      <Typography.Text type="secondary">未设置</Typography.Text>
                    )}
                  </div>
                </div>
              </Card>
            </List.Item>
          )}
        />
      )}

      <Modal
        title={editing ? '编辑交易系统' : '新建交易系统'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        width={640}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="系统名称"
            rules={[{ required: true, message: '请输入系统名称' }]}
          >
            <Input placeholder="如：均线趋势系统" />
          </Form.Item>
          <Form.Item name="description" label="系统描述">
            <TextArea rows={2} placeholder="这套系统的整体思路概述" />
          </Form.Item>
          <Row gutter={12}>
            {TIMEFRAME_LABELS.map((tf) => (
              <Col span={8} key={tf.key}>
                <Form.Item name={tf.key} label={`${tf.label}周期`}>
                  <Select
                    allowClear
                    placeholder="选择周期"
                    options={TIMEFRAME_OPTIONS.map((t) => ({ value: t, label: t }))}
                  />
                </Form.Item>
              </Col>
            ))}
          </Row>
          {RULE_FIELDS.map((f) => (
            <Form.Item key={f.key} name={f.key} label={f.label}>
              <TextArea rows={2} placeholder={f.placeholder} />
            </Form.Item>
          ))}

          <Divider orientation="left" plain style={{ margin: '12px 0' }}>
            交易策略（多个为"或"关系，符合任一即可入场；趋势/仓位/风险共用）
          </Divider>
          <Form.List name="trade_strategies">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...rest }) => (
                  <div
                    key={key}
                    style={{
                      border: '1px dashed #d9d9d9',
                      padding: 12,
                      borderRadius: 6,
                      marginBottom: 12,
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
                      <Form.Item
                        {...rest}
                        name={[name, 'name']}
                        label={`策略${name + 1}名称`}
                        rules={[{ required: true, message: '请输入策略名称' }]}
                        style={{ marginBottom: 0 }}
                      >
                        <Input placeholder="如：突破策略" style={{ width: 220 }} />
                      </Form.Item>
                      <Button
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={() => remove(name)}
                      />
                    </div>
                    <Form.Item {...rest} name={[name, 'entry_rule']} label="入场策略">
                      <TextArea
                        rows={2}
                        placeholder="如：15分钟突破前高，且1小时方向向上时做多"
                      />
                    </Form.Item>
                    <Row gutter={12}>
                      <Col span={12}>
                        <Form.Item {...rest} name={[name, 'stop_loss_rule']} label="初始止损策略">
                          <TextArea
                            rows={2}
                            placeholder="如：跌破突破K线最低点或入场前低"
                          />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item {...rest} name={[name, 'take_profit_rule']} label="止盈策略">
                          <TextArea
                            rows={2}
                            placeholder="如：到达前期高点或盈亏比2:1分批止盈"
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                  </div>
                ))}
                <Button
                  type="dashed"
                  onClick={() =>
                    add({
                      name: '',
                      entry_rule: '',
                      stop_loss_rule: '',
                      take_profit_rule: '',
                    })
                  }
                  block
                  icon={<PlusOutlined />}
                >
                  添加交易策略
                </Button>
              </>
            )}
          </Form.List>
        </Form>
      </Modal>
    </Card>
  )
}
