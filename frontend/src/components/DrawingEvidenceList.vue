<template>
  <section class="evidence-list" data-testid="evidence-list">
    <header class="evidence-head">
      <div>
        <h3>已领用图纸附件</h3>
        <p class="evidence-desc">
          领图凭据无效也可先办理，记录为“待补领”；修订版发布后旧附件标记“已替代”，
          仍可按图纸编号、年代或覆盖部位查回。
        </p>
      </div>
      <button class="btn ghost" type="button" data-testid="refresh-evidence" @click="load">刷新</button>
    </header>
    <div class="evidence-filter">
      <input v-model="keyword" data-testid="evidence-filter" placeholder="按构件名称 / 图纸编号 / 覆盖部位检索" />
      <button class="btn" type="button" data-testid="query-evidence" @click="load">查询</button>
    </div>
    <p v-if="error" class="error-text" data-testid="evidence-error">{{ error }}</p>
    <table v-if="items.length" class="data-table">
      <thead>
        <tr>
          <th>证据号</th><th>构件</th><th>图纸编号</th><th>年代</th><th>覆盖部位</th>
          <th>领用附件</th><th>领用版次</th><th>实际生效版次</th><th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in items"
          :key="row.id"
          :data-testid="`evidence-row-${row.id}`"
          :data-superseded="row.已替代 ? 'true' : 'false'"
        >
          <td>{{ row.证据号 }}</td>
          <td>{{ row.构件名称 || '—' }}</td>
          <td>{{ row.图纸编号 }}</td>
          <td>{{ row.绘制年代 }}</td>
          <td>{{ row.覆盖部位 }}</td>
          <td>{{ row.领用附件 }}</td>
          <td>{{ row.领用版次 }}</td>
          <td>{{ row.实际生效版次 }}</td>
          <td>
            <span :class="row.已替代 ? 'warn-text' : 'ok-text'">
              {{ row.已替代 ? '已替代（附件可查）' : row.状态 }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-state">暂无已领用图纸附件</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { listDrawingEvidence } from '@/api/backend-service'
import type { EvidenceItem } from '@/data/types'

const items = ref<EvidenceItem[]>([])
const keyword = ref('')
const error = ref('')

async function load() {
  error.value = ''
  try {
    const filters: Record<string, string> = {}
    const needle = keyword.value.trim()
    if (needle) filters['图纸编号'] = needle
    items.value = (await listDrawingEvidence(filters)).items
    if (needle) {
      const lowered = needle.toLowerCase()
      items.value = items.value.filter((row) =>
        [row.构件名称, row.图纸编号, row.覆盖部位, row.绘制年代].some((value) =>
          String(value ?? '').toLowerCase().includes(lowered),
        ),
      )
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '已领用图纸附件读取失败'
  }
}

onMounted(load)
</script>

<style scoped>
.evidence-list { background:#fff; border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:14px; }
.evidence-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
.evidence-head h3 { margin:0 0 4px; font-size:15px; }
.evidence-desc { color: var(--muted); font-size:12px; margin:0 0 10px; line-height:1.6; }
.evidence-filter { display:flex; gap:8px; margin-bottom:10px; }
.evidence-filter input { flex:1; border:1px solid var(--border); border-radius:6px; padding:6px 8px; font-size:12px; }
.ok-text { color:#0f766e; }
.warn-text { color:#b45309; }
</style>
