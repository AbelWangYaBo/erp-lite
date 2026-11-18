<script setup>
// 生产工单：列表+详情（下达/领料/完工入库/关闭，累计领料成本与完工量）
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import StatusTag from '@/components/StatusTag.vue'
import { useAuthStore } from '@/stores/auth'
import { fmtMoney, fmtTime } from '@/utils/format'

const auth = useAuthStore()

const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20, keyword: '', status: '' })
const materials = ref([])
const warehouses = ref([])
const mlabel = (id) => {
  const m = materials.value.find((x) => x.id === id)
  return m ? `${m.code} ${m.name}` : id
}
const wname = (id) => warehouses.value.find((w) => w.id === id)?.name || id

async function load() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.keyword) params.keyword = query.keyword
    if (query.status) params.status = query.status
    const d = await request.get('/manufacturing-orders', { params })
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

// ---------- 新建 ----------
const createVisible = ref(false)
const createRef = ref()
const createForm = reactive({ material_id: null, planned_qty: 1, warehouse_id: null, remark: '' })
const createRules = {
  material_id: [{ required: true, message: '请选择生产物料', trigger: 'change' }],
  warehouse_id: [{ required: true, message: '请选择入库仓库', trigger: 'change' }],
}

function openCreate() {
  createForm.material_id = null
  createForm.planned_qty = 1
  createForm.warehouse_id = null
  createForm.remark = ''
  createVisible.value = true
}

async function submitCreate() {
  await createRef.value.validate()
  await request.post('/manufacturing-orders', {
    material_id: createForm.material_id,
    planned_qty: createForm.planned_qty,
    warehouse_id: createForm.warehouse_id,
    remark: createForm.remark || null,
  })
  ElMessage.success('工单已创建（草稿）：请下达后领料生产')
  createVisible.value = false
  load()
}

// ---------- 详情 ----------
const detailVisible = ref(false)
const detail = ref(null)
const detailLoading = ref(false)

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await request.get(`/manufacturing-orders/${row.id}`)
    loadAudits(row.id)
  } finally {
    detailLoading.value = false
  }
}

// v3 修复：MoOut 已返回 received_qty/completed_qty（原字段不存在恒显示 0）
const receivedQty = computed(() => {
  const mo = detail.value
  if (!mo) return null
  return mo.received_qty ?? mo.completed_qty ?? 0
})

// ---------- 状态动作 ----------
const canRelease = (s) => s === 'draft'
const canIssue = (s) => ['approved', 'partial'].includes(s)
const canReceive = (s) => ['approved', 'partial'].includes(s)
const canClose = (s) => ['approved', 'partial', 'completed'].includes(s)
const canVoid = (s) => s === 'draft'
const canReject = (s) => s === 'approved'

const ACTION_TEXT = { approve: '下达', void: '作废', close: '关闭' }
// 按钮级权限（PRD-v2 §2.5：MO 建改审领产 production/admin）
const canCreate = computed(() => auth.has('mo.create'))
const actionAuth = {
  approve: computed(() => auth.has('mo.release')),
  issue: computed(() => auth.has('mo.issue')),
  unissue: computed(() => auth.has('mo.unissue')),
  receive: computed(() => auth.has('mo.receive')),
  close: computed(() => auth.has('mo.close')),
  void: computed(() => auth.has('mo.void')),
  reject: computed(() => auth.has('mo.reject')),
}

async function doAction(action) {
  const text = ACTION_TEXT[action]
  await ElMessageBox.confirm(`确认${text}该生产工单？`, '操作确认', { type: 'warning' })
  await request.post(`/manufacturing-orders/${detail.value.id}/${action}`)
  ElMessage.success(`${text}成功${action === 'void' ? '：工单已关闭留痕' : ''}`)
  detail.value = await request.get(`/manufacturing-orders/${detail.value.id}`)
  load()
}

// ---------- 拒绝（approved → draft，原因留痕；后端校验无领料记录才允许） ----------
async function doReject() {
  const { value } = await ElMessageBox.prompt('拒绝后工单退回草稿，创建人可修改后重新下达', '拒绝生产工单', {
    type: 'warning',
    inputPlaceholder: '请输入拒绝原因（必填）',
    inputValidator: (v) => (v && v.trim() ? true : '拒绝原因不能为空'),
  })
  await request.post(`/manufacturing-orders/${detail.value.id}/reject`, { reason: value.trim() })
  ElMessage.success('已退回草稿：创建人可修改后重新提交下达')
  detail.value = await request.get(`/manufacturing-orders/${detail.value.id}`)
  load()
}

// ---------- 领料 ----------
const issueVisible = ref(false)
const issueRows = ref([])
// material_id → qualified 批次下拉（批次物料必须逐行指定批次）
const issueBatchOptions = ref({})
const blankIssue = () => ({ material_id: null, qty: 1, needBatch: false, batch_id: null })

