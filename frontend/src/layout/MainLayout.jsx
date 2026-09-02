import { Layout, Menu, Typography, Space, Button, Dropdown, message } from 'antd'
import {
  DashboardOutlined,
  UploadOutlined,
  SettingOutlined,
  LineChartOutlined,
  UserOutlined,
  TeamOutlined,
  LogoutOutlined,
  KeyOutlined,
  ApiOutlined,
  CalendarOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { changePassword } from '../api'
import ChangePasswordModal from '../components/ChangePasswordModal'
import ApiSettingsModal from '../components/ApiSettingsModal'
import { useState } from 'react'

const { Sider, Content, Header } = Layout

export default function MainLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()
  const [pwOpen, setPwOpen] = useState(false)
  const [apiOpen, setApiOpen] = useState(false)

  const items = [
    { key: '/', icon: <DashboardOutlined />, label: '交易记录' },
    { key: '/upload', icon: <UploadOutlined />, label: '录入交易' },
    { key: '/plans', icon: <CalendarOutlined />, label: '交易计划' },
    { key: '/systems', icon: <SettingOutlined />, label: '交易系统' },
    { key: '/analysis', icon: <LineChartOutlined />, label: '阶段复盘' },
    // V1.008 复盘总结独立模块（手写阶段总结 + 草稿缓存，AI 阶段分析自动参考）
    { key: '/summaries', icon: <FileTextOutlined />, label: '复盘总结' },
    ...(user?.is_admin
      ? [{ key: '/users', icon: <TeamOutlined />, label: '用户管理' }]
      : []),
    ...(user?.is_admin
      ? [{ key: '/futures', icon: <ApiOutlined />, label: '期货参数' }]
      : []),
  ]

  const selectedKey =
    items.find((i) => i.key === location.pathname)?.key ||
    (location.pathname.startsWith('/upload') ? '/upload' : '/')

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="dark" width={220}>
        <div style={{ padding: '16px', color: '#fff' }}>
          <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
            AIReviewSystem
          </Typography.Title>
          <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.6)', marginTop: 4 }}>
            V1.0.8.2 · {user?.is_admin ? '管理员' : '普通用户'}
          </div>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={items}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            padding: '0 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <Typography.Text strong style={{ fontSize: 15 }}>
            AIReviewSystem · AI 交易复盘
          </Typography.Text>
          <Dropdown
            menu={{
              items: [
                { key: 'me', icon: <UserOutlined />, label: user?.username, disabled: true },
                { type: 'divider' },
                {
                  key: 'password',
                  icon: <KeyOutlined />,
                  label: '修改密码',
                  onClick: () => setPwOpen(true),
                },
                {
                  key: 'api',
                  icon: <ApiOutlined />,
                  label: 'API 设置',
                  onClick: () => setApiOpen(true),
                },
                { type: 'divider' },
                { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: logout },
              ],
            }}
          >
            <Space style={{ cursor: 'pointer' }}>
              <UserOutlined />
              <span>{user?.username}</span>
            </Space>
          </Dropdown>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
      <ApiSettingsModal open={apiOpen} onClose={() => setApiOpen(false)} />
    </Layout>
  )
}
