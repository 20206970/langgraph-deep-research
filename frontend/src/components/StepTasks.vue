<script setup>
import { ref } from 'vue'

const props = defineProps({
  tasks: {
    type: Array,
    default: () => []
  },
  topic: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['confirm', 'back'])

const expandedTask = ref(null)

const toggleTask = (index) => {
  expandedTask.value = expandedTask.value === index ? null : index
}
</script>

<template>
  <div class="step-tasks">
    <div class="card">
      <h2>研究任务规划</h2>
      <p class="description">
        主题: <strong>{{ topic }}</strong>
      </p>

      <div class="tasks-list" v-if="tasks.length > 0">
        <div
          v-for="(task, index) in tasks"
          :key="index"
          class="task-card"
          :class="{ expanded: expandedTask === index }"
          @click="toggleTask(index)"
        >
          <div class="task-header">
            <span class="task-number">{{ index + 1 }}</span>
            <div class="task-info">
              <h3>{{ task.title }}</h3>
              <p class="task-intent">{{ task.intent }}</p>
            </div>
            <span class="expand-icon">{{ expandedTask === index ? '−' : '+' }}</span>
          </div>

          <div v-if="expandedTask === index" class="task-details">
            <span class="query-label">检索关键词:</span>
            <span class="query-tag">{{ task.query }}</span>
          </div>
        </div>
      </div>

      <div v-else class="empty-state">
        <p>暂无任务数据</p>
      </div>

      <div class="actions">
        <button class="btn-secondary" @click="emit('back')">上一步</button>
        <button class="btn-primary" @click="emit('confirm')">执行任务</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-tasks {
  max-width: 700px;
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

.description {
  color: var(--text-secondary);
  text-align: center;
  margin-bottom: 24px;
}

.description strong {
  color: var(--accent);
}

.tasks-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 24px;
}

.task-card {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 16px;
  cursor: pointer;
  transition: var(--transition);
}

.task-card:hover {
  border-color: var(--accent);
}

.task-card.expanded {
  border-color: var(--accent);
}

.task-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.task-number {
  width: 28px;
  height: 28px;
  background: var(--accent);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 600;
  flex-shrink: 0;
}

.task-info {
  flex: 1;
}

.task-info h3 {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 4px;
  font-family: 'Inter', sans-serif;
}

.task-intent {
  font-size: 0.875rem;
  color: var(--text-secondary);
}

.expand-icon {
  color: var(--text-secondary);
  font-size: 1.25rem;
  line-height: 1;
}

.task-details {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.query-label {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-right: 8px;
}

.query-tag {
  display: inline-block;
  padding: 4px 10px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
}

.empty-state {
  text-align: center;
  padding: 32px;
  color: var(--text-secondary);
}

.actions {
  display: flex;
  gap: 12px;
  justify-content: center;
}

.btn-primary, .btn-secondary {
  padding: 12px 24px;
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  font-weight: 500;
  flex: 1;
  max-width: 160px;
}

.btn-primary {
  background: var(--accent);
  color: white;
  border: none;
}

.btn-primary:hover {
  background: var(--accent-hover);
  box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
}

.btn-secondary {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
}

.btn-secondary:hover {
  border-color: var(--text-secondary);
  color: var(--text-primary);
}
</style>