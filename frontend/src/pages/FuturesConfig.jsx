import { useEffect, useState } from 'react'
import {
  Card,
  Table,
  Tag,
  Button,
  Space,
  message,
  Popconfirm,
  Alert,
  Tabs,
  Modal,
  Form,
  Input,
  InputNumber,
  Statistic,
  Row,
  Col,
} from 'antd'
import { ReloadOutlined, PlusOutlined, SyncOutlined } from '@ant-design/icons'
import {
  listFuturesConfig,
  getFuturesStatus,
  syncFutures,
  createFuturesVariety,
  updateFuturesVariety,
  deleteFuturesVariety,
  createFuturesContract,
  deleteFuturesContract,
} from '../api'

const pct = (v) => (v != null ? `${(v * 100).toFixed(0)}%` : '-')

export default function FuturesConfig() {
  const [list, setList] = useState([])
  const [status, setStatus] = useState({ last_sync: '', new_varieties: [] })
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  // 新增/编辑品种
  const [vOpen, setVOpen] = useState(false)
  const [editRow, setEditRow] = useState(null)
  const [vForm] = Form.useForm()
  // 新增合约覆盖
  const [cOpen, setCOpen] = useState(false)
  const [cForm] = Form.useForm()

  const fetchData = async () => {
    setLoading(true)
    try {
      const [cfg, st] = await Promise.all([listFuturesConfig(), getFuturesStatus()])
      setList(cfg)
      setStatus(st)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const handleSync = async () => {
    setSyncing(true)
    try {
      const r = await syncFutures()
      message.success(`同步完成：${r.synced} 个品种`)
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setSyncing(false)
    }
  }

  const openAdd = () => {
    setEditRow(null)
    vForm.resetFields()
    setVOpen(true)
  }
  const openEdit = (row) => {
    setEditRow(row)
    vForm.setFieldsValue({
      code: row.code,
      name: row.name,
      exchange: row.exchange,
      multiplier: row.multiplier,
      margin_rate: row.margin_rate,
    })
    setVOpen(true)
  }
  const handleSaveVariety = async () => {
    const values = await vForm.validateFields()
    try {
      if (editRow) {
        await updateFuturesVariety(editRow.code, values)
        message.success('品种配置已更新')
      } else {
        await createFuturesVariety(values)
        message.success('品种已补录')
      }
      setVOpen(false)
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }
  const handleDelVariety = async (code) => {
    try {
      await deleteFuturesVariety(code)
      message.success('已删除')
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  const handleSaveContract = async () => {
    const values = await cForm.validateFields()
    try {
      await createFuturesContract(values)
      message.success('合约覆盖已添加')
      setCOpen(false)
      cForm.resetFields()
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }
  const handleDelContract = async (code) => {
    try {
      await deleteFuturesContract(code)
      message.success('已删除')
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  const varietyColumns = [
    { title: '代码', dataIndex: 'code', width: 80 },
    { title: '品种名', dataIndex: 'name', width: 120, render: (v) => v || '-' },
    { title: '交易所', dataIndex: 'exchange', width: 90, render: (v) => v || '-' },
    {
      title: '合约乘数',
      dataIndex: 'multiplier',
      width: 110,
      render: (v, r) => (
        <span>
          {v ?? '-'}
          {r.multiplier_source === 'manual' ? (
            <Tag color="blue" style={{ marginLeft: 4 }}>
              手录
            </Tag>
          ) : r.multiplier_source === 'missing' ? (
            <Tag color="red" style={{ marginLeft: 4 }}>
              缺乘数
            </Tag>
          ) : null}
        </span>
      ),
    },
    {
      title: '保证金率',
      dataIndex: 'margin_rate',
      width: 110,
      render: (v, r) =>
        v != null ? (
          <span>
            {pct(v)}
            {r.margin_source === 'eastmoney' ? (
              <Tag color="green" style={{ marginLeft: 4 }}>
                东财同步
              </Tag>
            ) : r.margin_source === 'manual' ? (
              <Tag color="blue" style={{ marginLeft: 4 }}>
                手动
              </Tag>
            ) : (
              <Tag style={{ marginLeft: 4 }}>默认10%</Tag>
            )}
          </span>
        ) : (
          <Tag>默认10%</Tag>
        ),
    },
    { title: '更新时间', dataIndex: 'updated_at', width: 150, render: (v) => v || '-' },
    {
      title: '操作',
      key: 'action',
      width: 130,
      render: (_, r) => (
        <Space>
          <Button size="small" onClick={() => openEdit(r)}>
            编辑
          </Button>
          <Popconfirm title={`删除品种「${r.code}」配置？`} onConfirm={() => handleDelVariety(r.code)}>
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const contractColumns = [
    { title: '合约代码', dataIndex: 'code', width: 120 },
    { title: '品种名', dataIndex: 'name', width: 140, render: (v) => v || '-' },
    { title: '保证金率', dataIndex: 'margin_rate', width: 110, render: (v) => pct(v) },
    { title: '更新时间', dataIndex: 'updated_at', render: (v) => v || '-' },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, r) => (
        <Popconfirm title={`删除合约覆盖「${r.code}」？`} onConfirm={() => handleDelContract(r.code)}>
          <Button size="small" danger>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ]

  const varietyRows = list.filter((r) => r.level === 'variety')
  const contractRows = list.filter((r) => r.level === 'contract')

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {status.new_varieties?.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`检测到 ${status.new_varieties.length} 个新品种乘数未配置`}
          description={
            <Space wrap>
              {status.new_varieties.map((v) => (
                <Tag key={v.code} color="orange">
                  {v.code}({v.name}) 保证金 {pct(v.margin_rate)}
                </Tag>
              ))}
              <span style={{ fontSize: 12, color: '#888' }}>
                请补录合约乘数后即可自动计算占用资金
              </span>
            </Space>
          }
        />
      )}
      <Card
        title="期货参数管理（仅管理员）"
        extra={
          <Space>
            <Statistic
              title="上次同步"
              value={status.last_sync || '未同步'}
              valueStyle={{ fontSize: 14 }}
            />
            <Button icon={<SyncOutlined />} loading={syncing} onClick={handleSync}>
              立即同步保证金率
            </Button>
          </Space>
        }
      >
        <Alert
          style={{ marginBottom: 16 }}
          type="info"
          showIcon
          message="保证金率来自东方财富每日自动同步（期货公司实际收取比例）；合约乘数内置 88 个主流品种，新品种可在下方补录。"
        />
        <Tabs
          items={[
            {
              key: 'variety',
              label: `品种配置（${varietyRows.length}）`,
              children: (
                <Table
                  rowKey={(r) => `v-${r.code}`}
                  columns={varietyColumns}
                  dataSource={varietyRows}
                  loading={loading}
                  size="middle"
                  pagination={{ pageSize: 20, showSizeChanger: false }}
                />
              ),
            },
            {
              key: 'contract',
              label: `合约覆盖（${contractRows.length}）`,
              children: (
                <Table
                  rowKey={(r) => `c-${r.code}`}
                  columns={contractColumns}
                  dataSource={contractRows}
                  loading={loading}
                  size="middle"
                  pagination={false}
                />
              ),
            },
          ]}
        />
        <Space style={{ marginTop: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
            补录品种
          </Button>
          <Button icon={<PlusOutlined />} onClick={() => { cForm.resetFields(); setCOpen(true) }}>
            合约保证金率覆盖
          </Button>
          <Button icon={<ReloadOutlined />} onClick={fetchData}>
            刷新
          </Button>
        </Space>
      </Card>

      {/* 新增/编辑品种 */}
      <Modal
        title={editRow ? `编辑品种 ${editRow.code}` : '补录品种'}
        open={vOpen}
        onCancel={() => setVOpen(false)}
        onOk={handleSaveVariety}
        destroyOnClose
      >
        <Form form={vForm} layout="vertical">
          <Form.Item
            name="code"
            label="品种代码"
            rules={[{ required: true, message: '请输入品种代码，如 AL' }]}
          >
            <Input placeholder="如 AL / RB / BZ" disabled={!!editRow} />
          </Form.Item>
          <Form.Item name="name" label="品种名">
            <Input placeholder="如 沪铝 / 纯苯" />
          </Form.Item>
          <Form.Item name="exchange" label="交易所">
            <Input placeholder="如 上期所 / 大商所 / 郑商所" />
          </Form.Item>
          <Form.Item
            name="multiplier"
            label="合约乘数（吨/手或克/手）"
            extra="留空则沿用内置表（新品种必填）"
          >
            <InputNumber style={{ width: '100%' }} min={0} placeholder="如 5" />
          </Form.Item>
          <Form.Item name="margin_rate" label="保证金率（0.17=17%）" extra="留空则用东财同步值或默认10%">
            <InputNumber style={{ width: '100%' }} min={0} max={1} step={0.01} placeholder="如 0.17" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 新增合约覆盖 */}
      <Modal title="合约保证金率覆盖" open={cOpen} onCancel={() => setCOpen(false)} onOk={handleSaveContract} destroyOnClose>
        <Form form={cForm} layout="vertical">
          <Form.Item
            name="code"
            label="合约代码"
            rules={[{ required: true, message: '请输入完整合约代码，如 AL2609' }]}
          >
            <Input placeholder="如 AL2609（个别合约费率不同时配置）" />
          </Form.Item>
          <Form.Item name="name" label="品种名">
            <Input placeholder="如 沪铝" />
          </Form.Item>
          <Form.Item
            name="margin_rate"
            label="保证金率（0.17=17%）"
            rules={[{ required: true, message: '请输入保证金率' }]}
          >
            <InputNumber style={{ width: '100%' }} min={0} max={1} step={0.01} placeholder="如 0.25" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
