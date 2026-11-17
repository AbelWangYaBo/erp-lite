<script setup>
// 采购/销售订单共用模块：列表+新建+详情抽屉（行表格、审计留痕 tab、状态动作、行级分批出入库）
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import StatusTag from '@/components/StatusTag.vue'
import { useAuthStore } from '@/stores/auth'
import { fmtMoney, fmtTime, today } from '@/utils/format'

const auth = useAuthStore()
const props = defineProps({ kind: { type: String, required: true } })
const isPO = computed(() => props.kind === 'purchase')
const base = computed(() => (isPO.value ? '/purchase-orders' : '/sales-orders'))
const inOutLabel = computed(() => (isPO.value ? '入库' : '出库'))
const qtyField = computed(() => (isPO.value ? 'received_qty' : 'shipped_qty'))
// 按钮级权限（PRD-v2 §2.5 矩阵）
const canCreate = computed(() => auth.has(isPO.value ? 'po.create' : 'so.create'))
const actionAuth = {
  approve: computed(() => auth.has(isPO.value ? 'po.approve' : 'so.approve')),
  void: computed(() => auth.has(isPO.value ? 'po.void' : 'so.void')),
  close: computed(() => auth.has(isPO.value ? 'po.close' : 'so.close')),
  inOut: computed(() => auth.has(isPO.value ? 'po.receive' : 'so.ship')),
}

// ---------- 列表 ----------
const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20, keyword: '', status: '' })

async function load() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.keyword) params.keyword = query.keyword
    if (query.status) params.status = query.status
    const d = await request.get(base.value, { params })
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

// ---------- 基础数据映射 ----------
const partners = ref([])
const materials = ref([])
const warehouses = ref([])
const pname = (id) => partners.value.find((p) => p.id === id)?.name || id
const mlabel = (id) => {
  const m = materials.value.find((x) => x.id === id)
  return m ? `${m.code} ${m.name}` : id
}
const wname = (id) => warehouses.value.find((w) => w.id === id)?.name || id

// ---------- 新建 ----------
const createVisible = ref(false)
const createRef = ref()
const blankLine = () => ({ material_id: null, qty: 1, unit_price: 0, warehouse_id: null })
const createForm = reactive({ partner_id: null, order_date: today(), expected_date: null, remark: '', lines: [] })
const createRules = {
  partner_id: [{ required: true, message: '请选择往来单位', trigger: 'change' }],
  order_date: [{ required: true, message: '请选择日期', trigger: 'change' }],
}

function openCreate() {
  createForm.partner_id = null
  createForm.order_date = today()
  createForm.expected_date = null
  createForm.remark = ''
  createForm.lines = [blankLine()]
  createVisible.value = true
}

function addLine() {
  createForm.lines.push(blankLine())
}

function removeLine(i) {
  createForm.lines.splice(i, 1)
}

async function submitCreate() {
  await createRef.value.validate()
  const lines = createForm.lines.filter((l) => l.material_id && l.warehouse_id)
  if (!lines.length) {
    ElMessage.warning('请至少填写一行有效的物料明细')
    return
  }
  const payload = {
    partner_id: createForm.partner_id,
    order_date: createForm.order_date,
    remark: createForm.remark || null,
    lines: lines.map((l) => ({
      material_id: l.material_id,
      qty: l.qty,
      unit_price: l.unit_price,
      warehouse_id: l.warehouse_id,
    })),
  }
  if (isPO.value) payload.expected_date = createForm.expected_date || null
  await request.post(base.value, payload)
  ElMessage.success('已创建')
  createVisible.value = false
  load()
}

// ---------- 详情抽屉 ----------
const detailVisible = ref(false)
const detail = ref(null)
const detailLoading = ref(false)

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await request.get(`${base.value}/${row.id}`)
  } finally {
    detailLoading.value = false
  }
}

const detailQtyTotal = computed(() => {
  const lines = detail.value?.lines || []
  return lines.reduce((s, l) => s + Number(l.qty) * Number(l.unit_price), 0)
})

function lineAmount(l) {
  return fmtMoney(Number(l.qty) * Number(l.unit_price))
}

// ---------- 状态动作（按状态显隐） ----------
const canApprove = (s) => s === 'draft'
const canInOut = (s) => ['approved', 'partial'].includes(s)
const canVoid = (s) => ['draft', 'approved', 'partial'].includes(s)
const canClose = (s) => ['approved', 'partial'].includes(s)

const ACTION_TEXT = { approve: '审核', void: '作废', close: '关闭' }

