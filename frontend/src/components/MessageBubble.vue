<script setup>
import { computed } from 'vue'
import ReportCard from './ReportCard.vue'

const props = defineProps({
  message: { type: Object, required: true }
})

const emit = defineEmits(['read-report'])

const isUser = computed(() => props.message.role === 'user')
const isReport = computed(() => props.message.message_type === 'research_report')
const isTaskPlan = computed(() => props.message.message_type === 'task_plan')
const isLoading = computed(() => props.message.message_type === 'loading')

const emitReadReport = () => emit('read-report', props.message)
</script>

<template>
  <div class="message-row" :class="{ user: isUser }">
    <span class="seal" :class="isUser ? 'seal-user' : 'seal-ai'">{{ isUser ? '问' : '研' }}</span>

    <div class="entry" :class="{ 'entry-user': isUser }">
      <ReportCard
        v-if="isReport"
        :content="message.content"
        :topic="''"
        @read="emitReadReport"
      />

      <div v-else-if="isTaskPlan && message.tasks" class="task-plan">
        <p class="plan-label">研究提纲</p>
        <div v-for="(task, i) in message.tasks" :key="i" class="plan-item">
          <span class="plan-number">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="plan-title">{{ task.title }}</span>
        </div>
      </div>

      <div v-else-if="isLoading" class="loading-entry">
        <span class="loading-dot"></span>
        <span class="loading-dot"></span>
        <span class="loading-dot"></span>
        <span class="loading-text">正在调档与整理…</span>
      </div>

      <div v-else class="text-content">
        <p v-for="(line, i) in message.content.split('\n')" :key="i">{{ line }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message-row {
  display: flex;
  gap: 12px;
  animation: rise-in 280ms ease both;
  max-width: 92%;
}

.message-row.user {
  flex-direction: row-reverse;
  margin-left: auto;
}

/* 印章式方标 */
.seal {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 6px;
  font-family: var(--font-serif);
  font-size: 0.86rem;
  font-weight: 700;
  margin-top: 2px;
}

.seal-user {
  border: 1px solid var(--accent);
  background: var(--accent-soft);
  color: var(--accent-hover);
}

.seal-ai {
  border: 1px solid var(--border-strong);
  background: var(--ink-800);
  color: var(--text-secondary);
}

/* AI 条目：档案记录 */
.entry {
  min-width: 0;
  border-left: 2px solid var(--border);
  padding: 2px 0 2px 14px;
}

/* 用户条目：朱砂信笺 */
.entry-user {
  border: 1px solid rgba(192, 73, 47, 0.4);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius-md) var(--radius-sm) var(--radius-sm) var(--radius-md);
  background: rgba(192, 73, 47, 0.09);
  padding: 10px 16px;
}

.text-content {
  color: var(--text-primary);
  font-size: 0.9375rem;
  line-height: 1.7;
  overflow-wrap: break-word;
  white-space: pre-wrap;
}

.text-content p { margin: 3px 0; }
.text-content p:empty { height: 8px; }

/* 研究提纲：编号档案列表 */
.task-plan { padding: 2px 0; }

.plan-label {
  color: var(--text-faint);
  font-size: 0.74rem;
  letter-spacing: 0.3em;
  margin-bottom: 9px;
}

.plan-item {
  display: flex;
  align-items: baseline;
  gap: 11px;
  border-bottom: 1px dashed var(--border);
  padding: 6px 2px;
  font-size: 0.875rem;
}

.plan-item:last-child { border-bottom: none; }

.plan-number {
  flex: 0 0 auto;
  color: var(--accent-hover);
  font-family: var(--font-mono);
  font-size: 0.7rem;
}

.plan-title { color: var(--text-primary); line-height: 1.55; }

/* 加载态 */
.loading-entry {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
}

.loading-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--status-running);
  animation: breathe 1.2s ease-in-out infinite;
}

.loading-dot:nth-child(2) { animation-delay: 0.18s; }
.loading-dot:nth-child(3) { animation-delay: 0.36s; }

.loading-text {
  margin-left: 6px;
  color: var(--text-faint);
  font-size: 0.78rem;
}

@media (max-width: 720px) {
  .message-row { max-width: 100%; }
}
</style>
