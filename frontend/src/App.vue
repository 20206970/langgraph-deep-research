<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  clearAuthSession,
  createSession,
  getCurrentUser,
  getDocuments,
  getStoredToken,
  login,
  register,
  sendMessage,
  setUnauthorizedHandler,
  streamResearch
} from './api/research.js'
import AuthView from './components/AuthView.vue'
import ChatInput from './components/ChatInput.vue'
import DocumentPanel from './components/DocumentPanel.vue'
import DocumentScopeSelector from './components/DocumentScopeSelector.vue'
import HistoryPanel from './components/HistoryPanel.vue'
import MessageBubble from './components/MessageBubble.vue'
import ReportReader from './components/ReportReader.vue'
import ResearchPipeline from './components/ResearchPipeline.vue'

const user = ref(null)
const authPending = ref(false)
const authError = ref('')
const isBootstrapping = ref(true)
const sessionId = ref(null)
const messages = ref([])
const isLoading = ref(false)
const showHistory = ref(false)
const showDocuments = ref(false)
const messagesContainer = ref(null)
const taskProgress = ref({})
const documents = ref([])
const selectedDocumentIds = ref([])
const useAllMyDocuments = ref(false)
const documentRetrieval = ref(null)
const readerState = ref({ open: false, topic: '', markdown: '', meta: {} })
let removeUnauthorizedHandler = null

const progressItems = computed(() => Object.values(taskProgress.value))
const documentScope = computed(() => ({
  documentIds: selectedDocumentIds.value,
  useAllMyDocuments: useAllMyDocuments.value
}))

const resetWorkspace = () => {
  user.value = null
  sessionId.value = null
  messages.value = []
  taskProgress.value = {}
  documents.value = []
  selectedDocumentIds.value = []
  useAllMyDocuments.value = false
  documentRetrieval.value = null
  showHistory.value = false
  showDocuments.value = false
  isLoading.value = false
}

const handleUnauthorized = () => {
  resetWorkspace()
  authError.value = '登录状态已失效，请重新登录。'
}

const getErrorMessage = (requestError, fallback) => requestError.response?.data?.detail || requestError.message || fallback

const updateTaskProgress = (event) => {
  if (event.type === 'document_retrieval') {
    documentRetrieval.value = event.payload || null
    return
  }
  if (!event.task_id) return
  const payload = event.payload || {}
  const previous = taskProgress.value[event.task_id]
  const status = payload.status || (
    event.type === 'task_failed' ? 'failed' :
      event.type === 'task_completed' ? 'succeeded' :
        event.type === 'retrying' ? 'retrying' : 'running'
  )
  taskProgress.value = {
    ...taskProgress.value,
    [event.task_id]: {
      taskId: event.task_id,
      status,
      attempt: payload.attempt || previous?.attempt || 0,
      retryDelay: event.type === 'retrying'
        ? (payload.retry_delay_seconds ?? null)
        : (previous?.retryDelay ?? null),
      error: payload.error_message || ''
    }
  }
}

const openReader = ({ topic = '', markdown = '', meta = {} }) => {
  readerState.value = { open: true, topic, markdown, meta }
}

const openReaderFromMessage = (message) => {
  openReader({
    topic: message.topic || '',
    markdown: message.content,
    meta: message.meta || {}
  })
}

const closeReader = () => {
  readerState.value = { ...readerState.value, open: false }
}

