<script setup>
// 审计追踪：audit_records 全局查询（含 old→new）+ operation_logs（v3：支持人/角色/动作/时间过滤）
import { onMounted, reactive, ref } from 'vue'
import request from '@/api/request'
import { fmtTime, DOC_TYPE_OPTIONS } from '@/utils/format'

const tab = ref('records')

// 八角色（与后端 Role 枚举对齐，用于筛选下拉）
const ROLE_OPTIONS = [
  { value: 'admin', label: '系统管理员' },
  { value: 'purchase', label: '采购员' },
  { value: 'sales', label: '销售员' },
  { value: 'warehouse', label: '仓管员' },
  { value: 'production', label: '生产计划员' },
  { value: 'qc', label: '检验员' },
  { value: 'qa', label: '质量保证员' },
  { value: 'finance', label: '财务专员' },
]

// ---------- 留痕查询 ----------
const records = ref([])
const recordsTotal = ref(0)
const recordsLoading = ref(false)
const recordsQuery = reactive({
  page: 1,
  page_size: 20,
  doc_type: '',
  doc_id: null,
  actor: '',
  role: '',
  action: '',
  range: [],
})

async function loadRecords() {
  recordsLoading.value = true
  try {
    const params = { page: recordsQuery.page, page_size: recordsQuery.page_size }
    if (recordsQuery.doc_type) params.doc_type = recordsQuery.doc_type
    if (recordsQuery.doc_id) params.doc_id = recordsQuery.doc_id
    if (recordsQuery.actor) params.actor = recordsQuery.actor
    if (recordsQuery.role) params.role = recordsQuery.role
    if (recordsQuery.action) params.action = recordsQuery.action
    if (recordsQuery.range?.[0]) params.from = recordsQuery.range[0]
    if (recordsQuery.range?.[1]) params.to = recordsQuery.range[1]
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
  inspection: '检验录入',
  judge: '复核判定',
  dispose: '不合格处置',
  audit: '资质审核',
  revoke: '资质撤销',
  settle: '核销',
  reverse: '冲回',
  issue: '领料',
  unissue: '退料',
  receive: '收货/完工',
  ship: '出库',
  reject: '拒绝',
  post: '过账',
}

// ---------- 操作日志 ----------
const logs = ref([])
const logsTotal = ref(0)
const logsLoading = ref(false)
const logsQuery = reactive({
  page: 1,
  page_size: 20,
  doc_type: '',
  doc_id: null,
  actor: '',
  action: '',
  range: [],
})

async function loadLogs() {
  logsLoading.value = true
  try {
    const params = { page: logsQuery.page, page_size: logsQuery.page_size }
    if (logsQuery.doc_type) params.doc_type = logsQuery.doc_type
    if (logsQuery.doc_id) params.doc_id = logsQuery.doc_id
    if (logsQuery.actor) params.actor = logsQuery.actor
    if (logsQuery.action) params.action = logsQuery.action
    if (logsQuery.range?.[0]) params.from = logsQuery.range[0]
    if (logsQuery.range?.[1]) params.to = logsQuery.range[1]
    const d = await request.get('/audit/logs', { params })
    logs.value = d.items
    logsTotal.value = d.total
  } finally {
    logsLoading.value = false
  }
}

function searchLogs() {
  logsQuery.page = 1
  loadLogs()
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
          <el-input v-model="recordsQuery.doc_id" placeholder="单据ID" clearable style="width: 110px" @keyup.enter="searchRecords" />
          <el-input v-model="recordsQuery.actor" placeholder="操作人" clearable style="width: 120px" @keyup.enter="searchRecords" />
          <el-select v-model="recordsQuery.role" clearable placeholder="角色" style="width: 140px" @change="searchRecords">
            <el-option v-for="r in ROLE_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
          <el-input v-model="recordsQuery.action" placeholder="动作" clearable style="width: 120px" @keyup.enter="searchRecords" />
          <el-date-picker v-model="recordsQuery.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" style="width: 260px" />
          <el-button type="primary" @click="searchRecords">查询</el-button>
        </div>
        <el-table :data="records" border size="small" v-loading="recordsLoading">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="doc_type" label="单据类型" width="140" />
          <el-table-column prop="doc_id" label="单据ID" width="80" />
          <el-table-column label="单据号" min-width="130">
            <template #default="{ row }">{{ row.doc_no || '-' }}</template>
          </el-table-column>
          <el-table-column label="动作" width="100">
            <template #default="{ row }">{{ actionLabels[row.action] || row.action }}</template>
          </el-table-column>
          <el-table-column prop="actor" label="操作人" width="100" />
          <el-table-column prop="role" label="角色" width="110">
            <template #default="{ row }">{{ row.role || '-' }}</template>
          </el-table-column>
          <el-table-column prop="field" label="字段" width="130" />
          <el-table-column label="旧值 → 新值" min-width="180">
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
        <div class="toolbar">
          <el-select v-model="logsQuery.doc_type" clearable filterable placeholder="单据类型" style="width: 190px" @change="searchLogs">
            <el-option v-for="t in DOC_TYPE_OPTIONS" :key="t" :label="t" :value="t" />
          </el-select>
          <el-input v-model="logsQuery.doc_id" placeholder="单据ID" clearable style="width: 110px" @keyup.enter="searchLogs" />
          <el-input v-model="logsQuery.actor" placeholder="操作人" clearable style="width: 120px" @keyup.enter="searchLogs" />
          <el-input v-model="logsQuery.action" placeholder="动作" clearable style="width: 120px" @keyup.enter="searchLogs" />
          <el-date-picker v-model="logsQuery.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" style="width: 260px" />
          <el-button type="primary" @click="searchLogs">查询</el-button>
        </div>
        <el-table :data="logs" border size="small" v-loading="logsLoading">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="actor" label="操作人" width="110" />
          <el-table-column prop="action" label="动作" width="150" />
          <el-table-column label="单据" width="160">
            <template #default="{ row }">
              <span v-if="row.doc_type">{{ row.doc_type }}#{{ row.doc_id }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column prop="detail" label="详情" min-width="220" />
        </el-table>
        <el-pagination
          v-model:current-page="logsQuery.page"
          v-model:page-size="logsQuery.page_size"
          :total="logsTotal"
          layout="total, prev, pager, next, sizes"
          style="margin-top: 12px; justify-content: flex-end"
          @current-change="loadLogs"
          @size-change="searchLogs"
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
  flex-wrap: wrap;
}
</style>