async function doAction(action) {
  const text = ACTION_TEXT[action]
  await ElMessageBox.confirm(`确认${text}该${isPO.value ? '采购' : '销售'}订单？`, '操作确认', { type: 'warning' })
  await request.post(`${base.value}/${detail.value.id}/${action}`)
  ElMessage.success(`${text}成功`)
  detail.value = await request.get(`${base.value}/${detail.value.id}`)
  load()
}

// ---------- 入库/出库（行级可分批录入数量；批次物料必带批次） ----------
const inOutVisible = ref(false)
const inOutRows = ref([])
// SO 出库用：material_id → qualified 批次下拉
const batchOptions = ref({})

function openInOut() {
  inOutRows.value = (detail.value.lines || []).map((l) => {
    const material = materials.value.find((m) => m.id === l.material_id)
    const remaining = Number(l.qty) - Number(l[qtyField.value])
    return {
      line_id: l.id,
      material_id: l.material_id,
      qty: l.qty,
      done: l[qtyField.value],
      remaining,
      warehouse_id: l.warehouse_id,
      input_qty: remaining > 0 ? remaining : 0,
      // v2 批次：批次物料 PO 入库填 batch_no / SO 出库选 qualified batch_id
      needBatch: !!material?.batch_required,
      batch_no: '',
      batch_id: null,
    }
  })
  batchOptions.value = {}
  inOutVisible.value = true
  if (!isPO.value) loadQualifiedBatches()
}

// 合格批次下拉（拉 /batches?material_id=&insp_status=qualified）
async function loadQualifiedBatches() {
  const ids = [...new Set(inOutRows.value.filter((r) => r.needBatch).map((r) => r.material_id))]
  await Promise.all(
    ids.map(async (mid) => {
      try {
        const d = await request.get('/batches', {
          params: { material_id: mid, insp_status: 'qualified', page: 1, page_size: 100 },
          silent: true,
        })
        batchOptions.value[mid] = d.items
      } catch {
        batchOptions.value[mid] = []
      }
    }),
  )
}

async function submitInOut() {
  const selected = inOutRows.value.filter((r) => Number(r.input_qty) > 0)
  if (!selected.length) {
    ElMessage.warning('请至少为一行录入本次数量')
    return
  }
  // 批次物料必填批次
  const missing = selected.find((r) => r.needBatch && (isPO.value ? !r.batch_no?.trim() : !r.batch_id))
  if (missing) {
    ElMessage.warning(`物料「${mlabel(missing.material_id)}」已启用批次管理，${inOutLabel.value}必须${isPO.value ? '填写批次号' : '选择合格批次'}`)
    return
  }
  const body = selected.map((r) => {
    const item = { line_id: r.line_id, qty: r.input_qty, warehouse_id: r.warehouse_id }
    if (r.needBatch) {
      if (isPO.value) item.batch_no = r.batch_no.trim()
      else item.batch_id = r.batch_id
    }
    return item
  })
  const over = body.find((r) => {
    const row = inOutRows.value.find((x) => x.line_id === r.line_id)
    return Number(r.qty) > Number(row.remaining)
  })
  if (over) {
    ElMessage.warning('录入数量不能超过剩余未完成数量')
    return
  }
  await request.post(`${base.value}/${detail.value.id}/${isPO.value ? 'receive' : 'ship'}`, body)
  // SO 成品出库：提示带 UDI-DI + 批号（GMP 追溯信息）
  if (isPO.value) {
    const batches = selected.filter((r) => r.needBatch).map((r) => `${mlabel(r.material_id)} 批次 ${r.batch_no.trim()}`)
    ElMessage.success(`入库成功${batches.length ? '：' + batches.join('；') : ''}`)
  } else {
    const infos = selected
      .filter((r) => r.needBatch)
      .map((r) => {
        const m = materials.value.find((x) => x.id === r.material_id)
        const b = (batchOptions.value[r.material_id] || []).find((x) => x.id === r.batch_id)
        const udi = m?.udi_di ? `UDI-DI ${m.udi_di}` : 'UDI-DI 未登记'
        return `${m ? m.code : r.material_id}（${udi} / 批次 ${b?.batch_no || r.batch_id}）`
      })
    ElMessage({
      type: 'success',
      message: infos.length ? `出库成功：${infos.join('；')}` : '出库成功',
      duration: 6000,
    })
  }
  inOutVisible.value = false
  detail.value = await request.get(`${base.value}/${detail.value.id}`)
  load()
}

