<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  deleteDocument,
  getDocument,
  getDocuments,
  getDocumentUsage,
  restoreDocument,
  retryDocumentVersion,
  uploadDocument,
  uploadDocumentVersion
} from '../api/research.js'

const emit = defineEmits(['close', 'documents-updated'])

const documents = ref([])
const usage = ref(null)
const details = ref({})
const isLoading = ref(true)
const uploadPending = ref(false)
const actionPending = ref({})
const error = ref('')
const uploadInput = ref(null)
const versionInput = ref(null)
const versionTargetId = ref(null)
let pollTimer = null

const statusLabels = {
  queued: '等待处理',
  processing: '处理中',
  ready: '可用于检索',
  failed: '处理失败',
  archived: '历史版本',
  deleted: '已删除',
  succeeded: '已完成',
  cancelled: '已取消'
}

const stageLabels = {
  queued: '等待处理',
  converting: '转换文档',
  vision_enriching: '解析图片',
  chunking: '构建分块',
  indexing: '建立索引',
  complete: '处理完成'
}

const visionLabels = {
  not_configured: '图片增强未配置',
  pending: '图片待处理',
  succeeded: '图片增强完成',
  partial: '图片增强部分完成',
  failed: '图片增强失败'
}

const activeDocuments = computed(() => documents.value.filter((document) => document.deleted_at == null))
const hasProcessingDocument = computed(() => documents.value.some((document) => {
  const version = document.current_version
  return version?.status === 'queued' || version?.status === 'processing'
}))

const getErrorMessage = (requestError) => requestError.response?.data?.detail || requestError.message || '操作未完成，请稍后重试。'

const setActionPending = (key, value) => {
  actionPending.value = { ...actionPending.value, [key]: value }
}

