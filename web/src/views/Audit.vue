<script setup>
// 审计追踪：audit_records 全局查询（含 old→new）+ operation_logs
import { onMounted, reactive, ref } from 'vue'
import request from '@/api/request'
import { fmtTime, DOC_TYPE_OPTIONS } from '@/utils/format'

const tab = ref('records')

// ---------- 留痕查询 ----------
const records = ref([])
const recordsTotal = ref(0)
const recordsLoading = ref(false)
const recordsQuery = reactive({ page: 1, page_size: 20, doc_type: '', doc_id: null })

async function loadRecords() {
  recordsLoading.value = true
  try {
    const params = { page: recordsQuery.page, page_size: recordsQuery.page_size }
    if (recordsQuery.doc_type) params.doc_type = recordsQuery.doc_type
    if (recordsQuery.doc_id) params.doc_id = recordsQuery.doc_id
    const d = await request.get('/audit/records', { params })
    records.value = d.items
    recordsTotal.value = d.total
  } finally {
    recordsLoading.value = false
  }
}

function searchRecords() {
  recordsQuery.page = 1
  loadRecords()
}

const actionLabels = {
  create: '新增',
  update: '修改',
  status: '状态变更',
  void: '作废',
  close: '关闭',
  delete: '删除',
}

// ---------- 操作日志 ----------
const logs = ref([])
const logsTotal = ref(0)
const logsLoading = ref(false)
const logsQuery = reactive({ page: 1, page_size: 20 })

async function loadLogs() {
  logsLoading.value = true
  try {
    const d = await request.get('/audit/logs', { params: { page: logsQuery.page, page_size: logsQuery.page_size } })
    logs.value = d.items
    logsTotal.value = d.total
  } finally {
    logsLoading.value = false
  }
}

// 首次切入日志 tab 时加载
const logsLoaded = ref(false)
function onTabChange(name) {
  if (name === 'logs' && !logsLoaded.value) {
    logsLoaded.value = true
    loadLogs()
  }
}

onMounted(loadRecords)
</script>

<template>
  <el-card>
    <el-tabs v-model="tab" @tab-change="onTabChange">
      <el-tab-pane label="单据留痕" name="records">
        <div class="toolbar">
          <el-select v-model="recordsQuery.doc_type" clearable filterable placeholder="单据类型" style="width: 190px" @change="searchRecords">
            <el-option v-for="t in DOC_TYPE_OPTIONS" :key="t" :label="t" :value="t" />
          </el-select>
          <el-input v-model="recordsQuery.doc_id" placeholder="单据ID" clearable style="width: 130px" @keyup.enter="searchRecords" />
          <el-button type="primary" @click="searchRecords">查询</el-button>
        </div>
        <el-table :data="records" border size="small" v-loading="recordsLoading">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="doc_type" label="单据类型" width="130" />
          <el-table-column prop="doc_id" label="单据ID" width="80" />
          <el-table-column label="动作" width="90">
            <template #default="{ row }">{{ actionLabels[row.action] || row.action }}</template>
          </el-table-column>
          <el-table-column prop="actor" label="操作人" width="100" />
          <el-table-column prop="field" label="字段" width="130" />
          <el-table-column label="旧值 → 新值" min-width="200">
            <template #default="{ row }">{{ row.old_value ?? '-' }} → {{ row.new_value ?? '-' }}</template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="recordsQuery.page"
          v-model:page-size="recordsQuery.page_size"
          :total="recordsTotal"
          layout="total, prev, pager, next, sizes"
          style="margin-top: 12px; justify-content: flex-end"
          @current-change="loadRecords"
          @size-change="searchRecords"
        />
      </el-tab-pane>

      <el-tab-pane label="操作日志" name="logs">
        <el-table :data="logs" border size="small" v-loading="logsLoading">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="actor" label="操作人" width="110" />
          <el-table-column prop="action" label="动作" width="180" />
          <el-table-column prop="detail" label="详情" min-width="220" />
        </el-table>
        <el-pagination
          v-model:current-page="logsQuery.page"
          v-model:page-size="logsQuery.page_size"
          :total="logsTotal"
          layout="total, prev, pager, next, sizes"
          style="margin-top: 12px; justify-content: flex-end"
          @current-change="loadLogs"
          @size-change="() => { logsQuery.page = 1; loadLogs() }"
        />
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
</style>
