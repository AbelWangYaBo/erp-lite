import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

request.interceptors.request.use((config) => {
  const token = localStorage.getItem('erp_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

request.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const raw = err.response?.data?.detail
    let msg
    if (Array.isArray(raw)) {
      // FastAPI 422 校验错误：detail 为数组，转可读文本（loc 去掉首段 body/query）
      msg = raw.map((e) => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`).join('；')
    } else {
      msg = typeof raw === 'string' ? raw : err.message || '请求失败'
    }
    // silent: 调用方自行处理错误时不弹全局提示
    if (!err.config?.silent) ElMessage.error(msg)
    if (err.response?.status === 401) {
      localStorage.removeItem('erp_token')
      localStorage.removeItem('erp_username')
      if (router.currentRoute.value.path !== '/login') router.push('/login')
    }
    return Promise.reject(err)
  },
)

export default request
