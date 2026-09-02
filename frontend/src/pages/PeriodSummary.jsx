import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import dayjs from 'dayjs'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
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
  BarChartOutlined,
  CopyOutlined,
  DeleteOutlined,
} from '@ant-design/icons'
import {
  listPhaseReviews,
  savePhaseReview,
  updatePhaseReview,
  deletePhaseReview,
  parsePhaseReviewFile,
  listMarketReviews,
  generateMarketReview,
  deleteMarketReview,
} from '../api'
import { useDraft } from '../utils/draft'
import MarkdownView from '../components/MarkdownView'

const INSTRUMENT_TYPES = ['A股', '商品期货', '数字货币']

// V1.008.1 频率元信息（antd DatePicker picker 支持 week/month/quarter/year）
const PERIOD_META = {
  week: { label: '周复盘', format: 'YYYY-MM-DD', placeholder: '选择周（周一~周日）' },
  month: { label: '月复盘', format: 'YYYY-MM', placeholder: '选择月（1号~月末）' },
  quarter: { label: '季度复盘', format: 'YYYY [Q]Q', placeholder: '选择季度（季初~季末）' },
  year: { label: '年度复盘', format: 'YYYY', placeholder: '选择年度（1月1日~12月31日）' },
}
// 历史列表 tag 展示映射
const PERIOD_TAG_META = {
  week: { color: 'blue', label: '周' },
  month: { color: 'purple', label: '月' },
  quarter: { color: 'green', label: '季' },
  year: { color: 'orange', label: '年' },
  custom: { color: 'cyan', label: 'AI' },
}

dayjs.extend(quarterOfYear)

// 草稿缓存 key：阶段总结（周/月 × 起止 × 品种）
const draftKeyOf = (mode, p, instrumentType) =>
  `phase_summary:${mode}:${p.start}~${p.end}:${instrumentType || 'all'}`

