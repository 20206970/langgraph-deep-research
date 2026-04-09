<script setup>
import { ref, onMounted, computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import DOMPurify from 'dompurify'
import { getHistoryList, getHistoryDetail } from '../api/research.js'

// 配置 marked
marked.setOptions({
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  },
  breaks: true,
  gfm: true
})

const emit = defineEmits(['view', 'back', 'delete'])

const historyList = ref([])
const isLoading = ref(true)
const selectedItem = ref(null)
const selectedReport = ref('')
const selectedTopic = ref('')

const renderedReport = computed(() => {
  if (!selectedReport.value) return ''
  const html = marked(selectedReport.value)
  return DOMPurify.sanitize(html)
})

onMounted(async () => {
  await loadHistory()
})

const loadHistory = async () => {
  isLoading.value = true
  try {
    historyList.value = await getHistoryList()
  } catch (err) {
    console.error('加载历史失败:', err)
  } finally {
    isLoading.value = false
  }
}

const viewHistory = async (item) => {
  try {
    const detail = await getHistoryDetail(item.id)
    selectedItem.value = item
    selectedReport.value = detail.report
    selectedTopic.value = detail.topic
  } catch (err) {
    console.error('加载详情失败:', err)
  }
}

const closeDetail = () => {
  selectedItem.value = null
  selectedReport.value = ''
  selectedTopic.value = ''
}

const downloadReport = (report, topic) => {
  const filename = `${topic.replace(/[^a-zA-Z0-9\u4e00-\u9fa5]/g, '_')}_研究报告.md`
  const blob = new Blob([report], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="step-history">
    <!-- 历史记录列表 -->
    <div v-if="!selectedItem" class="card">
      <h2>历史研究记录</h2>

      <div v-if="isLoading" class="loading">
        <div class="spinner"></div>
        <p>加载中...</p>
      </div>

      <div v-else-if="historyList.length === 0" class="empty">
        <p>暂无历史记录</p>
      </div>

      <div v-else class="history-list">
        <div
          v-for="item in historyList"
          :key="item.id"
          class="history-item"
          @click="viewHistory(item)"
        >
          <div class="history-info">
            <h3>{{ item.topic }}</h3>
            <p class="time">{{ item.created_at }}</p>
          </div>
          <span class="arrow">›</span>
        </div>
      </div>

      <div class="actions">
        <button class="btn-secondary" @click="emit('back')">返回</button>
      </div>
    </div>

    <!-- 历史详情 -->
    <div v-else class="card">
      <div class="detail-header">
        <button class="btn-text" @click="closeDetail">‹ 返回列表</button>
      </div>

      <h2>研究报告</h2>
      <p class="topic">主题: {{ selectedTopic }}</p>

      <div class="report-content" v-html="renderedReport"></div>

      <div class="actions">
        <button class="btn-secondary" @click="downloadReport(selectedReport, selectedTopic)">
          下载报告
        </button>
        <button class="btn-primary" @click="closeDetail">查看更多</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-history {
  max-width: 800px;
  margin: 0 auto;
}

.card {
  background: rgba(30, 41, 59, 0.8);
  backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 32px;
  box-shadow: var(--shadow);
}

.card h2 {
  font-size: 1.5rem;
  margin-bottom: 8px;
  text-align: center;
}

.topic {
  color: var(--text-secondary);
  text-align: center;
  margin-bottom: 24px;
  font-size: 0.875rem;
}

.loading, .empty {
  text-align: center;
  padding: 48px;
  color: var(--text-secondary);
}

.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 24px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: var(--transition);
}

.history-item:hover {
  border-color: var(--accent);
}

.history-info h3 {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 4px;
}

.history-info .time {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.arrow {
  font-size: 1.5rem;
  color: var(--text-secondary);
}

.detail-header {
  margin-bottom: 16px;
}

.btn-text {
  background: none;
  border: none;
  color: var(--accent);
  font-size: 0.875rem;
  cursor: pointer;
  padding: 0;
}

.btn-text:hover {
  text-decoration: underline;
}

.report-content {
  line-height: 1.8;
  font-size: 0.9375rem;
  max-height: 500px;
  overflow-y: auto;
  padding: 16px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
}

.actions {
  margin-top: 24px;
  display: flex;
  gap: 12px;
  justify-content: center;
}

.btn-primary, .btn-secondary {
  padding: 12px 24px;
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  font-weight: 500;
}

.btn-primary {
  background: var(--accent);
  color: white;
  border: none;
}

.btn-primary:hover {
  background: var(--accent-hover);
}

.btn-secondary {
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--border);
}

.btn-secondary:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>