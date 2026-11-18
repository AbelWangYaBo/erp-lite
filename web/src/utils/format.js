// 展示格式化：金额保留 2 位，后端给 4 位则原样显示 4 位
export function fmtMoney(v) {
  if (v === null || v === undefined || v === '') return ''
  const s = String(v)
  const i = s.indexOf('.')
  if (i === -1) return Number(s).toFixed(2)
  const decimals = s.length - i - 1
  return decimals > 2 ? s : Number(s).toFixed(2)
}

// 时间：UTC ISO → 本地展示（截秒，去 T）
export function fmtTime(v) {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return String(v)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

// 日期：YYYY-MM-DD
export function today() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// 单据/库存常用字典标签
export const CATEGORY_LABELS = { raw: '原材料', semi: '半成品', finished: '成品' }
export const PARTNER_TYPE_LABELS = { supplier: '供应商', customer: '客户' }
export const MOVE_TYPE_LABELS = {
  purchase_in: '采购入库',
  sale_out: '销售出库',
  mo_issue: '生产领料',
  mo_issue_return: '领料退回',
  mo_receipt: '完工入库',
  purchase_return: '采购退货出库',
  sale_return: '销售退货入库',
  adjust: '库存调整',
}
// 单据类型中文名（DocLink 兜底文案 / 追溯与审计展示）
export const DOC_TYPE_LABELS = {
  purchase_order: '采购订单',
  sales_order: '销售订单',
  manufacturing_order: '生产工单',
  batch: '批次',
  stock_count: '盘点单',
  sales_return: '销售退货单',
  purchase_return: '采购退货单',
  payable: '应付',
  receivable: '应收',
  settlement: '核销单',
  partner_cert: '供应商资质',
  material: '物料',
}
export const DOC_TYPE_OPTIONS = [
  'material', 'warehouse', 'partner', 'purchase_order', 'sales_order',
  'bom', 'manufacturing_order', 'payable', 'receivable', 'settlement',
  'batch', 'partner_cert', 'stock_count', 'sales_return', 'purchase_return',
  'user', 'inspection_record',
]
