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
      <div class="product-mark">LG</div>
      <p class="eyebrow">PRIVATE RESEARCH WORKSPACE</p>
      <h1 id="auth-title">LangGraph<br>深度研究</h1>
      <p class="intro-copy">围绕你的论文资料组织检索、证据与研究结论。</p>
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
  background: #12241f;
}

.auth-intro {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: clamp(48px, 10vh, 120px) clamp(36px, 9vw, 144px);
  color: #eef5ec;
}

.product-mark {
  display: grid;
  width: 46px;
  height: 46px;
  place-items: center;
  margin-bottom: 64px;
  border: 1px solid #74c69d;
  color: #b7e4c7;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.78rem;
  font-weight: 700;
}

.eyebrow {
  color: #9fc5ad;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem;
  font-weight: 700;
}

h1 {
  max-width: 640px;
  margin: 14px 0 20px;
  font-family: Georgia, 'Noto Serif SC', serif;
  font-size: clamp(3rem, 5.2vw, 5.8rem);
  font-weight: 500;
  line-height: 0.98;
}

.intro-copy {
  max-width: 380px;
  color: #bfd7c4;
  font-size: 1rem;
  line-height: 1.8;
}

.auth-form {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: clamp(36px, 7vw, 112px);
  background: #f4f7f2;
  color: #1f3029;
}

.auth-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  margin-bottom: 30px;
  border-bottom: 1px solid #cad7cd;
}

.auth-tabs button {
  padding: 10px 0;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: #64746a;
  font-size: 0.92rem;
}

.auth-tabs button.active {
  border-bottom-color: #167d6a;
  color: #173d32;
  font-weight: 700;
}

form {
  display: grid;
  gap: 18px;
}

label {
  display: grid;
  gap: 7px;
  color: #42544a;
  font-size: 0.82rem;
  font-weight: 700;
}

input {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid #b8c8bd;
  border-radius: 4px;
  background: #fff;
  color: #183228;
  font: inherit;
  font-weight: 400;
}

input:focus {
  outline: 3px solid rgba(22, 125, 106, 0.18);
  border-color: #167d6a;
}

.auth-error {
  color: #a23333;
  font-size: 0.83rem;
  line-height: 1.5;
}

.auth-submit {
  min-height: 46px;
  border: 1px solid #126b5b;
  border-radius: 4px;
  background: #167d6a;
  color: #fff;
  font-size: 0.92rem;
  font-weight: 700;
}

.auth-submit:hover:not(:disabled) {
  background: #115f51;
}

.auth-submit:disabled {
  cursor: wait;
  opacity: 0.65;
}

@media (max-width: 760px) {
  .auth-shell { grid-template-columns: 1fr; }
  .auth-intro { min-height: 35vh; padding: 42px 28px; }
  .product-mark { margin-bottom: 36px; }
  h1 { font-size: 3.15rem; }
  .auth-form { min-height: 65vh; padding: 36px 28px; }
}
</style>
