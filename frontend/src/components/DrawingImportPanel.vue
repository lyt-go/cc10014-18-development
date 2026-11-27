<template>
  <section class="import-panel" data-testid="drawing-import-panel">
    <header class="import-head">
      <div>
        <h3>批量导入图纸清单 <small v-if="batchInfo" class="batch-info">{{ batchInfo }}</small></h3>
        <p class="import-desc">
          清单先预检、再确认导入。预检不发布图纸；确认导入后，受理行生成导入回执，
          发布结果同步到构件登记核验清单。同编号图纸按“编号 + 绘制年代 + 覆盖部位”区分。
        </p>
      </div>
      <button class="btn" type="button" data-testid="download-template" @click="downloadTemplate">
        下载导入模板
      </button>
    </header>

    <textarea
      v-model="content"
      class="import-input"
      data-testid="import-content"
      rows="6"
      placeholder="粘贴 CSV/TSV 清单，首行可为表头：图纸编号,绘制年代,覆盖部位,附件,绘制内容,版次"
    />
    <label class="file-row">
      <span>或选择清单文件</span>
      <input type="file" accept=".csv,.tsv,.txt" data-testid="import-file" @change="loadFile" />
    </label>
    <div class="import-actions">
      <button class="btn" type="button" data-testid="precheck-import" :disabled="busy" @click="runPrecheck">
        {{ busy ? '处理中…' : '预检清单' }}
      </button>
      <button
        class="btn primary"
        type="button"
        data-testid="commit-import"
        :disabled="busy || !lines.length"
        @click="runCommit"
      >
        确认导入
      </button>
      <span v-if="message" :class="messageIsError ? 'error-text' : 'ok-text'" data-testid="import-message">
        {{ message }}
      </span>
    </div>

    <div v-if="summary" class="import-summary" data-testid="import-summary">
      <span>共 {{ summary.total }} 行</span>
      <span>受理/通过 {{ summary.accepted ?? summary.passed }} 行</span>
      <span :class="summary.failed ? 'error-text' : ''">错误 {{ summary.failed }} 行</span>
      <span v-if="receiptNo">回执号 {{ receiptNo }}</span>
    </div>

    <table v-if="lines.length" class="data-table import-table" data-testid="import-lines">
      <thead>
        <tr>
          <th>原行号</th>
          <th>图纸编号</th>
          <th>绘制年代</th>
          <th>覆盖部位</th>
          <th>附件</th>
          <th>版次</th>
          <th>处理结果</th>
          <th>凭据</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="line in lines"
          :key="line.line_no"
          :data-testid="`import-line-${line.line_no}`"
          :data-ok="line.ok ? 'true' : 'false'"
        >
          <td>{{ line.line_no }}</td>
          <td>{{ line.图纸编号 || '—' }}</td>
          <td>{{ line.绘制年代 || '—' }}</td>
          <td>{{ line.覆盖部位 || '—' }}</td>
          <td>{{ line.附件 || '—' }}</td>
          <td>{{ line.版次 || '—' }}</td>
          <td>
            <span v-if="line.ok" class="ok-text">{{ line.outcome || '通过' }}</span>
            <span v-else class="error-text" :data-testid="`import-error-${line.line_no}`">
              {{ line.errors.join('；') }}
            </span>
          </td>
          <td>
            <span v-if="line.token" class="token">{{ line.token }}</span>
            <span v-else>—</span>
          </td>
        </tr>
      </tbody>
    </table>

    <section v-if="checklist.length" class="import-check" data-testid="post-publish-checklist">
      <h4>构件登记核验清单（发布后同步）</h4>
      <table class="data-table">
        <thead>
          <tr><th>图纸编号</th><th>绘制年代</th><th>覆盖部位</th><th>版次</th><th>附件</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in checklist" :key="item.id">
            <td>{{ item.图纸编号 }}</td>
            <td>{{ item.绘制年代 }}</td>
            <td>{{ item.覆盖部位 }}</td>
            <td>{{ item.版次 }}</td>
            <td>{{ item.附件 }}</td>
            <td>{{ item.状态 }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import {
  commitDrawingImport,
  downloadImportTemplate,
  listDrawingChecklist,
  loadImportBatches,
  precheckDrawingImport,
} from '@/api/backend-service'
import type { ChecklistItem, ImportLine, ImportReceipt } from '@/data/types'

const content = ref('')
const lines = ref<ImportLine[]>([])
const summary = ref<ImportReceipt['summary'] | null>(null)
const receiptNo = ref('')
const message = ref('')
const messageIsError = ref(false)
const busy = ref(false)
const checklist = ref<ChecklistItem[]>([])
const batchInfo = ref('')
let lastPrecheck: ImportReceipt | null = null

async function refreshBatches() {
  try {
    const info = await loadImportBatches()
    batchInfo.value = `已预检 ${info.prechecks} 次、回执 ${info.receipts} 份、累计错误行 ${info.error_lines}`
  } catch {
    batchInfo.value = ''
  }
}

async function refreshChecklist() {
  try {
    checklist.value = (await listDrawingChecklist()).items
  } catch {
    checklist.value = []
  }
}

async function runPrecheck() {
  busy.value = true
  message.value = ''
  try {
    const result = await precheckDrawingImport(content.value)
    lastPrecheck = result
    lines.value = result.lines ?? result.items ?? []
    summary.value = result.summary ?? null
    receiptNo.value = ''
    message.value = result.message ?? '预检完成'
    messageIsError.value = !!(result.summary?.failed)
  } catch (e) {
    message.value = e instanceof Error ? e.message : '预检失败'
    messageIsError.value = true
  } finally {
    busy.value = false
  }
}

async function runCommit() {
  busy.value = true
  message.value = ''
  try {
    const result = await commitDrawingImport(lastPrecheck ?? {}, content.value)
    lines.value = result.lines ?? result.items ?? lines.value
    summary.value = result.summary ?? null
    receiptNo.value = result.receipt_no ?? result.回执号 ?? ''
    message.value = result.message ?? '导入完成'
    messageIsError.value = !!(result.summary?.failed)
    await Promise.all([refreshChecklist(), refreshBatches()])
  } catch (e) {
    message.value = e instanceof Error ? e.message : '导入失败'
    messageIsError.value = true
  } finally {
    busy.value = false
  }
}

async function loadFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  content.value = await file.text()
  lines.value = []
  summary.value = null
  receiptNo.value = ''
  message.value = `已载入 ${file.name}，请先预检`
  messageIsError.value = false
}

async function downloadTemplate() {
  try { await downloadImportTemplate() }
  catch (e) { message.value = e instanceof Error ? e.message : '模板下载失败'; messageIsError.value = true }
}

void refreshChecklist()
void refreshBatches()
</script>

<style scoped>
.import-panel { background:#fff; border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:14px; }
.import-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
.import-desc { color: var(--muted); font-size: 12px; margin: 4px 0 10px; line-height: 1.6; }
.file-row { display:flex; align-items:center; gap:8px; font-size:12px; color:var(--muted); margin-top:6px; }
.import-input { width:100%; box-sizing:border-box; border:1px solid var(--border); border-radius:6px; padding:8px; font-family:monospace; font-size:12px; }
.import-actions { display:flex; align-items:center; gap:10px; margin:10px 0; }
.import-summary { display:flex; gap:14px; font-size:12px; color:var(--muted); margin-bottom:8px; }
.import-table { margin-bottom:12px; }
.token { font-family:monospace; font-size:11px; color:#0f766e; }
.ok-text { color:#0f766e; }
.import-check h4 { margin: 8px 0; font-size:13px; }
.batch-info { font-size:12px; font-weight:normal; color:var(--muted); margin-left:8px; }
</style>
