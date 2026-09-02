import client from './client'

// ---------- 认证 ----------
export const register = (username, password) =>
  client.post('/api/auth/register', { username, password }).then((r) => r.data)

export const login = (username, password) =>
  client.post('/api/auth/login', { username, password }).then((r) => r.data)

export const fetchMe = () => client.get('/api/auth/me').then((r) => r.data)

export const changePassword = (old_password, new_password) =>
  client
    .post('/api/auth/change-password', { old_password, new_password })
    .then((r) => r.data)

// 阶段复盘 AI 分析
export const periodAiAnalysis = (data) =>
  client.post('/api/analysis/period-ai', data).then((r) => r.data)

// ---------- 阶段总结（V1.008：周/月粒度 × 品种维度，手写总结 + AI 连续性） ----------
export const listPhaseReviews = (params = {}) =>
  client.get('/api/phase-reviews', { params }).then((r) => r.data)
export const listPhaseReviewHistory = (params = {}) =>
  client.get('/api/phase-reviews/history', { params }).then((r) => r.data)
export const savePhaseReview = (data) =>
  client.post('/api/phase-reviews', data).then((r) => r.data)
export const updatePhaseReview = (id, data) =>
  client.put(`/api/phase-reviews/${id}`, data).then((r) => r.data)
export const deletePhaseReview = (id) =>
  client.delete(`/api/phase-reviews/${id}`).then((r) => r.data)
export const parsePhaseReviewFile = (file) => {
  const fd = new FormData()
  fd.append('file', file)
  return client.post('/api/phase-reviews/parse-file', fd).then((r) => r.data)
}

// ---------- 交易计划 ----------
export const listTradePlans = (params = {}) =>
  client.get('/api/trade-plans', { params }).then((r) => r.data)

export const createTradePlan = (data) =>
  client.post('/api/trade-plans', data).then((r) => r.data)

export const updateTradePlan = (id, data) =>
  client.put(`/api/trade-plans/${id}`, data).then((r) => r.data)

export const deleteTradePlan = (id) =>
  client.delete(`/api/trade-plans/${id}`).then((r) => r.data)

export const executeTradePlan = (id, linked_trade_id) =>
  client.post(`/api/trade-plans/${id}/execute`, { linked_trade_id }).then((r) => r.data)

export const cancelTradePlan = (id) =>
  client.post(`/api/trade-plans/${id}/cancel`).then((r) => r.data)

export const reviewTradePlan = (id) =>
  client.post(`/api/trade-plans/${id}/review`).then((r) => r.data)

export const compareTradePlan = (id) =>
  client.post(`/api/trade-plans/${id}/comparison`).then((r) => r.data)

// ---------- 账户资金流水 ----------
export const getAccountSummary = () =>
  client.get('/api/accounts/summary').then((r) => r.data)

export const listAccountFlows = () =>
  client.get('/api/accounts/flows').then((r) => r.data)

export const createAccountFlow = (data) =>
  client.post('/api/accounts/flows', data).then((r) => r.data)

export const updateAccountFlow = (id, data) =>
  client.put(`/api/accounts/flows/${id}`, data).then((r) => r.data)

export const deleteAccountFlow = (id) =>
  client.delete(`/api/accounts/flows/${id}`).then((r) => r.data)

// 用户管理（管理员）
export const listUsers = () => client.get('/api/users').then((r) => r.data)

export const setUserStatus = (id, is_active) =>
  client.patch(`/api/users/${id}`, { is_active }).then((r) => r.data)

// ---------- 截图 ----------
export const uploadScreenshot = (file) => {
  const form = new FormData()
  form.append('file', file)
  return client.post('/api/screenshots', form).then((r) => r.data)
}

export const listScreenshots = () => client.get('/api/screenshots').then((r) => r.data)

export const deleteScreenshot = (id) =>
  client.delete(`/api/screenshots/${id}`).then((r) => r.data)

// 孤儿截图（未被任何交易引用、上传超过N天）
export const listOrphanScreenshots = (days = 7) =>
  client.get('/api/screenshots/orphans', { params: { days } }).then((r) => r.data)

export const cleanupOrphanScreenshots = (days = 7) =>
  client.post('/api/screenshots/cleanup-orphans', null, { params: { days } }).then((r) => r.data)

