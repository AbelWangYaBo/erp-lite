<script setup>
// BOM：选父物料 → 行编辑表格（子物料/用量/损耗率），保存 POST /boms/{material_id}
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/api/request'

const materials = ref([])
const parentId = ref(null)
const loading = ref(false)
const lines = ref([])
const hasBom = ref(false)

const blankLine = () => ({ material_id: null, qty_per: 1, loss_pct: 0 })

async function loadBom() {
  if (!parentId.value) {
    lines.value = []
    hasBom.value = false
    return
  }
  loading.value = true
  try {
    // 无 BOM 时 404 静默处理为空表
    const d = await request.get(`/boms/${parentId.value}`, { silent: true }).catch((e) => {
      if (e.response?.status === 404) return null
      throw e
    })
    hasBom.value = !!d
    const src = d?.lines || []
    lines.value = src.length ? src.map((l) => ({ ...l })) : [blankLine()]
  } finally {
    loading.value = false
  }
}

function addLine() {
  lines.value.push(blankLine())
}

function removeLine(i) {
  lines.value.splice(i, 1)
}

async function save() {
  if (!parentId.value) {
    ElMessage.warning('请先选择父物料')
    return
  }
  const valid = lines.value.filter((l) => l.material_id && Number(l.qty_per) > 0)
  if (!valid.length) {
    ElMessage.warning('请至少填写一行有效的子物料明细')
    return
  }
  await ElMessageBox.confirm('确认保存该 BOM？', '操作确认', { type: 'warning' })
  await request.post(`/boms/${parentId.value}`, {
    lines: valid.map((l) => ({ material_id: l.material_id, qty_per: l.qty_per, loss_pct: l.loss_pct || 0 })),
  })
  ElMessage.success('BOM 已保存')
  loadBom()
}

onMounted(async () => {
  const ms = await request.get('/materials', { params: { page_size: 100 } })
  materials.value = ms.items
})
</script>

<template>
  <el-card>
    <div class="toolbar">
      <span>父物料：</span>
      <el-select v-model="parentId" filterable placeholder="选择父物料" style="width: 280px" @change="loadBom">
        <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
      </el-select>
      <el-tag v-if="parentId" :type="hasBom ? 'success' : 'info'" size="small">{{ hasBom ? '已有 BOM' : '暂无 BOM，保存后创建' }}</el-tag>
      <span class="spacer" />
      <el-button type="primary" @click="save">保存 BOM</el-button>
    </div>

    <el-table :data="lines" border size="small" v-loading="loading">
      <el-table-column label="子物料" min-width="220">
        <template #default="{ row }">
          <el-select v-model="row.material_id" filterable size="small">
            <el-option v-for="m in materials" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="单件用量 qty_per" width="170">
        <template #default="{ row }">
          <el-input-number v-model="row.qty_per" :min="0.0001" :precision="4" :controls="false" size="small" style="width: 100%" />
        </template>
      </el-table-column>
      <el-table-column label="损耗率 % loss_pct" width="170">
        <template #default="{ row }">
          <el-input-number v-model="row.loss_pct" :min="0" :max="100" :precision="2" :controls="false" size="small" style="width: 100%" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80">
        <template #default="{ $index }">
          <el-button link type="danger" size="small" @click="removeLine($index)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-button size="small" style="margin-top: 10px" @click="addLine">添加行</el-button>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.spacer {
  flex: 1;
}
</style>
