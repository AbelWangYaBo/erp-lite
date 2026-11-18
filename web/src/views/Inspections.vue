<script setup>
// 检验工作台（v3 双段制）：qc 录入（batch.inspect）→ qa 判定（batch.judge）→ 不合格处置（batch.dispose）→ 历史记录
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'
import DocLink from '@/components/DocLink.vue'
import { useAuthStore } from '@/stores/auth'
import { INSP_STATUS_META } from '@/constants'
import { fmtMoney, fmtTime } from '@/utils/format'

const auth = useAuthStore()
const canInspect = computed(() => auth.has('batch.inspect'))
const canJudge = computed(() => auth.has('batch.judge'))
const canDispose = computed(() => auth.has('batch.dispose'))

const activeTab = ref(
  canInspect.value ? 'inspect' : canJudge.value ? 'judge' : canDispose.value ? 'rejected' : 'history',
)
const materials = ref([])
const mlabel = (id) => {
  const m = materials.value.find((x) => x.id === id)
  return m ? `${m.code} ${m.name}` : id
}
const statusMeta = (s) => INSP_STATUS_META[s] || { label: s, type: 'info' }
const CONCLUSION_LABELS = { pass: '合格', fail: '不合格' }
const conclusionMeta = (c) => (c === 'pass' ? 'success' : c === 'fail' ? 'danger' : 'warning')

async function fetchPendingBatches() {
  const d = await request.get('/batches', { params: { insp_status: 'pending', page: 1, page_size: 100 } })
  return Promise.all(
    d.items.map((b) => request.get(`/batches/${b.id}`, { silent: true }).catch(() => b)),
  )
}

// ---------- tab1：待录入检验（qc） ----------
const pendingList = ref([])
const pendingLoading = ref(false)

async function loadInspectList() {
  pendingLoading.value = true
  try {
    pendingList.value = await fetchPendingBatches()
  } finally {
    pendingLoading.value = false
  }
}

// 录入弹窗：检项动态行 + 建议结论 + 备注
const inspectVisible = ref(false)
const inspectBatch = ref(null)
const inspectForm = reactive({ items: [{ name: '', result: '' }], suggest_conclusion: 'pass', remark: '' })

async function openInspect(row) {
  // 已有未判定记录时后端会 400，先前端拦截提示，少走一步
  try {
    const recs = await request.get(`/batches/${row.id}/inspections`, { silent: true })
    if ((recs || []).some((r) => r.conclusion == null)) {
      ElMessage.warning('该批次已有待判定检验记录，请等待 qa 在「待判定」tab 复核')
      return
    }
  } catch {
    // 拉取失败不阻断，交由后端提交时校验
  }
  inspectBatch.value = row
  inspectForm.items = [{ name: '', result: '' }]
  inspectForm.suggest_conclusion = 'pass'
  inspectForm.remark = ''
  inspectVisible.value = true
}

function addInspectItem() {
  inspectForm.items.push({ name: '', result: '' })
}

async function submitInspect() {
  const items = inspectForm.items.filter((i) => i.name && i.result)
  if (!items.length) {
    ElMessage.warning('请至少填写一项检项（名称与结果）')
    return
  }
  await request.post(`/batches/${inspectBatch.value.id}/inspect`, {
    items,
    suggest_conclusion: inspectForm.suggest_conclusion,
    remark: inspectForm.remark || null,
  })
  ElMessage.success('检验记录已录入，批次保持待检，请通知 qa 在「待判定」tab 复核判定')
  inspectVisible.value = false
  loadInspectList()
}

// ---------- tab2：待判定（qa） ----------
const judgeList = ref([])
const judgeLoading = ref(false)

async function loadJudgeList() {
  judgeLoading.value = true
  try {
    judgeList.value = await fetchPendingBatches()
  } finally {
    judgeLoading.value = false
  }
}

// 判定弹窗：先拉检验记录展示检项 + 建议，再选结论
const judgeVisible = ref(false)
const judgeBatch = ref(null)
const judgeRecord = ref(null)
const judgeForm = reactive({ conclusion: 'pass' })

