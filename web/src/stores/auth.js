import { defineStore } from 'pinia'
import router from '@/router'
import request from '@/api/request'
import { ROLE_LABELS } from '@/constants'

const LS = {
  token: 'erp_token',
  username: 'erp_username',
  role: 'erp_role',
  actions: 'erp_actions',
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(LS.token) || '',
    username: localStorage.getItem(LS.username) || '',
    role: localStorage.getItem(LS.role) || '',
    actions: (() => {
      try {
        return JSON.parse(localStorage.getItem(LS.actions) || '[]')
      } catch {
        return []
      }
    })(),
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    roleLabel: (s) => ROLE_LABELS[s.role] || s.role || '-',
  },
  actions: {
    setAuth(token, username) {
      this.token = token
      this.username = username || ''
      localStorage.setItem(LS.token, token)
      localStorage.setItem(LS.username, this.username)
    },
    // 登录：换 token 后立刻拉取身份与权限集
    async login(username, password) {
      const d = await request.post('/auth/login', { username, password })
      this.setAuth(d.token, username)
      await this.fetchMe()
      return d
    },
    // GET /auth/me → role + actions（菜单/按钮显隐依据）
    async fetchMe() {
      const d = await request.get('/auth/me')
      this.role = d.role || ''
      this.actions = Array.isArray(d.actions) ? d.actions : []
      localStorage.setItem(LS.role, this.role)
      localStorage.setItem(LS.actions, JSON.stringify(this.actions))
      return d
    },
    // 权限判断：持有该动作键才显示对应菜单/按钮
    has(action) {
      return this.actions.includes(action)
    },
    // 演示身份切换：用演示账号重新登录换 token，并刷新权限（调用方负责刷新页面数据）
    async switchTo(account) {
      await this.login(account.username, account.password)
      return account
    },
    logout() {
      this.token = ''
      this.username = ''
      this.role = ''
      this.actions = []
      Object.values(LS).forEach((k) => localStorage.removeItem(k))
      router.push('/login')
    },
  },
})
