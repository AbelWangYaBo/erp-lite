<script setup>
// 收付款核销：选 partner → 列出 open/partial 明细 → 勾选 + 金额分摊 → 提交；下方核销单列表
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'
import { fmtMoney, today } from '@/utils/format'

const partners = ref([])
const pname = (id) => partners.value.find((p) => p.id === id)?.name || id

// ---------- 新建核销单 ----------
const formRef = ref()
const form = reactive({ type: 'payment', partner_id: null, date: today() })
const formRules = {
  partner_id: [{ required: true, message: '请选择往来单位', trigger: 'change' }],
  date: [{ required: true, message: '请选择日期', trigger: 'change' }],
}
const bills = ref([])
const billsLoading = ref(false)
const selected = ref([])
const submitLoading = ref(false)

// payment 付供应商（核销 AP），receipt 收客户（核销 AR）
const targetType = computed(() => (form.type === 'payment' ? 'ap' : 'ar'))
const billApi = computed(() => (form.type === 'payment' ? '/payables' : '/receivables'))
const targetLabel = computed(() => (form.type === 'payment' ? '应付' : '应收'))

const partnerOptions = computed(() => {
  const want = form.type === 'payment' ? 'supplier' : 'customer'
  return partners.value.filter((p) => p.type === want)
})

async function loadBills() {
  if (!form.partner_id) {
    bills.value = []
    return
  }
  billsLoading.value = true
  selected.value = []
  try {
    const d = await request.get(billApi.value, {
      params: { partner_id: form.partner_id, page: 1, page_size: 100 },
    })
    // 只列出 open / partial 的明细
    bills.value = d.items.filter((b) => b.status === 'open' || b.status === 'partial')
  } finally {
    billsLoading.value = false
  }
}

function onTypeChange() {
  form.partner_id = null
  bills.value = []
}

const selectedTotal = computed(() =>
  selected.value.reduce((s, b) => s + Number(b.alloc_amount || 0), 0),
)

const remaining = (b) => Number(b.amount) - Number(b.settled_amount || 0)

function onSelectChange(rows) {
  selected.value = rows
  // 勾选时默认按全额分摊
  rows.forEach((b) => {
    if (b.alloc_amount === undefined || b.alloc_amount === null) b.alloc_amount = remaining(b)
  })
}

async function submit() {
  await formRef.value.validate()
  const lines = selected.value.filter((b) => Number(b.alloc_amount) > 0)
  if (!lines.length) {
    ElMessage.warning('请勾选并录入分摊金额')
    return
  }
  const bad = lines.find((b) => Number(b.alloc_amount) > remaining(b))
  if (bad) {
    ElMessage.warning('分摊金额不能超过未核销余额')
    return
  }
  const payload = {
    type: form.type,
    partner_id: form.partner_id,
    amount: selectedTotal.value,
    date: form.date,
    lines: lines.map((b) => ({
      target_type: targetType.value,
      target_id: b.id,
      amount: b.alloc_amount,
    })),
  }
  await ElMessageBox.confirm(`确认提交核销单？合计金额 ${fmtMoney(payload.amount)}`, '提交确认', { type: 'warning' })
  submitLoading.value = true
  try {
    await request.post('/settlements', payload)
    ElMessage.success('核销单已提交')
    form.partner_id = null
    bills.value = []
    selected.value = []
    loadList()
  } finally {
    submitLoading.value = false
  }
}

// ---------- 核销单列表 ----------
const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20 })

async function loadList() {
  loading.value = true
  try {
    const d = await request.get('/settlements', { params: { page: query.page, page_size: query.page_size } })
    list.value = d.items
    total.value = d.total
  } finally {
    loading.value = false
  }
}

const typeLabel = (t) => (t === 'payment' ? '付款' : t === 'receipt' ? '收款' : t)
const linesText = (s) => (s.lines || []).map((l) => `${l.target_type}#${l.target_id}:${fmtMoney(l.amount)}`).join('；')

onMounted(async () => {
  loadList()
  const ps = await request.get('/partners', { params: { page_size: 100 } })
  partners.value = ps.items
})
</script>

<template>
  <el-card>
    <div class="section-title">新建核销单</div>
    <el-form ref="formRef" :model="form" :rules="formRules" inline>
      <el-form-item label="类型">
        <el-radio-group v-model="form.type" @change="onTypeChange">
          <el-radio-button value="payment">付款（应付）</el-radio-button>
          <el-radio-button value="receipt">收款（应收）</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="往来单位" prop="partner_id">
        <el-select v-model="form.partner_id" filterable style="width: 220px" placeholder="先选类型再选单位" @change="loadBills">
          <el-option v-for="p in partnerOptions" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="日期" prop="date">
        <el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" />
      </el-form-item>
    </el-form>

    <el-table
      :data="bills"
      border
      size="small"
      v-loading="billsLoading"
      @selection-change="onSelectChange"
    >
      <el-table-column type="selection" width="45" />
      <el-table-column prop="id" label="明细ID" width="80" />
      <el-table-column label="来源" min-width="130">
        <template #default="{ row }">{{ row.source_type }}#{{ row.source_id }}</template>
      </el-table-column>
      <el-table-column label="金额" align="right" width="120">
        <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
      </el-table-column>
      <el-table-column label="已核销" align="right" width="120">
        <template #default="{ row }">{{ fmtMoney(row.settled_amount) }}</template>
      </el-table-column>
      <el-table-column label="未核销余额" align="right" width="120">
        <template #default="{ row }">{{ fmtMoney(remaining(row)) }}</template>
      </el-table-column>
      <el-table-column label="本次分摊金额" width="160">
        <template #default="{ row }">
          <el-input-number v-model="row.alloc_amount" :min="0" :max="remaining(row)" :precision="2" :controls="false" size="small" style="width: 100%" />
        </template>
      </el-table-column>
      <template #empty>请选择往来单位后加载未结明细</template>
    </el-table>
    <div class="submit-row">
      <span>合计：{{ fmtMoney(selectedTotal) }}</span>
      <el-button type="primary" :loading="submitLoading" @click="submit">提交核销单</el-button>
    </div>

    <div class="section-title">核销单列表</div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="类型" width="90">
        <template #default="{ row }">{{ typeLabel(row.type) }}</template>
      </el-table-column>
      <el-table-column label="往来单位" min-width="130">
        <template #default="{ row }">{{ pname(row.partner_id) }}</template>
      </el-table-column>
      <el-table-column prop="date" label="日期" width="110" />
      <el-table-column label="金额" align="right" width="120">
        <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
      </el-table-column>
      <el-table-column label="核销明细" min-width="240">
        <template #default="{ row }">{{ linesText(row) }}</template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="query.page"
      v-model:page-size="query.page_size"
      :total="total"
      layout="total, prev, pager, next, sizes"
      style="margin-top: 12px; justify-content: flex-end"
      @current-change="loadList"
      @size-change="() => { query.page = 1; loadList() }"
    />
  </el-card>
</template>

<style scoped>
.section-title {
  font-weight: 600;
  margin: 6px 0 10px;
}
.submit-row {
  margin-top: 10px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
  font-weight: 600;
}
</style>
