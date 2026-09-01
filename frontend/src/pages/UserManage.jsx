import { useEffect, useState } from 'react'
import { Card, Table, Tag, Switch, Button, Space, message, Popconfirm, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { listUsers, setUserStatus } from '../api'
import { useAuth } from '../auth/AuthContext'

export default function UserManage() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(false)
  const { user: me } = useAuth()

  const fetchData = async () => {
    setLoading(true)
    try {
      setUsers(await listUsers())
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const handleToggle = async (id, is_active) => {
    try {
      const res = await setUserStatus(id, is_active)
      message.success(res?.message || (is_active ? '已启用' : '已禁用'))
      fetchData()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '用户名', dataIndex: 'username' },
    {
      title: '角色',
      dataIndex: 'is_admin',
      width: 90,
      render: (v) => (v ? <Tag color="gold">管理员</Tag> : <Tag>普通用户</Tag>),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 110,
      render: (v) =>
        v ? <Tag color="green">正常</Tag> : <Tag color="red">已禁用</Tag>,
    },
    {
      title: '注册时间',
      dataIndex: 'created_at',
      width: 180,
      render: (v) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, r) =>
        r.id === me?.id ? (
          <Typography.Text type="secondary">当前账号</Typography.Text>
        ) : (
          <Popconfirm
            title={r.is_active ? `禁用用户「${r.username}」？` : `启用用户「${r.username}」？`}
            onConfirm={() => handleToggle(r.id, !r.is_active)}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" danger={r.is_active}>
              {r.is_active ? '禁用' : '启用'}
            </Button>
          </Popconfirm>
        ),
    },
  ]

  return (
    <Card
      title="用户管理"
      extra={
        <Button icon={<ReloadOutlined />} onClick={fetchData}>
          刷新
        </Button>
      }
    >
      <Table
        rowKey="id"
        columns={columns}
        dataSource={users}
        loading={loading}
        pagination={false}
        size="middle"
      />
    </Card>
  )
}