function openIssue() {
  issueRows.value = [blankIssue()]
  issueBatchOptions.value = {}
  issueVisible.value = true
}

function addIssue() {
  issueRows.value.push(blankIssue())
}

async function onIssueMaterialChange(row) {
  const m = materials.value.find((x) => x.id === row.material_id)
  row.needBatch = !!m?.batch_required
  row.batch_id = null
  if (!row.needBatch) return
  try {
    const d = await request.get('/batches', {
      params: { material_id: row.material_id, insp_status: 'qualified', page: 1, page_size: 100 },
      silent: true,
    })
    issueBatchOptions.value[row.material_id] = d.items
  } catch {
    issueBatchOptions.value[row.material_id] = []
  }
}

async function submitIssue() {
  const body = issueRows.value.filter((r) => r.material_id && Number(r.qty) > 0)
  if (!body.length) {
    ElMessage.warning('请至少填写一行领料明细')
    return
  }
  // 批次物料必须指定合格批次（后端放行拦截：仅 qualified 可领）
  const missing = body.find((r) => r.needBatch && !r.batch_id)
  if (missing) {
    ElMessage.warning(`物料「${mlabel(missing.material_id)}」已启用批次管理，领料必须选择合格批次`)
    return
  }
  await request.post(`/manufacturing-orders/${detail.value.id}/issue`, {
    lines: body.map((r) => ({
      material_id: r.material_id,
      qty: r.qty,
      batch_id: r.needBatch ? r.batch_id : null,
    })),
  })
  ElMessage.success('领料成功：物料已出库计入工单成本，可完工入库或继续领料')
  issueVisible.value = false
  detail.value = await request.get(`/manufacturing-orders/${detail.value.id}`)
  load()
}

// ---------- 领料退回（mo.unissue：按 物料+批次 退回指定数量，红字冲回） ----------
const unissueVisible = ref(false)
const unissueForm = reactive({ material_id: null, qty: 1, batch_id: null })

function openUnissue(row) {
  unissueForm.material_id = row.material_id
  unissueForm.qty = 1
  unissueForm.batch_id = null
  unissueVisible.value = true
}

async function submitUnissue() {
  if (!unissueForm.qty || Number(unissueForm.qty) <= 0) {
    ElMessage.warning('请输入退回数量')
    return
  }
  const m = materials.value.find((x) => x.id === unissueForm.material_id)
  if (m?.batch_required && !unissueForm.batch_id) {
    ElMessage.warning(`物料「${mlabel(unissueForm.material_id)}」已启用批次管理，退回必须填写批次ID`)
    return
  }
  await request.post(`/manufacturing-orders/${detail.value.id}/unissue`, {
    material_id: unissueForm.material_id,
    qty: unissueForm.qty,
    batch_id: unissueForm.batch_id || null,
  })
  ElMessage.success('退料成功：成本已按原领料单价冲回，批次现存回增，可重新领料')
  unissueVisible.value = false
  detail.value = await request.get(`/manufacturing-orders/${detail.value.id}`)
  load()
}

// ---------- 完工入库 ----------
const receiveVisible = ref(false)
const receiveForm = reactive({ qty: 1, extra_cost: 0 })

function openReceive() {
  // v3 修复：默认值 = 计划量 − 已完工量（原恒按计划量，二次完工必超发）
  const remain = Number(detail.value.planned_qty) - Number(receivedQty.value ?? 0)
  receiveForm.qty = remain > 0 ? remain : 0
  receiveForm.extra_cost = 0
  receiveVisible.value = true
}

async function submitReceive() {
  if (!receiveForm.qty || Number(receiveForm.qty) <= 0) {
    ElMessage.warning('请输入完工入库数量')
    return
  }
  await request.post(`/manufacturing-orders/${detail.value.id}/receive`, {
    qty: receiveForm.qty,
    extra_cost: receiveForm.extra_cost || 0,
  })
  ElMessage.success(
    `完工 ${fmtMoney(receiveForm.qty)} 入库，已生成待检批次（qc 录入检验 → qa 判定放行）`,
  )
  receiveVisible.value = false
  detail.value = await request.get(`/manufacturing-orders/${detail.value.id}`)
  load()
}

// ---------- 审计留痕（/audit/records 按 MO 过滤） ----------
const audits = ref([])
const auditsLoading = ref(false)

