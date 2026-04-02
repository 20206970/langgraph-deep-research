<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'

const props = defineProps({
  report: {
    type: String,
    default: ''
  },
  tasks: Array,
  topic: String
})

const emit = defineEmits(['reset'])

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

const renderedReport = computed(() => {
  if (!props.report) return ''
  return marked(props.report)
})
</script>

<template>
  <div class="step-report">
    <div class="card">
      <div class="card-header">
        <h2>研究报告</h2>
        <p class="topic">主题: {{ topic }}</p>
      </div>

      <div class="report-content" v-html="renderedReport"></div>

      <div class="actions">
        <button class="btn-primary" @click="emit('reset')">
          新建研究
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-report {
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

.card-header {
  text-align: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.card-header h2 {
  font-size: 1.5rem;
  margin-bottom: 4px;
}

.topic {
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.report-content {
  line-height: 1.8;
  font-size: 0.9375rem;
}

.report-content :deep(h1) {
  font-size: 1.75rem;
  margin: 24px 0 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}

.report-content :deep(h2) {
  font-size: 1.375rem;
  margin: 20px 0 12px;
}

.report-content :deep(h3) {
  font-size: 1.125rem;
  margin: 16px 0 8px;
}

.report-content :deep(p) {
  margin: 12px 0;
}

.report-content :deep(ul),
.report-content :deep(ol) {
  margin: 12px 0;
  padding-left: 24px;
}

.report-content :deep(li) {
  margin: 6px 0;
}

.report-content :deep(code) {
  background: var(--bg-primary);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.875em;
}

.report-content :deep(pre) {
  background: var(--bg-primary);
  padding: 16px;
  border-radius: var(--radius-md);
  overflow-x: auto;
  margin: 16px 0;
}

.report-content :deep(pre code) {
  background: none;
  padding: 0;
}

.report-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
}

.report-content :deep(th),
.report-content :deep(td) {
  border: 1px solid var(--border);
  padding: 10px 14px;
  text-align: left;
}

.report-content :deep(th) {
  background: var(--bg-primary);
  font-weight: 600;
}

.report-content :deep(blockquote) {
  border-left: 3px solid var(--accent);
  padding-left: 16px;
  margin: 16px 0;
  color: var(--text-secondary);
}

.report-content :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 24px 0;
}

.actions {
  margin-top: 32px;
  text-align: center;
}

.btn-primary {
  padding: 14px 32px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 1rem;
  font-weight: 500;
}

.btn-primary:hover {
  background: var(--accent-hover);
  box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
}
</style>