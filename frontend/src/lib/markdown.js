import { marked } from 'marked'
import hljs from 'highlight.js'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true, gfm: true })

export function renderMarkdown(markdown) {
  if (!markdown) return ''
  return DOMPurify.sanitize(marked(markdown))
}

/* marked v12 已移除渲染期 highlight 选项，改为渲染后 DOM 补染 */
export function highlightCodeBlocks(root) {
  if (!root) return
  root.querySelectorAll('pre code').forEach((block) => {
    if (block.dataset.highlighted === 'yes') return
    try {
      hljs.highlightElement(block)
    } catch {
      /* 未知语言等情况忽略，保持原文 */
    }
  })
}

/* 从已渲染 DOM 抽取目录（不改报告原标题，仅给目录配档案式序号） */
export function extractOutline(root) {
  if (!root) return []
  const headings = [...root.querySelectorAll('h1, h2, h3')]
  headings.forEach((heading, index) => {
    if (!heading.id) heading.id = `sec-${index}`
  })
  return headings.map((heading, index) => ({
    id: heading.id,
    level: Number(heading.tagName.slice(1)),
    label: heading.textContent.trim(),
    catalogNumber: String(index + 1).padStart(2, '0')
  }))
}

export function countCjkChars(markdown) {
  const matches = (markdown || '').match(/[一-鿿㐀-䶿]/g)
  return matches ? matches.length : 0
}

export function downloadMarkdown(markdown, topic) {
  const filename = `${(topic || '研究报告').replace(/[^a-zA-Z0-9一-龥]/g, '_')}.md`
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
