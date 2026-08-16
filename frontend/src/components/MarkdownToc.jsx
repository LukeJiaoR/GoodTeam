/* eslint-disable react-refresh/only-export-components */
import { useMemo, useState } from 'react';

export function extractHeadings(markdown) {
  if (typeof markdown !== 'string' || !markdown.trim()) return [];
  const lines = markdown.split('\n');
  const headings = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    const match = line.match(/^(#{1,4})\s+(.+)$/);
    if (match) {
      const level = match[1].length;
      const text = match[2].trim();
      const id = `heading-${headings.length}-${text.toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, '-')}`;
      headings.push({ level, text, id, line: i + 1 });
    }
  }

  return headings;
}

export default function MarkdownToc({ content, t }) {
  const [open, setOpen] = useState(false);
  const headings = useMemo(() => extractHeadings(content), [content]);

  if (headings.length < 2) return null;

  return (
    <nav className="markdown-toc" aria-label={t ? t('review.toc.title') : 'Table of Contents'}>
      <button
        type="button"
        className="markdown-toc__toggle"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span>📑 {t ? t('review.toc.title') : 'Table of Contents'} ({headings.length})</span>
        <span className="markdown-toc__arrow">{open ? '▲' : '▼'}</span>
      </button>
      {open ? (
        <ol className="markdown-toc__list">
          {headings.map((heading) => (
            <li
              key={heading.id}
              className={`markdown-toc__item markdown-toc__item--level-${heading.level}`}
            >
              <span className="markdown-toc__text">{heading.text}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </nav>
  );
}
