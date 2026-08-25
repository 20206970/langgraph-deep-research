import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const TOKEN_STORAGE_KEY = 'langgraph-deep-research.access-token'
let unauthorizedHandler = null

const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 300000,
  headers: { Accept: 'application/json' }
})

const notifyUnauthorized = () => {
  clearAuthSession()
  unauthorizedHandler?.()
}

apiClient.interceptors.request.use((config) => {
  const token = getStoredToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  if (config.data instanceof FormData) delete config.headers['Content-Type']
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) notifyUnauthorized()
    return Promise.reject(error)
  }
)

export const getStoredToken = () => localStorage.getItem(TOKEN_STORAGE_KEY)

export const setAuthSession = (token) => {
  localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

export const clearAuthSession = () => {
  localStorage.removeItem(TOKEN_STORAGE_KEY)
}

export const setUnauthorizedHandler = (handler) => {
  unauthorizedHandler = handler
  return () => {
    if (unauthorizedHandler === handler) unauthorizedHandler = null
  }
}

export async function register(credentials) {
  const response = await apiClient.post('/auth/register', credentials)
  setAuthSession(response.data.access_token)
  return response.data
}

export async function login(credentials) {
  const response = await apiClient.post('/auth/login', credentials)
  setAuthSession(response.data.access_token)
  return response.data
}

export async function getCurrentUser() {
  const response = await apiClient.get('/auth/me')
  return response.data
}

const researchScopePayload = ({ documentIds = [], useAllMyDocuments = false } = {}) => {
  if (useAllMyDocuments) return { use_all_my_documents: true }
  if (documentIds.length) return { document_ids: documentIds }
  return {}
}

export async function createPlan(topic) {
  const response = await apiClient.post('/plan', { topic }, { timeout: 120000 })
  return response.data
}

export async function createResearch(topic, documentScope = {}) {
  const response = await apiClient.post('/research', { topic, ...researchScopePayload(documentScope) })
  return response.data
}

export async function streamResearch(topic, onEvent, sessionId = null, documentScope = {}) {
  const token = getStoredToken()
  const response = await fetch(`${API_BASE}/research/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify({
      topic,
      session_id: sessionId,
      ...researchScopePayload(documentScope)
    })
  })

  if (response.status === 401) notifyUnauthorized()
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
    onEvent({ ...JSON.parse(dataLines.join('\n')), eventType, eventId })
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

export async function getDocuments({ includeDeleted = false, limit = 100, offset = 0 } = {}) {
  const response = await apiClient.get('/documents', {
    params: {
      include_deleted: includeDeleted,
      limit,
      offset
    }
  })
  return response.data
}

export async function getDocument(documentId) {
  const response = await apiClient.get(`/documents/${documentId}`)
  return response.data
}

export async function getDocumentUsage() {
  const response = await apiClient.get('/documents/usage')
  return response.data
}

export async function uploadDocument(file) {
  const form = new FormData()
  form.append('file', file)
  const response = await apiClient.post('/documents', form)
  return response.data
}

export async function uploadDocumentVersion(documentId, file) {
  const form = new FormData()
  form.append('file', file)
  const response = await apiClient.post(`/documents/${documentId}/versions`, form)
  return response.data
}

export async function retryDocumentVersion(documentId, versionId) {
  const response = await apiClient.post(`/documents/${documentId}/versions/${versionId}/retry`)
  return response.data
}

export async function deleteDocument(documentId) {
  const response = await apiClient.delete(`/documents/${documentId}`)
  return response.data
}

export async function restoreDocument(documentId) {
  const response = await apiClient.post(`/documents/${documentId}/restore`)
  return response.data
}