async function openJudge(row) {
  let recs = []
  try {
    recs = await request.get(`/batches/${row.id}/inspections`, { silent: true })
  } catch {
    recs = []
  }
  const pendingRec = (recs || []).find((r) => r.conclusion == null)
  if (!pendingRec) {
    ElMessage.warning('该批次暂无待判定检验记录，请先由 qc 录入检验记录')
    return
  }
  judgeBatch.value = row
  judgeRecord.value = pendingRec
  judgeForm.conclusion = pendingRec.suggest_conclusion === 'fail' ? 'fail' : 'pass'
  judgeVisible.value = true
}

async function submitJudge() {
  await request.post(`/batches/${judgeBatch.value.id}/judge`, { conclusion: judgeForm.conclusion })
  ElMessage.success(
    judgeForm.conclusion === 'pass'
      ? '判定合格，批次已放行：warehouse 可出库 / production 可领料'
      : '判定不合格，批次已拦截，请到「不合格处置」tab 进行退货或销毁',
  )
  judgeVisible.value = false
  loadJudgeList()
}

const recItemsText = (r) => (r.items || []).map((i) => `${i.name}：${i.result}`).join('；')

// ---------- tab3：不合格处置（qa） ----------
const rejectedList = ref([])
const rejectedLoading = ref(false)

async function loadRejected() {
  rejectedLoading.value = true
  try {
    const d = await request.get('/batches', { params: { insp_status: 'rejected', page: 1, page_size: 100 } })
    rejectedList.value = await Promise.all(
      d.items.map((b) => request.get(`/batches/${b.id}`, { silent: true }).catch(() => b)),
    )
  } finally {
    rejectedLoading.value = false
  }
}

// 处置弹窗（选择退货/销毁）
const disposeVisible = ref(false)
const disposeBatch = ref(null)
const disposeForm = reactive({ disposition: 'return', remark: '' })

function openDisposeDialog(row) {
  disposeBatch.value = row
  disposeForm.disposition = 'return'
  disposeForm.remark = ''
  disposeVisible.value = true
}

async function submitDispose() {
  const res = await request.post(`/batches/${disposeBatch.value.id}/dispose`, {
    disposition: disposeForm.disposition,
    remark: disposeForm.remark || null,
  })
  ElMessage.success(
    disposeForm.disposition === 'return'
      ? '已生成采购退货单，应付已冲减（finance 应付账款可查），批次已关闭留痕'
      : `已报废，现存清零，损失 ${fmtMoney(res?.loss_amount ?? 0)}，批次已关闭留痕`,
  )
  disposeVisible.value = false
  loadRejected()
}

// ---------- tab4：历史检验记录（含检项明细展开） ----------
const records = ref([])
const recordsLoading = ref(false)
const filteredRecords = computed(() =>
  records.value.filter((r) => ['inspection', 'judge', 'dispose'].includes(r.action)),
)
const inspCache = ref({}) // batch_id → 检验记录（含检项明细）
const inspLoading = ref({})

async function loadRecords() {
  recordsLoading.value = true
  try {
    const d = await request.get('/audit/records', { params: { doc_type: 'batch', page: 1, page_size: 100 } })
    records.value = d.items || []
  } finally {
    recordsLoading.value = false
  }
}

async function onExpandRow(row, expandedRows) {
  if (!expandedRows.includes(row) || inspCache.value[row.doc_id]) return
  inspLoading.value = { ...inspLoading.value, [row.doc_id]: true }
  try {
    const recs = await request.get(`/batches/${row.doc_id}/inspections`, { silent: true })
    inspCache.value = { ...inspCache.value, [row.doc_id]: recs || [] }
  } catch {
    inspCache.value = { ...inspCache.value, [row.doc_id]: [] }
  } finally {
    inspLoading.value = { ...inspLoading.value, [row.doc_id]: false }
  }
}

const ACTION_LABELS = { inspection: '检验录入', judge: '复核判定', dispose: '不合格处置' }

function onTab(tab) {
  if (tab === 'inspect') loadInspectList()
  else if (tab === 'judge') loadJudgeList()
  else if (tab === 'rejected') loadRejected()
  else loadRecords()
}

onMounted(() => {
  if (activeTab.value === 'history') loadRecords()
  else if (activeTab.value === 'rejected') loadRejected()
  else if (activeTab.value === 'judge') loadJudgeList()
  else loadInspectList()
})
onMounted(async () => {
  const ms = await request.get('/materials', { params: { page_size: 100 } })
  materials.value = ms.items
})
</script>

