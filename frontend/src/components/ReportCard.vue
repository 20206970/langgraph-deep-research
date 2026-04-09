<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import DOMPurify from 'dompurify'

const props = defineProps({
  content: {
    type: String,
    default: ''
  },
  topic: {
    type: String,
    default: ''
  }
})

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

const renderedContent = computed(() => {
  if (!props.content) return ''
  const html = marked(props.content)
  return DOMPurify.sanitize(html)
})

const downloadReport = () => {
  const filename = `${(props.topic || '研究报告').replace(/[^a-zA-Z0-9\u4e00-\u9fa5]/g, '_')}.md`
  const blob = new Blob([props.content], { type: 'text/markdown;charset=utf-8' })
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
  <div class="report-card">
    <div class="report-content" v-html="renderedContent"></div>
    <div class="report-actions">
      <button class="btn-download" @click="downloadReport">下载报告</button>
    </div>
  </div>
</template>

<style scoped>
.report-card {
  margin-top: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.report-content {
  padding: 16px 20px;
  max-height: 500px;
  overflow-y: auto;
  background: rgba(15, 23, 42, 0.6);
  line-height: 1.8;
  font-size: 0.9rem;
}

.report-content :deep(h1) {
  font-size: 1.5rem;
  margin: 20px 0 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}

.report-content :deep(h2) {
  font-size: 1.25rem;
  margin: 16px 0 10px;
}

.report-content :deep(h3) {
  font-size: 1.05rem;
  margin: 12px 0 8px;
}

.report-content :deep(p) {
  margin: 10px 0;
}

.report-content :deep(ul),
.report-content :deep(ol) {
  margin: 10px 0;
  padding-left: 24px;
}

.report-content :deep(li) {
  margin: 4px 0;
}

.report-content :deep(code) {
  background: var(--bg-primary);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.85em;
}

.report-content :deep(pre) {
  background: var(--bg-primary);
  padding: 14px;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  margin: 12px 0;
}

.report-content :deep(pre code) {
  background: none;
  padding: 0;
}

.report-content :deep(a) {
  color: var(--accent);
  text-decoration: none;
}

.report-content :deep(a:hover) {
  text-decoration: underline;
}

.report-content :deep(blockquote) {
  border-left: 3px solid var(--accent);
  padding-left: 14px;
  margin: 12px 0;
  color: var(--text-secondary);
}

.report-actions {
  padding: 8px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
}

.btn-download {
  padding: 6px 14px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.8rem;
}

.btn-download:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
