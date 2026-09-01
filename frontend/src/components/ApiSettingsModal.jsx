import { useEffect, useState } from 'react'
import { Modal, Form, Input, message, Alert, Typography } from 'antd'
import { getLlmSettings, updateLlmSettings } from '../api'

export default function ApiSettingsModal({ open, onClose }) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [fetching, setFetching] = useState(false)
  const [source, setSource] = useState('') // db / env：当前配置来源

  useEffect(() => {
    if (!open) return
    setFetching(true)
    getLlmSettings()
      .then((cfg) => {
        setSource(cfg.source)
        form.setFieldsValue({
          api_key: cfg.api_key,
          base_url: cfg.base_url,
          model: cfg.model,
        })
      })
      .catch(() => {}) // 拦截器已提示
      .finally(() => setFetching(false))
  }, [open, form])

  const handleSubmit = async (values) => {
    setLoading(true)
    try {
      const res = await updateLlmSettings({
        api_key: values.api_key ?? '',
        base_url: values.base_url ?? '',
        model: values.model ?? '',
      })
      setSource(res.source)
      message.success('API 配置已保存，AI 分析下次调用即生效')
      onClose()
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title="API 设置"
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      cancelText="取消"
      confirmLoading={loading}
      destroyOnClose
      width={560}
    >
      {fetching && (
        <Alert type="info" showIcon message="正在读取当前配置…" style={{ marginBottom: 16 }} />
      )}
      {!fetching && source === 'db' && (
        <Alert
          type="success"
          showIcon
          message="当前使用软件内保存的配置"
          style={{ marginBottom: 16 }}
        />
      )}
      {!fetching && source === 'env' && (
        <Alert
          type="info"
          showIcon
          message="当前使用 .env 文件的默认配置；保存后将切换到软件内配置"
          style={{ marginBottom: 16 }}
        />
      )}

      <Form form={form} layout="vertical" onFinish={handleSubmit}>
        <Form.Item
          name="api_key"
          label="API Key"
          extra="支持 DeepSeek / 通义千问 / Kimi / GLM 等所有 OpenAI 兼容服务。留空则使用 .env 中的默认值"
        >
          <Input.Password placeholder="sk-…" autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="base_url"
          label="接口地址（Base URL）"
          extra={
            <span>
              留空则使用默认值。切换千问填{' '}
              <Typography.Text code>https://dashscope.aliyuncs.com/compatible-mode/v1</Typography.Text>
            </span>
          }
        >
          <Input placeholder="https://api.deepseek.com" />
        </Form.Item>
        <Form.Item
          name="model"
          label="模型名称"
          extra={
            <span>
              留空则使用默认值。千问可填{' '}
              <Typography.Text code>qwen-plus</Typography.Text>
              {' '}，Kimi 可填{' '}
              <Typography.Text code>moonshot-v1-8k</Typography.Text>
            </span>
          }
        >
          <Input placeholder="deepseek-chat" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
