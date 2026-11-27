import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  ChecklistItem,
  EntryRow,
  EvidenceItem,
  ImportReceipt,
  OverviewResult,
  PageResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type DependentStatus = {
  module: string
  entry_id: number
  entry: Record<string, unknown>
  upstream: { module: string; id: number; name: string; status: string }[]
  downstream: { module: string; id: number; name: string; status: string }[]
}

export async function fetchDependentStatus(
  moduleKey: string,
  entryId: number,
): Promise<DependentStatus> {
  const response = await request(`/api/cross-module/dependent-status/${moduleKey}/${entryId}`)
  if (!response.ok) throw new Error('跨模块状态查询失败')
  return await response.json()
}

export async function fetchDependencyGraph(): Promise<{ nodes: unknown[]; edges: unknown[] }> {
  const response = await request('/api/cross-module/dependency-graph')
  if (!response.ok) throw new Error('依赖图加载失败')
  return await response.json()
}

// ── 历史图纸批量导入 / 构件登记核验清单 ──

export async function precheckDrawingImport(
  content: string,
  operator = '',
): Promise<ImportReceipt> {
  const response = await request('/api/historical_drawing/import/precheck', {
    method: 'POST',
    body: JSON.stringify({ content, operator }),
  })
  return await response.json()
}

export async function commitDrawingImport(
  receipt: ImportReceipt,
  content = '',
): Promise<ImportReceipt> {
  // 优先回传原始清单文本，后端按同一份文本重新校验；没有文本时按预检行回传。
  const rows = (receipt.lines ?? receipt.items ?? []).map((line) => ({
    line_no: line.line_no ?? line.行号,
    ...(line.values ?? {}),
  }))
  const response = await request('/api/historical_drawing/import/commit', {
    method: 'POST',
    body: JSON.stringify({
      precheck_id: receipt.precheck_id,
      content: content || undefined,
      rows: content ? undefined : rows,
    }),
  })
  return await response.json()
}

export async function listDrawingReceipts(): Promise<{ receipts: ImportReceipt[] }> {
  const response = await request('/api/historical_drawing/import/receipts')
  if (!response.ok) throw new Error('导入回执读取失败')
  return await response.json()
}

export async function listDrawingChecklist(
  filters: Record<string, string> = {},
): Promise<{ items: ChecklistItem[]; total: number }> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/component_registry/checklist?${query}`)
  if (!response.ok) throw new Error('图纸核验清单读取失败')
  return await response.json()
}

export async function claimDrawing(
  drawingId: number,
  token: string,
  componentName = '',
): Promise<{ ok: boolean; message: string; evidence: EvidenceItem | null }> {
  const response = await request('/api/component_registry/claim', {
    method: 'POST',
    body: JSON.stringify({ drawing_id: drawingId, 凭据: token, 构件名称: componentName }),
  })
  return await response.json()
}

export async function listDrawingEvidence(
  filters: Record<string, string> = {},
): Promise<{ items: EvidenceItem[]; total: number }> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/component_registry/evidence?${query}`)
  if (!response.ok) throw new Error('已领用图纸附件读取失败')
  return await response.json()
}

export async function downloadImportTemplate(): Promise<void> {
  const response = await request('/api/historical_drawing/import/template')
  if (!response.ok) throw new Error('导入模板下载失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'historical_drawing_import_template.csv'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}


export type ImportBatchInfo = {
  ok: boolean
  prechecks: number
  receipts: number
  latest_precheck: string
  latest_receipt: string
  error_lines: number
}

export async function loadImportBatches(): Promise<ImportBatchInfo> {
  const response = await request('/api/historical_drawing/import/batches')
  if (!response.ok) throw new Error('导入批次统计读取失败')
  return await response.json()
}
