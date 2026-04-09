<script setup>
import { computed } from 'vue'
import ReportCard from './ReportCard.vue'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const isUser = computed(() => props.message.role === 'user')
const isReport = computed(() => props.message.message_type === 'research_report')
const isTaskPlan = computed(() => props.message.message_type === 'task_plan')
</script>

<template>
  <div class="message-bubble" :class="{ user: isUser, assistant: !isUser }">
    <div class="avatar">
      <span v-if="isUser">U</span>
      <span v-else>AI</span>
    </div>

    <div class="bubble-content">
      <ReportCard
        v-if="isReport"
        :content="message.content"
        :topic="''"
      />

      <div v-else-if="isTaskPlan && message.tasks" class="task-list">
        <p class="task-label">任务规划：</p>
        <div v-for="(task, i) in message.tasks" :key="i" class="task-chip">
          <span class="task-num">{{ i + 1 }}</span>
          <span>{{ task.title }}</span>
        </div>
      </div>

      <div v-else class="text-content">
        <p v-for="(line, i) in message.content.split('\n')" :key="i">{{ line }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message-bubble {
  display: flex;
  gap: 12px;
  padding: 4px 0;
  max-width: 85%;
}

.message-bubble.user {
  margin-left: auto;
  flex-direction: row-reverse;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 600;
  flex-shrink: 0;
}

.message-bubble.user .avatar {
  background: var(--accent);
  color: white;
}

.message-bubble.assistant .avatar {
  background: var(--border);
  color: var(--text-secondary);
}

.bubble-content {
  min-width: 0;
}

.message-bubble.user .bubble-content {
  background: rgba(59, 130, 246, 0.15);
  border-radius: var(--radius-lg) var(--radius-sm) var(--radius-lg) var(--radius-lg);
  padding: 12px 16px;
}

.message-bubble.assistant .bubble-content {
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm) var(--radius-lg) var(--radius-lg) var(--radius-lg);
  padding: 12px 16px;
}

.text-content {
  line-height: 1.6;
  font-size: 0.9375rem;
}

.text-content p {
  margin: 4px 0;
}

.text-content p:empty {
  height: 8px;
}

.task-list {
  padding: 4px 0;
}

.task-label {
  font-size: 0.8rem;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.task-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  margin-bottom: 6px;
  background: var(--bg-primary);
  border-radius: var(--radius-sm);
  font-size: 0.875rem;
}

.task-num {
  width: 22px;
  height: 22px;
  background: var(--accent);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  font-weight: 600;
  flex-shrink: 0;
}
</style>
