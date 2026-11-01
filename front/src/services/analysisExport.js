// Export source locations with their verification limits.
export function buildAnalysisMarkdown({ title, sourceType, sourceId, artifact = {}, evidence = {} }) {
  const path = `/analysis/${encodeURIComponent(sourceType)}/${encodeURIComponent(sourceId)}`
  const footnotes = new Map()
  const escape = value => String(value || '').replace(/([[\]\\])/g, '\\$1')
  function refs(target) {
    const ids = []
    for (const item of evidence[target] || []) {
      if (!item.anchor_id || (item.verification_status && !['verified', 'anchor_verified'].includes(item.verification_status))) continue
      if (!footnotes.has(item.anchor_id)) footnotes.set(item.anchor_id, { ...item, number: footnotes.size + 1 })
      if (item.verification_status === 'anchor_verified') footnotes.get(item.anchor_id).anchorOnly = true
      const id = footnotes.get(item.anchor_id).number
      if (!ids.includes(id)) ids.push(id)
    }
    return ids.map(id => `[^source-${id}]`).join('')
  }
  const lines = [`# ${title} - 分析`, '', `> 来源：${sourceType === 'note' ? '笔记' : '知识库文档'} [${escape(title)}](${path})`, '']
  const coverage = artifact.meta?.coverage
  if (coverage?.partial) lines.push(`> 部分结果：已分析 ${coverage.analyzed_blocks}/${coverage.total_blocks} 段。未覆盖：${(coverage.missing_blocks || []).join('、') || '详见原文'}。`, '')
  if (artifact.overview?.text) lines.push('## 概览', '', `${artifact.overview.text}${refs('overview')}`, '')
  if (artifact.key_points?.length) {
    lines.push('## 关键结论', '')
    for (const item of artifact.key_points) lines.push(`- ${item.text}${refs(item.id)}`)
    lines.push('')
  }
  for (const section of artifact.sections || []) {
    lines.push(`## ${section.title}`, '')
    if (section.summary) lines.push(`${section.summary}${refs(section.id)}`, '')
    for (const claim of section.claims || []) lines.push(`- ${claim.text}${refs(claim.id)}`)
    lines.push('')
  }
  if (artifact.terms?.length) {
    lines.push('## 关键概念', '')
    for (const term of artifact.terms) lines.push(`- **${term.name}**${term.description ? `：${term.description}` : ''}${refs(term.id)}`)
    lines.push('')
  }
  if (artifact.open_questions?.length) {
    lines.push('## 待思考的问题', '')
    for (const question of artifact.open_questions) lines.push(`- ${question.text}${refs(question.id)}`)
    lines.push('')
  }
  if (footnotes.size) {
    lines.push('## 原文依据', '', '> 引文匹配或锚点定位不代表已验证结论的语义正确性。', '')
    for (const [anchor, item] of footnotes) {
      const location = [item.page_no ? `第 ${item.page_no} 页` : '', item.heading_path,
        item.char_start != null ? `字符 ${item.char_start}–${item.char_end ?? '?'}` : ''].filter(Boolean).join(' · ') || '原文片段'
      const note = item.anchorOnly ? '（仅核对锚点位置）' : ''
      lines.push(`[^source-${item.number}]: [${escape(location)}](${path}?anchor=${encodeURIComponent(anchor)})${note} — ${escape(item.supporting_quote || item.quote || item.quote_text).replace(/\r?\n/g, ' ')}`)
    }
  }
  return lines.join('\n')
}
