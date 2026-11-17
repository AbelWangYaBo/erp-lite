<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { DEMO_ACCOUNTS } from '@/constants'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const loading = ref(false)
const formRef = ref()
const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function onSubmit() {
  try {
    await formRef.value.validate()
  } catch {
    return // 校验失败：el-form 已在字段下方提示
  }
  loading.value = true
  try {
    // 登录后立即拉取 /auth/me 存 role + actions，供菜单/按钮显隐
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.push(route.query.redirect || '/')
  } catch {
    // 接口错误：request.js 拦截器已弹全局提示
  } finally {
    loading.value = false
  }
}

// 演示快捷角色：一键填入凭据（仅演示环境）
function quickFill(acc) {
  form.username = acc.username
  form.password = acc.password
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card">
      <h2 class="title">erp-lite 管理系统</h2>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="0" @keyup.enter="onSubmit">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="密码" />
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="onSubmit">
          登 录
        </el-button>
      </el-form>
      <el-divider>演示账号快捷入口</el-divider>
      <div class="quick-roles">
        <el-button
          v-for="acc in DEMO_ACCOUNTS"
          :key="acc.username"
          size="small"
          plain
          @click="quickFill(acc)"
        >
          {{ acc.label }}
        </el-button>
      </div>
      <p class="demo-note">演示凭据仅演示环境使用</p>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f2f5;
}
.login-card {
  width: 360px;
}
.title {
  text-align: center;
  margin: 0 0 20px;
}
.quick-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.demo-note {
  color: #999;
  font-size: 12px;
  text-align: center;
  margin: 12px 0 0;
}
</style>
