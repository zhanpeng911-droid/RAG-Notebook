import { describe, expect, it } from 'vitest'
import { buildAnalysisMarkdown } from '../../../src/services/analysisExport'

describe('可追溯分析导出', () => {
  it('每条结论保留可回到原文的锚点、位置和引文', () => {
    const text = buildAnalysisMarkdown({
      title: '文档', sourceType: 'document', sourceId: 'd1',
      artifact: { overview: { text: '概览' }, key_points: [{ id: 'p1', text: '结论' }], meta: { coverage: { partial: true, analyzed_blocks: 1, total_blocks: 3 } } },
      evidence: { p1: [{ anchor_id: 'a1', page_no: 2, quote_text: '原文依据', heading_path: '背景' }] },
    })
    expect(text).toContain('结论[^source-1]')
    expect(text).toContain('/analysis/document/d1?anchor=a1')
    expect(text).toContain('第 2 页')
    expect(text).toContain('原文依据')
    expect(text).toContain('1/3')
  })
  it('未核验或没有锚点的证据不能伪造来源', () => {
    const text = buildAnalysisMarkdown({ title: '笔记', sourceType: 'note', sourceId: 'n1', artifact: { key_points: [{ id: 'p', text: '问题' }] }, evidence: { p: [{ quote_text: '无锚点' }, { anchor_id: 'bad', verification_status: 'stale' }] } })
    expect(text).not.toContain('[^source-')
    expect(text).not.toContain('?anchor=bad')
  })
})
