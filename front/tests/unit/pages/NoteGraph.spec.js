import { createApp, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import NoteGraph from '../../../src/pages/knowledge/NoteGraphPage.vue'
import { useUserStore } from '../../../src/store/user'

const { api, route } = vi.hoisted(() => ({ api: {}, route: { query: {} } }))
vi.mock('../../../src/services/noteGraphApi', () => ({ noteGraphApi: api }))
vi.mock('../../../src/services/http', () => ({ default: { get: vi.fn() } }))
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ push: vi.fn() }) }))
vi.mock('vant', () => ({ showToast: vi.fn(), showConfirmDialog: vi.fn() }))
let app, host, user
async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); await nextTick() }
function mount() {
  const pinia = createPinia()
  user = useUserStore(pinia)
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp(NoteGraph).use(pinia)
  app.mount(host)
}
beforeEach(() => {
  sessionStorage.clear()
  route.query = {}
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  const graph = { nodes: [{ id: 'a', title: 'Note A', degree: 1 }], edges: [], counts: { visible_notes: 1 } }
  api.graph = vi.fn().mockResolvedValue(graph)
  api.localGraph = vi.fn().mockResolvedValue(graph)
  api.links = vi.fn().mockResolvedValue({ note: graph.nodes[0], outgoing: [], incoming: [], manual: [], unresolved: [] })
})
afterEach(() => { app?.unmount(); host?.remove(); vi.unstubAllGlobals(); vi.clearAllMocks() })
it('局部图明确禁用不会应用的全局筛选', async () => {
  route.query = { focus: 'a', depth: 2 }
  mount(); await flush()
  expect(api.localGraph).toHaveBeenCalledWith('a', 2)
  expect(host.querySelector('input[placeholder="标签筛选"]').disabled).toBe(true)
  expect(host.textContent).toContain('以上全局筛选暂不应用')
})
it('退出后丢弃在途图谱请求并清空关系视图', async () => {
  let resolve
  api.graph.mockImplementation(() => new Promise(done => { resolve = done }))
  mount(); await flush()
  user.authEpoch++
  await flush()
  resolve({ nodes: [{ id: 'secret', title: '旧账号私有标题' }], edges: [] })
  await flush()
  expect(host.textContent).not.toContain('旧账号私有标题')
  expect(host.textContent).toContain('登录状态已变化')
  expect(sessionStorage.getItem('notebook:note-graph:local')).toBeNull()
})