const ACTION_LABELS = {
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

async function loadAudits(moId) {
  auditsLoading.value = true
  try {
    const d = await request.get('/audit/records', {
      params: { doc_type: 'manufacturing_order', doc_id: moId, page: 1, page_size: 100 },
      silent: true,
    })
    audits.value = d.items || []
  } catch {
    audits.value = []
  } finally {
    auditsLoading.value = false
  }
}

onMounted(async () => {
  load()
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
    <div class="toolbar">
      <el-select v-model="query.status" clearable placeholder="状态" style="width: 130px" @change="onSearch">
        <el-option label="草稿" value="draft" />
        <el-option label="已下达" value="approved" />
        <el-option label="部分完工" value="partial" />
        <el-option label="已完成" value="completed" />
        <el-option label="已关闭" value="closed" />
        <el-option label="已作废" value="void" />
      </el-select>
      <el-input v-model="query.keyword" placeholder="工单号搜索" clearable style="width: 200px" @keyup.enter="onSearch" />
      <el-button type="primary" @click="onSearch">搜索</el-button>
      <span class="spacer" />
      <el-button v-if="canCreate" type="primary" @click="openCreate">新建生产工单</el-button>
    </div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="doc_no" label="工单号" min-width="130" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }"><StatusTag :status="row.status" /></template>
      </el-table-column>
      <el-table-column label="生产物料" min-width="150">
        <template #default="{ row }">{{ mlabel(row.material_id) }}</template>
      </el-table-column>
      <el-table-column label="计划数量" align="right" width="100">
        <template #default="{ row }">{{ fmtMoney(row.planned_qty) }}</template>
      </el-table-column>
      <el-table-column label="完工数量" align="right" width="100">
        <template #default="{ row }">{{ fmtMoney(row.received_qty ?? row.completed_qty ?? 0) }}</template>
      </el-table-column>
      <el-table-column label="累计领料成本" align="right" width="120">
        <template #default="{ row }">{{ fmtMoney(row.issued_qty_cost ?? row.issue_cost ?? 0) }}</template>
      </el-table-column>
      <el-table-column label="其他费用" align="right" width="100">
        <template #default="{ row }">{{ fmtMoney(row.extra_cost ?? 0) }}</template>
      </el-table-column>
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

    <!-- 新建 -->
    <el-dialog v-model="createVisible" title="新建生产工单" width="460px">
      <el-form ref="createRef" :model="createForm" :rules="createRules" label-width="90px">
        <el-form-item label="生产物料" prop="material_id">
          <el-select v-model="createForm.material_id" filterable style="width: 100%">
            <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="计划数量" prop="planned_qty">
          <el-input-number v-model="createForm.planned_qty" :min="0.0001" :precision="4" :controls="false" style="width: 100%" />
        </el-form-item>
        <el-form-item label="入库仓库" prop="warehouse_id">
          <el-select v-model="createForm.warehouse_id" style="width: 100%">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.remark" placeholder="工单备注（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">提交</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" :title="detail?.doc_no || '工单详情'" size="55%">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-alert
            v-if="detail.status === 'draft' && detail.reject_reason"
            :title="`上次下达拒绝原因：${detail.reject_reason}`"
            type="error"
            :closable="false"
            style="margin-bottom: 12px"
          />
          <el-descriptions :column="3" size="small" border>
            <el-descriptions-item label="工单号">{{ detail.doc_no }}</el-descriptions-item>
            <el-descriptions-item label="状态"><StatusTag :status="detail.status" /></el-descriptions-item>
            <el-descriptions-item label="生产物料">{{ mlabel(detail.material_id) }}</el-descriptions-item>
            <el-descriptions-item label="计划数量">{{ fmtMoney(detail.planned_qty) }}</el-descriptions-item>
            <el-descriptions-item label="完工数量">{{ fmtMoney(receivedQty ?? 0) }}</el-descriptions-item>
            <el-descriptions-item label="入库仓库">{{ wname(detail.warehouse_id) }}</el-descriptions-item>
            <el-descriptions-item label="累计领料成本">{{ fmtMoney(detail.issued_qty_cost ?? 0) }}</el-descriptions-item>
            <el-descriptions-item label="人工/制费">{{ fmtMoney(detail.extra_cost ?? 0) }}</el-descriptions-item>
          </el-descriptions>
          <div class="actions">
            <el-button v-if="canRelease(detail.status) && actionAuth.approve.value" type="primary" size="small" @click="doAction('approve')">下达</el-button>
            <el-button v-if="canIssue(detail.status) && actionAuth.issue.value" type="success" size="small" @click="openIssue">领料</el-button>
            <el-button v-if="canReceive(detail.status) && actionAuth.receive.value" type="success" size="small" @click="openReceive">完工入库</el-button>
            <el-button v-if="canReject(detail.status) && actionAuth.reject.value" type="warning" size="small" @click="doReject">拒绝</el-button>
            <el-button v-if="canClose(detail.status) && actionAuth.close.value" type="warning" size="small" @click="doAction('close')">关闭</el-button>
            <el-button v-if="canVoid(detail.status) && actionAuth.void.value" type="danger" size="small" @click="doAction('void')">作废</el-button>
          </div>

          <el-tabs>
            <el-tab-pane label="领料记录">
              <el-table :data="detail.issues || detail.issue_records || []" border size="small">
                <el-table-column label="物料" min-width="160">
                  <template #default="{ row }">{{ mlabel(row.material_id) }}</template>
                </el-table-column>
                <el-table-column label="数量" align="right" width="110">
                  <template #default="{ row }">{{ fmtMoney(row.qty) }}</template>
                </el-table-column>
                <el-table-column label="成本金额" align="right" width="120">
                  <template #default="{ row }">{{ fmtMoney(row.cost_amount) }}</template>
                </el-table-column>
                <el-table-column v-if="canIssue(detail.status) && actionAuth.unissue.value" label="操作" width="80" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="warning" size="small" @click="openUnissue(row)">退回</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane label="审计留痕">
              <el-table :data="audits" border size="small" v-loading="auditsLoading">
                <el-table-column label="时间" width="160">
                  <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                </el-table-column>
                <el-table-column label="动作" width="100">
                  <template #default="{ row }">{{ ACTION_LABELS[row.action] || row.action }}</template>
                </el-table-column>
                <el-table-column prop="actor" label="操作人" width="90" />
                <el-table-column prop="role" label="角色" width="110">
                  <template #default="{ row }">{{ row.role || '-' }}</template>
                </el-table-column>
                <el-table-column prop="field" label="字段" width="110" />
                <el-table-column label="变更" min-width="160">
                  <template #default="{ row }">{{ row.old_value ?? '-' }} → {{ row.new_value ?? '-' }}</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </template>
      </div>
    </el-drawer>

    <!-- 领料弹窗 -->
    <el-dialog v-model="issueVisible" title="生产领料" width="560px">
      <el-table :data="issueRows" border size="small">
        <el-table-column label="物料" min-width="220">
          <template #default="{ row }">
            <el-select v-model="row.material_id" filterable size="small" @change="onIssueMaterialChange(row)">
              <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="数量" width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.qty" :min="0.0001" :precision="4" :controls="false" size="small" style="width: 100%" />
          </template>
        </el-table-column>
        <el-table-column label="领料批次" width="190">
          <template #default="{ row }">
            <el-select
              v-if="row.needBatch"
              v-model="row.batch_id"
              filterable
              size="small"
              placeholder="选合格批次"
            >
              <el-option
                v-for="b in issueBatchOptions[row.material_id] || []"
                :key="b.id"
                :label="b.batch_no"
                :value="b.id"
              />
            </el-select>
            <span v-else style="color:#999">—</span>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 8px">
        <el-button size="small" @click="addIssue">添加行</el-button>
      </div>
      <template #footer>
        <el-button @click="issueVisible = false">取消</el-button>
        <el-button type="primary" @click="submitIssue">确认领料</el-button>
      </template>
    </el-dialog>

    <!-- 完工入库弹窗 -->
    <el-dialog v-model="receiveVisible" title="完工入库" width="420px">
      <el-form label-width="90px">
        <el-form-item label="入库数量">
          <el-input-number v-model="receiveForm.qty" :min="0.0001" :precision="4" :controls="false" style="width: 100%" />
        </el-form-item>
        <el-form-item label="其他费用">
          <el-input-number v-model="receiveForm.extra_cost" :min="0" :precision="4" :controls="false" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="receiveVisible = false">取消</el-button>
        <el-button type="primary" @click="submitReceive">确认入库</el-button>
      </template>
    </el-dialog>

    <!-- 领料退回弹窗 -->
    <el-dialog v-model="unissueVisible" title="领料退回" width="420px">
      <el-form label-width="90px">
        <el-form-item label="物料">
          <span>{{ mlabel(unissueForm.material_id) }}</span>
        </el-form-item>
        <el-form-item label="退回数量">
          <el-input-number v-model="unissueForm.qty" :min="0.0001" :precision="4" :controls="false" style="width: 100%" />
        </el-form-item>
        <el-form-item label="批次ID">
          <el-input-number v-model="unissueForm.batch_id" :min="1" :precision="0" :controls="false" style="width: 100%" placeholder="批次物料必填" />
          <div style="color:#999;font-size:12px">非批次物料可留空；批次物料请填写原领料批次ID</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="unissueVisible = false">取消</el-button>
        <el-button type="primary" @click="submitUnissue">确认退回</el-button>
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
.actions {
  margin: 12px 0;
  display: flex;
  gap: 8px;
}
.section-title {
  font-weight: 600;
  margin: 12px 0 8px;
}
</style>
