// 全局常量：角色/演示账号/状态字典（演示凭据仅演示环境使用，生产环境严禁内置明文密码）

// 角色中文名（对应后端 Role 枚举，v3 八角色）
export const ROLE_LABELS = {
  admin: '系统管理员',
  purchase: '采购员',
  sales: '销售员',
  warehouse: '仓管员',
  production: '生产计划员',
  qc: '检验员',
  qa: '质量保证员',
  finance: '财务专员',
}

// 演示八账号（v3）：仅演示环境用，一键切换身份
export const DEMO_ACCOUNTS = [
  { username: 'admin', password: 'admin123', label: '系统管理员' },
  { username: 'purchase', password: 'p123', label: '采购员' },
  { username: 'sales', password: 's123', label: '销售员' },
  { username: 'warehouse', password: 'w123', label: '仓管员' },
  { username: 'production', password: 'p123456', label: '生产计划员' },
  { username: 'qc', password: 'qc123', label: '检验员' },
  { username: 'qa', password: 'q123', label: '质量保证员' },
  { username: 'finance', password: 'f123', label: '财务专员' },
]

// 批次检验状态 tag（待检黄/合格绿/不合格红/已关闭灰）
export const INSP_STATUS_META = {
  pending: { label: '待检', type: 'warning' },
  qualified: { label: '合格', type: 'success' },
  rejected: { label: '不合格', type: 'danger' },
  closed: { label: '已关闭', type: 'info' },
}

// 供应商资质审核状态 tag
export const CERT_STATUS_META = {
  draft: { label: '草稿', type: 'warning' },
  audited: { label: '已审核', type: 'success' },
  revoked: { label: '已撤销', type: 'info' },
}

// 资质临期预警：剩余天数 ≤ 该值红色高亮
export const CERT_EXPIRING_RED_DAYS = 7
