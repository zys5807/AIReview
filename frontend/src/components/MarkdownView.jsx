import { Fragment } from 'react'
import { Typography, Table } from 'antd'

/**
 * MarkdownView — 无依赖的轻量 markdown 渲染（V1.008.2 盘面综述正文展示用）
 *
 * 支持：标题(#/##/###)、表格(| a | b |)、引用(>)、无序/有序列表、粗体(**x**)、
 *      分隔线(---)、普通段落。不注入 HTML，无 XSS 风险；不支持代码块/链接等复杂语法。
 */
const { Paragraph, Text } = Typography

// 行内解析：**加粗**
function renderInline(text, keyPrefix = 't') {
  const parts = String(text).split(/(\*\*[^*]+\*\*)/g).filter((p) => p !== '')
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**') && p.length > 4) {
      return <Text strong key={`${keyPrefix}-b-${i}`}>{p.slice(2, -2)}</Text>
    }
    return p
  })
}

function isTableSeparator(line) {
  // |---|---| 或 | :--- | 等分隔行
  return /^\|?[\s:|-]+\|?$/.test(line) && line.includes('-')
}

// 解析表格块：输入若干连续的 markdown 表格行
function renderTable(lines, key) {
  const rows = lines
    .filter((l) => l.trim().startsWith('|') && l.trim().endsWith('|'))
    .map((l) =>
      l
        .trim()
        .slice(1, -1)
        .split('|')
        .map((c) => c.trim()),
    )
  // 去掉分隔行 |---|---|
  const headerIdx = rows.findIndex((r) => r.some((c) => /^:?-{2,}:?$/.test(c)))
  let header = []
  let body = []
  if (headerIdx >= 0) {
    header = rows[headerIdx - 1] || []
    body = rows.slice(headerIdx + 1)
  } else {
    header = rows[0] || []
    body = rows.slice(1)
  }
  if (header.length === 0 && body.length === 0) return null
  const maxCol = Math.max(header.length, ...body.map((r) => r.length))
  const columns = Array.from({ length: maxCol }, (_, i) => ({
    title: header[i] != null ? renderInline(header[i], `h${key}-${i}`) : '',
    dataIndex: `c${i}`,
    key: `c${key}-${i}`,
    render: (v) => <span style={{ fontSize: 13 }}>{v}</span>,
  }))
  const data = body.map((r, ri) => {
    const row = {}
    for (let i = 0; i < maxCol; i++) row[`c${i}`] = r[i] != null ? renderInline(r[i], `r${key}-${ri}-${i}`) : ''
    return { key: `${key}-row-${ri}`, ...row }
  })
  return (
    <Table
      key={key}
      size="small"
      columns={columns}
      dataSource={data}
      pagination={false}
      bordered
      style={{ margin: '8px 0' }}
    />
  )
}

function renderList(lines, ordered, key) {
  const items = lines.map((l, i) => {
    const text = ordered ? l.replace(/^\s*\d+[.)]\s*/, '') : l.replace(/^\s*[-*+]\s+/, '')
    return (
      <li key={`${key}-li-${i}`} style={{ margin: '2px 0' }}>
        {renderInline(text, `${key}-li-${i}`)}
      </li>
    )
  })
  return ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>
}

/**
 * markdown 文本 → React 元素
 */
export function mdToElements(md) {
  if (!md) return null
  const lines = String(md).split('\n')
  const out = []
  let i = 0
  let blockKey = 0
  while (i < lines.length) {
    const line = lines[i]
    const key = `blk-${blockKey++}`
    // 空行
    if (line.trim() === '') {
      i++
      continue
    }
    // 分隔线
    if (/^\s*---+\s*$/.test(line)) {
      out.push(<div key={key} style={{ borderTop: '1px solid #f0f0f0', margin: '12px 0' }} />)
      i++
      continue
    }
    // 标题
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const level = h[1].length
      out.push(
        <div
          key={key}
          style={{
            fontSize: level === 1 ? 18 : level === 2 ? 16 : level === 3 ? 14.5 : 13.5,
            fontWeight: 600,
            margin: '14px 0 8px',
            color: '#262626',
          }}
        >
          {renderInline(h[2], key)}
        </div>,
      )
      i++
      continue
    }
    // 引用（连续 > 行合并为一段）
    if (line.trim().startsWith('>')) {
      const quoteLines = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''))
        i++
      }
      out.push(
        <div
          key={key}
          style={{
            borderLeft: '3px solid #d9d9d9',
            padding: '2px 0 2px 10px',
            color: '#8c8c8c',
            fontSize: 12.5,
            margin: '6px 0',
          }}
        >
          {quoteLines.map((q, qi) => (
            <div key={`${key}-q-${qi}`} style={{ margin: '1px 0' }}>
              {renderInline(q, `${key}-q-${qi}`)}
            </div>
          ))}
        </div>,
      )
      continue
    }
    // 表格块（连续以 | 开头的行）
    if (line.trim().startsWith('|')) {
      const tblLines = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tblLines.push(lines[i])
        i++
      }
      const t = renderTable(tblLines, key)
      if (t) out.push(t)
      continue
    }
    // 无序列表
    if (/^\s*[-*+]\s+/.test(line)) {
      const listLines = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        listLines.push(lines[i])
        i++
      }
      out.push(renderList(listLines, false, key))
      continue
    }
    // 有序列表
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const listLines = []
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        listLines.push(lines[i])
        i++
      }
      out.push(renderList(listLines, true, key))
      continue
    }
    // 普通段落：合并到下一个空行/块级语法
    const para = []
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^(#{1,4})\s/.test(lines[i]) &&
      !lines[i].trim().startsWith('|') &&
      !lines[i].trim().startsWith('>') &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]) &&
      !/^\s*---+\s*$/.test(lines[i])
    ) {
      para.push(lines[i])
      i++
    }
    if (para.length > 0) {
      out.push(
        <Paragraph
          key={key}
          style={{
            fontSize: 13.5,
            margin: '6px 0',
            whiteSpace: 'pre-wrap',
            color: '#3a3a3a',
          }}
        >
          {para.map((p, pi) => (
            <Fragment key={`${key}-p-${pi}`}>
              {pi > 0 && <br />}
              {renderInline(p, `${key}-p-${pi}`)}
            </Fragment>
          ))}
        </Paragraph>,
      )
    }
  }
  return out
}

/**
 * MarkdownView：markdown 文本 → 排版好的内容块
 */
export default function MarkdownView({ text, style }) {
  const els = mdToElements(text)
  if (!els) return null
  return <div style={style || {}}>{els}</div>
}
