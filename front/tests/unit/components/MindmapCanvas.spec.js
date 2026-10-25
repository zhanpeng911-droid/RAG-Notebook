import { createApp, nextTick, reactive } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import MindmapCanvas from '../../../src/components/MindmapCanvas.vue'

vi.mock('vant', () => ({ showToast: vi.fn() }))
vi.mock('../../../src/services/insightApi', () => ({ insightApi: { patchMindmap: vi.fn() } }))
let app, host
afterEach(() => { app?.unmount(); host?.remove() })
it('100 节点支持折叠、搜索展开，并显示未映射的用户修改', async () => {
  const root = { id: 'root', label: 'Root', children: Array.from({ length: 9 }, (_, i) => ({ id: `b${i}`, label: `Branch ${i}`, children: Array.from({ length: 10 }, (_, j) => ({ id: `b${i}-${j}`, label: `Topic ${i}-${j}`, anchor_ids: ['a'] })) })) }
  host = document.createElement('div'); document.body.appendChild(host)
  const props = reactive({ mindmap: { root, edit_conflicts: [{ node_id: 'old', label: '保留旧批注' }] }, revision: 'r', sourceType: 'note', sourceId: 'a' })
  app = createApp(MindmapCanvas, props); app.mount(host); await nextTick()
  expect(host.querySelectorAll('.mm-node-group')).toHaveLength(100)
  expect(host.textContent).toContain('未自动套用')
  expect(host.textContent).toContain('保留旧批注')
  const collapse = [...host.querySelectorAll('button')].find(b => b.textContent === '折叠')
  collapse.click(); await nextTick()
  expect(host.querySelectorAll('.mm-node-group')).toHaveLength(10)
  const search = host.querySelector('input')
  search.value = 'Topic 8-9'; search.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
  expect(host.querySelector('.mm-node.matched')).not.toBeNull()
  expect(host.querySelectorAll('.mm-node-group')).toHaveLength(20)
})
