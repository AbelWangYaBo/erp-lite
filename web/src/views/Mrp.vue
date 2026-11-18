<script setup>
// MRP：需求录入 → 运算结果表格 → 勾选未转换建议 → 一键转单
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import { fmtMoney } from '@/utils/format'

const materials = ref([])
const mlabel = (id) => {
  const m = materials.value.find((x) => x.id === id)
  return m ? `${m.code} ${m.name}` : id
}

// ---------- 需求录入 ----------
const demand = ref([{ material_id: null, qty: 1 }])
const running = ref(false)

function addDemand() {
  demand.value.push({ material_id: null, qty: 1 })
}

function removeDemand(i) {
  demand.value.splice(i, 1)
}

// ---------- 运算结果 ----------
const resultRows = ref([])
const typeLabel = (t) => (t === 'buy' ? '采购(buy)' : t === 'make' ? '生产(make)' : t)

async function run() {
  const items = demand.value.filter((d) => d.material_id && Number(d.qty) > 0)
  if (!items.length) {
    ElMessage.warning('请至少填写一行有效需求')
    return
  }
  running.value = true
  try {
    const d = await request.post('/mrp/run', {
      demand: items.map((d) => ({ material_id: d.material_id, qty: d.qty })),
    })
    resultRows.value = Array.isArray(d) ? d : d.items || d.suggestions || d.results || []
    ElMessage.success('运算完成')
    loadSuggestions()
  } finally {
    running.value = false
  }
}

// ---------- 建议列表 / 一键转单 ----------
const suggestions = ref([])
const suggLoading = ref(false)
const suggTable = ref()
const selected = ref([])

async function loadSuggestions() {
  suggLoading.value = true
  try {
    const d = await request.get('/mrp/suggestions', { params: { converted: false } })
    suggestions.value = Array.isArray(d) ? d : d.items || []
  } finally {
    suggLoading.value = false
  }
}

function onSelectChange(rows) {
  selected.value = rows
}

// 采购(buy)建议转单必须先选供应商（后端 400 要求 partner_id）
const suppliers = ref([])
const convertVisible = ref(false)
const convertForm = reactive({ partner_id: null })

async function openConvertDialog() {
  if (!suppliers.value.length) {
    const ps = await request.get('/partners', { params: { page_size: 100 } })
    suppliers.value = ps.items.filter((p) => p.type === 'supplier')
  }
  convertForm.partner_id = null
  convertVisible.value = true
}

async function doConvert(partnerId) {
  await ElMessageBox.confirm(`将选中的 ${selected.value.length} 条建议转为采购/生产工单草稿？`, '转单确认', { type: 'warning' })
  const ids = selected.value.map((s) => s.id)
  await request.post('/mrp/suggestions/convert', partnerId ? { ids, partner_id: partnerId } : { ids })
  ElMessage.success('转单成功：采购订单/生产工单草稿已生成，请到对应列表审核或下达')
  convertVisible.value = false
  selected.value = []
  loadSuggestions()
}

async function convert() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选要转单的建议')
    return
  }
  if (selected.value.some((s) => s.type === 'buy')) {
    await openConvertDialog()
    return
  }
  await doConvert(null)
}

async function submitConvert() {
  if (!convertForm.partner_id) {
    ElMessage.warning('请选择供应商')
    return
  }
  await doConvert(convertForm.partner_id)
}

onMounted(async () => {
  const ms = await request.get('/materials', { params: { page_size: 100 } })
  materials.value = ms.items
  loadSuggestions()
})
</script>

<template>
  <el-card>
    <div class="section-title">需求录入</div>
    <el-table :data="demand" border size="small">
      <el-table-column label="物料" min-width="240">
        <template #default="{ row }">
          <el-select v-model="row.material_id" filterable size="small">
            <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="需求数量" width="160">
        <template #default="{ row }">
          <el-input-number v-model="row.qty" :min="0.0001" :precision="4" :controls="false" size="small" style="width: 100%" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80">
        <template #default="{ $index }">
          <el-button link type="danger" size="small" @click="removeDemand($index)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="demand-actions">
      <el-button size="small" @click="addDemand">添加需求</el-button>
      <el-button type="primary" :loading="running" @click="run">执行 MRP 运算</el-button>
    </div>

    <template v-if="resultRows.length">
      <div class="section-title">运算结果</div>
      <el-table :data="resultRows" border size="small">
        <el-table-column label="物料" min-width="150">
          <template #default="{ row }">{{ mlabel(row.material_id) }}</template>
        </el-table-column>
        <el-table-column label="类型" width="120">
          <template #default="{ row }">{{ typeLabel(row.type) }}</template>
        </el-table-column>
        <el-table-column label="毛需求" align="right" width="110">
          <template #default="{ row }">{{ fmtMoney(row.gross_qty) }}</template>
        </el-table-column>
        <el-table-column label="净需求" align="right" width="110">
          <template #default="{ row }">{{ fmtMoney(row.net_qty) }}</template>
        </el-table-column>
      </el-table>
    </template>

    <div class="section-title">未转换建议（勾选后一键转单）</div>
    <el-table
      ref="suggTable"
      :data="suggestions"
      border
      size="small"
      v-loading="suggLoading"
      @selection-change="onSelectChange"
    >
      <el-table-column type="selection" width="45" />
      <el-table-column label="物料" min-width="170">
        <template #default="{ row }">{{ mlabel(row.material_id) }}</template>
      </el-table-column>
      <el-table-column label="类型" width="120">
        <template #default="{ row }">{{ typeLabel(row.type) }}</template>
      </el-table-column>
      <el-table-column label="净需求" align="right" width="110">
        <template #default="{ row }">{{ fmtMoney(row.net_qty) }}</template>
      </el-table-column>
      <el-table-column label="已转换" width="90">
        <template #default="{ row }">{{ row.converted ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="转换单据" width="130">
        <template #default="{ row }">{{ row.converted_doc_id || '-' }}</template>
      </el-table-column>
    </el-table>
    <div class="demand-actions">
      <el-button type="primary" @click="convert">一键转单</el-button>
    </div>

    <!-- 采购建议转单前选择供应商 -->
    <el-dialog v-model="convertVisible" title="选择转单供应商" width="420px">
      <el-form label-width="90px">
        <el-form-item label="供应商" required>
          <el-select v-model="convertForm.partner_id" filterable style="width: 100%" placeholder="必选（用于生成采购订单）">
            <el-option v-for="p in suppliers" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="convertVisible = false">取消</el-button>
        <el-button type="primary" @click="submitConvert">确认转单</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.section-title {
  font-weight: 600;
  margin: 6px 0 10px;
}
.demand-actions {
  margin: 10px 0 18px;
  display: flex;
  gap: 10px;
}
</style>
