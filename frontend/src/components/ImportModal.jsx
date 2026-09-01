import { useState } from 'react'
import {
  Modal,
  Upload,
  Button,
  Space,
  Table,
  Select,
  Alert,
  Tag,
  Typography,
  Spin,
  message,
  Steps,
} from 'antd'
import { InboxOutlined, ImportOutlined } from '@ant-design/icons'
import { parseImportFile, executeImport } from '../api'

const { Dragger } = Upload

// 标准字段（与后端 importer.STANDARD_FIELDS 一致）
const FIELD_OPTIONS = [
  { value: '', label: '不导入' },
  { value: 'datetime', label: '成交日期' },
  { value: 'time', label: '成交时间' },
  { value: 'code', label: '证券代码' },
  { value: 'name', label: '证券名称' },
  { value: 'direction', label: '买卖方向' },
  { value: 'price', label: '成交价格' },
  { value: 'volume', label: '成交数量' },
  { value: 'amount', label: '成交金额' },
  { value: 'fee', label: '手续费' },
  { value: 'stamp_tax', label: '印花税' },
  { value: 'transfer_fee', label: '过户费' },
  { value: 'close_pnl', label: '平仓盈亏' },
]

export default function ImportModal({ open, onClose, onSuccess }) {
  const [step, setStep] = useState(1)
  const [parsing, setParsing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [parse, setParse] = useState(null) // {headers, rows, mapping, total, file_id}
  const [colFields, setColFields] = useState([]) // 每列选中的字段

  const reset = () => {
    setStep(1)
    setParsing(false)
    setImporting(false)
    setParse(null)
    setColFields([])
  }

  const handleClose = () => {
    onClose()
    setTimeout(reset, 300)
  }

  const handleFile = async (file) => {
    if (parsing) return false
    setParsing(true)
    try {
      const r = await parseImportFile(file)
      // mapping: {field: col} → colFields: 每列字段
      const fields = []
      r.headers.forEach((_, i) => {
        fields.push('')
      })
      Object.entries(r.mapping || {}).forEach(([field, col]) => {
        fields[col] = field
      })
      setColFields(fields)
      setParse(r)
      setStep(2)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setParsing(false)
    }
    return false
  }

  const buildMapping = () => {
    const mapping = {}
    colFields.forEach((field, col) => {
      if (field && field !== '') mapping[field] = col
    })
    return mapping
  }

  const handleImport = async () => {
    if (!parse) return
    const mapping = buildMapping()
    setImporting(true)
    try {
      const res = await executeImport(parse.file_id, mapping)
      message.success(res?.message || '导入完成')
      onSuccess?.()
      handleClose()
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setImporting(false)
    }
  }

  const previewColumns = (parse?.headers || []).map((h, i) => ({
    title: h || `列${i + 1}`,
    dataIndex: `c${i}`,
    key: i,
    width: 110,
    ellipsis: true,
    render: (v) => v || '-',
  }))

  const previewData = (parse?.rows || []).map((row, ri) => {
    const obj = { key: ri }
    row.forEach((cell, ci) => {
      obj[`c${ci}`] = cell
    })
    return obj
  })

  return (
    <Modal
      title="导入交割单"
      open={open}
      onCancel={handleClose}
      width={860}
      footer={
        step === 2 ? (
          <Space>
            <Button onClick={() => setStep(1)}>返回重新选择</Button>
            <Button
              type="primary"
              icon={<ImportOutlined />}
              loading={importing}
              onClick={handleImport}
            >
              导入 {parse?.total ? `（共 ${parse.total} 行成交）` : ''}
            </Button>
          </Space>
        ) : null
      }
    >
      <Steps
        size="small"
        current={step - 1}
        items={[{ title: '上传文件' }, { title: '确认列映射' }, { title: '完成导入' }]}
        style={{ marginBottom: 16 }}
      />

      {step === 1 && (
        <div>
          <Dragger
            accept=".xlsx,.xlsm,.csv,.txt,.xls"
            showUploadList={false}
            beforeUpload={handleFile}
            disabled={parsing}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽交割单文件到此处</p>
            <p className="ant-upload-hint">
              支持同花顺 / 通达信 / 东方财富等导出的 Excel(.xlsx)、CSV、文本(txt) 文件
            </p>
          </Dragger>
          {parsing && (
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <Spin tip="正在解析文件..." />
            </div>
          )}
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 12 }}
            message="导入说明"
            description={
              <div style={{ fontSize: 12 }}>
                1. 在交易软件中导出"交割单/历史成交"为 Excel 或 CSV；
                <br />
                2. 系统会自动识别列并配对成完整交易（加仓/减仓/多空都支持）；
                <br />
                3. 重复导入会自动跳过；
                <br />
                4. 旧版 .xls 文件请先用 Excel 另存为 .xlsx。
              </div>
            }
          />
        </div>
      )}

      {step === 2 && parse && (
        <div>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            已识别 <b>{parse.total}</b> 行成交记录。请确认每列对应的字段（系统已自动匹配，如不准可手动修改）：
          </Typography.Paragraph>
          {parse.missing_required?.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 8 }}
              message="以下必要列未能自动识别，请在下方的下拉框中手动指定对应列"
              description={
                <div style={{ fontSize: 12 }}>
                  {FIELD_OPTIONS.filter((f) => parse.missing_required.includes(f.value)).map(
                    (f) => (
                      <Tag key={f.value} color="orange" style={{ margin: 2 }}>
                        {f.label}
                      </Tag>
                    ),
                  )}
                </div>
              }
            />
          )}
          <Space wrap size={4} style={{ marginBottom: 8 }}>
            {(parse.headers || []).map((h, i) => (
              <div key={i} style={{ display: 'inline-flex', flexDirection: 'column', gap: 2 }}>
                <Typography.Text style={{ fontSize: 12 }} ellipsis>
                  {h || `列${i + 1}`}
                </Typography.Text>
                <Select
                  size="small"
                  style={{ width: 110 }}
                  value={colFields[i]}
                  onChange={(v) => {
                    const next = [...colFields]
                    next[i] = v
                    setColFields(next)
                  }}
                  options={FIELD_OPTIONS}
                />
              </div>
            ))}
          </Space>
          <Table
            size="small"
            rowKey="key"
            pagination={{ pageSize: 8, showSizeChanger: false }}
            columns={previewColumns}
            dataSource={previewData}
            scroll={{ x: 'max-content' }}
          />
        </div>
      )}
    </Modal>
  )
}
