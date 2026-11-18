<script setup>
// 采购退货（v3）：QA 不合格处置联动生成，此处只读列表；单据号可跳转源单据
import { onMounted, reactive, ref } from 'vue'
import request from '@/api/request'
import { fmtMoney, fmtTime } from '@/utils/format'
import DocLink from '@/components/DocLink.vue'

const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20 })

async function load() {
  loading.value = true
  try {
    const d = await request.get('/purchase-returns', {
      params: { page: query.page, page_size: query.page_size },
    })
    list.value = d.items
    total.value = d.total
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <el-card>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="doc_no" label="单号" min-width="140" />
      <el-table-column label="采购订单" min-width="130">
        <template #default="{ row }">
          <DocLink type="purchase_order" :id="row.po_id" />
        </template>
      </el-table-column>
      <el-table-column label="批次" min-width="110">
        <template #default="{ row }">
          <DocLink type="batch" :id="row.batch_id" />
        </template>
      </el-table-column>
      <el-table-column label="数量" align="right" width="110">
        <template #default="{ row }">{{ fmtMoney(row.qty) }}</template>
      </el-table-column>
      <el-table-column label="金额" align="right" width="120">
        <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
      </el-table-column>
      <el-table-column prop="reason" label="原因" min-width="160">
        <template #default="{ row }">{{ row.reason || '-' }}</template>
      </el-table-column>
      <el-table-column prop="created_by" label="操作人" width="110">
        <template #default="{ row }">{{ row.created_by || '-' }}</template>
      </el-table-column>
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="query.page"
      v-model:page-size="query.page_size"
      :total="total"
      layout="total, prev, pager, next, sizes"
      style="margin-top: 12px; justify-content: flex-end"
      @current-change="load"
      @size-change="() => { query.page = 1; load() }"
    />
  </el-card>
</template>
