import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import dayjs from 'dayjs'
import {
  Card,
  Space,
  Button,
  DatePicker,
  Select,
  Radio,
  Tag,
  Typography,
  Spin,
  Empty,
  Modal,
  Input,
  Popconfirm,
  message,
  List,
  Divider,
  Alert,
} from 'antd'
import {
  FileTextOutlined,
  EditOutlined,
  UploadOutlined,
  HistoryOutlined,
  SyncOutlined,
  ClearOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import {
  listPhaseReviews,
  savePhaseReview,
  updatePhaseReview,
  deletePhaseReview,
  parsePhaseReviewFile,
} from '../api'
import { useDraft } from '../utils/draft'

const INSTRUMENT_TYPES = ['A股', '商品期货', '数字货币']

// 草稿缓存 key：阶段总结（周/月 × 起止 × 品种）
const draftKeyOf = (mode, p, instrumentType) =>
  `phase_summary:${mode}:${p.start}~${p.end}:${instrumentType || 'all'}`

export default function PeriodSummary() {
  const [periodMode, setPeriodMode] = useState('week') // week / month
  const [periodDate, setPeriodDate] = useState(() => dayjs())
  const [summaryType, setSummaryType] = useState('') // ''=全部/通用、A股/商品期货/数字货币
  const [curReview, setCurReview] = useState(null)
  const [allReviews, setAllReviews] = useState([])
  const [reviewLoading, setReviewLoading] = useState(false)
  // 编辑弹窗
  const [editOpen, setEditOpen] = useState(false)
  const [editDraftKey, setEditDraftKey] = useState(null)
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')
  const [draftTip, setDraftTip] = useState(false) // 是否显示"已恢复草稿"提示条
  const [saving, setSaving] = useState(false)
  const [importing, setImporting] = useState(false)
  // 历史
  const [historyOpen, setHistoryOpen] = useState(false)
  const [viewReview, setViewReview] = useState(null)
  const fileInputRef = useRef(null)

  // ===== 周/月粒度归一（周=周一~周日；月=1号~月末最后一日，范围含结束日）=====
  const normalizePeriod = useCallback((mode, v) => {
    if (!v) return null
    if (mode === 'week') {
      const start = v.subtract((v.day() || 7) - 1, 'day')
      return { start: start.format('YYYY-MM-DD'), end: start.add(6, 'day').format('YYYY-MM-DD') }
    }
    return {
      start: v.startOf('month').format('YYYY-MM-DD'),
      end: v.endOf('month').format('YYYY-MM-DD'),
    }
  }, [])

  // ===== 草稿缓存（编辑弹窗输入自动保存 / 误关恢复）=====
  const editFormData = useMemo(
    () => ({ title: editTitle, content: editContent }),
    [editTitle, editContent]
  )
  const { checkDraft, clear: clearDraftDraft, hasDraft, draftTime } = useDraft(editDraftKey, {
    formData: editOpen ? editFormData : null, // 弹窗关闭时不自动保存
  })

  // ===== 数据加载 =====
  const loadReview = useCallback(async () => {
    if (!periodDate) {
      setCurReview(null)
      return
    }
    const p = normalizePeriod(periodMode, periodDate)
    setReviewLoading(true)
    try {
      const params = { period_type: periodMode, start: p.start, end: p.end }
      if (summaryType) params.instrument_type = summaryType
      const list = await listPhaseReviews(params)
      const hit = list.find((r) => r.instrument_type === (summaryType || '')) || null
      setCurReview(hit)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setReviewLoading(false)
    }
  }, [periodMode, periodDate, summaryType, normalizePeriod])

  const loadHistory = useCallback(async () => {
    try {
      setAllReviews(await listPhaseReviews({}))
    } catch (e) {
      /* 拦截器已提示 */
    }
  }, [])

  useEffect(() => {
    loadReview()
    loadHistory()
  }, [loadReview, loadHistory])

  // ===== 写 / 编辑：打开弹窗，若有草稿则自动恢复 =====
  const openEdit = () => {
    const p = normalizePeriod(periodMode, periodDate)
    if (!p) return
    setEditDraftKey(draftKeyOf(periodMode, p, summaryType))
    setEditTitle(curReview?.title || '')
    setEditContent(curReview?.content || '')
    setDraftTip(false)
    setEditOpen(true)
  }

  // 打开弹窗后：检测未保存草稿 → 恢复并提示
  useEffect(() => {
    if (!editOpen || !editDraftKey) return
    const d = checkDraft()
    const hasText = d?.values && (d.values.title || d.values.content)
    if (hasText) {
      setEditTitle(d.values.title || '')
      setEditContent(d.values.content || '')
      setDraftTip(true)
    } else {
      setDraftTip(false)
    }
  }, [editOpen, editDraftKey, checkDraft])

  const handleImportFile = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setImporting(true)
    try {
      const name = file.name.toLowerCase()
      // .docx 由服务端零依赖解析；.txt/.md 前端直读
      const text = name.endsWith('.docx') ? (await parsePhaseReviewFile(file)).text : await file.text()
      setEditContent((c) => (c ? c + '\n\n' : '') + text.trim())
      message.success('已导入文件内容')
    } catch (err) {
      /* 拦截器已提示 */
    } finally {
      setImporting(false)
    }
  }

  const handleSaveReview = async () => {
    const p = normalizePeriod(periodMode, periodDate)
    if (!p) {
      message.warning('请先选择周/月')
      return
    }
    if (!editContent.trim()) {
      message.warning('总结内容不能为空')
      return
    }
    setSaving(true)
    try {
      const payload = {
        period_type: periodMode,
        start: p.start,
        end: p.end,
        instrument_type: summaryType || '',
        title: editTitle,
        content: editContent,
      }
      if (curReview) await updatePhaseReview(curReview.id, payload)
      else await savePhaseReview(payload)
      message.success('已保存')
      clearDraftDraft() // 保存成功 → 清除草稿
      setDraftTip(false)
      setEditOpen(false)
      loadReview()
      loadHistory()
    } catch (err) {
      /* 拦截器已提示 */
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteReview = async () => {
    if (!curReview) return
    try {
      await deletePhaseReview(curReview.id)
      message.success('已删除')
      setCurReview(null)
      loadHistory()
    } catch (err) {
      /* 拦截器已提示 */
    }
  }

  const handleDiscardDraft = () => {
    clearDraftDraft()
    setDraftTip(false)
    setEditTitle(curReview?.title || '')
    setEditContent(curReview?.content || '')
    message.info('已清空草稿')
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {/* 功能说明 */}
      <Alert
        type="info"
        showIcon
        icon={<FileTextOutlined />}
        message="手写阶段复盘总结"
        description={
          <span>
            按 <b>周 / 月 × 品种</b> 手写复盘总结（支持导入 .txt / .md / .docx）。
            写完后到【阶段复盘】页点击 <b>AI 阶段分析</b>，AI 会自动参考本模块的历史总结，
            并输出<b>改进落实情况追踪</b>（本期 vs 前几期连续对比），结果自动保存回对应总结。
            编辑内容<b>自动保存草稿</b>，误关弹窗不丢失，重新打开自动恢复。
          </span>
        }
      />

      {/* 筛选 + 当前总结 */}
      <Card
        size="small"
        title={
          <Space>
            <EditOutlined style={{ color: '#534AB7' }} />
            我的阶段总结
            <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 'normal' }}>
              周/月粒度 × 品种，AI 阶段分析自动参考并追踪连续性改进
            </Typography.Text>
          </Space>
        }
        extra={
          <Button size="small" icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
            历史总结（{allReviews.length}）
          </Button>
        }
      >
        <Space wrap size={12}>
          <Radio.Group value={periodMode} onChange={(e) => setPeriodMode(e.target.value)}>
            <Radio.Button value="week">周复盘</Radio.Button>
            <Radio.Button value="month">月复盘</Radio.Button>
          </Radio.Group>
          <DatePicker
            picker={periodMode}
            value={periodDate}
            onChange={setPeriodDate}
            allowClear
            format={periodMode === 'week' ? 'YYYY-MM-DD' : 'YYYY-MM'}
            placeholder={periodMode === 'week' ? '选择周（周一~周日）' : '选择月（1号~月末）'}
          />
          <Select
            allowClear
            placeholder="全部品种"
            style={{ width: 130 }}
            value={summaryType}
            onChange={setSummaryType}
            options={[
              { value: '', label: '全部/通用' },
              ...INSTRUMENT_TYPES.map((t) => ({ value: t, label: t })),
            ]}
          />
          {curReview ? (
            <>
              <Button size="small" icon={<EditOutlined />} onClick={openEdit}>
                编辑
              </Button>
              <Popconfirm title="删除该阶段总结？" onConfirm={handleDeleteReview}>
                <Button size="small" danger>
                  删除
                </Button>
              </Popconfirm>
            </>
          ) : (
            <Button size="small" type="primary" disabled={!periodDate} onClick={openEdit}>
              写总结
            </Button>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {curReview
              ? `最后编辑 ${curReview.updated_at}`
              : '选择周/月与品种后查看该阶段总结'}
          </Typography.Text>
        </Space>

        <Spin spinning={reviewLoading}>
          {curReview ? (
            <div style={{ marginTop: 12 }}>
              {curReview.title && (
                <Typography.Text strong style={{ marginRight: 8 }}>
                  {curReview.title}
                </Typography.Text>
              )}
              {curReview.has_ai_result && <Tag color="blue">AI 已分析</Tag>}
              <Typography.Paragraph
                ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}
                style={{ marginBottom: 0, marginTop: 6 }}
              >
                {curReview.content ||
                  (curReview.has_ai_result
                    ? '（本阶段暂无手写内容，仅保存了 AI 分析结果）'
                    : '')}
              </Typography.Paragraph>
            </div>
          ) : (
            periodDate && (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="本阶段还没有手写总结"
                style={{ marginTop: 12 }}
              />
            )
          )}
        </Spin>
      </Card>

      {/* 写/编辑阶段总结（草稿自动保存） */}
      <Modal
        title={curReview ? '编辑阶段总结' : '写阶段总结'}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleSaveReview}
        confirmLoading={saving}
        okText="保存"
        width={720}
        afterClose={() => setEditDraftKey(null)}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          {draftTip && (
            <Alert
              type="warning"
              showIcon
              closable
              onClose={() => setDraftTip(false)}
              message={
                <Space wrap>
                  已恢复未保存的草稿
                  {draftTime && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      （自动保存于 {dayjs(draftTime).format('HH:mm:ss')}）
                    </Typography.Text>
                  )}
                  <Button size="small" type="link" icon={<ClearOutlined />} onClick={handleDiscardDraft}>
                    清空草稿
                  </Button>
                </Space>
              }
            />
          )}
          <Input
            placeholder="标题（可选，如：第8周复盘）"
            value={editTitle}
            maxLength={100}
            onChange={(e) => setEditTitle(e.target.value)}
          />
          <Input.TextArea
            placeholder="手写本阶段复盘总结：执行情况、盈亏归因、纪律问题、下一期改进计划…（输入自动保存草稿，误关弹窗不丢失）"
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            autoSize={{ minRows: 8, maxRows: 20 }}
          />
          <Space>
            <Button
              size="small"
              icon={<UploadOutlined />}
              loading={importing}
              onClick={() => fileInputRef.current?.click()}
            >
              导入文件（.txt/.md/.docx）
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Word 由服务端解析；导入内容追加到文本框，保存前可编辑
            </Typography.Text>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.markdown,.docx"
              style={{ display: 'none' }}
              onChange={handleImportFile}
            />
          </Space>
        </Space>
      </Modal>

      {/* 历史总结列表 */}
      <Modal
        title={`历史阶段总结（${allReviews.length}）`}
        open={historyOpen}
        onCancel={() => setHistoryOpen(false)}
        footer={null}
        width={760}
      >
        <List
          size="small"
          dataSource={allReviews}
          locale={{ emptyText: '暂无总结，先写一篇吧' }}
          renderItem={(r) => (
            <List.Item style={{ cursor: 'pointer' }} onClick={() => setViewReview(r)}>
              <List.Item.Meta
                title={
                  <Space wrap>
                    <Tag color={r.period_type === 'week' ? 'blue' : 'purple'}>
                      {r.period_type === 'week' ? '周' : r.period_type === 'month' ? '月' : 'AI'}
                    </Tag>
                    <Typography.Text strong style={{ fontSize: 13 }}>
                      {r.start} ~ {r.end}
                    </Typography.Text>
                    {r.instrument_type && <Tag color="geekblue">{r.instrument_type}</Tag>}
                    {r.title && <Typography.Text type="secondary">{r.title}</Typography.Text>}
                    {r.has_ai_result && <Tag color="cyan">AI 结果</Tag>}
                  </Space>
                }
                description={
                  <Typography.Text type="secondary" ellipsis style={{ fontSize: 12 }}>
                    {r.content || (r.has_ai_result ? '（仅 AI 分析结果）' : '（空）')}
                  </Typography.Text>
                }
              />
            </List.Item>
          )}
        />
      </Modal>

      {/* 历史总结详情（含 AI 摘要） */}
      <Modal
        title={viewReview?.title || '阶段总结'}
        open={!!viewReview}
        onCancel={() => setViewReview(null)}
        footer={null}
        width={720}
      >
        {viewReview && (
          <div>
            <Space wrap style={{ marginBottom: 12 }}>
              <Tag
                color={
                  viewReview.period_type === 'week'
                    ? 'blue'
                    : viewReview.period_type === 'month'
                      ? 'purple'
                      : 'cyan'
                }
              >
                {viewReview.period_type === 'week'
                  ? '周复盘'
                  : viewReview.period_type === 'month'
                    ? '月复盘'
                    : 'AI 分析'}{' '}
                {viewReview.start} ~ {viewReview.end}
              </Tag>
              {viewReview.instrument_type && <Tag color="geekblue">{viewReview.instrument_type}</Tag>}
              {viewReview.has_ai_result && <Tag color="cyan">AI 结果</Tag>}
            </Space>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
              {viewReview.content || '（无手写内容）'}
            </Typography.Paragraph>
            {viewReview.has_ai_result && (
              <>
                <Divider orientation="left" plain>
                  <SyncOutlined style={{ color: '#534AB7' }} /> AI 分析摘要
                </Divider>
                <Typography.Paragraph style={{ fontSize: 13, color: '#595959' }}>
                  {viewReview.ai_summary || '（AI 分析结果已保存，可在该阶段重新分析查看）'}
                </Typography.Paragraph>
              </>
            )}
          </div>
        )}
      </Modal>

      {/* 底部提示：AI 联动入口在阶段复盘页 */}
      <Card size="small" style={{ background: '#fafafa' }}>
        <Space>
          <RobotOutlined style={{ color: '#534AB7' }} />
          <Typography.Text type="secondary">
            需要做 AI 阶段复盘？→ 到【阶段复盘】页选择时间段与品种后点击「AI 阶段分析」，
            将自动参考这里的手写总结，并输出「改进落实情况追踪」。
          </Typography.Text>
        </Space>
      </Card>
    </Space>
  )
}
