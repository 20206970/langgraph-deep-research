<script setup>
import { ref, onMounted, computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import DOMPurify from 'dompurify'
import { getHistoryList, getHistoryDetail } from '../api/research.js'

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

const emit = defineEmits(['close'])

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
  <div class="history-panel">
    <div class="panel-header">
      <h3>历史记录</h3>
      <button class="btn-close" @click="emit('close')">&times;</button>
    </div>

    <div class="panel-body">
      <div v-if="selectedItem" class="detail-view">
        <button class="btn-back" @click="closeDetail">&larr; 返回列表</button>
        <h4>{{ selectedTopic }}</h4>
        <div class="detail-report" v-html="renderedReport"></div>
        <button class="btn-download" @click="downloadReport(selectedReport, selectedTopic)">下载报告</button>
      </div>

      <div v-else>
        <div v-if="isLoading" class="loading">
          <div class="spinner"></div>
          <p>加载中...</p>
        </div>
        <div v-else-if="historyList.length === 0" class="empty">
          <p>暂无历史记录</p>
        </div>
        <div v-else class="history-items">
          <div
            v-for="item in historyList"
            :key="item.id"
            class="history-item"
            @click="viewHistory(item)"
          >
            <div class="item-topic">{{ item.topic }}</div>
            <div class="item-time">{{ item.created_at }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.history-panel {
  position: fixed;
  top: 0;
  right: 0;
  width: 380px;
  height: 100vh;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  z-index: 100;
  box-shadow: -4px 0 20px rgba(0, 0, 0, 0.3);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px;
  border-bottom: 1px solid var(--border);
}

.panel-header h3 {
  font-size: 1.1rem;
}

.btn-close {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.5rem;
  padding: 0;
  line-height: 1;
}

.btn-close:hover {
  color: var(--text-primary);
}

.panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.loading, .empty {
  text-align: center;
  padding: 40px 0;
  color: var(--text-secondary);
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 12px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.history-items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.history-item {
  padding: 12px 14px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: var(--transition);
}

.history-item:hover {
  border-color: var(--accent);
}

.item-topic {
  font-size: 0.9rem;
  font-weight: 500;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.item-time {
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.detail-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-view h4 {
  font-size: 1rem;
}

.btn-back {
  background: none;
  border: none;
  color: var(--accent);
  font-size: 0.85rem;
  cursor: pointer;
  padding: 0;
}

.btn-back:hover {
  text-decoration: underline;
}

.detail-report {
  max-height: 60vh;
  overflow-y: auto;
  padding: 12px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  line-height: 1.7;
  font-size: 0.85rem;
}

.btn-download {
  padding: 8px 16px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.8rem;
  align-self: flex-start;
}

.btn-download:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
