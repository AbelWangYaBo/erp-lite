import { defineStore } from 'pinia'
import router from '@/router'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('erp_token') || '',
    username: localStorage.getItem('erp_username') || '',
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
  },
  actions: {
    setAuth(token, username) {
      this.token = token
      this.username = username || ''
      localStorage.setItem('erp_token', token)
      localStorage.setItem('erp_username', this.username)
    },
    logout() {
      this.token = ''
      this.username = ''
      localStorage.removeItem('erp_token')
      localStorage.removeItem('erp_username')
      router.push('/login')
    },
  },
})
