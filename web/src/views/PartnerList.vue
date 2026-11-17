<script setup>
// 往来单位：type 过滤 + 搜索 + 分页 + 新增/编辑/删除
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import { PARTNER_TYPE_LABELS } from '@/utils/format'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20, keyword: '', type: '' })
const formRef = ref()
const dialogVisible = ref(false)
const editingId = ref(null)
const blank = { code: '', name: '', type: 'supplier', contact: '' }
const form = reactive({ ...blank })
const rules = {
  code: [{ required: true, message: '请输入编码', trigger: 'blur' }],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
}

async function load() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.keyword) params.keyword = query.keyword
    if (query.type) params.type = query.type
    const d = await request.get('/partners', { params })
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

function openDialog(row) {
  editingId.value = row ? row.id : null
  Object.assign(form, blank, row ? JSON.parse(JSON.stringify(row)) : {})
  dialogVisible.value = true
}

async function submit() {
  await formRef.value.validate()
  if (editingId.value) {
    await request.put(`/partners/${editingId.value}`, { ...form })
    ElMessage.success('已保存')
  } else {
    await request.post('/partners', { ...form })
    ElMessage.success('已新增')
  }
  dialogVisible.value = false
  load()
}

function remove(row) {
  ElMessageBox.confirm(`确认删除往来单位「${row.code}」？`, '删除确认', { type: 'warning' }).then(async () => {
    await request.delete(`/partners/${row.id}`)
    ElMessage.success('已删除')
    load()
  }).catch(() => {})
}

onMounted(load)
</script>

<template>
  <el-card>
    <div class="toolbar">
      <el-select v-model="query.type" clearable placeholder="类型" style="width: 120px" @change="onSearch">
        <el-option label="供应商" value="supplier" />
        <el-option label="客户" value="customer" />
      </el-select>
      <el-input v-model="query.keyword" placeholder="编码/名称搜索" clearable style="width: 220px" @keyup.enter="onSearch" />
      <el-button type="primary" @click="onSearch">搜索</el-button>
      <span class="spacer" />
      <el-button type="primary" @click="openDialog(null)">新增往来单位</el-button>
    </div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="code" label="编码" min-width="110" />
      <el-table-column prop="name" label="名称" min-width="160" />
      <el-table-column label="类型" width="90">
        <template #default="{ row }">{{ PARTNER_TYPE_LABELS[row.type] || row.type }}</template>
      </el-table-column>
      <el-table-column prop="contact" label="联系方式" min-width="130" />
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑往来单位' : '新增往来单位'" width="460px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="编码" prop="code">
          <el-input v-model="form.code" />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="类型" prop="type">
          <el-select v-model="form.type" style="width: 100%">
            <el-option label="供应商" value="supplier" />
            <el-option label="客户" value="customer" />
          </el-select>
        </el-form-item>
        <el-form-item label="联系方式">
          <el-input v-model="form.contact" />
        </el-form-item>
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
</style>
