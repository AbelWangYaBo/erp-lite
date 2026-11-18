<script setup>
// 角色工作台（v3 首页）：待办卡片网格，点击直达目标页面（GET /dashboard 按当前角色返回）
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import request from '@/api/request'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const todos = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await request.get('/dashboard')
    todos.value = Array.isArray(d?.todos) ? d.todos : []
  } catch {
    todos.value = [] // 空数据兜底：展示空态文案
  } finally {
    loading.value = false
  }
}

function go(todo) {
  if (todo.target) router.push(todo.target)
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <div class="head">
      <span class="hello">{{ auth.roleLabel }}（{{ auth.username || '-' }}）工作台</span>
    </div>
    <div v-if="todos.length" class="grid">
      <el-card v-for="t in todos" :key="t.key" shadow="hover" class="todo-card" @click="go(t)">
        <div class="count">{{ t.count }}</div>
        <div class="label">{{ t.label }}</div>
      </el-card>
    </div>
    <el-empty v-else description="当前没有待办事项" />
  </div>
</template>

<style scoped>
.head {
  margin-bottom: 16px;
}
.hello {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
}
.todo-card {
  cursor: pointer;
  text-align: center;
}
.todo-card :deep(.el-card__body) {
  padding: 20px 12px;
}
.count {
  font-size: 34px;
  font-weight: 700;
  line-height: 1.2;
  color: #409eff;
}
.label {
  margin-top: 8px;
  color: #606266;
  font-size: 13px;
}
</style>
