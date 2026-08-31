<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  countCjkChars,
  downloadMarkdown,
  highlightCodeBlocks,
  renderMarkdown
} from '../lib/markdown.js'

const props = defineProps({
  content: { type: String, default: '' },
  topic: { type: String, default: '' }
})

const emit = defineEmits(['read'])

const previewEl = ref(null)

const renderedContent = computed(() => renderMarkdown(props.content))
const charCount = computed(() => countCjkChars(props.content))

watch(
  () => props.content,
  async () => {
    await nextTick()
    highlightCodeBlocks(previewEl.value)
  },
  { immediate: true }
)

const handleDownload = () => downloadMarkdown(props.content, props.topic)
</script>

<template>
  <div class="report-card">
    <div class="report-head">
      <span class="report-mark">▍</span>
      <span class="report-label">研究报告</span>
      <span v-if="charCount" class="report-meta">约 {{ charCount.toLocaleString('zh-CN') }} 字</span>
    </div>

    <div class="report-preview paper-grain">
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div ref="previewEl" class="preview-article" v-html="renderedContent"></div>
    </div>

    <div class="report-foot">
      <button type="button" class="btn-read" @click="emit('read')">展开阅读</button>
      <button type="button" class="btn-dl" @click="handleDownload">下载 .md</button>
    </div>
  </div>
</template>

<style scoped>
/* 档案柜里抽出的一页手稿：纸面预览 + 渐隐遮罩 */
.report-card {
  margin-top: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--ink-800);
  overflow: hidden;
}

.report-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
}

.report-mark { color: var(--accent); font-size: 0.85rem; }

.report-label {
  color: var(--text-primary);
  font-family: var(--font-serif);
  font-size: 0.9rem;
  font-weight: 700;
  letter-spacing: 0.12em;
}

.report-meta {
  margin-left: auto;
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: 0.68rem;
}

.report-preview {
  position: relative;
  max-height: 300px;
  overflow: hidden;
  background: var(--paper);
  color: var(--paper-text);
  -webkit-mask-image: linear-gradient(180deg, #000 74%, transparent 99%);
  mask-image: linear-gradient(180deg, #000 74%, transparent 99%);
}

.preview-article {
  font-family: var(--font-serif);
  font-size: 0.82rem;
  padding: 18px 24px 34px;
}

.preview-article :deep(h1) {
  margin: 6px 0 12px;
  padding-bottom: 10px;
  border-bottom: 2px double rgba(38, 49, 41, 0.3);
  font-size: 1.15rem;
  text-align: center;
}

.preview-article :deep(h2) { margin: 18px 0 8px; font-size: 0.98rem; }
.preview-article :deep(h2)::before { content: '▍'; margin-right: 6px; color: var(--accent); }
.preview-article :deep(h3) { margin: 12px 0 6px; font-size: 0.88rem; color: #33413a; }

.preview-article :deep(p) {
  margin: 7px 0;
  line-height: 1.85;
  text-align: justify;
  text-indent: 2em;
}

.preview-article :deep(blockquote) {
  margin: 10px 0;
  border-left: 3px double rgba(38, 49, 41, 0.4);
  background: rgba(38, 49, 41, 0.045);
  color: var(--paper-text-secondary);
  font-size: 0.94em;
  padding: 6px 14px;
}

.preview-article :deep(blockquote p) { text-indent: 0; }

.preview-article :deep(code) {
  border-radius: 3px;
  background: rgba(38, 49, 41, 0.09);
  color: #8a3823;
  font-size: 0.86em;
  padding: 1px 5px;
}

.preview-article :deep(pre) {
  margin: 10px 0;
  border: 1px solid rgba(244, 239, 228, 0.07);
  border-radius: 6px;
  background: #0c1a12;
  overflow: hidden;
  padding: 12px 14px;
}

.preview-article :deep(pre code) { background: none; color: #dfe6d8; font-size: 0.76rem; padding: 0; }

.report-foot {
  display: flex;
  justify-content: flex-end;
  gap: 9px;
  border-top: 1px solid var(--border);
  padding: 9px 14px;
}

.btn-read {
  min-height: 32px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: var(--paper);
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  padding: 5px 18px;
}

.btn-read:hover { background: var(--accent-hover); }

.btn-dl {
  min-height: 32px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.78rem;
  padding: 5px 14px;
}

.btn-dl:hover { border-color: var(--border-strong); color: var(--paper); }
</style>
