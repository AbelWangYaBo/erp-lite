<script setup>
// 通用动态表格：按首行数据生成列，数值列右对齐并格式化金额
import { computed } from 'vue'
import { fmtMoney } from '@/utils/format'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  labels: { type: Object, default: () => ({}) },
})

const numRe = /(qty|amount|cost|price|total|sum|balance|debit|credit|net|gross)/i

const columns = computed(() => {
  if (!props.rows.length) return []
  const keys = Object.keys(props.rows[0]).filter((k) => k !== 'id')
  return keys.map((k) => ({
    prop: k,
    label: props.labels[k] || k,
    numeric: numRe.test(k),
  }))
})

function cell(row, col) {
  const v = row[col.prop]
  if (v === null || v === undefined) return ''
  return col.numeric ? fmtMoney(v) : String(v)
}
</script>

<template>
  <el-table :data="rows" border size="small" v-bind="$attrs">
    <el-table-column
      v-for="col in columns"
      :key="col.prop"
      :prop="col.prop"
      :label="col.label"
      :align="col.numeric ? 'right' : 'left'"
      :formatter="cell"
      min-width="110"
    />
    <template #empty>暂无数据</template>
  </el-table>
</template>
