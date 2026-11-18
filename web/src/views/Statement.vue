<script setup>
// 对账单（v3 勾稽口径）：期初未清 + 期间新增 − 期间核销 = 期末未清；明细 + 核销记录
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'
import DocLink from '@/components/DocLink.vue'
import { fmtMoney } from '@/utils/format'

const partners = ref([])
const query = reactive({ partner_id: null, type: '', range: [] })
const data = ref(null)
const loading = ref(false)

const TYPE_OPTIONS = [
  { value: 'supplier', label: '应付对账' },
  { value: 'customer', label: '应收对账' },
]

// 按对账类型过滤往来单位（供应商→应付，客户→应收）
const partnerOptions = computed(() =>
  query.type ? partners.value.filter((p) => p.type === query.type) : partners.value,
)

const SETTLE_TYPE_LABELS = { payment: '付款', receipt: '收款' }
const settleTypeLabel = (t) => SETTLE_TYPE_LABELS[t] || t

function onTypeChange() {
  query.partner_id = null
}

async function load() {
  if (!query.type || !query.partner_id) {
    ElMessage.warning('请先选择对账类型与往来单位')
    return
  }
  loading.value = true
  try {
    const params = { partner_id: query.partner_id, type: query.type }
    if (query.range?.[0]) params.from = query.range[0]
    if (query.range?.[1]) params.to = query.range[1]
    data.value = await request.get('/finance/statement', { params })
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  const ps = await request.get('/partners', { params: { page_size: 100 } })
  partners.value = ps.items
})
</script>

<template>
  <el-card>
    <div class="toolbar">
      <el-select v-model="query.type" clearable placeholder="对账类型" style="width: 150px" @change="onTypeChange">
        <el-option v-for="t in TYPE_OPTIONS" :key="t.value" :label="t.label" :value="t.value" />
      </el-select>
      <el-select v-model="query.partner_id" filterable clearable placeholder="往来单位" style="width: 240px">
        <el-option v-for="p in partnerOptions" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" />
      </el-select>
      <el-date-picker v-model="query.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" style="width: 260px" />
      <el-button type="primary" @click="load">查询</el-button>
    </div>

    <template v-if="data">
      <div class="stat-row" v-loading="loading">
        <div class="stat">
          <span class="stat-label">期初未清</span>
          <span class="stat-value">{{ fmtMoney(data.opening_balance) }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">期间新增</span>
          <span class="stat-value">{{ fmtMoney(data.totals.period_new_amount) }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">期间核销</span>
          <span class="stat-value">{{ fmtMoney(data.totals.settlement_total) }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">期末未清</span>
          <span class="stat-value">{{ fmtMoney(data.closing_balance) }}</span>
        </div>
      </div>

      <div class="section-title">账单明细</div>
      <el-table :data="data.details" border size="small">
        <el-table-column label="日期" width="110">
          <template #default="{ row }">{{ row.date }}</template>
        </el-table-column>
        <el-table-column label="来源" min-width="200">
          <template #default="{ row }">
            <DocLink :type="row.source_type" :id="row.source_id" :no="row.source_no" />
          </template>
        </el-table-column>
        <el-table-column label="金额" align="right" width="130">
          <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="已核销" align="right" width="130">
          <template #default="{ row }">{{ fmtMoney(row.settled_amount) }}</template>
        </el-table-column>
        <el-table-column label="余额" align="right" width="130">
          <template #default="{ row }">{{ fmtMoney(row.balance) }}</template>
        </el-table-column>
      </el-table>

      <div class="section-title">核销记录</div>
      <el-table :data="data.settlements" border size="small">
        <el-table-column prop="id" label="核销单ID" width="100" />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">{{ settleTypeLabel(row.type) }}</template>
        </el-table-column>
        <el-table-column prop="date" label="日期" width="110" />
        <el-table-column label="金额" align="right" width="130">
          <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="冲回" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.is_reverse" type="warning" size="small">冲回</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="180">
          <template #default="{ row }">{{ row.remark || '-' }}</template>
        </el-table-column>
        <template #empty>该期间暂无核销记录</template>
      </el-table>
    </template>
    <el-empty v-else-if="!loading" description="选择对账类型与往来单位后查询" />
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.stat-row {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}
.stat {
  flex: 1;
  padding: 12px 16px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.stat-label {
  color: #909399;
  font-size: 13px;
}
.stat-value {
  font-size: 18px;
  font-weight: 600;
}
.section-title {
  font-weight: 600;
  margin: 12px 0 8px;
}
</style>
