<script setup>
import { computed } from 'vue'

const props = defineProps({
  documents: { type: Array, default: () => [] },
  selectedIds: { type: Array, default: () => [] },
  useAll: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['update:selectedIds', 'update:useAll', 'open-library'])

const readyDocuments = computed(() => props.documents.filter((document) => {
  const version = document.current_version || document.currentVersion
  return document.deleted_at == null && version?.status === 'ready' && version.retrieval_enabled
}))

const scopeLabel = computed(() => {
  if (!readyDocuments.value.length) return '未选择论文'
  if (props.useAll) return `全部 ${readyDocuments.value.length} 篇可用论文`
  if (!props.selectedIds.length) return '未选择论文'
  return `已选择 ${props.selectedIds.length} 篇论文`
})

const toggleDocument = (documentId) => {
  const next = new Set(props.selectedIds)
  next.has(documentId) ? next.delete(documentId) : next.add(documentId)
  emit('update:selectedIds', [...next])
}

const setUseAll = (event) => {
  emit('update:useAll', event.target.checked)
  if (event.target.checked) emit('update:selectedIds', [])
}
</script>

<template>
  <section class="scope-selector" :class="{ disabled }" aria-label="本次研究引用范围">
    <div class="scope-summary">
      <span class="scope-kicker">资料范围</span>
      <strong>{{ scopeLabel }}</strong>
    </div>
    <details :open="readyDocuments.length > 0 && !disabled">
      <summary>调整</summary>
      <div v-if="readyDocuments.length" class="scope-options">
        <label class="scope-all">
          <input type="checkbox" :checked="useAll" :disabled="disabled" @change="setUseAll">
          使用全部可用论文
        </label>
        <label v-for="document in readyDocuments" :key="document.document_id" class="scope-document">
          <input
            type="checkbox"
            :checked="selectedIds.includes(document.document_id)"
            :disabled="disabled || useAll"
            @change="toggleDocument(document.document_id)"
          >
          <span>{{ document.title }}</span>
        </label>
      </div>
      <p v-else class="scope-empty">没有可用于检索的就绪论文。</p>
    </details>
    <button class="library-button" type="button" :disabled="disabled" @click="emit('open-library')">管理论文</button>
  </section>
</template>

<style scoped>
.scope-selector {
  display: grid;
  grid-template-columns: minmax(160px, auto) minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  width: min(800px, calc(100% - 48px));
  margin: 0 auto;
  padding: 10px 0;
  border-top: 1px solid var(--border);
  background: var(--bg-secondary);
}

.scope-summary { display: grid; gap: 1px; min-width: 0; }
.scope-kicker { color: var(--text-secondary); font-size: 0.7rem; font-weight: 700; }
.scope-summary strong { overflow: hidden; color: var(--text-primary); font-size: 0.82rem; text-overflow: ellipsis; white-space: nowrap; }

details { min-width: 0; }
summary { width: fit-content; color: var(--accent-hover); cursor: pointer; font-size: 0.8rem; }
.scope-options { display: grid; gap: 8px; max-height: 144px; margin-top: 10px; overflow-y: auto; }
.scope-all, .scope-document { display: flex; min-width: 0; gap: 8px; align-items: center; color: var(--text-secondary); font-size: 0.78rem; }
.scope-document span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.scope-empty { color: var(--text-secondary); font-size: 0.78rem; }
.library-button { border: 1px solid var(--border); border-radius: 4px; background: transparent; color: var(--text-secondary); font-size: 0.78rem; padding: 6px 9px; }
.library-button:hover:not(:disabled) { border-color: var(--accent); color: var(--text-primary); }
.disabled { opacity: 0.58; }

@media (max-width: 620px) {
  .scope-selector { grid-template-columns: 1fr auto; width: min(100% - 32px, 800px); }
  details { grid-column: 1 / -1; }
}
</style>
