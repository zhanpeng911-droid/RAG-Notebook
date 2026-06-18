import axios from 'axios'
import { useUserStore } from '../store/user'
import router from '../router'

const http = axios.create({
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

// Request: inject token + CSRF + cache busting for GET
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('jwt_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  const csrf = document.cookie
    .split('; ')
    .find(row => row.startsWith('csrftoken='))
    ?.split('=')[1]
  if (csrf) {
    config.headers['X-CSRFTOKEN'] = csrf
  }
  // GET 请求添加时间戳防缓存
  if (config.method === 'get') {
    config.params = { ...config.params, _t: Date.now() }
  }
  return config
})

// Response: 401 handling
let isRedirecting = false

http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !isRedirecting) {
      isRedirecting = true
      const userStore = useUserStore()
      userStore.clearAuth()
      router.push({
        name: 'Login',
        query: { redirect: router.currentRoute.value.fullPath }
      }).finally(() => { setTimeout(() => { isRedirecting = false }, 500) })
    }
    return Promise.reject(error)
  }
)

export default http
