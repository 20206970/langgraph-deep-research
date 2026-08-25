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
  const status = payload.status || (
    event.type === 'task_failed' ? 'failed' :
      event.type === 'task_completed' ? 'succeeded' : 'running'
  )
  taskProgress.value = {
    ...taskProgress.value,
    [event.task_id]: {
      taskId: event.task_id,
      status,
      attempt: payload.attempt || taskProgress.value[event.task_id]?.attempt || 0,
      error: payload.error_message || ''
    }
  }
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
      tasks: response.tasks
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
        <span class="product-mark">LG</span>
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
        />
      </div>
    </main>

    <section v-if="isLoading && progressItems.length" class="research-progress" aria-live="polite">
      <div class="progress-heading">研究进度</div>
      <div v-for="item in progressItems" :key="item.taskId" class="progress-row">
        <span class="progress-task">{{ item.taskId.slice(0, 12) }}</span>
        <span class="progress-status">{{ item.status }}</span>
        <span v-if="item.attempt" class="progress-attempt">第 {{ item.attempt }} 次</span>
        <span v-if="item.error" class="progress-error">{{ item.error }}</span>
      </div>
    </section>

    <p v-if="documentRetrieval" class="retrieval-status" :class="{ degraded: documentRetrieval.reranker_status === 'degraded' }" aria-live="polite">
      <template v-if="documentRetrieval.reranker_status === 'degraded'">私有资料精排不可用，当前按混合召回结果排序。</template>
      <template v-else>已检索私有资料：{{ documentRetrieval.parent_count || 0 }} 个相关片段参与研究。</template>
    </p>

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

    <HistoryPanel v-if="showHistory" @close="showHistory = false" />
    <DocumentPanel v-if="showDocuments" @close="showDocuments = false" @documents-updated="updateDocuments" />
  </div>
</template>

<style scoped>
.bootstrap-view { display: grid; min-height: 100vh; place-items: center; background: var(--bg-primary); color: var(--text-secondary); font-size: 0.9rem; }
.app { display: flex; min-height: 100vh; flex-direction: column; }
.header { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 13px 24px; border-bottom: 1px solid var(--border); background: var(--bg-secondary); }
.product-heading { display: flex; min-width: 0; align-items: center; gap: 10px; }
.product-mark { display: grid; width: 30px; height: 30px; flex: 0 0 auto; place-items: center; border: 1px solid var(--accent); border-radius: 4px; color: var(--accent-hover); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.64rem; font-weight: 700; }
.header h1 { font-family: inherit; font-size: 1rem; }
.header p { overflow: hidden; margin-top: 1px; color: var(--text-secondary); font-size: 0.7rem; text-overflow: ellipsis; white-space: nowrap; }
.header-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }
.btn-header { min-height: 30px; padding: 5px 9px; border: 1px solid var(--border); border-radius: 4px; background: transparent; color: var(--text-secondary); font-size: 0.76rem; }
.btn-header:hover { border-color: var(--accent); color: var(--text-primary); }
.btn-header.subtle { color: #cbd5e1; }
.chat-main { flex: 1; overflow-y: auto; padding: 24px; }
.messages-container { display: flex; max-width: 800px; margin: 0 auto; flex-direction: column; gap: 16px; }
.research-progress { width: min(800px, calc(100% - 48px)); margin: 0 auto 12px; padding: 10px 14px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-secondary); color: var(--text-secondary); font-size: 0.78rem; }
.progress-heading { margin-bottom: 6px; color: var(--text-primary); font-weight: 600; }
.progress-row { display: flex; min-height: 23px; align-items: center; gap: 10px; }
.progress-task { min-width: 108px; color: var(--text-primary); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.progress-status { min-width: 74px; }
.progress-error { overflow: hidden; color: #fca5a5; text-overflow: ellipsis; white-space: nowrap; }
.retrieval-status { width: min(800px, calc(100% - 48px)); margin: 0 auto 10px; padding: 8px 10px; border-left: 2px solid var(--accent); background: rgba(30, 41, 59, 0.78); color: var(--text-secondary); font-size: 0.76rem; }
.retrieval-status.degraded { border-left-color: #f7c873; color: #fde7b1; }

@media (max-width: 720px) {
  .header { align-items: flex-start; padding: 12px 16px; flex-direction: column; }
  .header-actions { justify-content: flex-start; }
  .chat-main { padding: 16px; }
  .research-progress, .retrieval-status { width: min(100% - 32px, 800px); }
  .progress-row { flex-wrap: wrap; gap: 3px 9px; }
  .progress-error { max-width: 100%; white-space: normal; }
}
</style>
