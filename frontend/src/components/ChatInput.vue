<script setup>
import { ref } from 'vue'

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['send'])
const text = ref('')

const handleSend = () => {
  const msg = text.value.trim()
  if (!msg || props.disabled) return
  emit('send', msg)
  text.value = ''
}

const handleKeydown = (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="chat-input">
    <div class="input-frame">
      <textarea
        v-model="text"
        placeholder="提出研究问题，如：对比近三年图神经网络在蛋白质结构预测中的进展…"
        :disabled="disabled"
        rows="2"
        @keydown="handleKeydown"
      ></textarea>
      <div class="input-foot">
        <span class="input-hint">Ctrl + Enter 开始研究</span>
        <button
          class="btn-send"
          :disabled="disabled || !text.trim()"
          @click="handleSend"
        >
          <span v-if="disabled" class="spinner"></span>
          <span v-else>开 始</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-input {
  width: min(800px, calc(100% - 48px));
  margin: 0 auto;
  padding: 0 0 22px;
}

.input-frame {
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--ink-900);
  overflow: hidden;
  transition: border-color var(--transition), box-shadow var(--transition);
}

.input-frame:focus-within {
  border-color: rgba(192, 73, 47, 0.65);
  box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow);
}

textarea {
  display: block;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 0.9375rem;
  font-family: inherit;
  line-height: 1.6;
  padding: 14px 16px 6px;
  resize: none;
}

textarea:focus { outline: none; }

textarea::placeholder { color: var(--text-faint); }

textarea:disabled { opacity: 0.55; }

.input-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px 10px 16px;
}

.input-hint {
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: 0.64rem;
  letter-spacing: 0.05em;
}

.btn-send {
  display: flex;
  min-width: 88px;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: var(--paper);
  font-size: 0.82rem;
  font-weight: 600;
  letter-spacing: 0.2em;
  padding: 9px 22px;
}

.btn-send:hover:not(:disabled) { background: var(--accent-hover); }

.btn-send:disabled {
  background: var(--ink-700);
  color: var(--text-faint);
  cursor: not-allowed;
}

.spinner {
  width: 15px;
  height: 15px;
  border: 2px solid rgba(246, 241, 230, 0.3);
  border-top-color: var(--paper);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 720px) {
  .chat-input { width: min(100% - 32px, 800px); }
  .input-hint { display: none; }
}
</style>
