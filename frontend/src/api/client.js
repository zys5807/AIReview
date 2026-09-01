import axios from 'axios'
import { message } from 'antd'

// 统一的 API 基础地址：
//  - 开发模式：.env.development 设 VITE_API_BASE=http://127.0.0.1:8000
//  - 生产模式（单服务）：不设/为空 → 相对路径，同源访问后端
export const API_BASE = import.meta.env.VITE_API_BASE || ''

export const getToken = () => localStorage.getItem('token')
export const setToken = (t) => localStorage.setItem('token', t)
export const clearToken = () => localStorage.removeItem('token')

const client = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
})

// 请求拦截：自动附加 Bearer Token
client.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截：统一错误提示 + 401 跳登录
client.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      clearToken()
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    const detail = err.response?.data?.detail
    const msg = typeof detail === 'string' ? detail : detail?.[0]?.msg
    message.error(msg || `请求失败：${err.message}`)
    return Promise.reject(err)
  },
)

export default client
