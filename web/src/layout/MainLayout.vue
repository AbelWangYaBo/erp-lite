<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 菜单分组：基础数据 / 采购 / 销售 / 库存 / 生产 / 财务 / 审计
const menus = [
  {
    group: '基础数据',
    items: [
      { path: '/materials', title: '物料' },
      { path: '/warehouses', title: '仓库' },
      { path: '/partners', title: '往来单位' },
    ],
  },
  { group: '采购管理', items: [{ path: '/purchase-orders', title: '采购订单' }] },
  { group: '销售管理', items: [{ path: '/sales-orders', title: '销售订单' }] },
  { group: '库存管理', items: [{ path: '/inventory', title: '库存查询' }] },
  {
    group: '生产管理',
    items: [
      { path: '/boms', title: 'BOM' },
      { path: '/mrp', title: 'MRP 运算' },
      { path: '/manufacturing-orders', title: '生产工单' },
    ],
  },
  {
    group: '财务管理',
    items: [
      { path: '/payables', title: '应付账款' },
      { path: '/receivables', title: '应收账款' },
      { path: '/settlements', title: '收付款核销' },
      { path: '/statement', title: '对账单' },
    ],
  },
  {
    group: '审计追踪',
    items: [{ path: '/audit', title: '审计追踪' }],
  },
]

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => route.meta.title || '')

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
        <el-menu-item-group v-for="g in menus" :key="g.group" :title="g.group">
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
        <span class="user">{{ auth.username || 'admin' }}</span>
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
  height: 100vh;
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
.main {
  background: #f5f7fa;
}
</style>
