<script setup>
// 供应商资质（V2-S4）：列表+新增/编辑（仅 draft，partner_cert.write）+审核（qa/admin：partner_cert.audit）+临期预警
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import { useAuthStore } from '@/stores/auth'
import { CERT_EXPIRING_RED_DAYS, CERT_STATUS_META } from '@/constants'
import { fmtTime, today } from '@/utils/format'

const auth = useAuthStore()
const canWrite = computed(() => auth.has('partner_cert.write'))
const canAudit = computed(() => auth.has('partner_cert.audit'))

const activeTab = ref('all')
const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20, partner_id: null, audit_status: '' })
const partners = ref([])
const suppliers = computed(() => partners.value.filter((p) => p.type === 'supplier'))
const pname = (id) => partners.value.find((p) => p.id === id)?.name || id
const statusMeta = (s) => CERT_STATUS_META[s] || { label: s, type: 'info' }

async function load() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.partner_id) params.partner_id = query.partner_id
    if (query.audit_status) params.audit_status = query.audit_status
    const d = await request.get('/partner-certs', { params })
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

// ---------- 临期预警 ----------
const expiringList = ref([])
const expiringLoading = ref(false)
const expiringDays = ref(30)

async function loadExpiring() {
  expiringLoading.value = true
  try {
    const d = await request.get('/partner-certs/expiring', { params: { days: expiringDays.value } })
    expiringList.value = d.items
  } finally {
    expiringLoading.value = false
  }
}

const daysLeftStyle = (days) =>
  days <= CERT_EXPIRING_RED_DAYS ? 'color:#f56c6c;font-weight:700' : 'color:#e6a23c'

// ---------- 新增/编辑（仅 draft 可改） ----------
const dialogVisible = ref(false)
const formRef = ref()
const editingId = ref(null)
const blank = {
  partner_id: null,
  cert_name: '',
  cert_no: '',
  issuer: '',
  valid_from: today(),
  valid_until: '',
  file_ref: '',
}
const form = reactive({ ...blank })
const rules = {
  partner_id: [{ required: true, message: '请选择供应商', trigger: 'change' }],
  cert_name: [{ required: true, message: '请输入证照名称', trigger: 'blur' }],
  cert_no: [{ required: true, message: '请输入证照编号', trigger: 'blur' }],
  valid_from: [{ required: true, message: '请选择生效日期', trigger: 'change' }],
  valid_until: [{ required: true, message: '请选择有效期至', trigger: 'change' }],
}

function openDialog(row) {
  editingId.value = row ? row.id : null
  Object.assign(form, blank, row ? JSON.parse(JSON.stringify(row)) : {})
  dialogVisible.value = true
}

async function submit() {
  await formRef.value.validate()
  const payload = {
    partner_id: form.partner_id,
    cert_name: form.cert_name,
    cert_no: form.cert_no,
    issuer: form.issuer || null,
    valid_from: form.valid_from,
    valid_until: form.valid_until,
    file_ref: form.file_ref || null,
  }
  if (editingId.value) {
    await request.put(`/partner-certs/${editingId.value}`, payload)
    ElMessage.success('已保存')
  } else {
    await request.post('/partner-certs', payload)
    ElMessage.success('资质已录入（草稿），待 QA 审核')
  }
  dialogVisible.value = false
  load()
}

// ---------- 审核（draft → audited） ----------
function onAudit(row) {
  ElMessageBox.confirm(
    `确认审核通过资质「${row.cert_name}（${row.cert_no}）」？审核后对采购生效`,
    '资质审核',
    { type: 'warning' },
  ).then(async () => {
    await request.post(`/partner-certs/${row.id}/audit`)
    ElMessage.success('审核通过')
    load()
  }).catch(() => {})
}

function onTab(tab) {
  if (tab === 'expiring') loadExpiring()
}

onMounted(load)
onMounted(async () => {
  const ps = await request.get('/partners', { params: { page_size: 100 } })
  partners.value = ps.items
})
</script>

