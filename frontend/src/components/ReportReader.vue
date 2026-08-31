<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  countCjkChars,
  downloadMarkdown,
  extractOutline,
  highlightCodeBlocks,
  renderMarkdown
} from '../lib/markdown.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  topic: { type: String, default: '' },
  markdown: { type: String, default: '' },
  meta: { type: Object, default: () => ({}) }
})

const emit = defineEmits(['close'])

const scrollContainer = ref(null)
const articleEl = ref(null)
const outline = ref([])
const activeId = ref('')
let observer = null

const rendered = computed(() => renderMarkdown(props.markdown))
const charCount = computed(() => countCjkChars(props.markdown))
const generatedAt = computed(() => {
  const raw = props.meta?.generatedAt
  if (!raw) return ''
  const date = new Date(raw)
  return Number.isNaN(date.getTime())
    ? String(raw)
    : `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
})

const metaLine = computed(() => {
  const parts = []
  if (props.meta?.taskCount) parts.push(`${props.meta.taskCount} 项研究任务`)
  if (charCount.value) parts.push(`约 ${charCount.value.toLocaleString('zh-CN')} 字`)
  if (generatedAt.value) parts.push(`生成于 ${generatedAt.value}`)
  return parts.join(' · ')
})

const setupReader = async () => {
  await nextTick()
  if (!articleEl.value || !scrollContainer.value) return
  highlightCodeBlocks(articleEl.value)
  outline.value = extractOutline(articleEl.value)
  observer?.disconnect()
  observer = new IntersectionObserver(
    (entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting)
      if (visible.length) activeId.value = visible[0].target.id
    },
    { root: scrollContainer.value, rootMargin: '-15% 0px -70% 0px', threshold: 0 }
  )
  articleEl.value.querySelectorAll('h1, h2, h3').forEach((heading) => observer.observe(heading))
}

const scrollTo = (id) => {
  const target = articleEl.value?.querySelector(`#${id}`)
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const printReport = () => {
  window.print()
}

const handleKeydown = (event) => {
  if (event.key === 'Escape') emit('close')
}

watch(
  () => props.open,
  (open) => {
    document.removeEventListener('keydown', handleKeydown)
    if (open) {
      document.addEventListener('keydown', handleKeydown)
      document.body.style.overflow = 'hidden'
      setupReader()
    } else {
      document.body.style.overflow = ''
      observer?.disconnect()
      observer = null
      activeId.value = ''
    }
  }
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  document.body.style.overflow = ''
  observer?.disconnect()
})
</script>

<template>
  <Teleport to="body">
    <Transition name="reader">
      <div v-if="open" class="reader-overlay" role="dialog" aria-modal="true">
        <aside v-if="outline.length" class="reader-toc">
          <p class="toc-heading">目 录</p>
          <nav class="toc-list">
            <button
              v-for="item in outline"
              :key="item.id"
              type="button"
              class="toc-item"
              :class="[`level-${item.level}`, { active: activeId === item.id }]"
              @click="scrollTo(item.id)"
            >
              <span class="toc-number">{{ item.catalogNumber }}</span>
              <span class="toc-label">{{ item.label }}</span>
            </button>
          </nav>
        </aside>

        <div ref="scrollContainer" class="reader-body">
          <header class="reader-topbar">
            <div class="topbar-title">
              <span class="topbar-mark">▍</span>
              <span class="topbar-text">{{ topic || '研究报告' }}</span>
            </div>
            <div class="topbar-actions">
              <button type="button" class="btn-topbar" @click="printReport">打印</button>
              <button type="button" class="btn-topbar" @click="downloadMarkdown(markdown, topic)">下载 .md</button>
              <button type="button" class="btn-topbar btn-close" aria-label="关闭" @click="emit('close')">✕</button>
            </div>
          </header>

          <article class="reader-paper paper-grain">
            <p v-if="metaLine" class="paper-meta">{{ metaLine }}</p>
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div ref="articleEl" class="paper-article" v-html="rendered"></div>
            <p class="paper-colophon">— 卷 完 —</p>
          </article>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.reader-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  background: rgba(6, 13, 9, 0.92);
}

/* ---------- 左侧目录 ---------- */
.reader-toc {
  display: flex;
  width: 252px;
  flex: 0 0 auto;
  flex-direction: column;
  border-right: 1px solid var(--border);
  background: var(--ink-900);
  padding: 26px 0 18px;
}

.toc-heading {
  padding: 0 20px 14px;
  color: var(--text-secondary);
  font-family: var(--font-serif);
  font-size: 0.86rem;
  letter-spacing: 0.55em;
}

.toc-list {
  flex: 1;
  overflow-y: auto;
}

.toc-item {
  display: flex;
  width: 100%;
  align-items: baseline;
  gap: 10px;
  border: none;
  border-left: 2px solid transparent;
  background: transparent;
  padding: 7px 18px;
  color: var(--text-faint);
  font-size: 0.8rem;
  line-height: 1.45;
  text-align: left;
}

.toc-item.level-2 { padding-left: 30px; }
.toc-item.level-3 { padding-left: 42px; font-size: 0.76rem; }

.toc-item:hover { color: var(--text-primary); }

.toc-item.active {
  border-left-color: var(--accent);
  background: linear-gradient(90deg, var(--accent-soft), transparent 70%);
  color: var(--paper);
}

.toc-item.active .toc-number { color: var(--accent-hover); }

.toc-number {
  flex: 0 0 auto;
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: 0.68rem;
  opacity: 0.85;
}

