import { Progress, List, Tag, Typography, Space, Divider, Empty } from 'antd'

const scoreColor = (v) => {
  if (v >= 80) return '#52c41a'
  if (v >= 60) return '#faad14'
  return '#ff4d4f'
}

/**
 * 复盘报告展示
 * @param {object} report - { score, analysis: {score, dimensions, strengths, weaknesses, improvements, summary}, model_name, created_at }
 */
export default function ReportView({ report }) {
  if (!report) return <Empty description="暂无报告" />

  const a = report.analysis || {}

  return (
    <Space orientation="vertical" size={12} style={{ width: '100%' }}>
      <div style={{ textAlign: 'center' }}>
        <Progress
          type="dashboard"
          percent={report.score ?? a.score ?? 0}
          format={(p) => <span style={{ fontSize: 28 }}>{p}</span>}
          strokeColor={scoreColor(report.score ?? a.score ?? 0)}
        />
        <div style={{ color: '#888', fontSize: 12, marginTop: 4 }}>
          综合评分
          {report.model_name && ` · ${report.model_name}`}
          {report.created_at &&
            ` · ${new Date(report.created_at).toLocaleString()}`}
        </div>
      </div>

      <Divider titlePlacement="left" plain style={{ margin: '8px 0' }}>
        各维度评分
      </Divider>
      {(a.dimensions || []).map((d) => (
        <div key={d.name} style={{ marginBottom: 6 }}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Typography.Text strong>{d.name}</Typography.Text>
            <Tag color={scoreColor(d.score)}>{d.score}</Tag>
          </Space>
          <Progress
            percent={d.score}
            showInfo={false}
            strokeColor={scoreColor(d.score)}
            size="small"
          />
          {d.comment && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {d.comment}
            </Typography.Text>
          )}
        </div>
      ))}

      <Divider titlePlacement="left" plain style={{ margin: '8px 0' }}>
        总结
      </Divider>
      <Typography.Paragraph style={{ marginBottom: 0 }}>
        {a.summary || '无总结'}
      </Typography.Paragraph>

      <Divider titlePlacement="left" plain style={{ margin: '8px 0' }}>
        交易优点
      </Divider>
      <List
        size="small"
        dataSource={a.strengths || []}
        renderItem={(item) => (
          <List.Item>
            <Typography.Text style={{ color: '#3f8600' }}>● {item}</Typography.Text>
          </List.Item>
        )}
      />

      <Divider titlePlacement="left" plain style={{ margin: '8px 0' }}>
        交易不足
      </Divider>
      <List
        size="small"
        dataSource={a.weaknesses || []}
        renderItem={(item) => (
          <List.Item>
            <Typography.Text style={{ color: '#cf1322' }}>● {item}</Typography.Text>
          </List.Item>
        )}
      />

      <Divider titlePlacement="left" plain style={{ margin: '8px 0' }}>
        改进建议
      </Divider>
      <List
        size="small"
        dataSource={a.improvements || []}
        renderItem={(item) => (
          <List.Item>
            <Typography.Text style={{ color: '#185fa5' }}>● {item}</Typography.Text>
          </List.Item>
        )}
      />
    </Space>
  )
}
