<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  pending: { type: Boolean, default: false },
  error: { type: String, default: '' }
})

const emit = defineEmits(['authenticate'])
const mode = ref('login')
const username = ref('')
const password = ref('')
const confirmation = ref('')
const validationError = ref('')

const isRegister = computed(() => mode.value === 'register')

const changeMode = (nextMode) => {
  mode.value = nextMode
  validationError.value = ''
  confirmation.value = ''
}

const submit = () => {
  validationError.value = ''
  if (username.value.trim().length < 3) {
    validationError.value = '用户名至少需要 3 个字符。'
    return
  }
  if (password.value.length < 8) {
    validationError.value = '密码至少需要 8 个字符。'
    return
  }
  if (isRegister.value && password.value !== confirmation.value) {
    validationError.value = '两次输入的密码不一致。'
    return
  }
  emit('authenticate', {
    mode: mode.value,
    credentials: { username: username.value.trim(), password: password.value }
  })
}
</script>

<template>
  <main class="auth-shell">
    <section class="auth-intro" aria-labelledby="auth-title">
      <div class="product-seal">研</div>
      <p class="eyebrow">PRIVATE RESEARCH ARCHIVE</p>
      <h1 id="auth-title">深度研究<br>工作台</h1>
      <p class="intro-copy">围绕你的论文资料组织检索、证据与研究结论，每一次研究都归档成一份完整的手稿。</p>
    </section>

    <section class="auth-form" aria-label="账户认证">
      <div class="auth-tabs" role="tablist" aria-label="认证方式">
        <button :class="{ active: !isRegister }" type="button" role="tab" @click="changeMode('login')">登录</button>
        <button :class="{ active: isRegister }" type="button" role="tab" @click="changeMode('register')">注册</button>
      </div>

      <form @submit.prevent="submit">
        <label>
          用户名
          <input v-model="username" autocomplete="username" maxlength="64" :disabled="pending" required>
        </label>
        <label>
          密码
          <input v-model="password" type="password" autocomplete="current-password" maxlength="72" :disabled="pending" required>
        </label>
        <label v-if="isRegister">
          确认密码
          <input v-model="confirmation" type="password" autocomplete="new-password" maxlength="72" :disabled="pending" required>
        </label>

        <p v-if="validationError || props.error" class="auth-error" role="alert">{{ validationError || props.error }}</p>
        <button class="auth-submit" type="submit" :disabled="pending">
          {{ pending ? '处理中' : isRegister ? '创建账户' : '进入工作区' }}
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.auth-shell {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(360px, 0.85fr);
  min-height: 100vh;
  background: var(--ink-950);
}

/* 左：档案室墙面 */
.auth-intro {
  display: flex;
  flex-direction: column;
  justify-content: center;
  border-right: 1px solid rgba(244, 239, 228, 0.08);
  background:
    radial-gradient(1200px 600px at 12% 8%, rgba(28, 54, 39, 0.55), transparent 62%),
    var(--ink-900);
  padding: clamp(48px, 10vh, 120px) clamp(36px, 9vw, 144px);
  color: var(--text-primary);
}

.product-seal {
  display: grid;
  width: 52px;
  height: 52px;
  place-items: center;
  margin-bottom: 60px;
  border-radius: 10px;
  background: var(--accent);
  color: var(--paper);
  font-family: var(--font-serif);
  font-size: 1.42rem;
  font-weight: 700;
  box-shadow: inset 0 0 0 2px rgba(246, 241, 230, 0.32), 0 6px 26px rgba(192, 73, 47, 0.4);
}

.eyebrow {
  color: var(--accent-hover);
  font-family: var(--font-mono);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.32em;
}

h1 {
  max-width: 640px;
  margin: 16px 0 22px;
  font-family: var(--font-serif);
  font-size: clamp(2.9rem, 5.2vw, 5.4rem);
  font-weight: 700;
  line-height: 1.04;
  letter-spacing: 0.04em;
}

.intro-copy {
  max-width: 400px;
  border-left: 2px solid rgba(192, 73, 47, 0.55);
  color: var(--text-secondary);
  font-size: 0.98rem;
  line-height: 1.9;
  padding-left: 16px;
}

/* 右：登记台纸面 */
.auth-form {
  display: flex;
  flex-direction: column;
  justify-content: center;
  background: var(--paper);
  color: var(--paper-text);
  padding: clamp(36px, 7vw, 112px);
}

.auth-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  margin-bottom: 30px;
  border-bottom: 1px solid rgba(38, 49, 41, 0.2);
}

.auth-tabs button {
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--paper-text-secondary);
  font-size: 0.92rem;
  padding: 10px 0;
}

.auth-tabs button.active {
  border-bottom-color: var(--accent);
  color: var(--paper-text);
  font-weight: 700;
}

form { display: grid; gap: 18px; }

label {
  display: grid;
  gap: 7px;
  color: var(--paper-text-secondary);
  font-size: 0.82rem;
  font-weight: 700;
}

input {
  width: 100%;
  min-height: 44px;
  border: 1px solid rgba(38, 49, 41, 0.28);
  border-radius: var(--radius-sm);
  background: #fdfaf3;
  color: var(--paper-text);
  font: inherit;
  font-weight: 400;
  padding: 10px 12px;
}

input:focus {
  outline: 3px solid rgba(192, 73, 47, 0.16);
  border-color: var(--accent);
}

.auth-error {
  color: var(--accent);
  font-size: 0.83rem;
  line-height: 1.5;
}

.auth-submit {
  min-height: 46px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: var(--paper);
  font-size: 0.92rem;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.auth-submit:hover:not(:disabled) { background: var(--accent-hover); }

.auth-submit:disabled {
  cursor: wait;
  opacity: 0.65;
}

@media (max-width: 760px) {
  .auth-shell { grid-template-columns: 1fr; }
  .auth-intro { min-height: 35vh; padding: 42px 28px; }
  .product-seal { margin-bottom: 32px; }
  h1 { font-size: 2.9rem; }
  .auth-form { min-height: 65vh; padding: 36px 28px; }
}
</style>