const scrollToBottom = () => {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

const loadDocuments = async () => {
  const response = await getDocuments()
  updateDocuments(response.items || [])
}

const updateDocuments = (items) => {
  documents.value = items
  const readyIds = new Set(items.filter((document) => {
    const version = document.current_version || document.currentVersion
    return document.deleted_at == null && version?.status === 'ready' && version.retrieval_enabled
  }).map((document) => document.document_id))
  selectedDocumentIds.value = selectedDocumentIds.value.filter((documentId) => readyIds.has(documentId))
}

const startNewChat = async () => {
  if (!user.value) return
  try {
    const session = await createSession()
    sessionId.value = session.id
    messages.value = session.messages || []
    taskProgress.value = {}
    documentRetrieval.value = null
    showHistory.value = false
    await nextTick()
    scrollToBottom()
  } catch (requestError) {
    if (!user.value) return
    sessionId.value = null
    messages.value = [{
      role: 'assistant',
      content: `无法创建对话：${getErrorMessage(requestError, '请检查后端服务是否运行。')}`,
      message_type: 'text'
    }]
  }
}

const initializeWorkspace = async (currentUser) => {
  user.value = currentUser
  authError.value = ''
  const documentRequest = loadDocuments().catch((requestError) => {
    if (user.value) console.error('Failed to load documents:', requestError)
  })
  await Promise.all([startNewChat(), documentRequest])
}

const authenticate = async ({ mode, credentials }) => {
  authPending.value = true
  authError.value = ''
  try {
    const response = mode === 'register' ? await register(credentials) : await login(credentials)
    await initializeWorkspace(response.user)
  } catch (requestError) {
    if (!user.value) authError.value = getErrorMessage(requestError, '认证失败，请检查用户名和密码。')
  } finally {
    authPending.value = false
  }
}

const logout = () => {
  clearAuthSession()
  resetWorkspace()
  authError.value = ''
}

const handleSend = async (text) => {
  if (!sessionId.value || !user.value || isLoading.value) return

  messages.value.push({ role: 'user', content: text, message_type: 'text' })
  await nextTick()
  scrollToBottom()

  isLoading.value = true
  taskProgress.value = {}
  documentRetrieval.value = null
  messages.value.push({ role: 'assistant', content: '', message_type: 'loading' })
  await nextTick()
  scrollToBottom()

  try {
    const hasResearchReport = messages.value.some((message) => message.message_type === 'research_report')
    let response
    if (!hasResearchReport) {
      let streamedReport = ''
      let streamError = ''
      await streamResearch(text, (event) => {
        updateTaskProgress(event)
        if (event.type === 'completed' && event.payload?.report_markdown) streamedReport = event.payload.report_markdown
        if (event.type === 'failed') streamError = event.payload?.error_message || '研究执行失败'
      }, sessionId.value, documentScope.value)
      if (streamError && !streamedReport) throw new Error(streamError)
      response = {
        reply: streamedReport || '研究完成，但未生成报告。',
        message_type: 'research_report',
        tasks: []
      }
    } else {
      response = await sendMessage(sessionId.value, text)
    }

    if (!user.value) return
    const lastIndex = messages.value.length - 1
    messages.value[lastIndex] = {
      role: 'assistant',
      content: response.reply,
      message_type: response.message_type,
      tasks: response.tasks,
      topic: response.message_type === 'research_report' ? text : undefined,
      meta: response.message_type === 'research_report'
        ? { taskCount: Object.keys(taskProgress.value).length, generatedAt: new Date().toISOString() }
        : undefined
    }
  } catch (requestError) {
    if (!user.value) return
    const lastIndex = messages.value.length - 1
    messages.value[lastIndex] = {
      role: 'assistant',
      content: `请求失败：${getErrorMessage(requestError, '研究请求未完成。')}`,
      message_type: 'text'
    }
  } finally {
    isLoading.value = false
    await nextTick()
    scrollToBottom()
  }
}

onMounted(async () => {
  removeUnauthorizedHandler = setUnauthorizedHandler(handleUnauthorized)
  if (!getStoredToken()) {
    isBootstrapping.value = false
    return
  }
  try {
    await initializeWorkspace(await getCurrentUser())
  } catch (requestError) {
    if (getStoredToken()) {
      clearAuthSession()
      handleUnauthorized()
    }
  } finally {
    isBootstrapping.value = false
  }
})

onBeforeUnmount(() => removeUnauthorizedHandler?.())
</script>

<template>
  <main v-if="isBootstrapping" class="bootstrap-view">正在恢复工作区...</main>

  <AuthView
    v-else-if="!user"
    :pending="authPending"
    :error="authError"
    @authenticate="authenticate"
  />

  <div v-else class="app">
    <header class="header">
      <div class="product-heading">
        <span class="product-seal">研</span>
        <div>
          <h1>深度研究工作区</h1>
          <p>{{ user.username }} 的私有资料与研究记录</p>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn-header" type="button" @click="startNewChat">新对话</button>
        <button class="btn-header" type="button" @click="showDocuments = true">论文资料库</button>
        <button class="btn-header" type="button" @click="showHistory = true">历史</button>
        <button class="btn-header subtle" type="button" @click="logout">退出</button>
      </div>
    </header>

    <main ref="messagesContainer" class="chat-main">
      <div class="messages-container">
        <MessageBubble
          v-for="(message, index) in messages"
          :key="index"
          :message="message"
          @read-report="openReaderFromMessage"
        />
      </div>
    </main>

    <ResearchPipeline
      v-if="(isLoading && progressItems.length) || documentRetrieval"
      :tasks="progressItems"
      :retrieval="documentRetrieval"
      :active="isLoading"
    />

    <DocumentScopeSelector
      :documents="documents"
      :selected-ids="selectedDocumentIds"
      :use-all="useAllMyDocuments"
      :disabled="isLoading"
      @update:selected-ids="selectedDocumentIds = $event"
      @update:use-all="useAllMyDocuments = $event"
      @open-library="showDocuments = true"
    />

    <ChatInput :disabled="isLoading || !sessionId" @send="handleSend" />

    <HistoryPanel v-if="showHistory" @close="showHistory = false" @read-report="openReader" />
    <DocumentPanel v-if="showDocuments" @close="showDocuments = false" @documents-updated="updateDocuments" />

    <ReportReader
      :open="readerState.open"
      :topic="readerState.topic"
      :markdown="readerState.markdown"
      :meta="readerState.meta"
      @close="closeReader"
    />
  </div>
</template>

<style scoped>
.bootstrap-view {
  display: grid;
  min-height: 100vh;
  place-items: center;
  background: var(--ink-950);
  color: var(--text-faint);
  font-size: 0.9rem;
  letter-spacing: 0.2em;
}

.app { display: flex; min-height: 100vh; flex-direction: column; }

/* 档案柜顶栏 */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  border-bottom: 1px solid var(--border);
  background: var(--ink-900);
  padding: 13px 24px;
}

