<script setup>
// 检验工作台（qa：inspect.judge）：待检批次判定 / 不合格批次处置 / 历史检验记录
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import { INSP_STATUS_META } from '@/constants'
import { fmtMoney, fmtTime } from '@/utils/format'

const activeTab = ref('pending')
const materials = ref([])
const mlabel = (id) => {
  const m = materials.value.find((x) => x.id === id)
  return m ? `${m.code} ${m.name}` : id
}
const statusMeta = (s) => INSP_STATUS_META[s] || { label: s, type: 'info' }

async function loadBatches(status) {
  const loading = status === 'pending' ? pendingLoading : rejectedLoading
  const target = status === 'pending' ? pendingList : rejectedList
  loading.value = true
  try {
    const d = await request.get('/batches', { params: { insp_status: status, page: 1, page_size: 100 } })
    const items = await Promise.all(
      d.items.map((b) => request.get(`/batches/${b.id}`, { silent: true }).catch(() => b)),
    )
    target.value = items
  } finally {
    loading.value = false
  }
}

// ---------- 待检批次 ----------
const pendingList = ref([])
const pendingLoading = ref(false)

// ---------- 判定弹窗 ----------
const judgeVisible = ref(false)
const judgeBatch = ref(null)
const judgeForm = reactive({ items: [{ name: '', result: '' }], conclusion: 'pass', remark: '' })

function openJudge(row) {
  judgeBatch.value = row
  judgeForm.items = [{ name: '', result: '' }]
  judgeForm.conclusion = 'pass'
  judgeForm.remark = ''
  judgeVisible.value = true
}

function addJudgeItem() {
  judgeForm.items.push({ name: '', result: '' })
}

async function submitJudge() {
  const items = judgeForm.items.filter((i) => i.name && i.result)
  if (!items.length) {
    ElMessage.warning('请至少填写一项检项（名称与结果）')
    return
  }
  await request.post(`/batches/${judgeBatch.value.id}/inspect`, {
    items,
    conclusion: judgeForm.conclusion,
    remark: judgeForm.remark || null,
  })
  ElMessage.success(judgeForm.conclusion === 'pass' ? '判定合格，批次已放行' : '判定不合格，请进行处置')
  judgeVisible.value = false
  loadBatches('pending')
  if (judgeForm.conclusion === 'fail') loadBatches('rejected')
}

// ---------- 不合格处置 ----------
const rejectedList = ref([])
const rejectedLoading = ref(false)

const DISPOSE_LABELS = { return: '退货', destroy: '销毁' }

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
  await request.post(`/batches/${disposeBatch.value.id}/dispose`, {
    disposition: disposeForm.disposition,
    remark: disposeForm.remark || null,
  })
  ElMessage.success(`已${DISPOSE_LABELS[disposeForm.disposition]}处置，批次关闭留痕`)
  disposeVisible.value = false
  loadBatches('rejected')
}

// ---------- 历史检验记录（审计留痕：inspection/dispose） ----------
const records = ref([])
const recordsLoading = ref(false)
const filteredRecords = computed(() =>
  records.value.filter((r) => ['inspection', 'dispose'].includes(r.action)),
)

async function loadRecords() {
  recordsLoading.value = true
  try {
    const d = await request.get('/audit/records', { params: { doc_type: 'batch', page: 1, page_size: 100 } })
    records.value = d.items || []
  } finally {
    recordsLoading.value = false
  }
}

function onTab(tab) {
  if (tab === 'pending') loadBatches('pending')
  else if (tab === 'rejected') loadBatches('rejected')
  else loadRecords()
}

onMounted(() => loadBatches('pending'))
onMounted(async () => {
  const ms = await request.get('/materials', { params: { page_size: 100 } })
  materials.value = ms.items
})
</script>

<template>
  <el-card>
    <el-tabs v-model="activeTab" @tab-change="onTab">
      <el-tab-pane label="待检批次" name="pending">
        <el-table :data="pendingList" border size="small" v-loading="pendingLoading">
          <el-table-column prop="batch_no" label="批次号" min-width="150" />
          <el-table-column label="物料" min-width="170">
            <template #default="{ row }">{{ row.material_code || mlabel(row.material_id) }}</template>
          </el-table-column>
          <el-table-column label="现存" align="right" width="110">
            <template #default="{ row }">{{ fmtMoney(row.stock_qty ?? 0) }}</template>
          </el-table-column>
          <el-table-column label="来源单据" min-width="140">
            <template #default="{ row }">{{ row.source_type }} #{{ row.source_id }}</template>
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

      <el-tab-pane label="不合格处置" name="rejected">
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
        <el-table :data="filteredRecords" border size="small" v-loading="recordsLoading">
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="批次ID" width="90">
            <template #default="{ row }">{{ row.doc_id }}</template>
          </el-table-column>
          <el-table-column label="动作" width="90">
            <template #default="{ row }">{{ row.action === 'inspection' ? '检验判定' : '不合格处置' }}</template>
          </el-table-column>
          <el-table-column prop="actor" label="操作人" width="100" />
          <el-table-column prop="field" label="字段" width="110" />
          <el-table-column label="变更">
            <template #default="{ row }">{{ row.old_value ?? '-' }} → {{ row.new_value ?? '-' }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 判定弹窗：检项动态行 + 结论 + 备注 -->
    <el-dialog v-model="judgeVisible" :title="`检验判定：${judgeBatch?.batch_no || ''}`" width="640px">
      <el-form label-width="90px">
        <el-form-item label="检项记录">
          <div style="width: 100%">
            <el-table :data="judgeForm.items" border size="small">
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
                  <el-button link type="danger" size="small" :disabled="judgeForm.items.length <= 1" @click="judgeForm.items.splice($index, 1)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-button size="small" style="margin-top: 8px" @click="addJudgeItem">添加检项</el-button>
          </div>
        </el-form-item>
        <el-form-item label="结论">
          <el-radio-group v-model="judgeForm.conclusion">
            <el-radio value="pass">合格（放行）</el-radio>
            <el-radio value="fail">不合格（拦截）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="judgeForm.remark" type="textarea" :rows="2" placeholder="检验备注（可选）" />
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
