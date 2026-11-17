<script setup>
// 应付/应收共用列表（列表 + 状态 tag + 金额右对齐）
import { computed, onMounted, reactive, ref } from 'vue'
import request from '@/api/request'
import StatusTag from '@/components/StatusTag.vue'
import { fmtMoney } from '@/utils/format'

const props = defineProps({ type: { type: String, required: true } })
const isAP = computed(() => props.type === 'payable')
const base = computed(() => (isAP.value ? '/payables' : '/receivables'))
const title = computed(() => (isAP.value ? '应付' : '应收'))

const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20, status: '' })
const partners = ref([])
const pname = (id) => partners.value.find((p) => p.id === id)?.name || id

async function load() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
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

const remaining = (row) => fmtMoney(Number(row.amount) - Number(row.settled_amount || 0))

onMounted(async () => {
  load()
  const ps = await request.get('/partners', { params: { page_size: 100 } })
  partners.value = ps.items
})
</script>

<template>
  <el-card>
    <div class="toolbar">
      <el-select v-model="query.status" clearable placeholder="结算状态" style="width: 140px" @change="onSearch">
        <el-option label="未结" value="open" />
        <el-option label="部分结算" value="partial" />
        <el-option label="已结清" value="settled" />
      </el-select>
      <el-button type="primary" @click="onSearch">搜索</el-button>
      <span class="spacer" />
      <span class="title">{{ title }}账款（由出入库自动生成）</span>
    </div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="id" label="单据ID" width="80" />
      <el-table-column label="往来单位" min-width="140">
        <template #default="{ row }">{{ pname(row.partner_id) }}</template>
      </el-table-column>
      <el-table-column label="来源" min-width="130">
        <template #default="{ row }">{{ row.source_type }}#{{ row.source_id }}</template>
      </el-table-column>
      <el-table-column label="金额" align="right" width="130">
        <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
      </el-table-column>
      <el-table-column label="已核销" align="right" width="130">
        <template #default="{ row }">{{ fmtMoney(row.settled_amount) }}</template>
      </el-table-column>
      <el-table-column label="未核销余额" align="right" width="130">
        <template #default="{ row }">{{ remaining(row) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }"><StatusTag :status="row.status" /></template>
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
.title {
  color: #999;
}
</style>
