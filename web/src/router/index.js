import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layout/MainLayout.vue'

const routes = [
  { path: '/login', name: 'login', component: () => import('@/views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: MainLayout,
    redirect: '/materials',
    children: [
      { path: 'materials', name: 'materials', component: () => import('@/views/MaterialList.vue'), meta: { title: '物料' } },
      { path: 'warehouses', name: 'warehouses', component: () => import('@/views/WarehouseList.vue'), meta: { title: '仓库' } },
      { path: 'partners', name: 'partners', component: () => import('@/views/PartnerList.vue'), meta: { title: '往来单位' } },
      { path: 'purchase-orders', name: 'purchaseOrders', component: () => import('@/views/PurchaseOrderList.vue'), meta: { title: '采购订单' } },
      { path: 'sales-orders', name: 'salesOrders', component: () => import('@/views/SalesOrderList.vue'), meta: { title: '销售订单' } },
      { path: 'inventory', name: 'inventory', component: () => import('@/views/Inventory.vue'), meta: { title: '库存' } },
      { path: 'boms', name: 'boms', component: () => import('@/views/Bom.vue'), meta: { title: 'BOM' } },
      { path: 'mrp', name: 'mrp', component: () => import('@/views/Mrp.vue'), meta: { title: 'MRP 运算' } },
      { path: 'manufacturing-orders', name: 'manufacturingOrders', component: () => import('@/views/ManufacturingOrderList.vue'), meta: { title: '生产工单' } },
      { path: 'payables', name: 'payables', component: () => import('@/views/Payables.vue'), meta: { title: '应付账款' } },
      { path: 'receivables', name: 'receivables', component: () => import('@/views/Receivables.vue'), meta: { title: '应收账款' } },
      { path: 'settlements', name: 'settlements', component: () => import('@/views/Settlements.vue'), meta: { title: '收付款核销' } },
      { path: 'statement', name: 'statement', component: () => import('@/views/Statement.vue'), meta: { title: '对账单' } },
      { path: 'audit', name: 'audit', component: () => import('@/views/Audit.vue'), meta: { title: '审计追踪' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局守卫：无 token 一律跳登录页
router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!localStorage.getItem('erp_token')) {
    return { path: '/login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined }
  }
  return true
})

export default router
