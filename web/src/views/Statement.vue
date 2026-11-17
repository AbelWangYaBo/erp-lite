<script setup>
// 对账单：选 partner + 期间 → 表格
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'
import DynamicTable from '@/components/DynamicTable.vue'

const partners = ref([])
const query = reactive({ partner_id: null, type: '', range: [] })
const rows = ref([])
const loading = ref(false)

const LABELS = {
  date: '日期',
  doc_no: '单据号',
  source_type: '来源类型',
  source_id: '来源ID',
  summary: '摘要',
  remark: '备注',
  debit: '借方（应付/发货）',
  credit: '贷方（收付/收款）',
  amount: '金额',
  balance: '余额',
}

async function load() {
  if (!query.partner_id) {
    ElMessage.warning('请先选择往来单位')
    return
  }
  loading.value = true
  try {
    const params = { partner_id: query.partner_id }
    if (query.type) params.type = query.type
    if (query.range?.[0]) params.from = query.range[0]
    if (query.range?.[1]) params.to = query.range[1]
    const d = await request.get('/finance/statement', { params })
    rows.value = Array.isArray(d) ? d : d.items || []
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
      <el-select v-model="query.partner_id" filterable placeholder="往来单位" style="width: 240px">
        <el-option v-for="p in partners" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" />
      </el-select>
      <el-select v-model="query.type" clearable placeholder="类型" style="width: 130px">
        <el-option label="应付(AP)" value="ap" />
        <el-option label="应收(AR)" value="ar" />
      </el-select>
      <el-date-picker v-model="query.range" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" style="width: 260px" />
      <el-button type="primary" @click="load">查询</el-button>
    </div>
    <DynamicTable :rows="rows" :labels="LABELS" border size="small" v-loading="loading" />
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
</style>
