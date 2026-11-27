<template>
  <section class="evidence-picker" data-testid="drawing-evidence-picker">
    <header class="picker-head">
      <h4>图纸证据（来自构件登记核验清单）</h4>
      <button class="btn ghost" type="button" data-testid="refresh-checklist" @click="loadChecklist">
        刷新清单
      </button>
    </header>
    <p class="picker-desc">
      仅列出已确认导入并发布的图纸；同编号不同年代或区域的图纸分别可选。
      未取得有效凭据也可领图办理，登记提交时按当时实际生效的版次。
    </p>
    <p v-if="message" class="error-text" data-testid="picker-message">{{ message }}</p>

    <label class="picker-field">
      <span>按部位/年代筛选</span>
      <input v-model="filterText" data-testid="checklist-filter" placeholder="输入覆盖部位或年代" />
    </label>

    <table v-if="filtered.length" class="data-table">
      <thead>
        <tr><th>选择</th><th>图纸编号</th><th>绘制年代</th><th>覆盖部位</th><th>版次</th><th>附件</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in filtered" :key="item.id" :data-testid="`checklist-item-${item.id}`">
          <td>
            <button class="link" type="button" :data-testid="`pick-drawing-${item.drawing_id}`" @click="pick(item)">
              选用
            </button>
          </td>
          <td>{{ item.图纸编号 }}</td>
          <td>{{ item.绘制年代 }}</td>
          <td>{{ item.覆盖部位 }}</td>
          <td>{{ item.版次 }}</td>
          <td>{{ item.附件 }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-state">核验清单暂无可选图纸，请先到历史图纸确认批量导入。</p>

    <form class="picker-field claim-row" @submit.prevent="claimSelected">
      <span>导入回执凭据</span>
      <input v-model="token" data-testid="claim-token" placeholder="选填：DWG-... 凭据" />
      <button class="btn" type="submit" data-testid="claim-drawing" :disabled="!selectedIds.length">
        领图并登记
      </button>
    </form>

    <table v-if="evidence.length" class="data-table" data-testid="evidence-table">
      <thead>
        <tr><th>证据号</th><th>构件</th><th>图纸</th><th>附件</th><th>领用版次</th><th>状态</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in evidence" :key="row.id" :data-testid="`evidence-${row.id}`">
          <td>{{ row.证据号 }}</td>
          <td>{{ row.构件名称 || '—' }}</td>
          <td>{{ row.图纸编号 }} / {{ row.覆盖部位 }}</td>
          <td>{{ row.领用附件 }}</td>
          <td>{{ row.领用版次 }}</td>
          <td>
            <span :class="row.已替代 ? 'error-text' : 'ok-text'">
              {{ row.已替代 ? '已替代（附件可查）' : row.状态 }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  claimDrawing,
  listDrawingChecklist,
  listDrawingEvidence,
} from '@/api/backend-service'
import type { ChecklistItem, EvidenceItem } from '@/data/types'

const props = defineProps<{ componentName?: string }>()
const emit = defineEmits<{
  (event: 'evidence', payload: EvidenceItem): void
  (event: 'token', payload: string): void
}>()

const checklist = ref<ChecklistItem[]>([])
const evidence = ref<EvidenceItem[]>([])
const filterText = ref('')
const token = ref('')
const message = ref('')
const selected = ref<ChecklistItem[]>([])

const selectedIds = computed(() => selected.value.map((item) => item.drawing_id))

const filtered = computed(() => {
  const needle = filterText.value.trim().toLowerCase()
  if (!needle) return checklist.value
  return checklist.value.filter((item) =>
    [item.图纸编号, item.绘制年代, item.覆盖部位].some((v) => String(v).toLowerCase().includes(needle)),
  )
})

async function loadChecklist() {
  message.value = ''
  try {
    checklist.value = (await listDrawingChecklist()).items
    evidence.value = (await listDrawingEvidence()).items
  } catch (e) {
    message.value = e instanceof Error ? e.message : '核验清单读取失败'
  }
}

function pick(item: ChecklistItem) {
  const exists = selected.value.some((row) => row.drawing_id === item.drawing_id)
  if (exists) {
    message.value = '该图纸已在本次选取中'
    return
  }
  if (selected.value.length) {
    message.value = '一次只选取一份图纸办理登记'
    return
  }
  selected.value = [item]
  message.value = ''
}

async function claimSelected() {
  message.value = ''
  if (!selected.value.length) return
  try {
    emit('token', token.value)
    const result = await claimDrawing(selected.value[0].drawing_id, token.value, props.componentName ?? '')
    if (result.evidence) {
      evidence.value = [result.evidence, ...evidence.value]
      emit('evidence', result.evidence)
    }
    message.value = result.message
    selected.value = []
  } catch (e) {
    message.value = e instanceof Error ? e.message : '领图失败'
  }
}

void loadChecklist()
</script>

<style scoped>
.evidence-picker { border:1px solid var(--border); border-radius:8px; padding:12px; margin-top:12px; background:#fafbfc; }
.picker-head { display:flex; justify-content:space-between; align-items:center; }
.picker-head h4 { margin:0 0 4px; font-size:13px; }
.picker-desc { color: var(--muted); font-size:12px; margin: 4px 0 8px; }
.picker-field { display:flex; gap:8px; align-items:center; font-size:12px; margin-bottom:8px; }
.picker-field input { flex:1; border:1px solid var(--border); border-radius:6px; padding:6px 8px; font-size:12px; }
.claim-row { margin-top:10px; }
.ok-text { color:#0f766e; }
</style>
