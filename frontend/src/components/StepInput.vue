<script setup>
import { ref } from 'vue'
import { createResearch } from '../api/research.js'

const emit = defineEmits(['submit'])

const topic = ref('')
const isLoading = ref(false)
const error = ref('')

const handleSubmit = async () => {
  if (topic.value.trim().length < 2) {
    error.value = '请输入至少 2 个字符的研究主题'
    return
  }

  error.value = ''
  isLoading.value = true

  try {
    const data = await createResearch(topic.value)
    emit('submit', {
      topic: topic.value,
      tasks: data.todo_items || []
    })
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || '请求失败，请重试'
  } finally {
    isLoading.value = false
  }
}
</script>

<template>
  <div class="step-input">
    <div class="card">
      <h2>开始您的研究</h2>
      <p class="description">输入您想深入了解的主题，AI 将为您规划任务并生成研究报告</p>

      <div class="input-group">
        <textarea
          v-model="topic"
          placeholder="输入您想研究的主题..."
          :disabled="isLoading"
          @keydown.enter.ctrl="handleSubmit"
        ></textarea>
      </div>

      <div v-if="error" class="error-message">
        {{ error }}
      </div>

      <button
        class="btn-primary"
        :disabled="isLoading || topic.trim().length < 2"
        @click="handleSubmit"
      >
        <span v-if="isLoading" class="spinner"></span>
        <span v-else>开始研究</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.step-input {
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
  font-size: 0.875rem;
}

.input-group {
  margin-bottom: 16px;
}

textarea {
  width: 100%;
  min-height: 120px;
  padding: 16px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 1rem;
  resize: vertical;
  transition: var(--transition);
}

textarea:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
}

textarea:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error-message {
  color: #ef4444;
  font-size: 0.875rem;
  margin-bottom: 16px;
  padding: 8px 12px;
  background: rgba(239, 68, 68, 0.1);
  border-radius: var(--radius-sm);
}

.btn-primary {
  width: 100%;
  padding: 14px 24px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 1rem;
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.btn-primary:hover:not(:disabled) {
  background: var(--accent-hover);
  box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>