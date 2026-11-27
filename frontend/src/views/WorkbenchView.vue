<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <DrawingImportPanel v-if="meta.key === 'historical_drawing'" />
    <DrawingEvidenceList v-if="meta.key === 'component_registry'" />

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
        >
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label
          v-for="field in createFields"
          :key="field"
          class="create-field"
        >
          <span>{{ field }}</span>
          <input
            v-model="createValues[field]"
            :data-testid="`create-${field}`"
            :required="isCreateRequired(field)"
            :readonly="isDerivedField(field)"
          />
        </label>
        <DrawingEvidencePicker
          v-if="meta.key === 'component_registry'"
          :component-name="createValues['构件名称']"
          @evidence="applyEvidence"
          @token="applyToken"
        />
        <p v-if="meta.key === 'component_registry'" class="picker-desc">
          引用图纸为选填项：从核验清单选用后自动回填；不引用图纸也可登记构件。
          同编号不同年代或区域的图纸在清单中分列，按覆盖部位选取。
        </p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import { DRAWING_REFERENCE_FIELDS, OPTIONAL_CREATE_FIELDS } from '@/data/modules'
import type { EntryRow, EvidenceItem } from '@/data/types'
import DrawingEvidencePicker from '@/components/DrawingEvidencePicker.vue'
import DrawingImportPanel from '@/components/DrawingImportPanel.vue'
import DrawingEvidenceList from '@/components/DrawingEvidenceList.vue'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

// 构件登记的图纸引用字段由核验清单回填，表单里只读展示，避免与清单口径不一致。
const hiddenCreateFields = new Set(['图纸ID'])
const createFields = computed(() => meta.fields.filter((field) => !hiddenCreateFields.has(field)))
const optionalCreateFields = new Set(OPTIONAL_CREATE_FIELDS[meta.key] ?? [])

function isCreateRequired(field: string): boolean {
  return !optionalCreateFields.has(field) && !isDrawingReference(field)
}

function isDrawingReference(field: string): boolean {
  return meta.key === 'component_registry' && DRAWING_REFERENCE_FIELDS.includes(field)
}

// 版次、附件、证据号与图纸ID只能由核验清单回填，手填容易和清单口径不一致。
const derivedFields = new Set(['图纸版次', '图纸证据', '图纸证据ID', '图纸ID'])

function isDerivedField(field: string): boolean {
  return meta.key === 'component_registry' && derivedFields.has(field)
}

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((r) => r.status === meta.statuses[1]).length },
  { label: meta.metrics[2], value: rows.value.filter((r) => r.status === meta.statuses[meta.statuses.length - 1]).length },
])

const statusSummary = computed(() =>
  meta.statuses.map((s) => ({ status: s, count: rows.value.filter((r) => String(r.status) === s).length })),
)

function resetFilters() { filters.value = {}; void reload() }

async function exportRows() {
  errorMessage.value = ''
  try { await downloadEntries(meta.key, filters.value) }
  catch (e) { errorMessage.value = e instanceof Error ? e.message : '导出失败' }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((f) => [f, '']))
  errorMessage.value = ''
  showCreate.value = true
}

// 领图凭据一并带上；无有效凭据也可继续办理，由后端记为待补领。
function applyToken(value: string) {
  createValues.value['图纸凭据'] = value
}

// 选取核验清单中的图纸证据：回填引用字段，提交时后端再解析实际生效版次。
function applyEvidence(evidence: EvidenceItem) {
  createValues.value['图纸证据ID'] = String(evidence.id)
  createValues.value['图纸编号'] = String(evidence.图纸编号 ?? '')
  createValues.value['绘制年代'] = String(evidence.绘制年代 ?? '')
  createValues.value['覆盖部位'] = String(evidence.覆盖部位 ?? '')
  createValues.value['图纸ID'] = String(evidence.图纸ID ?? '')
}

async function submitCreate() {
  creating.value = true; errorMessage.value = ''
  try {
    const r = await createEntry(meta.key, createValues.value)
    if (!r.ok) throw new Error(r.message)
    showCreate.value = false; await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '登记失败' }
  finally { creating.value = false }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const r = await applyAction(meta.key, Number(row.id), action)
    if (!r.ok) throw new Error(r.message)
    await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '操作失败' }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items; total.value = payload.total
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '业务列表读取失败' }
}

onMounted(reload)
</script>

<style scoped>
.modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.35); display:flex; align-items:center; justify-content:center; z-index:100; }
.modal { background:#fff; border-radius:8px; padding:24px; width:480px; max-height:80vh; overflow-y:auto; }
.create-field { display:flex; flex-direction:column; gap:4px; margin-bottom:12px; }
.create-field span { font-size:13px; color:var(--text-secondary,#4a5568); }
.create-field input { padding:6px 8px; border:1px solid var(--border-color,#e2e8f0); border-radius:4px; font-size:13px; }
</style>