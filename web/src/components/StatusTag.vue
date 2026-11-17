<script setup>
import { computed } from 'vue'

// 单据状态 tag：draft 灰 / approved 蓝 / partial 橙 / completed 绿 / closed 深灰 / void 红
const STATUS_MAP = {
  draft: { label: '草稿', type: 'info', effect: 'light' },
  approved: { label: '已审核', type: 'primary', effect: 'light' },
  partial: { label: '部分完成', type: 'warning', effect: 'light' },
  completed: { label: '已完成', type: 'success', effect: 'light' },
  closed: { label: '已关闭', type: 'info', effect: 'dark' },
  void: { label: '已作废', type: 'danger', effect: 'light' },
  // 财务状态
  open: { label: '未结', type: 'primary', effect: 'light' },
  settled: { label: '已结清', type: 'success', effect: 'light' },
}

const props = defineProps({
  status: { type: String, default: '' },
  map: { type: Object, default: null },
})

const conf = computed(() => (props.map || STATUS_MAP)[props.status] || { label: props.status, type: 'info', effect: 'light' })
</script>

<template>
  <el-tag :type="conf.type" :effect="conf.effect" size="small">{{ conf.label }}</el-tag>
</template>
