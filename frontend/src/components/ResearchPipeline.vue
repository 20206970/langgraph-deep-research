<script setup>
defineProps({
  tasks: { type: Array, default: () => [] },
  retrieval: { type: Object, default: null },
  active: { type: Boolean, default: false }
})

const STATUS_LABELS = {
  running: '运行中',
  succeeded: '完成',
  failed: '失败',
  retrying: '重试中'
}
</script>

<template>
  <section class="pipeline" aria-live="polite">
    <header class="pipeline-head">
      <span class="head-mark">▍</span>
      <span class="head-title">研究管线</span>
      <span v-if="active" class="head-live">
        <span class="live-dot"></span>进行中
      </span>
    </header>

    <div class="pipeline-body">
      <div
        v-for="(task, index) in tasks"
        :key="task.taskId"
        class="task-row"
        :style="{ '--i': index }"
      >
        <span class="task-id">{{ task.taskId.slice(0, 12) }}</span>
        <span class="status-chip" :class="`s-${task.status}`">
          <span v-if="task.status === 'succeeded'" class="chip-icon">✓</span>
          <span v-else-if="task.status === 'failed'" class="chip-icon">✕</span>
          <span v-else-if="task.status === 'retrying'" class="chip-icon">↻</span>
          <span v-else class="chip-icon chip-dot"></span>
          {{ STATUS_LABELS[task.status] || task.status }}
        </span>
        <span v-if="task.attempt > 1" class="attempt-badge">第 {{ task.attempt }} 次</span>
        <span v-if="task.status === 'retrying' && task.retryDelay" class="retry-badge">退避 {{ task.retryDelay }}s</span>
        <span v-if="task.error" class="task-error" :title="task.error">{{ task.error }}</span>
      </div>

      <div
        v-if="retrieval"
        class="retrieval-row"
        :class="{ degraded: retrieval.reranker_status === 'degraded' }"
      >
        <span class="retrieval-icon">◈</span>
        <template v-if="retrieval.reranker_status === 'degraded'">
          私有资料精排不可用，当前按混合召回结果排序。
        </template>
        <template v-else>
          已检索私有资料：{{ retrieval.parent_count || 0 }} 个相关片段参与研究。
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.pipeline {
  width: min(800px, calc(100% - 48px));
  margin: 0 auto 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--ink-900);
  font-size: 0.78rem;
  overflow: hidden;
}

.pipeline-head {
  display: flex;
  align-items: center;
  gap: 7px;
  border-bottom: 1px solid var(--border);
  padding: 9px 15px;
}

.head-mark { color: var(--accent); }

.head-title {
  color: var(--text-primary);
  font-family: var(--font-serif);
  font-size: 0.84rem;
  font-weight: 700;
  letter-spacing: 0.14em;
}

.head-live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
  color: var(--status-running);
  font-size: 0.7rem;
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--status-running);
  animation: pulse-dot 1.4s ease-in-out infinite;
}

.pipeline-body { padding: 6px 15px 10px; }

.task-row {
  display: flex;
  min-height: 27px;
  align-items: center;
  gap: 10px;
  animation: rise-in 240ms ease both;
  animation-delay: calc(var(--i, 0) * 40ms);
}

.task-id {
  min-width: 104px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: 0.72rem;
}

.status-chip {
  display: inline-flex;
  min-width: 66px;
  align-items: center;
  gap: 5px;
  border-radius: 3px;
  font-size: 0.72rem;
  padding: 2px 8px;
}

.status-chip.s-running { color: var(--status-running); background: rgba(217, 164, 65, 0.12); }
.status-chip.s-succeeded { color: var(--status-success); background: rgba(88, 196, 122, 0.12); }
.status-chip.s-failed { color: var(--status-failed); background: rgba(209, 106, 90, 0.14); }
.status-chip.s-retrying { color: var(--status-running); background: rgba(217, 164, 65, 0.12); }

.chip-icon { font-size: 0.7rem; line-height: 1; }

.chip-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse-dot 1.4s ease-in-out infinite;
}

.attempt-badge {
  border: 1px solid var(--border);
  border-radius: 3px;
  color: var(--text-faint);
  font-size: 0.66rem;
  padding: 1px 6px;
  white-space: nowrap;
}

.retry-badge {
  border: 1px solid rgba(217, 164, 65, 0.4);
  border-radius: 3px;
  color: var(--status-running);
  font-family: var(--font-mono);
  font-size: 0.66rem;
  padding: 1px 6px;
  white-space: nowrap;
}

.task-error {
  overflow: hidden;
  color: var(--error-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.retrieval-row {
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px dashed var(--border);
  color: var(--text-secondary);
  font-size: 0.74rem;
  margin-top: 7px;
  padding-top: 8px;
}

.retrieval-icon { color: var(--accent-hover); }

.retrieval-row.degraded { color: var(--status-running); }
.retrieval-row.degraded .retrieval-icon { color: var(--status-running); }

@media (max-width: 720px) {
  .pipeline { width: min(100% - 32px, 800px); }
  .task-row { flex-wrap: wrap; gap: 4px 9px; }
  .task-error { max-width: 100%; white-space: normal; }
}
</style>
