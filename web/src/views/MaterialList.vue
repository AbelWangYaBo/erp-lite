<script setup>
// 物料：标准表格 + 搜索 + 分页 + 新增/编辑/删除
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import { CATEGORY_LABELS } from '@/utils/format'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20, keyword: '' })
const warehouses = ref([])
const formRef = ref()
const dialogVisible = ref(false)
const editingId = ref(null)

const blank = { code: '', name: '', spec: '', unit: '', category: 'raw', default_warehouse_id: null, batch_required: false, udi_di: '', udi_pi_rule: '' }
const form = reactive({ ...blank })
const rules = {
  code: [{ required: true, message: '请输入编码', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  unit: [{ required: true, message: '请输入单位', trigger: 'blur' }],
  category: [{ required: true, message: '请选择分类', trigger: 'change' }],
}

async function load() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.keyword) params.keyword = query.keyword
    const d = await request.get('/materials', { params })
    list.value = d.items
    total.value = d.total
  } finally {
    loading.value = false
  }
}

function onSearch() {
  query.page = 1
  load()
}

async function openDialog(row) {
  editingId.value = row ? row.id : null
  Object.assign(form, blank, row ? JSON.parse(JSON.stringify(row)) : {})
  dialogVisible.value = true
}

async function submit() {
  await formRef.value.validate()
  const payload = { ...form }
  if (!payload.default_warehouse_id) payload.default_warehouse_id = null
  payload.batch_required = !!payload.batch_required
  payload.udi_di = payload.udi_di || null
  payload.udi_pi_rule = payload.udi_pi_rule || null
  if (editingId.value) {
    await request.put(`/materials/${editingId.value}`, payload)
    ElMessage.success('已保存')
  } else {
    await request.post('/materials', payload)
    ElMessage.success('已新增')
  }
  dialogVisible.value = false
  load()
}

function remove(row) {
  ElMessageBox.confirm(`确认删除物料「${row.code}」？`, '删除确认', { type: 'warning' }).then(async () => {
    await request.delete(`/materials/${row.id}`)
    ElMessage.success('已删除')
    load()
  }).catch(() => {})
}

const categoryLabel = computed(() => (c) => CATEGORY_LABELS[c] || c)

onMounted(load)
onMounted(async () => {
  const d = await request.get('/warehouses', { params: { page_size: 100 } })
  warehouses.value = d.items
})
</script>

<template>
  <el-card>
    <div class="toolbar">
      <el-input v-model="query.keyword" placeholder="编码/名称搜索" clearable style="width: 220px" @keyup.enter="onSearch" />
      <el-button type="primary" @click="onSearch">搜索</el-button>
      <span class="spacer" />
      <el-button type="primary" @click="openDialog(null)">新增物料</el-button>
    </div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="code" label="编码" min-width="110" />
      <el-table-column prop="name" label="名称" min-width="130" />
      <el-table-column prop="spec" label="规格" min-width="110" />
      <el-table-column prop="unit" label="单位" width="70" />
      <el-table-column label="分类" width="90">
        <template #default="{ row }">{{ categoryLabel(row.category) }}</template>
      </el-table-column>
      <el-table-column label="默认仓库" min-width="100">
        <template #default="{ row }">{{ warehouses.find((w) => w.id === row.default_warehouse_id)?.name || '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDialog(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="query.page"
      v-model:page-size="query.page_size"
      :total="total"
      layout="total, prev, pager, next, sizes"
      style="margin-top: 12px; justify-content: flex-end"
      @current-change="load"
      @size-change="onSearch"
    />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑物料' : '新增物料'" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="编码" prop="code">
          <el-input v-model="form.code" :disabled="!!editingId" />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="规格">
          <el-input v-model="form.spec" />
        </el-form-item>
        <el-form-item label="单位" prop="unit">
          <el-input v-model="form.unit" />
        </el-form-item>
        <el-form-item label="分类" prop="category">
          <el-select v-model="form.category" style="width: 100%">
            <el-option label="原材料" value="raw" />
            <el-option label="半成品" value="semi" />
            <el-option label="成品" value="finished" />
          </el-select>
        </el-form-item>
        <el-form-item label="默认仓库">
          <el-select v-model="form.default_warehouse_id" clearable style="width: 100%">
            <el-option v-for="w in warehouses" :key="w.id" :label="`${w.code} ${w.name}`" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="批次管理">
          <el-switch v-model="form.batch_required" active-text="启用" inactive-text="关闭" />
          <span class="field-tip">启用后出入库必须指定批次，且仅 QA 合格批次可出库/领料</span>
        </el-form-item>
        <template v-if="form.category === 'finished'">
          <el-form-item label="UDI-DI">
            <el-input v-model="form.udi_di" placeholder="器械唯一标识 DI（成品）" maxlength="64" />
          </el-form-item>
          <el-form-item label="PI 组成规则">
            <el-input v-model="form.udi_pi_rule" placeholder="生产标识 PI 组成规则（成品，可选）" maxlength="128" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.spacer {
  flex: 1;
}
.field-tip {
  margin-left: 12px;
  color: #999;
  font-size: 12px;
}
</style>
