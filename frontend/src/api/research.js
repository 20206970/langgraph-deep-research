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

export async function streamResearch(topic, onEvent, sessionId = null) {
  const response = await fetch(`${API_BASE}/research/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic, session_id: sessionId })
  })

  if (!response.ok) {
    let detail = `请求失败（${response.status}）`
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(detail)
  }
  if (!response.body) throw new Error('服务端未返回 SSE 流')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const consume = (block) => {
    let eventType = 'message'
    let eventId = ''
    const dataLines = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) eventType = line.slice(6).trim()
      else if (line.startsWith('id:')) eventId = line.slice(3).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!dataLines.length) return
    const data = JSON.parse(dataLines.join('\n'))
    onEvent({ ...data, eventType, eventId })
  }

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() || ''
    blocks.filter(Boolean).forEach(consume)
    if (done) break
  }
  if (buffer.trim()) consume(buffer)
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
