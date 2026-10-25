import { createApp, nextTick, reactive } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AnalysisWorkspace from '../../../src/pages/knowledge/AnalysisWorkspacePage.vue'

const { api, route, toast } = vi.hoisted(() => ({
  api: {}, route: {}, toast: vi.fn(),
}))
vi.mock('../../../src/services/insightApi', () => ({ insightApi: api }))
vi.mock('../../../src/services/http', () => ({ default: { get: vi.fn() } }))
vi.mock('vue-router', () => ({ useRoute: () => route.value, useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }))
vi.mock('vant', () => ({ showToast: toast }))
vi.mock('../../../src/components/MindmapCanvas.vue', () => ({ default: { template: '<div>导图</div>' } }))

let app, host
async function flush() {
  for (let i = 0; i < 12; i++) await Promise.resolve()
  await nextTick()
}
function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = createApp(AnalysisWorkspace).use(createPinia())
  app.mount(host)
}
beforeEach(() => {
  vi.useFakeTimers()
  route.value = reactive({ params: { id: 'doc-a' }, meta: { sourceType: 'document' }, path: '/analysis/document/doc-a', query: {} })
  api.getSourceState = vi.fn().mockResolvedValue({ status: 'queued', result_available: true, result_status: 'partial', title: '样本', job: { job_id: 'job-a', status: 'queued' } })
  api.getSourceText = vi.fn().mockResolvedValue({ segments: [{ anchor_id: 'a1', text: '原文' }] })
  api.getDocumentInsight = vi.fn().mockResolvedValue({ artifact: { overview: { text: '已有可读结果' }, meta: { coverage: { partial: true, total_blocks: 3, analyzed_blocks: 1 } } }, evidence: {} })
  api.getDocumentMindmap = vi.fn().mockResolvedValue({ status: 'ready', revision: 'r1', mindmap: { root: { id: 'root', label: '样本' } } })
  api.getJob = vi.fn().mockResolvedValue({ status: 'running', job_id: 'job-a' })
})
afterEach(() => {
  app?.unmount()
  host?.remove()
  vi.useRealTimers()
  vi.clearAllMocks()
})
describe('分析工作台的持久任务恢复', () => {
  it('刷新排队任务时保留旧结果，禁止重复提交并恢复轮询', async () => {
    mount()
    await flush()
    expect(host.textContent).toContain('已有可读结果')
    expect(host.textContent).toContain('排队中')
    const generate = [...host.querySelectorAll('button')].find(b => b.textContent.includes('生成中'))
    expect(generate?.disabled).toBe(true)
    await vi.advanceTimersByTimeAsync(1500)
    expect(api.getJob).toHaveBeenCalledWith('job-a')
    expect(host.textContent).toContain('已有可读结果')
  })
  it('重生成失败仍可查看旧结果并看到错误', async () => {
    api.getJob.mockResolvedValue({ status: 'failed', error: 'provider unavailable' })
    mount()
    await flush()
    await vi.advanceTimersByTimeAsync(1500)
    await flush()
    expect(host.textContent).toContain('已有可读结果')
    expect(host.textContent).toContain('provider unavailable')
  })
  it('切换来源后丢弃旧任务的迟到结果', async () => {
    let resolveJob
    api.getJob.mockImplementation(() => new Promise(resolve => { resolveJob = resolve }))
    mount()
    await flush()
    await vi.advanceTimersByTimeAsync(1500)
    api.getSourceState.mockResolvedValue({ status: 'not_generated', title: '新来源' })
    route.value.params.id = 'doc-b'
    await flush()
    resolveJob({ status: 'failed', error: '旧来源错误' })
    await flush()
    expect(host.textContent).toContain('新来源')
    expect(host.textContent).not.toContain('旧来源错误')
  })
})
