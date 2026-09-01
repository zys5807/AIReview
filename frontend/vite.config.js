import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // 注意：AI 会话环境中 Vite 预构建可能与 safe-delete 冲突，
  // 在那种环境下需要临时加 optimizeDeps.disabled，但在用户自己启动时应保持默认
  optimizeDeps: {
    include: ['echarts', 'echarts-for-react', 'dayjs'],
  },
})
