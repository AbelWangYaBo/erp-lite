<script setup>
// 单据跳转链接：按 type 映射列表页路由，点击携带 keyword 跳转（列表页支持 keyword 则过滤，不支持也无害）。
// 无映射的 source_type 渲染纯文本兜底。
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const DOC_ROUTES = {
  purchase_order: '/purchase-orders',
  sales_order: '/sales-orders',
  manufacturing_order: '/manufacturing-orders',
  batch: '/batches',
  stock_count: '/stock-counts',
  sales_return: '/sales-returns',
  purchase_return: '/purchase-returns',
  payable: '/payables',
  receivable: '/receivables',
  settlement: '/settlements',
  partner_cert: '/partner-certs',
}

const props = defineProps({
  type: { type: String, required: true },
  id: { type: Number, default: null },
  no: { type: String, default: '' },
})

const router = useRouter()
const path = computed(() => DOC_ROUTES[props.type] || '')
const text = computed(() => props.no || (props.id != null ? `#${props.id}` : '-'))

function go() {
  if (!path.value) return
  router.push({ path: path.value, query: props.no ? { keyword: props.no } : undefined })
}
</script>

<template>
  <el-link v-if="path" type="primary" @click="go">{{ text }}</el-link>
  <span v-else>{{ text }}</span>
</template>
