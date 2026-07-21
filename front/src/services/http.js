import axios from 'axios'
import { useUserStore } from '../store/user'
import router from '../router'

const AUTH_EXCLUDED_PATHS = new Set([
  '/user/login/',
  '/user/register/'
])

const PUBLIC_AUTH_PAGE_NAMES = new Set(['Login', 'Register'])
const PUBLIC_AUTH_PAGE_PATHS = new Set(['/login', '/register'])

// 这些路径的 GET 禁止浏览器/中间层缓存（不再用全局 _t 污染 query）
const NO_STORE_GET_PATHS = [
  '/user/',
  '/note/',
  '/chat/',
  '/knowledge/',
  '/review/',
  '/org/',
  '/space/',
  '/audit/',
  '/file/'
]

function shouldAttachAuthHeader(config) {
  const url = config?.url || ''
  return !AUTH_EXCLUDED_PATHS.has(url)
}

function shouldNoStore(url = '') {
  return NO_STORE_GET_PATHS.some((prefix) => url.includes(prefix))
}

function isPublicAuthPage() {
  if (PUBLIC_AUTH_PAGE_NAMES.has(router.currentRoute.value.name)) {
    return true
  }

  const browserPath = typeof window !== 'undefined'
    ? window.location.pathname.replace(/\/+$/, '') || '/'
    : ''
  return PUBLIC_AUTH_PAGE_PATHS.has(browserPath)
}

const http = axios.create({
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

// Request: inject bearer token；敏感 GET 使用 Cache-Control，不再附加 _t
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('jwt_token')
  if (token && shouldAttachAuthHeader(config)) {
    config.headers.Authorization = `Bearer ${token}`
  }
  if (!config.headers['X-Request-Id']) {
    config.headers['X-Request-Id'] =
      (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : `req-${Date.now()}-${Math.random().toString(16).slice(2)}`
  }
  if (config.method === 'get' && shouldNoStore(config.url || '')) {
    config.headers['Cache-Control'] = 'no-store'
    config.headers['Pragma'] = 'no-cache'
  }
  return config
})

// Response: 401 handling
let isRedirecting = false

http.interceptors.response.use(
  (response) => response,
  (error) => {
    const requestUrl = error.config?.url || ''
    const shouldRedirect = !error.config?.skipAuthRedirect
      && !AUTH_EXCLUDED_PATHS.has(requestUrl)
      && !isPublicAuthPage()

    if (error.response?.status === 401 && shouldRedirect && !isRedirecting) {
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
