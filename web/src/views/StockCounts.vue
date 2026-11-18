<script setup>
// 盘点（v3）：仓管建单（按仓库快照账面）+ 录实盘（draft），finance 过账生成差异调整流水
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'
import { fmtMoney, fmtTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import DocLink from '@/components/DocLink.vue'

const auth = useAuthStore()
const canEdit = () => auth.has('stockcount.create')
const canPost = () => auth.has('stockcount.post')

const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20 })
const tableRef = ref()

const materials = ref([])
const warehouses = ref([])
const mlabel = (id) => {
  const m = materials.value.find((x) => x.id === id)
  return m ? `${m.code} ${m.name}` : id
}
const wname = (id) => warehouses.value.find((w) => w.id === id)?.name || id

const SC_STATUS = {
  draft: { label: '草稿', type: 'warning' },
  posted: { label: '已过账', type: 'success' },
}
const statusMeta = (s) => SC_STATUS[s] || { label: s, type: 'info' }

async function load() {
  loading.value = true
  try {
    const d = await request.get('/inventory/stock-counts', {
      params: { page: query.page, page_size: query.page_size },
    })
    list.value = d.items
    total.value = d.total
  } finally {
    loading.value = false
  }
}

// ---------- 新建盘点 ----------
const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive({ warehouse_id: null, remark: '' })

function openCreate() {
  form.warehouse_id = null
  form.remark = ''
  dialogVisible.value = true
}

async function onCreate() {
  if (!form.warehouse_id) {
    ElMessage.warning('请选择仓库')
    return
  }
  submitting.value = true
  try {
    const d = await request.post('/inventory/stock-counts', {
      warehouse_id: form.warehouse_id,
      remark: form.remark || null,
    })
    ElMessage.success(`已建单并快照 ${(d.lines || []).length} 行账面`)
    dialogVisible.value = false
    query.page = 1
    load()
  } catch {
    // 接口错误：request.js 拦截器已弹全局提示
  } finally {
    submitting.value = false
  }
}

// ---------- 录实盘：展开行内编辑 counted_qty 后整单保存 ----------
function openEdit(row) {
  tableRef.value?.toggleRowExpansion(row, true)
}

async function saveCount(row) {
  const lines = (row.lines || [])
    .filter((l) => l.counted_qty != null)
    .map((l) => ({ line_id: l.id, counted_qty: l.counted_qty }))
  if (!lines.length) {
    ElMessage.warning('请先录入实盘数量')
    return
  }
  try {
    await request.put(`/inventory/stock-counts/${row.id}`, { lines })
    ElMessage.success(`已保存实盘：${row.doc_no}`)
    load()
  } catch {
    // 接口错误：request.js 拦截器已弹全局提示
  }
}

// ---------- 过账（finance） ----------
async function onPost(row) {
  try {
    const d = await request.post(`/inventory/stock-counts/${row.id}/post`)
    const n = (d.lines || []).filter((l) => Number(l.diff_qty) !== 0).length
    ElMessage.success(`差异 ${n} 行已生成调整流水`)
    load()
  } catch {
    // 接口错误：request.js 拦截器已弹全局提示
  }
}

onMounted(load)
onMounted(async () => {
  const [ms, ws] = await Promise.all([
    request.get('/materials', { params: { page_size: 100 } }),
    request.get('/warehouses', { params: { page_size: 100 } }),
  ])
  materials.value = ms.items
  warehouses.value = ws.items
})
</script>

<template>
  <el-card>
    <div class="toolbar">
      <el-button v-if="canEdit()" type="primary" @click="openCreate">新建盘点</el-button>
    </div>
    <el-table ref="tableRef" :data="list" border size="small" v-loading="loading" row-key="id">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="lines">
            <el-table :data="row.lines" border size="small">
              <el-table-column label="物料" min-width="160">
                <template #default="{ row: line }">{{ mlabel(line.material_id) }}</template>
              </el-table-column>
              <el-table-column label="批次" width="110">
                <template #default="{ row: line }">
                  <DocLink v-if="line.batch_id != null" type="batch" :id="line.batch_id" />
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column label="账面数量" align="right" width="110">
                <template #default="{ row: line }">{{ fmtMoney(line.system_qty) }}</template>
              </el-table-column>
              <el-table-column label="实盘数量" align="right" width="170">
                <template #default="{ row: line }">
                  <el-input-number
                    v-if="row.status === 'draft' && canEdit()"
                    v-model="line.counted_qty"
                    :min="0"
                    size="small"
                    controls-position="right"
                  />
                  <span v-else>{{ line.counted_qty != null ? fmtMoney(line.counted_qty) : '-' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="差异" align="right" width="110">
                <template #default="{ row: line }">
                  <span :style="Number(line.diff_qty) !== 0 ? 'color:#f56c6c' : ''">{{ fmtMoney(line.diff_qty) }}</span>
                </template>
              </el-table-column>
            </el-table>
            <el-button
              v-if="row.status === 'draft' && canEdit()"
              type="primary"
              size="small"
              style="margin-top: 8px"
              @click="saveCount(row)"
            >
              保存实盘
            </el-button>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="doc_no" label="单号" min-width="130" />
      <el-table-column label="仓库" min-width="110">
        <template #default="{ row }">{{ wname(row.warehouse_id) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusMeta(row.status).type" size="small">{{ statusMeta(row.status).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="130">
        <template #default="{ row }">{{ row.remark || '-' }}</template>
      </el-table-column>
      <el-table-column prop="created_by" label="创建人" width="100">
        <template #default="{ row }">{{ row.created_by || '-' }}</template>
      </el-table-column>
      <el-table-column prop="posted_by" label="过账人" width="100">
        <template #default="{ row }">{{ row.posted_by || '-' }}</template>
      </el-table-column>
      <el-table-column label="过账时间" width="150">
        <template #default="{ row }">{{ row.posted_at ? fmtTime(row.posted_at) : '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'draft' && canEdit()" link type="primary" @click="openEdit(row)">录实盘</el-button>
          <el-button v-if="row.status === 'draft' && canPost()" link type="success" @click="onPost(row)">过账</el-button>
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
      @size-change="() => { query.page = 1; load() }"
    />

    <!-- 新建盘点：选仓库即按账面快照行 -->
    <el-dialog v-model="dialogVisible" title="新建盘点" width="440px">
      <el-form label-width="80px">
        <el-form-item label="仓库" required>
          <el-select v-model="form.warehouse_id" filterable placeholder="选择仓库" style="width: 100%">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="255" placeholder="选填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="onCreate">提交</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.lines {
  padding: 8px 12px;
}
</style>
