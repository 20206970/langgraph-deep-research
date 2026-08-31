<script setup>
import { onMounted, ref } from 'vue'
import { getHistoryDetail, getHistoryList } from '../api/research.js'

const emit = defineEmits(['close', 'read-report'])

const historyList = ref([])
const isLoading = ref(true)

onMounted(async () => {
  isLoading.value = true
  try {
    historyList.value = await getHistoryList()
  } catch (err) {
    console.error('加载历史失败:', err)
  } finally {
    isLoading.value = false
  }
})

const viewHistory = async (item) => {
  try {
    const detail = await getHistoryDetail(item.id)
    emit('read-report', {
      topic: detail.topic,
      markdown: detail.report,
      meta: { generatedAt: item.created_at }
    })
  } catch (err) {
    console.error('加载详情失败:', err)
  }
}
</script>

<template>
  <div class="history-layer" role="presentation" @click.self="emit('close')">
    <aside class="history-panel" role="dialog" aria-modal="true" aria-label="研究档案">
      <header class="panel-header">
        <div>
          <p class="panel-kicker">ARCHIVE</p>
          <h3>研究档案</h3>
        </div>
        <button class="icon-button" type="button" aria-label="关闭研究档案" @click="emit('close')">&times;</button>
      </header>

      <div class="panel-body">
        <div v-if="isLoading" class="panel-state">
          <span class="spinner"></span>
          <p>正在调阅档案…</p>
        </div>
        <div v-else-if="!historyList.length" class="panel-state">暂无研究记录。</div>
        <div v-else class="history-items">
          <button
            v-for="(item, index) in historyList"
            :key="item.id"
            type="button"
            class="history-item"
            @click="viewHistory(item)"
          >
            <span class="item-number">{{ String(index + 1).padStart(2, '0') }}</span>
            <span class="item-main">
              <span class="item-topic">{{ item.topic }}</span>
              <span class="item-time">{{ item.created_at }}</span>
            </span>
            <span class="item-arrow">›</span>
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.history-layer {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  justify-content: flex-end;
  background: rgba(4, 10, 7, 0.6);
}

.history-panel {
  display: flex;
  width: min(400px, 100%);
  height: 100%;
  flex-direction: column;
  border-left: 1px solid var(--border);
  background: var(--ink-900);
  box-shadow: -16px 0 44px rgba(0, 0, 0, 0.45);
  animation: rise-in 260ms ease both;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  border-bottom: 1px solid var(--border);
  padding: 20px 20px 16px;
}

.panel-kicker {
  color: var(--accent-hover);
  font-family: var(--font-mono);
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.25em;
}

.panel-header h3 {
  margin-top: 3px;
  font-size: 1.12rem;
  letter-spacing: 0.08em;
}

.icon-button {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  font-size: 1.3rem;
  line-height: 1;
}

.icon-button:hover { border-color: var(--accent); color: var(--paper); }

.panel-body { flex: 1; overflow-y: auto; padding: 14px; }

.panel-state {
  display: grid;
  justify-items: center;
  gap: 12px;
  padding: 48px 0;
  color: var(--text-faint);
  font-size: 0.82rem;
}

.spinner {
  width: 26px;
  height: 26px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.history-items {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.history-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--ink-800);
  color: inherit;
  padding: 11px 13px;
  text-align: left;
}

.history-item:hover {
  border-color: rgba(192, 73, 47, 0.55);
  background: var(--ink-700);
}

.item-number {
  flex: 0 0 auto;
  color: var(--accent-hover);
  font-family: var(--font-mono);
  font-size: 0.72rem;
}

.item-main { display: grid; min-width: 0; flex: 1; gap: 3px; }

.item-topic {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 0.84rem;
  line-height: 1.45;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.item-time {
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: 0.66rem;
}

.item-arrow { color: var(--text-faint); font-size: 1.05rem; }

@media (max-width: 620px) {
  .history-panel { width: 100%; border-left: 0; }
}
</style>
