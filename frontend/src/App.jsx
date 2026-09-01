import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Spin } from 'antd'
import { AuthProvider, useAuth } from './auth/AuthContext'
import MainLayout from './layout/MainLayout'
import Dashboard from './pages/Dashboard'
import TradeUpload from './pages/TradeUpload'
import TradingSystems from './pages/TradingSystems'
import PeriodicAnalysis from './pages/PeriodicAnalysis'
import TradePlans from './pages/TradePlans'
import Login from './pages/Login'
import UserManage from './pages/UserManage'

// 路由守卫：未登录跳转登录页
function RequireAuth({ children }) {
  const { user, loading } = useAuth()
  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  return children
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <MainLayout />
              </RequireAuth>
            }
          >
            <Route index element={<Dashboard />} />
            <Route path="upload" element={<TradeUpload />} />
            <Route path="plans" element={<TradePlans />} />
            <Route path="systems" element={<TradingSystems />} />
            <Route path="analysis" element={<PeriodicAnalysis />} />
            <Route path="users" element={<UserManage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
