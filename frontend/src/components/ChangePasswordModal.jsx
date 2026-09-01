import { useState } from 'react'
import { Modal, Form, Input, message } from 'antd'
import { changePassword } from '../api'

export default function ChangePasswordModal({ open, onClose }) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (values) => {
    setLoading(true)
    try {
      await changePassword(values.old_password, values.new_password)
      message.success('密码已修改')
      form.resetFields()
      onClose()
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title="修改密码"
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="确认修改"
      cancelText="取消"
      confirmLoading={loading}
      destroyOnClose
    >
      <Form form={form} layout="vertical" onFinish={handleSubmit}>
        <Form.Item
          name="old_password"
          label="原密码"
          rules={[{ required: true, message: '请输入原密码' }]}
        >
          <Input.Password placeholder="原密码" />
        </Form.Item>
        <Form.Item
          name="new_password"
          label="新密码"
          rules={[
            { required: true, message: '请输入新密码' },
            { min: 6, message: '新密码至少6位' },
          ]}
        >
          <Input.Password placeholder="新密码（至少6位）" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
