<script setup>
// 批次台账：全员可查（batch.trace）。列表+筛选；行操作「追溯」弹窗（正向追溯/反向召回两个 tab）
import { onMounted, reactive, ref } from 'vue'
import request from '@/api/request'
import { INSP_STATUS_META } from '@/constants'
import { fmtMoney, fmtTime } from '@/utils/format'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20, material_id: null, insp_status: '', keyword: '' })
const materials = ref([])
const partners = ref([])
const mlabel = (id) => {
  const m = materials.value.find((x) => x.id === id)
  return m ? `${m.code} ${m.name}` : id
}
const pname = (id) => (id ? partners.value.find((p) => p.id === id)?.name || `#${id}` : '-')
const statusMeta = (s) => INSP_STATUS_META[s] || { label: s, type: 'info' }
const SOURCE_LABELS = {
  purchase_order: '采购入库',
  manufacturing_order: '完工入库',
  adjust: '库存调整',
}
const sourceLabel = (row) => `${SOURCE_LABELS[row.source_type] || row.source_type} #${row.source_id}`

async function load() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.material_id) params.material_id = query.material_id
    if (query.insp_status) params.insp_status = query.insp_status
    if (query.keyword) params.keyword = query.keyword
    const d = await request.get('/batches', { params })
    // 列表项不含现存量/均价/物料编码：逐行拉详情补齐（演示规模可接受）
    const items = await Promise.all(
      d.items.map((b) => request.get(`/batches/${b.id}`, { silent: true }).catch(() => b)),
    )
    list.value = items
    total.value = d.total
  } finally {
    loading.value = false
  }
}

function onSearch() {
  query.page = 1
  load()
}

// ---------- 追溯弹窗 ----------
const traceVisible = ref(false)
const traceBatch = ref(null)
const traceTab = ref('forward')
const traceLoading = ref(false)
const traceData = ref(null)

async function openTrace(row) {
  traceBatch.value = row
  traceTab.value = 'forward'
  traceData.value = null
  traceVisible.value = true
  await loadTrace('forward')
}

async function loadTrace(dir) {
  traceLoading.value = true
  traceData.value = null
  try {
    const path = dir === 'forward' ? 'trace-forward' : 'trace-backward'
    traceData.value = await request.get(`/batches/${traceBatch.value.id}/${path}`, { silent: true })
  } catch {
    traceData.value = null // 非该方向的追溯（如原料批次正向）后端 400，展示空态
  } finally {
    traceLoading.value = false
  }
}

function onTraceTab(tab) {
  loadTrace(tab)
}

onMounted(load)
onMounted(async () => {
  const [ms, ps] = await Promise.all([
    request.get('/materials', { params: { page_size: 100 } }),
    request.get('/partners', { params: { page_size: 100 } }),
  ])
  materials.value = ms.items
  partners.value = ps.items
})
</script>

