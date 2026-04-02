<script setup>
import { ref } from 'vue'
import ProgressBar from './components/ProgressBar.vue'
import StepInput from './components/StepInput.vue'
import StepTasks from './components/StepTasks.vue'
import StepResearch from './components/StepResearch.vue'
import StepReport from './components/StepReport.vue'

const currentStep = ref(1)
const topic = ref('')
const tasks = ref([])
const taskResults = ref([])
const report = ref('')
const isLoading = ref(false)

const handleTopicSubmit = (data) => {
  topic.value = data.topic
  tasks.value = data.tasks
  currentStep.value = 2
}

const handleTasksConfirm = () => {
  currentStep.value = 3
}

const handleResearchComplete = (results) => {
  taskResults.value = results
}

const handleReportGenerated = (reportData) => {
  report.value = reportData
  currentStep.value = 4
}

const handleReset = () => {
  currentStep.value = 1
  topic.value = ''
  tasks.value = []
  taskResults.value = []
  report.value = ''
}
</script>

<template>
  <div class="app">
    <header class="header">
      <h1>🔬 LangGraph 深度研究助手</h1>
    </header>

    <ProgressBar :currentStep="currentStep" :totalSteps="4" />

    <main class="main">
      <StepInput
        v-if="currentStep === 1"
        @submit="handleTopicSubmit"
        :isLoading="isLoading"
      />

      <StepTasks
        v-else-if="currentStep === 2"
        :tasks="tasks"
        :topic="topic"
        @confirm="handleTasksConfirm"
        @back="currentStep = 1"
        :isLoading="isLoading"
      />

      <StepResearch
        v-else-if="currentStep === 3"
        :tasks="tasks"
        :topic="topic"
        @complete="handleResearchComplete"
        @report="handleReportGenerated"
        @back="currentStep = 2"
        :isLoading="isLoading"
        @update:loading="isLoading = $event"
      />

      <StepReport
        v-else-if="currentStep === 4"
        :report="report"
        :tasks="tasks"
        :topic="topic"
        @reset="handleReset"
      />
    </main>
  </div>
</template>

<style scoped>
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.header {
  text-align: center;
  padding: 32px 24px 16px;
}

.header h1 {
  font-size: 1.75rem;
  color: var(--text-primary);
}

.main {
  flex: 1;
  padding: 24px;
}
</style>