// K线识别（OCR可选；未配置OCR时仍能识别颜色/箭头/形状）
export const recognizeScreenshot = (id) =>
  client.post(`/api/screenshots/${id}/recognize`).then((r) => r.data)

// ---------- 交易记录 ----------
export const createTrade = (data) => client.post('/api/trades', data).then((r) => r.data)

export const listTrades = (params = {}) =>
  client.get('/api/trades', { params }).then((r) => r.data)

export const getTrade = (id) => client.get(`/api/trades/${id}`).then((r) => r.data)

export const updateTrade = (id, data) =>
  client.put(`/api/trades/${id}`, data).then((r) => r.data)

export const deleteTrade = (id) => client.delete(`/api/trades/${id}`).then((r) => r.data)

// 占用资金自动计算（前端录入实时预填）
export const calcTradeCapital = (data) =>
  client.post('/api/trades/calc-capital', data).then((r) => r.data)

// ---------- 期货参数（V1.007） ----------
export const listFuturesConfig = () => client.get('/api/futures/config').then((r) => r.data)
export const getFuturesStatus = () => client.get('/api/futures/status').then((r) => r.data)
export const syncFutures = () => client.post('/api/futures/sync').then((r) => r.data)
export const createFuturesVariety = (data) =>
  client.post('/api/futures/varieties', data).then((r) => r.data)
export const updateFuturesVariety = (code, data) =>
  client.put(`/api/futures/varieties/${code}`, data).then((r) => r.data)
export const deleteFuturesVariety = (code) =>
  client.delete(`/api/futures/varieties/${code}`).then((r) => r.data)
export const createFuturesContract = (data) =>
  client.post('/api/futures/contracts', data).then((r) => r.data)
export const deleteFuturesContract = (code) =>
  client.delete(`/api/futures/contracts/${code}`).then((r) => r.data)

// 品种分类统计
export const getTradeStats = () => client.get('/api/trades/stats').then((r) => r.data)

// ---------- 交易系统 ----------
export const createTradingSystem = (data) =>
  client.post('/api/trading-systems', data).then((r) => r.data)

export const listTradingSystems = () =>
  client.get('/api/trading-systems').then((r) => r.data)

export const updateTradingSystem = (id, data) =>
  client.put(`/api/trading-systems/${id}`, data).then((r) => r.data)

export const deleteTradingSystem = (id) =>
  client.delete(`/api/trading-systems/${id}`).then((r) => r.data)

// ---------- 交割单导入 ----------
export const parseImportFile = (file) => {
  const fd = new FormData()
  fd.append('file', file)
  return client.post('/api/import/parse', fd).then((r) => r.data)
}

export const executeImport = (file_id, mapping) =>
  client.post('/api/import/execute', { file_id, mapping }).then((r) => r.data)

// ---------- 复盘报告 ----------
export const analyzeTrade = (tradeId) =>
  client.post(`/api/trades/${tradeId}/analyze`).then((r) => r.data)

export const listReports = (tradeId) =>
  client.get(`/api/trades/${tradeId}/reports`).then((r) => r.data)

export const getReport = (reportId) =>
  client.get(`/api/reports/${reportId}`).then((r) => r.data)

// ---------- 阶段复盘 ----------
export const getPeriodStats = (params = {}) =>
  client.get('/api/analysis/period', { params }).then((r) => r.data)

export const getScoreTrend = (params = {}) =>
  client.get('/api/analysis/scores', { params }).then((r) => r.data)

// ---------- 盘面综述（V1.008.2 功能1：阶段盘面综述生成/回看） ----------
export const generateMarketReview = (data) =>
  // A股全量采集+AI点评最长可达 2 分钟，单独放宽超时
  client.post('/api/market-reviews/generate', data, { timeout: 180000 }).then((r) => r.data)

export const listMarketReviews = (params = {}) =>
  client.get('/api/market-reviews', { params }).then((r) => r.data)

export const getMarketReview = (id) =>
  client.get(`/api/market-reviews/${id}`).then((r) => r.data)

export const deleteMarketReview = (id) =>
  client.delete(`/api/market-reviews/${id}`).then((r) => r.data)

// ---------- 应用设置（API 配置，软件内管理） ----------
export const getLlmSettings = () => client.get('/api/settings/llm').then((r) => r.data)

export const updateLlmSettings = (data) =>
  client.put('/api/settings/llm', data).then((r) => r.data)
