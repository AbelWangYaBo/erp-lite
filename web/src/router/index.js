import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import MainLayout from '@/layout/MainLayout.vue'
import { useAuthStore } from '@/stores/auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('@/views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: MainLayout,
    redirect: '/batches', // 批次追溯全员可用，作为各角色的安全落点
    children: [
      // actions：持有其中任一动作才可见/可入；不配 = 全员可见
      { path: 'materials', name: 'materials', component: () => import('@/views/MaterialList.vue'), meta: { title: '物料', actions: ['master.write'] } },
      { path: 'warehouses', name: 'warehouses', component: () => import('@/views/WarehouseList.vue'), meta: { title: '仓库', actions: ['master.write'] } },
      { path: 'partners', name: 'partners', component: () => import('@/views/PartnerList.vue'), meta: { title: '往来单位', actions: ['master.write'] } },
      { path: 'purchase-orders', name: 'purchaseOrders', component: () => import('@/views/PurchaseOrderList.vue'), meta: { title: '采购订单', actions: ['po.create', 'po.receive'] } },
      { path: 'sales-orders', name: 'salesOrders', component: () => import('@/views/SalesOrderList.vue'), meta: { title: '销售订单', actions: ['so.create', 'so.ship'] } },
      { path: 'inventory', name: 'inventory', component: () => import('@/views/Inventory.vue'), meta: { title: '库存查询' } },
      { path: 'batches', name: 'batches', component: () => import('@/views/Batches.vue'), meta: { title: '批次台账' } },
      { path: 'inspections', name: 'inspections', component: () => import('@/views/Inspections.vue'), meta: { title: '检验工作台', actions: ['inspect.judge'] } },
      { path: 'partner-certs', name: 'partnerCerts', component: () => import('@/views/PartnerCerts.vue'), meta: { title: '供应商资质', actions: ['partner_cert.write', 'partner_cert.audit'] } },
      { path: 'boms', name: 'boms', component: () => import('@/views/Bom.vue'), meta: { title: 'BOM', actions: ['bom.write'] } },
      { path: 'mrp', name: 'mrp', component: () => import('@/views/Mrp.vue'), meta: { title: 'MRP 运算', actions: ['mrp.run'] } },
      { path: 'manufacturing-orders', name: 'manufacturingOrders', component: () => import('@/views/ManufacturingOrderList.vue'), meta: { title: '生产工单', actions: ['mo.create', 'mo.release', 'mo.issue', 'mo.receive'] } },
      { path: 'users', name: 'users', component: () => import('@/views/Users.vue'), meta: { title: '用户管理', actions: ['users.manage'] } },
      { path: 'payables', name: 'payables', component: () => import('@/views/Payables.vue'), meta: { title: '应付账款', actions: ['settlement.create'] } },
      { path: 'receivables', name: 'receivables', component: () => import('@/views/Receivables.vue'), meta: { title: '应收账款', actions: ['settlement.create'] } },
      { path: 'settlements', name: 'settlements', component: () => import('@/views/Settlements.vue'), meta: { title: '收付款核销', actions: ['settlement.create'] } },
      { path: 'statement', name: 'statement', component: () => import('@/views/Statement.vue'), meta: { title: '对账单', actions: ['settlement.create'] } },
      { path: 'audit', name: 'audit', component: () => import('@/views/Audit.vue'), meta: { title: '审计追踪', actions: ['audit.read'] } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局守卫：无 token 跳登录；有 token 但无权限动作 → 回批次台账 + 提示
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (!localStorage.getItem('erp_token')) {
    return { path: '/login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined }
  }
  const auth = useAuthStore()
  // 旧会话只有 token 没有 me 数据时补拉一次
  if (!auth.actions.length) {
    try {
      await auth.fetchMe()
    } catch {
      /* 401 已由拦截器处理跳登录 */
    }
  }
  const need = to.meta.actions
  if (need?.length && !need.some((a) => auth.has(a))) {
    ElMessage.warning(`当前角色（${auth.roleLabel}）无权访问该页面`)
    return { path: '/batches' }
  }
  return true
})

export default router