.toc-label {
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* ---------- 阅读主体 ---------- */
.reader-body {
  flex: 1;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.reader-topbar {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  min-height: 54px;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  border-bottom: 1px solid var(--border);
  background: rgba(10, 22, 16, 0.88);
  backdrop-filter: blur(8px);
  padding: 10px 26px;
}

.topbar-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}

.topbar-mark { color: var(--accent); }

.topbar-text {
  overflow: hidden;
  color: var(--text-primary);
  font-family: var(--font-serif);
  font-size: 0.95rem;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-actions { display: flex; flex: 0 0 auto; gap: 8px; }

.btn-topbar {
  min-height: 30px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.76rem;
  padding: 4px 12px;
}

.btn-topbar:hover {
  border-color: var(--border-strong);
  color: var(--paper);
}

.btn-topbar.btn-close:hover {
  border-color: var(--accent);
  color: var(--accent-hover);
}

/* ---------- 纸面版心 ---------- */
.reader-paper {
  max-width: 880px;
  margin: 34px auto 70px;
  border-radius: 3px;
  background: var(--paper);
  color: var(--paper-text);
  box-shadow: var(--shadow), 0 0 0 1px rgba(244, 239, 228, 0.06);
  padding: 58px 72px 64px;
}

.paper-meta {
  margin-bottom: 34px;
  border-bottom: 1px solid rgba(38, 49, 41, 0.18);
  color: var(--paper-text-secondary);
  font-family: var(--font-mono);
  font-size: 0.7rem;
  letter-spacing: 0.08em;
  padding-bottom: 12px;
  text-align: center;
}

.paper-colophon {
  margin-top: 52px;
  color: var(--paper-text-secondary);
  font-family: var(--font-serif);
  font-size: 0.82rem;
  letter-spacing: 0.5em;
  text-align: center;
}

/* ---------- 手稿排版 ---------- */
.paper-article { font-family: var(--font-serif); }

.paper-article :deep(h1) {
  margin: 8px 0 26px;
  padding-bottom: 18px;
  border-bottom: 3px double rgba(38, 49, 41, 0.35);
  font-size: 1.8rem;
  letter-spacing: 0.04em;
  line-height: 1.4;
  text-align: center;
}

.paper-article :deep(h2) {
  margin: 38px 0 14px;
  font-size: 1.3rem;
  letter-spacing: 0.03em;
}

.paper-article :deep(h2)::before {
  content: '▍';
  margin-right: 7px;
  color: var(--accent);
}

.paper-article :deep(h3) {
  margin: 26px 0 10px;
  color: #33413a;
  font-size: 1.06rem;
}

.paper-article :deep(p) {
  margin: 11px 0;
  line-height: 1.95;
  text-align: justify;
  text-indent: 2em;
}

.paper-article :deep(li) {
  margin: 5px 0;
  line-height: 1.85;
}

.paper-article :deep(ul), .paper-article :deep(ol) {
  margin: 12px 0 12px 2em;
}

.paper-article :deep(li > p) { text-indent: 0; margin: 4px 0; }

.paper-article :deep(blockquote) {
  margin: 18px 0;
  border-left: 3px double rgba(38, 49, 41, 0.4);
  background: rgba(38, 49, 41, 0.045);
  color: var(--paper-text-secondary);
  font-size: 0.94em;
  padding: 10px 20px;
}

.paper-article :deep(blockquote p) { text-indent: 0; }

.paper-article :deep(code) {
  border-radius: 3px;
  background: rgba(38, 49, 41, 0.09);
  color: #8a3823;
  font-size: 0.86em;
  padding: 1px 6px;
}

.paper-article :deep(pre) {
  margin: 16px 0;
  border: 1px solid rgba(244, 239, 228, 0.07);
  border-radius: 8px;
  background: #0c1a12;
  line-height: 1.7;
  overflow-x: auto;
  padding: 16px 20px;
}

.paper-article :deep(pre code) {
  background: none;
  color: #dfe6d8;
  font-size: 0.84rem;
  padding: 0;
}

.paper-article :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92em;
  margin: 16px 0;
}

.paper-article :deep(th), .paper-article :deep(td) {
  border: 1px solid rgba(38, 49, 41, 0.22);
  padding: 7px 11px;
}

.paper-article :deep(th) {
  background: var(--paper-dim);
  font-weight: 700;
}

.paper-article :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.paper-article :deep(hr) {
  border: none;
  border-top: 1px solid rgba(38, 49, 41, 0.28);
  margin: 30px auto;
  width: 38%;
}

.paper-article :deep(img) { max-width: 100%; }
.paper-article :deep(strong) { color: #1d2b22; }

/* ---------- 进出场 ---------- */
.reader-enter-active { transition: opacity 240ms ease; }
.reader-leave-active { transition: opacity 160ms ease; }
.reader-enter-from, .reader-leave-to { opacity: 0; }
.reader-enter-active .reader-paper { animation: rise-in 320ms ease both; }

/* ---------- 响应式 ---------- */
@media (max-width: 960px) {
  .reader-toc { display: none; }
  .reader-paper { margin: 16px 12px 40px; padding: 34px 22px 44px; }
  .reader-topbar { padding: 10px 16px; }
}

/* ---------- 打印：只留纸面 ---------- */
@media print {
  .reader-overlay { position: static; background: none; display: block; }
  .reader-toc, .reader-topbar, .paper-colophon { display: none !important; }
  .reader-body { overflow: visible; }
  .reader-paper {
    box-shadow: none;
    margin: 0;
    max-width: 100%;
    padding: 0;
    background: #fff;
  }
}
</style>
