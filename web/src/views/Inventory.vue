<script setup>
// 库存：现存量汇总（tab1）+ 台账流水带筛选（tab2）
import { onMounted, reactive, ref, watch } from 'vue'
import request from '@/api/request'
import { fmtMoney, fmtTime, MOVE_TYPE_LABELS } from '@/utils/format'

const materials = ref([])
const warehouses = ref([])
const mlabel = (id) => {
  const m = materials.value.find((x) => x.id === id)
  return m ? `${m.code} ${m.name}` : id
}
const wname = (id) => warehouses.value.find((w) => w.id === id)?.name || id

// ---------- 现存量 ----------
const stockTab = ref('stock')
const stock = ref([])
const stockLoading = ref(false)
const stockQuery = reactive({ material_id: null, warehouse_id: null })

async function loadStock() {
  stockLoading.value = true
  try {
    const params = {}
    if (stockQuery.material_id) params.material_id = stockQuery.material_id
    if (stockQuery.warehouse_id) params.warehouse_id = stockQuery.warehouse_id
    stock.value = await request.get('/inventory/stock', { params })
  } finally {
    stockLoading.value = false
  }
}

// ---------- 台账流水 ----------
const moves = ref([])
const movesTotal = ref(0)
const movesLoading = ref(false)
const movesQuery = reactive({ page: 1, page_size: 20, material_id: null, warehouse_id: null, range: [] })

async function loadMoves() {
  movesLoading.value = true
  try {
    const params = { page: movesQuery.page, page_size: movesQuery.page_size }
    if (movesQuery.material_id) params.material_id = movesQuery.material_id
    if (movesQuery.warehouse_id) params.warehouse_id = movesQuery.warehouse_id
    if (movesQuery.range?.[0]) params.from = movesQuery.range[0]
    if (movesQuery.range?.[1]) params.to = movesQuery.range[1]
    const d = await request.get('/inventory/moves', { params })
    moves.value = d.items
    movesTotal.value = d.total
  } finally {
    movesLoading.value = false
  }
}

function searchMoves() {
  movesQuery.page = 1
  loadMoves()
}

const moveTypeLabel = (t) => MOVE_TYPE_LABELS[t] || t

// 首次切入台账 tab 时加载
const movesLoaded = ref(false)
watch(stockTab, (t) => {
  if (t === 'moves' && !movesLoaded.value) {
    movesLoaded.value = true
    loadMoves()
  }
})

onMounted(async () => {
  loadStock()
  const [ms, ws] = await Promise.all([
    request.get('/materials', { params: { page_size: 100 } }),
    request.get('/warehouses', { params: { page_size: 100 } }),
  ])
  materials.value = ms.items
  warehouses.value = ws.items
})
</script>

<template>
  <el-card>
    <el-tabs v-model="stockTab">
      <el-tab-pane label="现存量汇总" name="stock">
        <div class="toolbar">
          <el-select v-model="stockQuery.material_id" clearable filterable placeholder="物料" style="width: 220px" @change="loadStock">
            <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
          </el-select>
          <el-select v-model="stockQuery.warehouse_id" clearable placeholder="仓库" style="width: 160px" @change="loadStock">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
          <el-button type="primary" @click="loadStock">查询</el-button>
        </div>
        <el-table :data="stock" border size="small" v-loading="stockLoading">
          <el-table-column label="物料编码" min-width="120">
            <template #default="{ row }">{{ row.material_code }}</template>
          </el-table-column>
          <el-table-column label="物料名称" min-width="140">
            <template #default="{ row }">{{ row.material_name }}</template>
          </el-table-column>
          <el-table-column label="现存合计" align="right" width="110">
            <template #default="{ row }">{{ fmtMoney(row.qty) }}</template>
          </el-table-column>
          <el-table-column label="合格" align="right" width="100">
            <template #default="{ row }">{{ row.qualified_qty != null ? fmtMoney(row.qualified_qty) : '-' }}</template>
          </el-table-column>
          <el-table-column label="待检" align="right" width="110">
            <template #default="{ row }">
              <span v-if="row.pending_qty != null">
                <span v-if="Number(row.pending_qty) > 0" class="pending-dot" title="存在待检批次：qc 录入检验 → qa 判定后放行" />
                {{ fmtMoney(row.pending_qty) }}
              </span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="不合格" align="right" width="100">
            <template #default="{ row }">{{ row.rejected_qty != null ? fmtMoney(row.rejected_qty) : '-' }}</template>
          </el-table-column>
          <el-table-column label="移动均价" align="right" width="120">
            <template #default="{ row }">{{ fmtMoney(row.avg_cost) }}</template>
          </el-table-column>
          <el-table-column label="库存金额" align="right" width="130">
            <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="库存台账" name="moves">
        <div class="toolbar">
          <el-select v-model="movesQuery.material_id" clearable filterable placeholder="物料" style="width: 220px">
            <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
          </el-select>
          <el-select v-model="movesQuery.warehouse_id" clearable placeholder="仓库" style="width: 160px">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
          <el-date-picker v-model="movesQuery.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" style="width: 260px" />
          <el-button type="primary" @click="searchMoves">查询</el-button>
        </div>
        <el-table :data="moves" border size="small" v-loading="movesLoading">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.moved_at) }}</template>
          </el-table-column>
          <el-table-column label="物料" min-width="150">
            <template #default="{ row }">{{ mlabel(row.material_id) }}</template>
          </el-table-column>
          <el-table-column label="仓库" width="110">
            <template #default="{ row }">{{ wname(row.warehouse_id) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="100">
            <template #default="{ row }">{{ moveTypeLabel(row.move_type) }}</template>
          </el-table-column>
          <el-table-column label="数量" align="right" width="110">
            <template #default="{ row }">{{ fmtMoney(row.qty) }}</template>
          </el-table-column>
          <el-table-column label="单位成本" align="right" width="110">
            <template #default="{ row }">{{ fmtMoney(row.unit_cost) }}</template>
          </el-table-column>
          <el-table-column label="金额" align="right" width="120">
            <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
          </el-table-column>
          <el-table-column label="来源" min-width="140">
            <template #default="{ row }">{{ row.source_type }}#{{ row.source_id }}</template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="movesQuery.page"
          v-model:page-size="movesQuery.page_size"
          :total="movesTotal"
          layout="total, prev, pager, next, sizes"
          style="margin-top: 12px; justify-content: flex-end"
          @current-change="loadMoves"
          @size-change="searchMoves"
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
.pending-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e6a23c;
  margin-right: 6px;
  vertical-align: middle;
}
</style>
