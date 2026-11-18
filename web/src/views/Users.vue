<script setup>
// 用户管理（admin：users.manage）：账号列表 + 新建账号 + 角色调整（v3 八角色，权限即时生效）
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import { fmtTime } from '@/utils/format'

// v3 八角色：后端角色变更即时生效，前端菜单在下次页面跳转时自动刷新
const ROLE_LABELS_V3 = {
  admin: '系统管理员',
  purchase: '采购员',
  sales: '销售员',
  warehouse: '仓管员',
  production: '生产计划员',
  qc: '检验员',
  qa: '质量保证员',
  finance: '财务专员',
}
const ROLE_OPTIONS = Object.entries(ROLE_LABELS_V3).map(([value, label]) => ({ value, label }))

const list = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await request.get('/users')
    list.value = d.items
  } finally {
    loading.value = false
  }
}

// ---------- 新建账号 ----------
const createVisible = ref(false)
const createRef = ref()
const createForm = reactive({ username: '', password: '', role: 'purchase' })
const createRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入初始密码', trigger: 'blur' }],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
}

function openCreate() {
  createForm.username = ''
  createForm.password = ''
  createForm.role = 'purchase'
  createVisible.value = true
}

async function submitCreate() {
  await createRef.value.validate()
  await request.post('/users', { ...createForm })
  ElMessage.success('账号已创建')
  createVisible.value = false
  load()
}

// ---------- 改角色 ----------
const roleEditing = ref({})

function onRoleChange(row) {
  const newRole = roleEditing.value[row.id]
  if (!newRole || newRole === row.role) return
  ElMessageBox.confirm(
    `确认将用户「${row.username}」角色调整为「${ROLE_LABELS_V3[newRole]}」？权限即时生效；前端菜单将在下次页面跳转时自动刷新`,
    '角色调整',
    { type: 'warning' },
  ).then(async () => {
    await request.put(`/users/${row.id}/role`, { role: newRole })
    ElMessage.success('角色已调整：权限即时生效，前端菜单将在下次页面跳转时自动刷新')
    row.role = newRole
  }).catch(() => {
    roleEditing.value[row.id] = row.role
  })
}

onMounted(load)
</script>

<template>
  <el-card>
    <div class="toolbar">
      <span class="hint">角色决定可执行动作（RBAC）；调整角色后权限即时生效，前端菜单将在下次页面跳转时自动刷新</span>
      <span class="spacer" />
      <el-button type="primary" @click="openCreate">新建账号</el-button>
    </div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="username" label="用户名" min-width="140" />
      <el-table-column label="角色" min-width="170">
        <template #default="{ row }">
          <el-select
            v-model="roleEditing[row.id]"
            :placeholder="ROLE_LABELS_V3[row.role] || row.role"
            size="small"
            style="width: 150px"
            @change="onRoleChange(row)"
          >
            <el-option v-for="r in ROLE_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVisible" title="新建账号" width="440px">
      <el-form ref="createRef" :model="createForm" :rules="createRules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="createForm.username" />
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input v-model="createForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option v-for="r in ROLE_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}
.spacer {
  flex: 1;
}
.hint {
  color: #999;
  font-size: 12px;
}
</style>