onMounted(async () => {
  load()
  const [ps, ms, ws] = await Promise.all([
    request.get('/partners', { params: { page_size: 100 } }),
    request.get('/materials', { params: { page_size: 100 } }),
    request.get('/warehouses', { params: { page_size: 100 } }),
  ])
  partners.value = ps.items
  materials.value = ms.items
  warehouses.value = ws.items
})
</script>

<template>
  <el-card>
    <div class="toolbar">
      <el-select v-model="query.status" clearable placeholder="状态" style="width: 130px" @change="onSearch">
        <el-option label="草稿" value="draft" />
        <el-option label="已审核" value="approved" />
        <el-option label="部分完成" value="partial" />
        <el-option label="已完成" value="completed" />
        <el-option label="已关闭" value="closed" />
        <el-option label="已作废" value="void" />
      </el-select>
      <el-input v-model="query.keyword" placeholder="单号搜索" clearable style="width: 200px" @keyup.enter="onSearch" />
      <el-button type="primary" @click="onSearch">搜索</el-button>
      <span class="spacer" />
      <el-button v-if="canCreate" type="primary" @click="openCreate">新建{{ isPO ? '采购' : '销售' }}订单</el-button>
    </div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="doc_no" label="单号" min-width="130" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }"><StatusTag :status="row.status" /></template>
      </el-table-column>
      <el-table-column label="往来单位" min-width="130">
        <template #default="{ row }">{{ pname(row.partner_id) }}</template>
      </el-table-column>
      <el-table-column prop="order_date" label="订单日期" width="110" />
      <el-table-column v-if="isPO" prop="expected_date" label="期望到货" width="110" />
      <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip />
      <el-table-column prop="created_by" label="创建人" width="90" />
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
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

    <!-- 新建对话框 -->
    <el-dialog v-model="createVisible" :title="`新建${isPO ? '采购' : '销售'}订单`" width="780px" top="6vh">
      <el-form ref="createRef" :model="createForm" :rules="createRules" label-width="90px">
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="往来单位" prop="partner_id">
              <el-select v-model="createForm.partner_id" filterable style="width: 100%">
                <el-option v-for="p in partners" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="订单日期" prop="order_date">
              <el-date-picker v-model="createForm.order_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col v-if="isPO" :span="8">
            <el-form-item label="期望到货">
              <el-date-picker v-model="createForm.expected_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="createForm.remark" />
        </el-form-item>
        <el-form-item label="明细">
          <el-table :data="createForm.lines" border size="small" style="width: 100%">
            <el-table-column label="物料" min-width="180">
              <template #default="{ row }">
                <el-select v-model="row.material_id" filterable size="small">
                  <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="数量" width="120">
              <template #default="{ row }">
                <el-input-number v-model="row.qty" :min="0.0001" :precision="4" :controls="false" size="small" style="width: 100%" />
              </template>
            </el-table-column>
            <el-table-column label="单价" width="120">
              <template #default="{ row }">
                <el-input-number v-model="row.unit_price" :min="0" :precision="4" :controls="false" size="small" style="width: 100%" />
              </template>
            </el-table-column>
            <el-table-column label="仓库" min-width="140">
              <template #default="{ row }">
                <el-select v-model="row.warehouse_id" size="small">
                  <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="" width="60">
              <template #default="{ $index }">
                <el-button link type="danger" size="small" @click="removeLine($index)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-button size="small" style="margin-top: 8px" @click="addLine">添加行</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">提交</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" :title="detail?.doc_no || '详情'" size="62%">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <div class="detail-head">
            <el-descriptions :column="3" size="small" border>
              <el-descriptions-item label="单号">{{ detail.doc_no }}</el-descriptions-item>
              <el-descriptions-item label="状态"><StatusTag :status="detail.status" /></el-descriptions-item>
              <el-descriptions-item label="往来单位">{{ pname(detail.partner_id) }}</el-descriptions-item>
              <el-descriptions-item label="订单日期">{{ detail.order_date }}</el-descriptions-item>
              <el-descriptions-item v-if="isPO" label="期望到货">{{ detail.expected_date || '-' }}</el-descriptions-item>
              <el-descriptions-item label="创建人">{{ detail.created_by || '-' }}</el-descriptions-item>
              <el-descriptions-item label="备注" :span="3">{{ detail.remark || '-' }}</el-descriptions-item>
            </el-descriptions>
            <div class="actions">
              <el-button v-if="canApprove(detail.status) && actionAuth.approve.value" type="primary" size="small" @click="doAction('approve')">审核</el-button>
              <el-button v-if="canInOut(detail.status) && actionAuth.inOut.value" type="success" size="small" @click="openInOut">{{ inOutLabel }}</el-button>
              <el-button v-if="canClose(detail.status) && actionAuth.close.value" type="warning" size="small" @click="doAction('close')">关闭</el-button>
              <el-button v-if="canVoid(detail.status) && actionAuth.void.value" type="danger" size="small" @click="doAction('void')">作废</el-button>
            </div>
          </div>

          <el-tabs>
            <el-tab-pane :label="`${inOutLabel}明细`">
              <el-table :data="detail.lines" border size="small">
                <el-table-column label="物料" min-width="150">
                  <template #default="{ row }">{{ mlabel(row.material_id) }}</template>
                </el-table-column>
                <el-table-column label="数量" align="right" width="110">
                  <template #default="{ row }">{{ fmtMoney(row.qty) }}</template>
                </el-table-column>
                <el-table-column label="单价" align="right" width="110">
                  <template #default="{ row }">{{ fmtMoney(row.unit_price) }}</template>
                </el-table-column>
                <el-table-column label="金额" align="right" width="120">
                  <template #default="{ row }">{{ lineAmount(row) }}</template>
                </el-table-column>
                <el-table-column :label="isPO ? '已收数量' : '已发数量'" align="right" width="110">
                  <template #default="{ row }">{{ fmtMoney(row[qtyField]) }}</template>
                </el-table-column>
                <el-table-column label="仓库" width="110">
                  <template #default="{ row }">{{ wname(row.warehouse_id) }}</template>
                </el-table-column>
              </el-table>
              <div class="sum">金额合计：{{ fmtMoney(detailQtyTotal) }}</div>
            </el-tab-pane>
            <el-tab-pane label="审计留痕">
              <el-table :data="detail.audits || []" border size="small">
                <el-table-column label="时间" width="160">
                  <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                </el-table-column>
                <el-table-column prop="action" label="动作" width="80" />
                <el-table-column prop="actor" label="操作人" width="90" />
                <el-table-column prop="field" label="字段" width="110" />
                <el-table-column label="变更">
                  <template #default="{ row }">{{ row.old_value ?? '-' }} → {{ row.new_value ?? '-' }}</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </template>
      </div>
    </el-drawer>

    <!-- 入库/出库弹窗：行级分批录入数量 -->
    <el-dialog v-model="inOutVisible" :title="`${inOutLabel}录入`" width="640px">
      <el-table :data="inOutRows" border size="small">
        <el-table-column label="物料" min-width="150">
          <template #default="{ row }">{{ mlabel(row.material_id) }}</template>
        </el-table-column>
        <el-table-column label="订单数量" align="right" width="100">
          <template #default="{ row }">{{ fmtMoney(row.qty) }}</template>
        </el-table-column>
        <el-table-column :label="isPO ? '已收' : '已发'" align="right" width="100">
          <template #default="{ row }">{{ fmtMoney(row.done) }}</template>
        </el-table-column>
        <el-table-column label="剩余" align="right" width="100">
          <template #default="{ row }">{{ fmtMoney(row.remaining) }}</template>
        </el-table-column>
        <el-table-column :label="`本次${inOutLabel}`" width="140">
          <template #default="{ row }">
            <el-input-number v-model="row.input_qty" :min="0" :max="row.remaining" :precision="4" :controls="false" size="small" style="width: 100%" :disabled="row.remaining <= 0" />
          </template>
        </el-table-column>
        <el-table-column label="仓库" width="130">
          <template #default="{ row }">
            <el-select v-model="row.warehouse_id" size="small">
              <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column v-if="isPO" label="批次号" width="150">
          <template #default="{ row }">
            <el-input
              v-if="row.needBatch"
              v-model="row.batch_no"
              size="small"
              placeholder="必填"
            />
            <span v-else style="color:#999">—</span>
          </template>
        </el-table-column>
        <el-table-column v-else label="出库批次" width="170">
          <template #default="{ row }">
            <el-select
              v-if="row.needBatch"
              v-model="row.batch_id"
              size="small"
              filterable
              placeholder="选合格批次"
            >
              <el-option
                v-for="b in batchOptions[row.material_id] || []"
                :key="b.id"
                :label="b.batch_no"
                :value="b.id"
              />
            </el-select>
            <span v-else style="color:#999">—</span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="inOutVisible = false">取消</el-button>
        <el-button type="primary" @click="submitInOut">确认{{ inOutLabel }}</el-button>
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
.detail-head {
  margin-bottom: 12px;
}
.actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
.sum {
  margin-top: 8px;
  text-align: right;
  font-weight: 600;
}
</style>
