/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 预检/导入回执的逐行结果。失败行保留原行号与错误。 */
export type ImportLine = {
  line_no: number
  行号: number
  ok: boolean
  passed: boolean
  errors: string[]
  错误: string[]
  outcome?: string
  结果?: string
  图纸编号: string
  绘制年代: string
  覆盖部位: string
  附件: string
  图纸身份: string
  图纸ID: number | null
  版次: string
  凭据: string
  [field: string]: unknown
}

export type ImportReceipt = {
  ok?: boolean
  message?: string
  receipt_id?: number
  回执号?: string
  receipt_no?: string
  precheck_id?: string | null
  批次号?: string
  status?: string
  created_at?: string
  total?: number
  accepted?: number
  failed?: number
  published?: number
  summary?: { total: number; accepted?: number; passed?: number; failed: number; published?: number }
  lines?: ImportLine[]
  items?: ImportLine[]
  errors?: ImportLine[]
  error_lines?: ImportLine[]
  [field: string]: unknown
}

/** 构件登记核验清单条目，只含已发布并当前有效的图纸。 */
export type ChecklistItem = {
  id: number
  drawing_id: number
  图纸ID: number
  图纸编号: string
  绘制年代: string
  覆盖部位: string
  图纸身份: string
  版次: string
  版次序号: number
  附件: string
  有效: boolean
  状态: string
  导入批次?: string
  [field: string]: unknown
}

/** 已领用附件记录，可能带“已替代”标记但附件始终可查。 */
export type EvidenceItem = {
  id: number
  证据号: string
  凭据: string
  凭据有效: boolean
  领用人: string
  构件名称: string
  领用图纸ID: number
  图纸ID: number
  图纸编号: string
  绘制年代: string
  覆盖部位: string
  图纸身份: string
  领用版次: string
  实际生效版次: string
  领用附件: string
  状态: string
  已替代: boolean
  有效附件: boolean
  [field: string]: unknown
}
