<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { DEMO_ACCOUNTS } from '@/constants'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 菜单分组：基础数据 / 采购 / 销售 / 库存 / 生产 / 质量 / 财务 / 审计 / 系统
// actions：持有其中任一动作才显示该项（PRD-v2 §2.5 矩阵）；不配 = 全员可见
const menus = [
  { group: '工作台', items: [{ path: '/dashboard', title: '工作台' }] },
  {
    group: '基础数据',
    items: [
      { path: '/materials', title: '物料', actions: ['master.write'] },
      { path: '/warehouses', title: '仓库', actions: ['master.write'] },
      { path: '/partners', title: '往来单位', actions: ['master.write'] },
    ],
  },
  {
    group: '采购管理',
    items: [
      { path: '/purchase-orders', title: '采购订单', actions: ['po.create', 'po.receive'] },
      { path: '/purchase-returns', title: '采购退货' },
    ],
  },
  {
    group: '销售管理',
    items: [
      { path: '/sales-orders', title: '销售订单', actions: ['so.create', 'so.ship'] },
      { path: '/sales-returns', title: '销售退货', actions: ['ar.read'] },
    ],
  },
  {
    group: '库存管理',
    items: [
      { path: '/inventory', title: '库存查询' },
      { path: '/stock-counts', title: '盘点', actions: ['stockcount.create', 'stockcount.post'] },
    ],
  },
  {
    group: '生产管理',
    items: [
      { path: '/boms', title: 'BOM', actions: ['bom.write'] },
      { path: '/mrp', title: 'MRP 运算', actions: ['mrp.run'] },
      { path: '/manufacturing-orders', title: '生产工单', actions: ['mo.create', 'mo.release', 'mo.issue', 'mo.receive'] },
    ],
  },
  {
    group: '质量管理',
    items: [
      { path: '/batches', title: '批次台账' },
      { path: '/inspections', title: '检验工作台', actions: ['batch.inspect', 'batch.judge'] },
      { path: '/partner-certs', title: '供应商资质', actions: ['partner_cert.write', 'partner_cert.audit', 'partner_cert.revoke'] },
    ],
  },
  {
    group: '财务管理',
    items: [
      { path: '/payables', title: '应付账款', actions: ['ap.read'] },
      { path: '/receivables', title: '应收账款', actions: ['ar.read'] },
      { path: '/settlements', title: '收付款核销', actions: ['settlement.create', 'settlement.reverse'] },
      { path: '/statement', title: '对账单', actions: ['statement.read'] },
    ],
  },
  { group: '审计追踪', items: [{ path: '/audit', title: '审计追踪', actions: ['audit.read'] }] },
  { group: '系统管理', items: [{ path: '/users', title: '用户管理', actions: ['users.manage'] }] },
]

// 按当前角色动作集过滤：空组隐藏，空项隐藏
const visibleMenus = computed(() =>
  menus
    .map((g) => ({
      ...g,
      items: g.items.filter((it) => !it.actions?.length || it.actions.some((a) => auth.has(a))),
    }))
    .filter((g) => g.items.length),
)

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => route.meta.title || '')

// 演示身份切换：重新登录对应账号 → 刷新权限 → 整页刷新加载新角色数据
async function onSwitch(account) {
  try {
    await auth.switchTo(account)
    ElMessage.success(`已切换为 ${account.label}（${account.username}）`)
    router.push('/dashboard')
    setTimeout(() => window.location.reload(), 300)
  } catch {
    // 登录失败：拦截器已提示
  }
}

function onLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">erp-lite</div>
      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item-group v-for="g in visibleMenus" :key="g.group" :title="g.group">
          <el-menu-item v-for="it in g.items" :key="it.path" :index="it.path">
            {{ it.title }}
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="page-title">{{ pageTitle }}</span>
        <span class="spacer" />
        <!-- 身份徽章：角色中文名 + 用户名 -->
        <el-tag type="primary" effect="dark" size="small" class="role-badge">{{ auth.roleLabel }}</el-tag>
        <span class="user">{{ auth.username || '-' }}</span>
        <!-- 切换身份（演示环境专用） -->
        <el-dropdown trigger="click" @command="onSwitch">
          <el-button size="small" type="warning" plain>切换身份</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item
                v-for="acc in DEMO_ACCOUNTS"
                :key="acc.username"
                :command="acc"
                :disabled="acc.username === auth.username"
              >
                {{ acc.label }}（{{ acc.username }}）
                <span v-if="acc.username === auth.username" style="color:#999"> · 当前</span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button link type="danger" @click="onLogout">退出登录</el-button>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100%;
}
.aside {
  border-right: 1px solid #e6e6e6;
  background: #fff;
}
.logo {
  height: 56px;
  line-height: 56px;
  text-align: center;
  font-size: 18px;
  font-weight: 600;
  border-bottom: 1px solid #e6e6e6;
}
.menu {
  border-right: none;
}
.header {
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid #e6e6e6;
  background: #fff;
}
.page-title {
  font-size: 16px;
  font-weight: 600;
}
.spacer {
  flex: 1;
}
.user {
  color: #666;
}
.role-badge {
  flex-shrink: 0;
}
</style>
