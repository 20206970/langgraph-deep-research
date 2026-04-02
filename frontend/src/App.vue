<template>
  <div id="app">
    <!-- 主容器 -->
    <div class="container">
      <!-- 页面头部 -->
      <header class="header">
        <h1>LangGraph 深度研究助手</h1>
        <p class="subtitle">基于 LangGraph 的智能研究工作流</p>
      </header>

      <!-- 主内容区域 - 多步骤研究流程 -->
      <main class="main-content">
        <!-- TODO: 集成各步骤组件 -->
        <div class="placeholder">
          <p>正在加载研究工作流...</p>
        </div>
      </main>

      <!-- 页脚 -->
      <footer class="footer">
        <p>Powered by LangGraph + Vue 3</p>
      </footer>
    </div>
  </div>
</template>

<script>
// 引入子组件 - 将在后续步骤中实现
// import StepInput from './components/StepInput.vue'
// import StepTasks from './components/StepTasks.vue'
// import StepResearch from './components/StepResearch.vue'
// import StepReport from './components/StepReport.vue'
// import ProgressBar from './components/ProgressBar.vue'

export default {
  name: 'App',
  components: {
    // StepInput,
    // StepTasks,
    // StepResearch,
    // StepReport,
    // ProgressBar
  },
  data() {
    return {
      // 研究任务状态
      currentStep: 1,
      researchTopic: '',
      isResearching: false
    }
  },
  methods: {
    // 处理研究主题提交
    handleSubmit(topic) {
      this.researchTopic = topic
      this.currentStep = 2
    },
    // 开始研究
    startResearch() {
      this.isResearching = true
      this.currentStep = 3
    }
  }
}
</script>

<style scoped>
/* 应用容器 */
#app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* 页面头部 */
.header {
  text-align: center;
  padding: 40px 0 32px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 32px;
}

.header h1 {
  font-size: 2rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.subtitle {
  color: var(--text-secondary);
  font-size: 1rem;
}

/* 主内容区域 */
.main-content {
  flex: 1;
  padding: 16px 0;
}

/* 占位符样式 - 后续将替换为实际组件 */
.placeholder {
  text-align: center;
  padding: 60px 20px;
  background: var(--bg-secondary);
  border-radius: var(--radius-lg);
  color: var(--text-secondary);
}

/* 页脚 */
.footer {
  text-align: center;
  padding: 24px 0;
  border-top: 1px solid var(--border);
  margin-top: 32px;
  color: var(--text-secondary);
  font-size: 0.875rem;
}
</style>