import { useState } from 'react'
import { Card, Form, Input, Button, Typography, message, Space, Alert } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { login, register } from '../api'
import { setToken } from '../api/client'
import { useAuth } from '../auth/AuthContext'

export default function Login() {
  const [mode, setMode] = useState('login') // login / register
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const { setUser } = useAuth()

  const handleSubmit = async (values) => {
    setLoading(true)
    try {
      const res =
        mode === 'login'
          ? await login(values.username, values.password)
          : await register(values.username, values.password)
      setToken(res.token)
      setUser(res.user)
      message.success(mode === 'login' ? '登录成功' : '注册成功')
      navigate('/')
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 380 }} title="AIReviewSystem">
        <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
          {mode === 'login' ? '登录你的账号' : '创建新账号'}
        </Typography.Paragraph>

        {mode === 'register' && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="第一个注册的用户将自动成为管理员，并继承历史数据"
          />
        )}

        <Form layout="vertical" onFinish={handleSubmit}>
          <Form.Item
            name="username"
            rules={[
              { required: true, message: '请输入用户名' },
              { min: 2, message: '用户名至少2个字符' },
            ]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" size="large" />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[
              { required: true, message: '请输入密码' },
              { min: 6, message: '密码至少6位' },
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码（至少6位）" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}>
            {mode === 'login' ? '登录' : '注册'}
          </Button>
        </Form>

        <Space direction="vertical" style={{ width: '100%', marginTop: 16, textAlign: 'center' }}>
          {mode === 'login' ? (
            <Typography.Link onClick={() => setMode('register')}>
              没有账号？立即注册
            </Typography.Link>
          ) : (
            <Typography.Link onClick={() => setMode('login')}>已有账号？去登录</Typography.Link>
          )}
        </Space>
      </Card>
    </div>
  )
}