export default function PeriodSummary() {
  const [periodMode, setPeriodMode] = useState('week') // week / month / quarter / year
  const [periodDate, setPeriodDate] = useState(() => dayjs())
  const [summaryType, setSummaryType] = useState('') // ''=全部/通用、A股/商品期货/数字货币
  const [curReview, setCurReview] = useState(null)
  const [allReviews, setAllReviews] = useState([])
  const [reviewLoading, setReviewLoading] = useState(false)
  // 历史筛选（V1.008.1：按频率 + 品种过滤）
  const [histType, setHistType] = useState('') // ''=全部 / week / month / quarter / year
  const [histInstrument, setHistInstrument] = useState('') // ''=全部
  const [histLoading, setHistLoading] = useState(false)
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
  // 盘面综述（V1.008.2 功能1）
  const [mrOpen, setMrOpen] = useState(false)
  const [mrLoading, setMrLoading] = useState(false)
  const [mrGenerating, setMrGenerating] = useState(false)
  const [mrReview, setMrReview] = useState(null) // 当前选中键下的已有综述
  const [mrError, setMrError] = useState('')

  // ===== 频率粒度归一（周=周一~周日；月=1号~月末；季=季初月1号~季末月末；年=1月1日~12月31日，范围含结束日）=====
  const normalizePeriod = useCallback((mode, v) => {
    if (!v) return null
    if (mode === 'week') {
      const start = v.subtract((v.day() || 7) - 1, 'day')
      return { start: start.format('YYYY-MM-DD'), end: start.add(6, 'day').format('YYYY-MM-DD') }
    }
    if (mode === 'quarter') {
      // 季度：季首月1号 ~ 季末月最后一日（手动计算，不依赖 quarterOfYear 插件）
      const q = Math.floor(v.month() / 3) // 0-3
      return {
        start: v.month(q * 3).startOf('month').format('YYYY-MM-DD'),
        end: v.month(q * 3 + 2).endOf('month').format('YYYY-MM-DD'),
      }
    }
    if (mode === 'year') {
      return {
        start: v.startOf('year').format('YYYY-MM-DD'),
        end: v.endOf('year').format('YYYY-MM-DD'),
      }
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
    setHistLoading(true)
    try {
      const params = {}
      if (histType) params.period_type = histType
      if (histInstrument) params.instrument_type = histInstrument
      setAllReviews(await listPhaseReviews(params))
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setHistLoading(false)
    }
  }, [histType, histInstrument])

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
      message.warning('请先选择周期')
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

  // ===== 盘面综述（V1.008.2 功能1）：按 频率→起止×品种 生成/回看 =====
  const openMarketReview = async () => {
    const p = normalizePeriod(periodMode, periodDate)
    if (!p) {
      message.warning('请先选择周期')
      return
    }
    setMrOpen(true)
    setMrReview(null)
    setMrError('')
    setMrLoading(true)
    try {
      const params = { start: p.start, end: p.end }
      if (summaryType) params.instrument_type = summaryType
      const list = await listMarketReviews(params)
      // 同键唯一：''（通用）与具体品种区分，取完全匹配项
      const hit = list.find((r) => (r.instrument_type || '') === (summaryType || '')) || null
      setMrReview(hit)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setMrLoading(false)
    }
  }

  const handleGenerateMarketReview = async () => {
    const p = normalizePeriod(periodMode, periodDate)
    if (!p) return
    setMrGenerating(true)
    setMrError('')
    try {
      const review = await generateMarketReview({
        instrument_type: summaryType || '',
        start: p.start,
        end: p.end,
      })
      setMrReview(review)
      message.success(mrReview ? '已重新生成并覆盖' : '盘面综述已生成')
    } catch (e) {
      setMrError(e?.response?.data?.detail || '生成失败，请稍后重试')
    } finally {
      setMrGenerating(false)
    }
  }

  const handleDeleteMarketReview = async () => {
    if (!mrReview) return
    try {
      await deleteMarketReview(mrReview.id)
      message.success('已删除盘面综述')
      setMrReview(null)
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  const handleCopyMarketReview = async () => {
    if (!mrReview) return
    try {
      await navigator.clipboard.writeText(mrReview.content || '')
      message.success('已复制全文，可直接粘贴到写总结')
    } catch (e) {
      message.warning('复制失败，请手动选择复制')
    }
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {/* 功能说明 */}
      <Alert
        type="info"
        showIcon
        icon={<FileTextOutlined />}
        message="手写阶段复盘总结 + 阶段盘面综述"
        description={
          <span>
            按 <b>周 / 月 / 季 / 年 × 品种</b> 手写复盘总结（支持导入 .txt / .md / .docx）。
            点击 <b>「盘面综述」</b> 可按当前所选周期与品种自动生成
            <b>行情数据速览 + AI 点评</b>报告，供写总结时参考（联网抓取，A股约 20~90 秒）。
            写完后到【阶段复盘】页点击 <b>AI 阶段分析</b>，AI 会自动参考本模块的历史总结、
            <b>自动结合该阶段盘面环境</b>解读交易，并输出<b>改进落实情况追踪</b>。
            编辑内容<b>自动保存草稿</b>，误关弹窗不丢失。
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
              周/月/季/年粒度 × 品种，AI 阶段分析自动参考并追踪连续性改进
            </Typography.Text>
          </Space>
        }
        extra={
          <Button
            size="small"
            icon={<HistoryOutlined />}
            onClick={() => {
              setHistoryOpen(true)
              loadHistory() // 打开时刷新（含筛选条件）
            }}
          >
            历史总结（{allReviews.length}）
          </Button>
        }
      >
        <Space wrap size={12}>
          <Radio.Group value={periodMode} onChange={(e) => setPeriodMode(e.target.value)}>
            {Object.entries(PERIOD_META).map(([k, m]) => (
              <Radio.Button key={k} value={k}>
                {m.label}
              </Radio.Button>
            ))}
          </Radio.Group>
          <DatePicker
            picker={periodMode}
            value={periodDate}
            onChange={setPeriodDate}
            allowClear
            format={PERIOD_META[periodMode].format}
            placeholder={PERIOD_META[periodMode].placeholder}
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
          <Button
            size="small"
            type="primary"
            ghost
            icon={<BarChartOutlined />}
            disabled={!periodDate}
            onClick={openMarketReview}
          >
            盘面综述
          </Button>
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
              : '选择周期与品种后查看该阶段总结'}
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

      {/* 历史总结列表（V1.008.1：频率 + 品种筛选） */}
      <Modal
        title={`历史阶段总结（${allReviews.length}）`}
        open={historyOpen}
        onCancel={() => setHistoryOpen(false)}
        footer={null}
        width={760}
      >
        <Space wrap style={{ marginBottom: 12 }}>
          <Select
            allowClear
            placeholder="全部频率"
            style={{ width: 130 }}
            value={histType || undefined}
            onChange={(v) => setHistType(v || '')}
            options={[
              { value: 'week', label: '周总结' },
              { value: 'month', label: '月总结' },
              { value: 'quarter', label: '季度总结' },
              { value: 'year', label: '年度总结' },
            ]}
          />
          <Select
            allowClear
            placeholder="全部品种"
            style={{ width: 130 }}
            value={histInstrument || undefined}
            onChange={(v) => setHistInstrument(v || '')}
            options={INSTRUMENT_TYPES.map((t) => ({ value: t, label: t }))}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            选 AI 分析生成的记录显示在「全部频率」中
          </Typography.Text>
        </Space>
        <Spin spinning={histLoading}>
          <List
            size="small"
            dataSource={allReviews}
            locale={{ emptyText: '暂无总结，先写一篇吧' }}
            renderItem={(r) => {
              const pt = PERIOD_TAG_META[r.period_type] || PERIOD_TAG_META.custom
              return (
                <List.Item style={{ cursor: 'pointer' }} onClick={() => setViewReview(r)}>
                  <List.Item.Meta
                    title={
                      <Space wrap>
                        <Tag color={pt.color}>{pt.label}</Tag>
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
              )
            }}
          />
        </Spin>
      </Modal>

      {/* 历史总结详情（含 AI 摘要） */}
      <Modal
        title={viewReview?.title || '阶段总结'}
        open={!!viewReview}
        onCancel={() => setViewReview(null)}
        footer={null}
        width={720}
      >
        {viewReview && (() => {
          const pt = PERIOD_TAG_META[viewReview.period_type] || PERIOD_TAG_META.custom
          return (
            <div>
              <Space wrap style={{ marginBottom: 12 }}>
                <Tag color={pt.color}>
                  {pt.label === 'AI' ? 'AI 分析' : `${pt.label}复盘`} {viewReview.start} ~ {viewReview.end}
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
          )
        })()}
      </Modal>

      {/* 盘面综述（V1.008.2 功能1）生成/回看 */}
      <Modal
        title={
          <Space>
            <BarChartOutlined style={{ color: '#534AB7' }} />
            阶段盘面综述
          </Space>
        }
        open={mrOpen}
        onCancel={() => setMrOpen(false)}
        width={860}
        footer={
          <Space>
            {mrReview && (
              <>
                <Popconfirm
                  title="删除该盘面综述？"
                  description="删除后需要重新生成才能恢复"
                  onConfirm={handleDeleteMarketReview}
                >
                  <Button size="small" danger icon={<DeleteOutlined />}>
                    删除
                  </Button>
                </Popconfirm>
                <Button size="small" icon={<CopyOutlined />} onClick={handleCopyMarketReview}>
                  复制全文
                </Button>
              </>
            )}
            <Button
              type="primary"
              loading={mrGenerating}
              onClick={handleGenerateMarketReview}
              icon={mrReview ? <SyncOutlined /> : <BarChartOutlined />}
            >
              {mrGenerating ? '采集中…' : mrReview ? '重新生成（覆盖）' : '生成盘面综述'}
            </Button>
          </Space>
        }
      >
        <Spin spinning={mrLoading}>
          {(() => {
            const p = normalizePeriod(periodMode, periodDate) || {}
            const instLabel = summaryType || '三大市场'
            return (
              <div style={{ maxHeight: '65vh', overflowY: 'auto', paddingRight: 6 }}>
                <Space wrap style={{ marginBottom: 8 }}>
                  <Tag color="geekblue">{instLabel}</Tag>
                  <Tag color="purple">
                    {PERIOD_META[periodMode]?.label || periodMode}：{p.start} ~ {p.end}
                  </Tag>
                  {mrReview && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      生成于 {mrReview.updated_at || mrReview.created_at}
                    </Typography.Text>
                  )}
                </Space>
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 10 }}
                  message={
                    <span>
                      联网抓取公开行情生成 <b>{instLabel}</b> 阶段盘面综述（数据速览 + AI 点评），
                      供写阶段复盘总结参考。生成耗时约 20~90 秒（A股较久），请耐心等待；
                      同时间段重复生成会<b>覆盖</b>旧报告。
                    </span>
                  }
                />
                {mrError && (
                  <Alert type="error" showIcon style={{ marginBottom: 10 }} message={mrError} />
                )}
                {mrReview ? (
                  <>
                    <Typography.Title level={5} style={{ marginTop: 4 }}>
                      {mrReview.title}
                    </Typography.Title>
                    <MarkdownView text={mrReview.content} />
                  </>
                ) : (
                  !mrLoading &&
                  !mrGenerating && (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description="该时间段还没有盘面综述，点击下方「生成盘面综述」开始"
                      style={{ margin: '24px 0' }}
                    />
                  )
                )}
                {mrGenerating && (
                  <div style={{ textAlign: 'center', padding: '30px 0' }}>
                    <Spin />
                    <div style={{ marginTop: 8, color: '#8c8c8c', fontSize: 13 }}>
                      正在采集行情并生成 AI 点评，预计 20~90 秒…
                    </div>
                  </div>
                )}
              </div>
            )
          })()}
        </Spin>
      </Modal>

      {/* 底部提示：AI 联动入口在阶段复盘页 */}
      <Card size="small" style={{ background: '#fafafa' }}>
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Space>
            <RobotOutlined style={{ color: '#534AB7' }} />
            <Typography.Text type="secondary">
              需要做 AI 阶段复盘？→ 到【阶段复盘】页选择时间段与品种后点击「AI 阶段分析」，
              将自动参考这里的手写总结、自动联网结合该阶段盘面环境，并输出「改进落实情况追踪」。
            </Typography.Text>
          </Space>
          <Space style={{ paddingLeft: 24 }}>
            <BarChartOutlined style={{ color: '#534AB7' }} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              盘面综述报告（含数据速览与 AI 点评）在【复盘总结】页生成后可复制全文、粘贴到下方编辑框，
              或作为 AI 阶段分析的参考素材。
            </Typography.Text>
          </Space>
        </Space>
      </Card>
    </Space>
  )
}
