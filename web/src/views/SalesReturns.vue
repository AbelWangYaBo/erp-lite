<script setup>
// 销售退货（v3）：列表 + 新建退货（回流入库批次回待检、冲应收）；单据号可跳转源单据
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/api/request'
import { fmtMoney, fmtTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import DocLink from '@/components/DocLink.vue'

const auth = useAuthStore()
const list = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 20 })

async function load() {
  loading.value = true
  try {
    const d = await request.get('/sales-returns', {
      params: { page: query.page, page_size: query.page_size },
    })
    list.value = d.items
    total.value = d.total
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ---------- 新建退货弹窗 ----------
const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref()
const form = reactive({ so_id: null, batch_id: null, qty: null, reason: '' })
const rules = {
  so_id: [{ required: true, message: '请选择销售订单', trigger: 'change' }],
  batch_id: [{ required: true, message: '请输入批次 ID', trigger: 'blur' }],
  qty: [{ required: true, message: '请输入退货数量', trigger: 'blur' }],
}
const soOptions = ref([])
const soLoading = ref(false)

function openCreate() {
  form.so_id = null
  form.batch_id = null
  form.qty = null
  form.reason = ''
  soOptions.value = []
  dialogVisible.value = true
  searchSO('')
}

// SO 远程搜索（按单号/备注关键词）
async function searchSO(keyword) {
  soLoading.value = true
  try {
    const params = { page: 1, page_size: 50 }
    if (keyword) params.keyword = keyword
    const d = await request.get('/sales-orders', { params, silent: true })
    soOptions.value = d.items
  } catch {
    soOptions.value = []
  } finally {
    soLoading.value = false
  }
}

async function onSubmit() {
  try {
    await formRef.value.validate()
  } catch {
    return // 校验失败：el-form 已在字段下方提示
  }
  submitting.value = true
  try {
    const d = await request.post('/sales-returns', {
      so_id: form.so_id,
      batch_id: form.batch_id,
      qty: form.qty,
      reason: form.reason || null,
    })
    ElMessage.success(`退货单 ${d.doc_no} 已建，批次已回待检，应收已冲减`)
    dialogVisible.value = false
    query.page = 1
    load()
  } catch {
    // 接口错误：request.js 拦截器已弹全局提示
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-card>
    <div class="toolbar">
      <el-button v-if="auth.has('sales_return.create')" type="primary" @click="openCreate">新建退货</el-button>
    </div>
    <el-table :data="list" border size="small" v-loading="loading">
      <el-table-column prop="doc_no" label="单号" min-width="140" />
      <el-table-column label="销售订单" min-width="130">
        <template #default="{ row }">
          <DocLink type="sales_order" :id="row.so_id" />
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

    <!-- 新建退货：选 SO + 批次 + 数量 + 原因 -->
    <el-dialog v-model="dialogVisible" title="新建销售退货" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="销售订单" prop="so_id">
          <el-select
            v-model="form.so_id"
            filterable
            remote
            :remote-method="searchSO"
            :loading="soLoading"
            placeholder="搜索单号"
            style="width: 100%"
          >
            <el-option v-for="o in soOptions" :key="o.id" :label="o.doc_no" :value="o.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="批次 ID" prop="batch_id">
          <el-input-number v-model="form.batch_id" :min="1" controls-position="right" style="width: 100%" />
        </el-form-item>
        <el-form-item label="退货数量" prop="qty">
          <el-input-number v-model="form.qty" :min="0.0001" controls-position="right" style="width: 100%" />
        </el-form-item>
        <el-form-item label="原因">
          <el-input v-model="form.reason" type="textarea" :rows="2" maxlength="255" placeholder="选填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="onSubmit">提交</el-button>
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
</style>