<template>
  <el-card>
    <el-tabs v-model="activeTab" @tab-change="onTab">
      <el-tab-pane v-if="canInspect" label="待录入检验" name="inspect">
        <el-table :data="pendingList" border size="small" v-loading="pendingLoading">
          <el-table-column prop="batch_no" label="批次号" min-width="150" />
          <el-table-column label="物料" min-width="170">
            <template #default="{ row }">{{ row.material_code || mlabel(row.material_id) }}</template>
          </el-table-column>
          <el-table-column label="现存" align="right" width="110">
            <template #default="{ row }">{{ fmtMoney(row.stock_qty ?? 0) }}</template>
          </el-table-column>
          <el-table-column label="来源单据" min-width="150">
            <template #default="{ row }">
              <DocLink :type="row.source_type" :id="row.source_id" />
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="statusMeta(row.insp_status).type" size="small">{{ statusMeta(row.insp_status).label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-button type="primary" size="small" @click="openInspect(row)">录入检验</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="canJudge" label="待判定" name="judge">
        <el-table :data="judgeList" border size="small" v-loading="judgeLoading">
          <el-table-column prop="batch_no" label="批次号" min-width="150" />
          <el-table-column label="物料" min-width="170">
            <template #default="{ row }">{{ row.material_code || mlabel(row.material_id) }}</template>
          </el-table-column>
          <el-table-column label="现存" align="right" width="110">
            <template #default="{ row }">{{ fmtMoney(row.stock_qty ?? 0) }}</template>
          </el-table-column>
          <el-table-column label="来源单据" min-width="150">
            <template #default="{ row }">
              <DocLink :type="row.source_type" :id="row.source_id" />
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="statusMeta(row.insp_status).type" size="small">{{ statusMeta(row.insp_status).label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button type="primary" size="small" @click="openJudge(row)">判定</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="canDispose" label="不合格处置" name="rejected">
        <el-table :data="rejectedList" border size="small" v-loading="rejectedLoading">
          <el-table-column prop="batch_no" label="批次号" min-width="150" />
          <el-table-column label="物料" min-width="170">
            <template #default="{ row }">{{ row.material_code || mlabel(row.material_id) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="statusMeta(row.insp_status).type" size="small">{{ statusMeta(row.insp_status).label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button type="danger" size="small" @click="openDisposeDialog(row)">处置</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="历史检验记录" name="history">
        <el-table :data="filteredRecords" border size="small" v-loading="recordsLoading" @expand-change="onExpandRow">
          <el-table-column type="expand" width="40">
            <template #default="{ row }">
              <div style="padding: 8px 12px" v-loading="!!inspLoading[row.doc_id]">
                <el-table
                  :data="inspCache[row.doc_id] || []"
                  border
                  size="small"
                >
                  <el-table-column label="录入时间" width="160">
                    <template #default="{ r }">{{ fmtTime(r.inspected_at) }}</template>
                  </el-table-column>
                  <el-table-column prop="recorder" label="录入人(qc)" width="100" />
                  <el-table-column label="检项明细" min-width="240">
                    <template #default="{ r }">{{ recItemsText(r) || '-' }}</template>
                  </el-table-column>
                  <el-table-column label="建议结论" width="90">
                    <template #default="{ r }">
                      <el-tag :type="conclusionMeta(r.suggest_conclusion)" size="small">
                        {{ CONCLUSION_LABELS[r.suggest_conclusion] || r.suggest_conclusion }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="判定结论" width="90">
                    <template #default="{ r }">
                      <el-tag v-if="r.conclusion" :type="conclusionMeta(r.conclusion)" size="small">
                        {{ CONCLUSION_LABELS[r.conclusion] || r.conclusion }}
                      </el-tag>
                      <el-tag v-else type="warning" size="small">待判定</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="judge_by" label="判定人(qa)" width="100">
                    <template #default="{ r }">{{ r.judge_by || '-' }}</template>
                  </el-table-column>
                  <el-table-column label="判定时间" width="160">
                    <template #default="{ r }">{{ r.judged_at ? fmtTime(r.judged_at) : '-' }}</template>
                  </el-table-column>
                </el-table>
                <div v-if="!inspLoading[row.doc_id] && !(inspCache[row.doc_id] || []).length" style="color:#999;padding:6px 0">
                  该批次暂无检验记录
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="批次ID" width="90">
            <template #default="{ row }">{{ row.doc_id }}</template>
          </el-table-column>
          <el-table-column label="动作" width="100">
            <template #default="{ row }">{{ ACTION_LABELS[row.action] || row.action }}</template>
          </el-table-column>
          <el-table-column prop="actor" label="操作人" width="100" />
          <el-table-column prop="role" label="角色" width="110">
            <template #default="{ row }">{{ row.role || '-' }}</template>
          </el-table-column>
          <el-table-column prop="field" label="字段" width="120" />
          <el-table-column label="变更" min-width="160">
            <template #default="{ row }">{{ row.old_value ?? '-' }} → {{ row.new_value ?? '-' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 录入检验弹窗（qc）：检项动态行 + 建议结论 + 备注 -->
    <el-dialog v-model="inspectVisible" :title="`录入检验：${inspectBatch?.batch_no || ''}`" width="640px">
      <el-form label-width="90px">
        <el-form-item label="检项记录">
          <div style="width: 100%">
            <el-table :data="inspectForm.items" border size="small">
              <el-table-column label="检项名称" min-width="180">
                <template #default="{ row }">
                  <el-input v-model="row.name" size="small" placeholder="如：外观 / 含量" />
                </template>
              </el-table-column>
              <el-table-column label="检验结果" min-width="180">
                <template #default="{ row }">
                  <el-input v-model="row.result" size="small" placeholder="如：合格 / 10.2mg" />
                </template>
              </el-table-column>
              <el-table-column label="" width="60">
                <template #default="{ $index }">
                  <el-button link type="danger" size="small" :disabled="inspectForm.items.length <= 1" @click="inspectForm.items.splice($index, 1)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-button size="small" style="margin-top: 8px" @click="addInspectItem">添加检项</el-button>
          </div>
        </el-form-item>
        <el-form-item label="建议结论">
          <el-radio-group v-model="inspectForm.suggest_conclusion">
            <el-radio value="pass">建议合格</el-radio>
            <el-radio value="fail">建议不合格</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="inspectForm.remark" type="textarea" :rows="2" placeholder="检验备注（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="inspectVisible = false">取消</el-button>
        <el-button type="primary" @click="submitInspect">提交录入</el-button>
      </template>
    </el-dialog>

    <!-- 判定弹窗（qa）：展示 qc 检项与建议 → 复核结论 -->
    <el-dialog v-model="judgeVisible" :title="`复核判定：${judgeBatch?.batch_no || ''}`" width="640px">
      <el-descriptions v-if="judgeRecord" :column="2" size="small" border style="margin-bottom: 12px">
        <el-descriptions-item label="录入人(qc)">{{ judgeRecord.recorder || '-' }}</el-descriptions-item>
        <el-descriptions-item label="录入时间">{{ fmtTime(judgeRecord.inspected_at) }}</el-descriptions-item>
        <el-descriptions-item label="检项明细" :span="2">{{ recItemsText(judgeRecord) || '-' }}</el-descriptions-item>
        <el-descriptions-item label="建议结论">
          <el-tag :type="conclusionMeta(judgeRecord.suggest_conclusion)" size="small">
            {{ CONCLUSION_LABELS[judgeRecord.suggest_conclusion] || judgeRecord.suggest_conclusion }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="备注">{{ judgeRecord.remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-form label-width="90px">
        <el-form-item label="判定结论">
          <el-radio-group v-model="judgeForm.conclusion">
            <el-radio value="pass">合格（放行）</el-radio>
            <el-radio value="fail">不合格（拦截）</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="judgeVisible = false">取消</el-button>
        <el-button type="primary" @click="submitJudge">提交判定</el-button>
      </template>
    </el-dialog>

    <!-- 不合格处置弹窗 -->
    <el-dialog v-model="disposeVisible" :title="`不合格处置：${disposeBatch?.batch_no || ''}`" width="460px">
      <el-form label-width="90px">
        <el-form-item label="处置方式">
          <el-radio-group v-model="disposeForm.disposition">
            <el-radio value="return">退货</el-radio>
            <el-radio value="destroy">销毁</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="disposeForm.remark" type="textarea" :rows="2" placeholder="处置备注（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="disposeVisible = false">取消</el-button>
        <el-button type="danger" @click="submitDispose">确认处置</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>