<template>
  <el-card>
    <div class="toolbar">
      <el-select v-model="query.material_id" clearable filterable placeholder="物料" style="width: 200px" @change="onSearch">
        <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
      </el-select>
      <el-select v-model="query.insp_status" clearable placeholder="检验状态" style="width: 130px" @change="onSearch">
        <el-option v-for="(meta, s) in INSP_STATUS_META" :key="s" :label="meta.label" :value="s" />
      </el-select>
      <el-input v-model="query.keyword" placeholder="批次号搜索" clearable style="width: 200px" @keyup.enter="onSearch" />
      <el-button type="primary" @click="onSearch">搜索</el-button>
    </div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="batch_no" label="批次号" min-width="150" />
      <el-table-column label="物料" min-width="160">
        <template #default="{ row }">{{ row.material_code || mlabel(row.material_id) }}</template>
      </el-table-column>
      <el-table-column label="检验状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusMeta(row.insp_status).type" size="small">{{ statusMeta(row.insp_status).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="现存" align="right" width="110">
        <template #default="{ row }">{{ fmtMoney(row.stock_qty ?? 0) }}</template>
      </el-table-column>
      <el-table-column label="批次均价" align="right" width="110">
        <template #default="{ row }">{{ row.avg_cost != null ? fmtMoney(row.avg_cost) : '-' }}</template>
      </el-table-column>
      <el-table-column label="来源单据" min-width="140">
        <template #default="{ row }">{{ sourceLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="供应商" min-width="120">
        <template #default="{ row }">{{ pname(row.supplier_id) }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openTrace(row)">追溯</el-button>
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

    <!-- 追溯弹窗：正向追溯 / 反向召回 -->
    <el-dialog v-model="traceVisible" :title="`批次追溯：${traceBatch?.batch_no || ''}`" width="760px" top="6vh">
      <el-descriptions v-if="traceBatch" :column="3" size="small" border style="margin-bottom: 12px">
        <el-descriptions-item label="批次号">{{ traceBatch.batch_no }}</el-descriptions-item>
        <el-descriptions-item label="物料">{{ traceBatch.material_code || mlabel(traceBatch.material_id) }}</el-descriptions-item>
        <el-descriptions-item label="检验状态">
          <el-tag :type="statusMeta(traceBatch.insp_status).type" size="small">{{ statusMeta(traceBatch.insp_status).label }}</el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <el-tabs v-model="traceTab" @tab-change="onTraceTab">
        <el-tab-pane label="正向追溯" name="forward">
          <div v-loading="traceLoading">
            <el-alert
              v-if="!traceLoading && !traceData"
              title="无正向追溯数据（原料批次请使用反向召回）"
              type="info"
              :closable="false"
            />
            <template v-if="traceData">
              <el-table :data="traceData.links" border size="small">
                <el-table-column prop="via_doc" label="经由单据" min-width="160" />
                <el-table-column label="下游批次" min-width="150">
                  <template #default="{ row }">{{ row.to_batch?.batch_no }}</template>
                </el-table-column>
                <el-table-column label="下游批次状态" width="110">
                  <template #default="{ row }">
                    <el-tag :type="statusMeta(row.to_batch?.insp_status).type" size="small">
                      {{ statusMeta(row.to_batch?.insp_status).label }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="partner" label="往来单位" min-width="120">
                  <template #default="{ row }">{{ row.partner || '-' }}</template>
                </el-table-column>
                <el-table-column label="数量" align="right" width="110">
                  <template #default="{ row }">{{ fmtMoney(row.qty) }}</template>
                </el-table-column>
              </el-table>
              <div v-if="!(traceData.links || []).length" style="color: #999; padding: 12px 0">该批次暂无下游使用记录</div>
            </template>
          </div>
        </el-tab-pane>
        <el-tab-pane label="反向召回" name="backward">
          <div v-loading="traceLoading">
            <el-alert
              v-if="!traceLoading && !traceData"
              title="无反向追溯数据（成品批次请使用正向追溯）"
              type="info"
              :closable="false"
            />
            <template v-if="traceData">
              <el-table :data="traceData.links" border size="small">
                <el-table-column prop="via_doc" label="经由单据" min-width="160" />
                <el-table-column label="上游批次" min-width="150">
                  <template #default="{ row }">{{ row.to_batch?.batch_no }}</template>
                </el-table-column>
                <el-table-column label="上游批次状态" width="110">
                  <template #default="{ row }">
                    <el-tag :type="statusMeta(row.to_batch?.insp_status).type" size="small">
                      {{ statusMeta(row.to_batch?.insp_status).label }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="partner" label="供应商" min-width="120">
                  <template #default="{ row }">{{ row.partner || '-' }}</template>
                </el-table-column>
                <el-table-column label="数量" align="right" width="110">
                  <template #default="{ row }">{{ fmtMoney(row.qty) }}</template>
                </el-table-column>
              </el-table>
              <div v-if="!(traceData.links || []).length" style="color: #999; padding: 12px 0">该批次暂无上游来源记录</div>
            </template>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
</style>
