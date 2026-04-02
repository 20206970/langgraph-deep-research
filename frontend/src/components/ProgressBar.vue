<script setup>
defineProps({
  currentStep: {
    type: Number,
    required: true
  },
  totalSteps: {
    type: Number,
    default: 4
  }
})

const steps = ['主题输入', '任务规划', '研究执行', '报告生成']
</script>

<template>
  <div class="progress-bar">
    <div class="steps">
      <div
        v-for="(step, index) in steps"
        :key="index"
        class="step"
        :class="{
          'completed': index + 1 < currentStep,
          'current': index + 1 === currentStep,
          'pending': index + 1 > currentStep
        }"
      >
        <div class="step-indicator">
          <span v-if="index + 1 < currentStep" class="check">✓</span>
          <span v-else>{{ index + 1 }}</span>
        </div>
        <span class="step-label">{{ step }}</span>
        <div v-if="index < totalSteps - 1" class="step-line"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.progress-bar {
  padding: 16px 24px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
}

.steps {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 0;
  max-width: 600px;
  margin: 0 auto;
}

.step {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  flex: 1;
}

.step:last-child .step-line {
  display: none;
}

.step-indicator {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 0.875rem;
  border: 2px solid var(--border);
  background: var(--bg-primary);
  transition: var(--transition);
  z-index: 1;
}

.step.completed .step-indicator {
  background: var(--success);
  border-color: var(--success);
  color: white;
}

.step.current .step-indicator {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
  animation: pulse 2s infinite;
}

.step-label {
  margin-top: 8px;
  font-size: 0.75rem;
  color: var(--text-secondary);
  text-align: center;
}

.step.completed .step-label,
.step.current .step-label {
  color: var(--text-primary);
}

.step-line {
  position: absolute;
  top: 18px;
  left: 50%;
  width: 100%;
  height: 2px;
  background: var(--border);
}

.step.completed .step-line {
  background: var(--success);
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
  50% { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); }
}

@media (max-width: 640px) {
  .step-label {
    font-size: 0.625rem;
  }
  .step-indicator {
    width: 28px;
    height: 28px;
    font-size: 0.75rem;
  }
  .step-line {
    top: 14px;
  }
}
</style>