const formatBytes = (value) => {
  if (!Number.isFinite(value)) return '--'
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB']
  let size = value / 1024
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(size >= 100 || index === 0 ? 0 : 1)} ${units[index]}`
}

const formatTime = (value) => {
  if (!value) return '--'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

const daysUntilPurge = (deletedAt) => {
  if (!deletedAt) return null
  const end = new Date(deletedAt).valueOf() + 30 * 24 * 60 * 60 * 1000
  return Math.max(0, Math.ceil((end - Date.now()) / (24 * 60 * 60 * 1000)))
}

const updateDocuments = (items) => {
  documents.value = items
  emit('documents-updated', items)
}

const loadDocuments = async ({ quiet = false } = {}) => {
  if (!quiet) isLoading.value = true
  try {
    const [response, usageResponse] = await Promise.all([
      getDocuments({ includeDeleted: true }),
      getDocumentUsage()
    ])
    updateDocuments(response.items || [])
    usage.value = usageResponse
  } catch (requestError) {
    if (!quiet) error.value = getErrorMessage(requestError)
  } finally {
    if (!quiet) isLoading.value = false
  }
}

const refreshDetail = async (documentId) => {
  const detail = await getDocument(documentId)
  details.value = { ...details.value, [documentId]: detail }
  return detail
}

const toggleDetails = async (documentId) => {
  error.value = ''
  if (details.value[documentId]) {
    const next = { ...details.value }
    delete next[documentId]
    details.value = next
    return
  }
  setActionPending(`detail:${documentId}`, true)
  try {
    await refreshDetail(documentId)
  } catch (requestError) {
    error.value = getErrorMessage(requestError)
  } finally {
    setActionPending(`detail:${documentId}`, false)
  }
}

const refreshAfterMutation = async (documentId) => {
  await loadDocuments({ quiet: true })
  if (details.value[documentId]) await refreshDetail(documentId)
}

const chooseUpload = () => uploadInput.value?.click()

const handleUpload = async (event) => {
  const [file] = event.target.files || []
  event.target.value = ''
  if (!file) return
  error.value = ''
  uploadPending.value = true
  try {
    const detail = await uploadDocument(file)
    details.value = { ...details.value, [detail.document_id]: detail }
    await loadDocuments({ quiet: true })
  } catch (requestError) {
    error.value = getErrorMessage(requestError)
  } finally {
    uploadPending.value = false
  }
}

const chooseNewVersion = (documentId) => {
  versionTargetId.value = documentId
  versionInput.value?.click()
}

const handleNewVersion = async (event) => {
  const [file] = event.target.files || []
  event.target.value = ''
  const documentId = versionTargetId.value
  versionTargetId.value = null
  if (!file || !documentId) return
  error.value = ''
  setActionPending(`version:${documentId}`, true)
  try {
    const detail = await uploadDocumentVersion(documentId, file)
    details.value = { ...details.value, [documentId]: detail }
    await loadDocuments({ quiet: true })
  } catch (requestError) {
    error.value = getErrorMessage(requestError)
  } finally {
    setActionPending(`version:${documentId}`, false)
  }
}

const retryVersion = async (documentId, versionId) => {
  error.value = ''
  const key = `retry:${versionId}`
  setActionPending(key, true)
  try {
    const detail = await retryDocumentVersion(documentId, versionId)
    details.value = { ...details.value, [documentId]: detail }
    await loadDocuments({ quiet: true })
  } catch (requestError) {
    error.value = getErrorMessage(requestError)
  } finally {
    setActionPending(key, false)
  }
}

const removeDocument = async (document) => {
  if (!window.confirm(`删除“${document.title}”后，30 天内可在此恢复。确定删除吗？`)) return
  error.value = ''
  const key = `delete:${document.document_id}`
  setActionPending(key, true)
  try {
    const detail = await deleteDocument(document.document_id)
    details.value = { ...details.value, [document.document_id]: detail }
    await loadDocuments({ quiet: true })
  } catch (requestError) {
    error.value = getErrorMessage(requestError)
  } finally {
    setActionPending(key, false)
  }
}

const recoverDocument = async (document) => {
  error.value = ''
  const key = `restore:${document.document_id}`
  setActionPending(key, true)
  try {
    const detail = await restoreDocument(document.document_id)
    details.value = { ...details.value, [document.document_id]: detail }
    await loadDocuments({ quiet: true })
  } catch (requestError) {
    error.value = getErrorMessage(requestError)
  } finally {
    setActionPending(key, false)
  }
}

const pollDocuments = async () => {
  if (!hasProcessingDocument.value || document.hidden) return
  await loadDocuments({ quiet: true })
  for (const document of documents.value) {
    if (details.value[document.document_id]) await refreshDetail(document.document_id)
  }
}

onMounted(async () => {
  await loadDocuments()
  pollTimer = window.setInterval(pollDocuments, 4000)
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <div class="document-layer" role="presentation" @click.self="emit('close')">
    <aside class="document-panel" role="dialog" aria-modal="true" aria-labelledby="document-library-title">
      <header class="panel-header">
        <div>
          <p class="panel-kicker">PRIVATE LIBRARY</p>
          <h2 id="document-library-title">论文资料库</h2>
        </div>
        <button class="icon-button" type="button" aria-label="关闭论文资料库" title="关闭" @click="emit('close')">&times;</button>
      </header>

      <section v-if="usage" class="usage-section" aria-label="存储用量">
        <div class="usage-head">
          <span>存储用量</span>
          <strong>{{ formatBytes(usage.used_bytes) }} / {{ formatBytes(usage.quota_bytes) }}</strong>
        </div>
        <div class="usage-track" aria-hidden="true">
          <span :style="{ width: `${Math.min(100, (usage.used_bytes / usage.quota_bytes) * 100)}%` }"></span>
        </div>
        <p>剩余 {{ formatBytes(usage.remaining_bytes) }}。单篇论文最大 50 MB。</p>
      </section>

      <section class="upload-section">
        <div>
          <h3>上传论文</h3>
          <p>PDF 或 Markdown 文件将独立入库，不会按文件名合并。</p>
        </div>
        <button class="upload-button" type="button" :disabled="uploadPending" @click="chooseUpload">
          {{ uploadPending ? '正在上传' : '上传论文' }}
        </button>
        <input ref="uploadInput" class="file-input" type="file" accept=".pdf,.md,.markdown,application/pdf,text/markdown" @change="handleUpload">
        <input ref="versionInput" class="file-input" type="file" accept=".pdf,.md,.markdown,application/pdf,text/markdown" @change="handleNewVersion">
      </section>

      <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

      <div v-if="isLoading" class="panel-state">正在读取论文资料库...</div>
      <div v-else-if="!documents.length" class="panel-state">还没有上传论文。</div>
      <div v-else class="document-list">
        <article v-for="document in documents" :key="document.document_id" class="document-row" :class="{ deleted: document.deleted_at }">
          <div class="document-summary">
            <button class="document-title" type="button" :aria-expanded="Boolean(details[document.document_id])" @click="toggleDetails(document.document_id)">
              {{ document.title }}
            </button>
            <p class="document-meta">
              <span :class="['status-dot', document.current_version?.status || 'unknown']"></span>
              {{ statusLabels[document.current_version?.status] || '尚未处理' }}
              <template v-if="document.current_version"> · v{{ document.current_version.version_number }} · {{ formatBytes(document.current_version.source_size) }}</template>
            </p>
            <p v-if="document.deleted_at" class="restore-note">已删除，{{ daysUntilPurge(document.deleted_at) }} 天内可恢复。</p>
          </div>
          <div class="document-actions">
            <button class="row-button" type="button" :disabled="actionPending[`detail:${document.document_id}`]" @click="toggleDetails(document.document_id)">
              {{ details[document.document_id] ? '收起' : '详情' }}
            </button>
            <button v-if="document.deleted_at" class="row-button primary" type="button" :disabled="actionPending[`restore:${document.document_id}`]" @click="recoverDocument(document)">
              {{ actionPending[`restore:${document.document_id}`] ? '恢复中' : '恢复' }}
            </button>
            <template v-else>
              <button class="row-button" type="button" :disabled="actionPending[`version:${document.document_id}`]" @click="chooseNewVersion(document.document_id)">
                {{ actionPending[`version:${document.document_id}`] ? '上传中' : '上传新版本' }}
              </button>
              <button class="row-button danger" type="button" :disabled="actionPending[`delete:${document.document_id}`]" @click="removeDocument(document)">
                {{ actionPending[`delete:${document.document_id}`] ? '删除中' : '删除' }}
              </button>
            </template>
          </div>

          <div v-if="details[document.document_id]" class="document-details">
            <div v-if="details[document.document_id].current_version" class="current-state">
              <span>{{ visionLabels[details[document.document_id].current_version.vision_status] || '图片状态未知' }}</span>
              <span v-if="details[document.document_id].current_version.error_summary" class="version-error">{{ details[document.document_id].current_version.error_summary }}</span>
            </div>

            <section class="detail-section">
              <h4>版本</h4>
              <div v-for="version in details[document.document_id].versions" :key="version.version_id" class="version-row">
                <div>
                  <strong>v{{ version.version_number }}</strong>
                  <span v-if="version.is_current" class="current-tag">当前</span>
                  <span>{{ version.source_filename }}</span>
                  <small>{{ statusLabels[version.status] || version.status }} · {{ formatTime(version.created_at) }}</small>
                  <small v-if="version.error_summary" class="version-error">{{ version.error_summary }}</small>
                </div>
                <button
                  v-if="version.status === 'failed' && !document.deleted_at"
                  class="row-button"
                  type="button"
                  :disabled="actionPending[`retry:${version.version_id}`]"
                  @click="retryVersion(document.document_id, version.version_id)"
                >
                  {{ actionPending[`retry:${version.version_id}`] ? '重试中' : '重试' }}
                </button>
              </div>
            </section>

            <section v-if="details[document.document_id].jobs.length" class="detail-section">
              <h4>处理记录</h4>
              <div v-for="job in details[document.document_id].jobs" :key="job.job_id" class="job-row">
                <span>{{ stageLabels[job.stage] || job.stage }}</span>
                <span>{{ statusLabels[job.status] || job.status }}</span>
                <span>第 {{ job.attempt }} 次</span>
                <span v-if="job.error_summary" class="version-error">{{ job.error_summary }}</span>
              </div>
            </section>
          </div>
        </article>
      </div>

      <footer class="panel-footer">{{ activeDocuments.length }} 篇有效论文 · 删除后保留 30 天</footer>
    </aside>
  </div>
</template>

<style scoped>
.document-layer { position: fixed; inset: 0; z-index: 120; display: flex; justify-content: flex-end; background: rgba(4, 10, 7, 0.6); }
.document-panel { display: flex; width: min(600px, 100%); height: 100%; flex-direction: column; overflow: hidden; border-left: 1px solid var(--border); background: var(--ink-900); box-shadow: -16px 0 44px rgba(0, 0, 0, 0.45); animation: rise-in 260ms ease both; }
.panel-header { display: flex; align-items: flex-start; justify-content: space-between; padding: 20px 22px 16px; border-bottom: 1px solid var(--border); }
.panel-kicker { color: var(--accent-hover); font-family: var(--font-mono); font-size: 0.67rem; font-weight: 700; letter-spacing: 0.25em; }
.panel-header h2 { margin-top: 3px; font-size: 1.2rem; letter-spacing: 0.06em; }
.icon-button { display: grid; width: 32px; height: 32px; place-items: center; border: 1px solid var(--border); border-radius: var(--radius-sm); background: transparent; color: var(--text-secondary); font-size: 1.35rem; line-height: 1; }
.icon-button:hover { border-color: var(--accent); color: var(--paper); }
.usage-section, .upload-section { padding: 14px 22px; border-bottom: 1px solid var(--border); }
.usage-head { display: flex; justify-content: space-between; gap: 12px; color: var(--text-secondary); font-size: 0.78rem; }
.usage-head strong { color: var(--text-primary); font-weight: 600; }
.usage-track { height: 5px; margin: 8px 0 5px; overflow: hidden; border-radius: 3px; background: var(--ink-950); }
.usage-track span { display: block; height: 100%; border-radius: inherit; background: var(--accent); }
.usage-section p, .upload-section p { color: var(--text-secondary); font-size: 0.74rem; }
.upload-section { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.upload-section h3 { font-family: inherit; font-size: 0.86rem; }
.upload-button, .row-button { border: 1px solid var(--border); border-radius: var(--radius-sm); background: transparent; color: var(--text-secondary); font-size: 0.75rem; white-space: nowrap; }
.upload-button { min-height: 34px; padding: 7px 12px; color: var(--text-primary); }
.upload-button:hover:not(:disabled), .row-button:hover:not(:disabled) { border-color: var(--border-strong); background: var(--ink-800); color: var(--paper); }
.file-input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.panel-error { margin: 12px 22px 0; color: var(--error-text); font-size: 0.79rem; line-height: 1.45; }
.panel-state { padding: 48px 22px; color: var(--text-faint); text-align: center; font-size: 0.84rem; }
.document-list { flex: 1; overflow-y: auto; }
.document-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; padding: 15px 22px; border-bottom: 1px solid var(--border); }
.document-row.deleted { opacity: 0.72; }
.document-summary { min-width: 0; }
.document-title { display: block; max-width: 100%; overflow: hidden; border: 0; background: transparent; color: var(--text-primary); font-size: 0.9rem; font-weight: 600; text-align: left; text-overflow: ellipsis; white-space: nowrap; }
.document-title:hover { color: var(--accent-hover); }
.document-meta, .restore-note { display: flex; align-items: center; margin-top: 4px; color: var(--text-secondary); font-size: 0.73rem; line-height: 1.4; }
.restore-note { color: var(--status-running); }
.status-dot { width: 7px; height: 7px; margin-right: 6px; border-radius: 50%; background: var(--text-faint); flex: 0 0 auto; }
.status-dot.ready, .status-dot.succeeded { background: var(--status-success); }
.status-dot.queued, .status-dot.processing { background: var(--status-running); animation: pulse-dot 1.4s ease-in-out infinite; }
.status-dot.failed { background: var(--status-failed); }
.document-actions { display: flex; flex-wrap: wrap; align-content: flex-start; justify-content: flex-end; gap: 6px; max-width: 178px; }
.row-button { min-height: 28px; padding: 4px 8px; }
.row-button.primary { border-color: rgba(192, 73, 47, 0.6); color: var(--accent-hover); }
.row-button.danger:hover:not(:disabled) { border-color: rgba(209, 106, 90, 0.6); color: var(--error-text); }
.row-button:disabled, .upload-button:disabled { cursor: wait; opacity: 0.58; }
.document-details { grid-column: 1 / -1; padding-top: 2px; }
.current-state { display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 11px; border-left: 2px solid var(--accent); background: var(--ink-800); color: var(--text-secondary); font-size: 0.74rem; }
.detail-section { margin-top: 14px; }
.detail-section h4 { margin-bottom: 6px; color: var(--text-faint); font-family: inherit; font-size: 0.73rem; font-weight: 700; letter-spacing: 0.12em; }
.version-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 8px 0; border-top: 1px solid var(--border); color: var(--text-secondary); font-size: 0.75rem; }
.version-row > div { display: grid; min-width: 0; gap: 2px; }
.version-row strong { color: var(--text-primary); }
.version-row span:not(.current-tag) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.version-row small { color: var(--text-faint); font-size: 0.7rem; }
.current-tag { display: inline-flex; width: fit-content; padding: 1px 5px; border: 1px solid rgba(192, 73, 47, 0.6); border-radius: 3px; color: var(--accent-hover); font-size: 0.66rem; }
.version-error { color: var(--error-text) !important; white-space: normal !important; }
.job-row { display: flex; flex-wrap: wrap; gap: 7px 12px; padding: 7px 0; border-top: 1px solid var(--border); color: var(--text-secondary); font-size: 0.72rem; }
.panel-footer { padding: 11px 22px; border-top: 1px solid var(--border); color: var(--text-faint); font-size: 0.72rem; }

@media (max-width: 620px) {
  .document-panel { width: 100%; border-left: 0; }
  .panel-header, .usage-section, .upload-section, .document-row, .panel-footer { padding-left: 16px; padding-right: 16px; }
  .upload-section { align-items: flex-end; }
  .document-row { grid-template-columns: 1fr; }
  .document-actions { justify-content: flex-start; max-width: none; }
}
</style>
