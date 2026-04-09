import axios from 'axios'

// 直接使用后端地址
const API_BASE = 'http://localhost:8000'

const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 300000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 创建任务规划（不保存历史）
export async function createPlan(topic) {
  const response = await apiClient.post('/plan', { topic }, {
    timeout: 120000
  })
  return response.data
}

// 执行完整研究（保存历史）
export async function createResearch(topic) {
  const response = await apiClient.post('/research', { topic })
  return response.data
}

export async function getHistoryList() {
  const response = await apiClient.get('/history')
  return response.data
}

export async function getHistoryDetail(historyId) {
  const response = await apiClient.get(`/history/${historyId}`)
  return response.data
}

// Session APIs for multi-turn conversations
export async function createSession() {
  const response = await apiClient.post('/sessions')
  return response.data
}

export async function sendMessage(sessionId, message) {
  const response = await apiClient.post(`/sessions/${sessionId}/chat`, { message })
  return response.data
}

export async function getSession(sessionId) {
  const response = await apiClient.get(`/sessions/${sessionId}`)
  return response.data
}