<script setup>
import { ref, onMounted } from 'vue'
import { createResearch } from '../api/research.js'

const props = defineProps({
  tasks: Array,
  topic: String
})

const emit = defineEmits(['complete', 'report', 'back', 'update:loading'])

const isLoading = ref(true)
const error = ref('')
const taskResults = ref([])
const currentTaskIndex = ref(0)

onMounted(async () => {
  await executeResearch()
})

const executeResearch = async () => {
  isLoading.value = true
  error.value = ''
  emit('update:loading', true)

  try {
    const data = await createResearch(props.topic)
    taskResults.value = data.todo_items || []
    emit('complete', taskResults.value)
    emit('report', data.report_markdown)
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || '研究失败，请重试'
  } finally {
    isLoading.value = false
    emit('update:loading', false)
  }
}
</script>

<template>
  <div class="step-research">
    <div class="card">
      <h2>研究执行中</h2>
      <p class="description">
        主题: <strong>{{ topic }}</strong>
      </p>

      <div v-if="isLoading" class="loading-state">
        <div class="loader">
          <div class="spinner-large"></div>
          <p>正在执行研究任务...</p>
          <p class="sub-text">这可能需要一些时间</p>
        </div>
      </div>

      <div v-else-if="error" class="error-state">
        <p class="error-message">{{ error }}</p>
        <button class="btn-primary" @click="executeResearch">重试</button>
      </div>

      <div v-else class="results-state">
        <div class="tasks-progress">
          <div
            v-for="(task, index) in taskResults"
            :key="index"
            class="task-item completed"
          >
            <span class="check">✓</span>
            <span>{{ task.title || `任务 ${index + 1}` }}</span>
          </div>
        </div>

        <p class="complete-text">所有任务已完成！</p>

        <button class="btn-primary" @click="emit('back')">查看结果</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-research {
  max-width: 600px;
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

.loading-state {
  text-align: center;
  padding: 48px 0;
}

.loader {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.spinner-large {
  width: 48px;
  height: 48px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.loader p {
  font-size: 1rem;
  color: var(--text-primary);
}

.loader .sub-text {
  font-size: 0.875rem;
  color: var(--text-secondary);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.error-state {
  text-align: center;
  padding: 24px;
}

.error-message {
  color: #ef4444;
  margin-bottom: 16px;
  padding: 12px;
  background: rgba(239, 68, 68, 0.1);
  border-radius: var(--radius-sm);
}

.results-state {
  text-align: center;
}

.tasks-progress {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 24px;
}

.task-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg-primary);
  border-radius: var(--radius-sm);
  text-align: left;
}

.task-item .check {
  color: var(--success);
  font-weight: bold;
}

.complete-text {
  color: var(--success);
  font-weight: 500;
  margin-bottom: 24px;
}

.btn-primary {
  padding: 12px 32px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  font-weight: 500;
}

.btn-primary:hover {
  background: var(--accent-hover);
  box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
}
</style>