<template>
  <el-card>
    <el-tabs v-model="activeTab" @tab-change="onTab">
      <el-tab-pane label="全部资质" name="all">
        <div class="toolbar">
          <el-select v-model="query.partner_id" clearable filterable placeholder="供应商" style="width: 200px" @change="onSearch">
            <el-option v-for="p in suppliers" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" />
          </el-select>
          <el-select v-model="query.audit_status" clearable placeholder="审核状态" style="width: 130px" @change="onSearch">
            <el-option v-for="(meta, s) in CERT_STATUS_META" :key="s" :label="meta.label" :value="s" />
          </el-select>
          <span class="spacer" />
          <el-button v-if="canWrite" type="primary" @click="openDialog(null)">新增资质</el-button>
        </div>
        <el-table :data="list" border size="small" v-loading="loading">
          <el-table-column label="供应商" min-width="140">
            <template #default="{ row }">{{ row.partner_name || pname(row.partner_id) }}</template>
          </el-table-column>
          <el-table-column prop="cert_name" label="证照名称" min-width="140" />
          <el-table-column prop="cert_no" label="证照编号" min-width="130" />
          <el-table-column prop="issuer" label="发证机构" min-width="120">
            <template #default="{ row }">{{ row.issuer || '-' }}</template>
          </el-table-column>
          <el-table-column prop="valid_from" label="生效日期" width="110" />
          <el-table-column prop="valid_until" label="有效期至" width="110" />
          <el-table-column label="审核状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusMeta(row.audit_status).type" size="small">{{ statusMeta(row.audit_status).label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="审核人/时间" min-width="150">
            <template #default="{ row }">
              <span v-if="row.audited_by">{{ row.audited_by }}<br />{{ fmtTime(row.audited_at) }}</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button v-if="canWrite && row.audit_status === 'draft'" link type="primary" @click="openDialog(row)">编辑</el-button>
              <el-button v-if="canAudit && row.audit_status === 'draft'" link type="success" @click="onAudit(row)">审核</el-button>
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
      </el-tab-pane>

      <el-tab-pane label="临期预警" name="expiring">
        <div class="toolbar">
          <span>未来</span>
          <el-input-number v-model="expiringDays" :min="1" :max="365" size="small" style="width: 110px" />
          <span>天内到期的已审核资质</span>
          <el-button size="small" @click="loadExpiring">刷新</el-button>
        </div>
        <el-table :data="expiringList" border size="small" v-loading="expiringLoading">
          <el-table-column label="供应商" min-width="140">
            <template #default="{ row }">{{ row.partner_name || pname(row.partner_id) }}</template>
          </el-table-column>
          <el-table-column prop="cert_name" label="证照名称" min-width="140" />
          <el-table-column prop="cert_no" label="证照编号" min-width="130" />
          <el-table-column label="有效期至" width="120">
            <template #default="{ row }">
              <span :style="daysLeftStyle(row.days_left)">{{ row.valid_until }}</span>
            </template>
          </el-table-column>
          <el-table-column label="剩余天数" width="110" align="center">
            <template #default="{ row }">
              <span :style="daysLeftStyle(row.days_left)">{{ row.days_left }} 天</span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑资质' : '新增资质'" width="520px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="供应商" prop="partner_id">
          <el-select v-model="form.partner_id" filterable :disabled="!!editingId" style="width: 100%">
            <el-option v-for="p in suppliers" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="证照名称" prop="cert_name">
          <el-input v-model="form.cert_name" placeholder="如：药品生产许可证" />
        </el-form-item>
        <el-form-item label="证照编号" prop="cert_no">
          <el-input v-model="form.cert_no" />
        </el-form-item>
        <el-form-item label="发证机构">
          <el-input v-model="form.issuer" />
        </el-form-item>
        <el-form-item label="生效日期" prop="valid_from">
          <el-date-picker v-model="form.valid_from" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="有效期至" prop="valid_until">
          <el-date-picker v-model="form.valid_until" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="文件引用">
          <el-input v-model="form.file_ref" placeholder="扫描件路径/编号（可选）" />
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
  align-items: center;
  margin-bottom: 12px;
}
.spacer {
  flex: 1;
}
</style>