.product-heading { display: flex; min-width: 0; align-items: center; gap: 12px; }

.product-seal {
  display: grid;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 7px;
  background: var(--accent);
  color: var(--paper);
  font-family: var(--font-serif);
  font-size: 1rem;
  font-weight: 700;
  box-shadow: inset 0 0 0 1.5px rgba(246, 241, 230, 0.35), 0 2px 10px rgba(192, 73, 47, 0.35);
}

.header h1 { font-size: 1.02rem; letter-spacing: 0.1em; }

.header p {
  overflow: hidden;
  margin-top: 1px;
  color: var(--text-faint);
  font-size: 0.7rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }

.btn-header {
  min-height: 30px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.76rem;
  padding: 5px 11px;
}

.btn-header:hover {
  border-color: var(--border-strong);
  background: var(--ink-800);
  color: var(--paper);
}

.btn-header.subtle:hover { border-color: rgba(209, 106, 90, 0.5); color: var(--error-text); }

.chat-main { flex: 1; overflow-y: auto; padding: 26px 24px; }

.messages-container {
  display: flex;
  max-width: 800px;
  margin: 0 auto;
  flex-direction: column;
  gap: 18px;
}

@media (max-width: 720px) {
  .header { align-items: flex-start; flex-direction: column; padding: 12px 16px; }
  .header-actions { justify-content: flex-start; }
  .chat-main { padding: 16px; }
}
</style>
