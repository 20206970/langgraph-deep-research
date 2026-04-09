<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { createSession, sendMessage } from './api/research.js'
import ChatInput from './components/ChatInput.vue'
import MessageBubble from './components/MessageBubble.vue'
import HistoryPanel from './components/HistoryPanel.vue'

const sessionId = ref(null)
const messages = ref([])
const isLoading = ref(false)
const showHistory = ref(false)
const messagesContainer = ref(null)

onMounted(async () => {
  try {
    const session = await createSession()
    sessionId.value = session.id
    messages.value = session.messages || []
    await nextTick()
    scrollToBottom()
  } catch (err) {
    console.error('Failed to create session:', err)
    messages.value = [{
      role: 'assistant',
      content: '连接失败，请检查后端服务是否运行。',
      message_type: 'text'
    }]
  }
})

const handleSend = async (text) => {
  if (!sessionId.value || isLoading.value) return

  // Add user message
  messages.value.push({ role: 'user', content: text, message_type: 'text' })
  await nextTick()
  scrollToBottom()

  // Add loading indicator
  isLoading.value = true
  messages.value.push({ role: 'assistant', content: '', message_type: 'loading' })
  await nextTick()
  scrollToBottom()

  try {
    const response = await sendMessage(sessionId.value, text)

    // Replace loading message with actual response
    const lastIdx = messages.value.length - 1
    messages.value[lastIdx] = {
      role: 'assistant',
      content: response.reply,
      message_type: response.message_type,
      tasks: response.tasks,
    }
  } catch (err) {
    const lastIdx = messages.value.length - 1
    messages.value[lastIdx] = {
      role: 'assistant',
      content: `请求失败：${err.response?.data?.detail || err.message}`,
      message_type: 'text',
    }
  } finally {
    isLoading.value = false
    await nextTick()
    scrollToBottom()
  }
}

const scrollToBottom = () => {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

const startNewChat = async () => {
  try {
    const session = await createSession()
    sessionId.value = session.id
    messages.value = session.messages || []
    showHistory.value = false
  } catch (err) {
    console.error('Failed to create new session:', err)
  }
}
</script>

<template>
  <div class="app">
    <header class="header">
      <h1>LangGraph 深度研究助手</h1>
      <div class="header-actions">
        <button class="btn-header" @click="startNewChat">新对话</button>
        <button class="btn-header" @click="showHistory = true">历史</button>
      </div>
    </header>

    <main class="chat-main" ref="messagesContainer">
      <div class="messages-container">
        <MessageBubble
          v-for="(msg, index) in messages"
          :key="index"
          :message="msg"
        />

        <div v-if="isLoading && messages[messages.length - 1]?.message_type !== 'loading'" class="typing-indicator">
          <div class="avatar">AI</div>
          <div class="dots">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>
    </main>

    <ChatInput
      :disabled="isLoading || !sessionId"
      @send="handleSend"
    />

    <HistoryPanel
      v-if="showHistory"
      @close="showHistory = false"
    />
  </div>
</template>

<style scoped>
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
}

.header h1 {
  font-size: 1.25rem;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.btn-header {
  padding: 6px 14px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: 0.8rem;
}

.btn-header:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.chat-main {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.messages-container {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.typing-indicator {
  display: flex;
  align-items: center;
  gap: 12px;
}

.typing-indicator .avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--border);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 600;
}

.dots {
  display: flex;
  gap: 4px;
  padding: 12px 16px;
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}

.dots span {
  width: 8px;
  height: 8px;
  background: var(--text-secondary);
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out;
}

.dots span:nth-child(1) { animation-delay: 0s; }
.dots span:nth-child(2) { animation-delay: 0.2s; }
.dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}
</style>
