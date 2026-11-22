/**
 * [INPUT]: 接收对话和项目概览的 Markdown，使用 ChatRichContent 渲染交互内容。
 * [OUTPUT]: 渲染 Markdown 和 Web 链接，将非路由的文件路径显示为文本。
 * [POS]: 工作台和项目概览共用的 Markdown 渲染器。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
/* eslint-disable react-refresh/only-export-components -- renderInlineMarkdown is exported for the link tests. */
import { useMemo, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import ChatRichContent, { InteractiveTable } from './ChatRichContent';
import { parseChatRichContent } from './chatRichContentParser';

/**
 * Validates whether a line is a GFM table separator row (e.g. |---|:---:|---:|).
 */
function isTableSeparatorRow(line) {
  if (!line) return false;
  const trimmed = line.trim();
  if (!trimmed.startsWith('|') || !trimmed.endsWith('|')) return false;
  const cells = trimmed.slice(1, -1).split('|').map((s) => s.trim());
  if (!cells.length) return false;
  return cells.every((cell) => /^:?-+:?$/.test(cell));
}

function isProductLink(url) {
  if (!url.startsWith('/')) return false;
  const path = url.split(/[?#]/, 1)[0];
  let parts;
  try {
    parts = path.split('/').slice(1).map(decodeURIComponent);
  } catch {
    return false;
  }
  if (parts.some((part) => (
    part === '.' || part === '..' || /[\\/%]/.test(part) || [...part].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)
  ))) return false;
  return /^\/(?:legacy|projects|inbox|activity|settings)?$/.test(path)
    || /^\/projects\/[^/]+\/(?:home|team|work|knowledge|settings(?:\/[^/]+)?)$/.test(path);
}

/**
 * Parses inline markdown tokens (bold, italic, strikethrough, inline code, links).
 * Returns an array of React nodes.
 */
export function renderInlineMarkdown(text) {
  if (!text) return null;

  // Regex splitting on code `...`, bold **...**, italic *...*, strikethrough ~~...~~, and links [...](...)
  const tokenRegex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~|\[[^\]]+\]\([^)]+\))/g;
  const parts = text.split(tokenRegex);

  return parts.map((part, index) => {
    if (!part) return null;

    // Inline code
    if (part.startsWith('`') && part.endsWith('`') && part.length >= 2) {
      return (
        <code key={index} className="md-inline-code">
          {part.slice(1, -1)}
        </code>
      );
    }

    // Bold
    if (part.startsWith('**') && part.endsWith('**') && part.length >= 4) {
      return (
        <strong key={index} className="md-bold">
          {renderInlineMarkdown(part.slice(2, -2))}
        </strong>
      );
    }

    // Italic
    if (part.startsWith('*') && part.endsWith('*') && part.length >= 2) {
      return (
        <em key={index} className="md-italic">
          {renderInlineMarkdown(part.slice(1, -1))}
        </em>
      );
    }

    // Strikethrough
    if (part.startsWith('~~') && part.endsWith('~~') && part.length >= 4) {
      return (
        <del key={index} className="md-del">
          {renderInlineMarkdown(part.slice(2, -2))}
        </del>
      );
    }

    // Links: [label](url)
    const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (linkMatch) {
      const [, label, url] = linkMatch;
      // 报告中的绝对文件路径显示为文本。站内链接须对应现行路由。
      const isWebLink = /^https?:\/\//i.test(url) || url.startsWith('#') || isProductLink(url);
      const isLocalReference = url.startsWith('/') || /^file:/i.test(url)
        || /^[A-Za-z]:[\\/]/.test(url);
      return isWebLink ? (
        <a
          key={index}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="md-link"
        >
          {label}
        </a>
      ) : (
        <span key={index} className="md-link-text">
          {isLocalReference ? `${label} (${url}; preview unavailable)` : label}
        </span>
      );
    }

    return part;
  });
}

/**
 * Parses markdown blocks and returns structured AST items.
 */
function parseMarkdownBlocks(markdown) {
  if (!markdown || typeof markdown !== 'string') return [];

  const rawLines = markdown.split('\n');
  const blocks = [];
  let i = 0;

  while (i < rawLines.length) {
    const line = rawLines[i];
    const trimmed = line.trim();

    // 1. Fenced Code Block
    const openingFence = trimmed.match(/^(`{3,})([^`]*)$/);
    if (openingFence) {
      const lang = openingFence[2].trim();
      const closingFence = new RegExp('^`{' + openingFence[1].length + ',}\\s*$');
      const codeLines = [];
      i++;
      while (i < rawLines.length && !closingFence.test(rawLines[i].trim())) {
        codeLines.push(rawLines[i]);
        i++;
      }
      i++; // skip closing ```
      blocks.push({
        type: 'code',
        lang: lang || 'text',
        content: codeLines.join('\n'),
      });
      continue;
    }

    // 2. Empty line
    if (!trimmed) {
      i++;
      continue;
    }

    // 3. Headings (# H1 - ###### H6)
    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const content = headingMatch[2].trim();
      blocks.push({
        type: 'heading',
        level,
        content,
      });
      i++;
      continue;
    }

    // 4. Horizontal Rule
    if (/^(\*\*\*|---|___)$/.test(trimmed)) {
      blocks.push({ type: 'hr' });
      i++;
      continue;
    }

    // 5. Blockquote
    if (line.startsWith('>')) {
      const quoteLines = [];
      while (i < rawLines.length && rawLines[i].startsWith('>')) {
        quoteLines.push(rawLines[i].replace(/^>\s?/, ''));
        i++;
      }
      blocks.push({
        type: 'blockquote',
        content: quoteLines.join('\n'),
      });
      continue;
    }

    // 6. GFM Table (| col1 | col2 | followed by separator |---|---|)
    if (
      trimmed.startsWith('|') &&
      trimmed.endsWith('|') &&
      i + 1 < rawLines.length &&
      isTableSeparatorRow(rawLines[i + 1])
    ) {
      const headerRow = trimmed.slice(1, -1).split('|').map((s) => s.trim());
      i += 2; // skip header and separator
      const rows = [];
      while (i < rawLines.length && rawLines[i].trim().startsWith('|') && rawLines[i].trim().endsWith('|')) {
        rows.push(rawLines[i].trim().slice(1, -1).split('|').map((s) => s.trim()));
        i++;
      }
      blocks.push({
        type: 'table',
        headers: headerRow,
        rows,
      });
      continue;
    }

    // 7. Lists: retain indentation and marker kind instead of flattening a plan.
    const listMatch = line.match(/^([ \t]*)([-*+]|\d+\.)\s+(.*)$/);
    if (listMatch) {
      const lists = [];
      const stack = [];
      while (i < rawLines.length && rawLines[i].trim()) {
        const currentLine = rawLines[i];
        const match = currentLine.match(/^([ \t]*)([-*+]|\d+\.)\s+(.*)$/);
        const indent = currentLine.match(/^[ \t]*/)[0].replace(/\t/g, '    ').length;
        if (!match) {
          // A continuation belongs to the nearest containing item, not whichever
          // deeply nested item happened to precede it.
          while (stack.length && indent <= stack.at(-1).indent) stack.pop();
          if (!stack.length) break;
          const item = stack.at(-1).list.items.at(-1);
          if (item.children.length) {
            const previous = item.children.at(-1);
            if (previous.type === 'continuation') previous.content += `\n${currentLine.trim()}`;
            else item.children.push({ type: 'continuation', content: currentLine.trim() });
          } else item.content += `\n${currentLine.trim()}`;
          i++;
          continue;
        }

        const ordered = /^\d+\./.test(match[2]);
        while (stack.length && indent < stack.at(-1).indent) stack.pop();
        if (stack.length && indent === stack.at(-1).indent && ordered !== stack.at(-1).list.ordered) {
          stack.pop();
        }
        if (!stack.length || indent > stack.at(-1).indent) {
          const list = { type: 'list', ordered, start: ordered ? Number.parseInt(match[2], 10) : undefined, items: [] };
          const parent = stack.at(-1)?.list.items.at(-1);
          (parent ? parent.children : lists).push(list);
          stack.push({ indent, list });
        }
        let content = match[3];
        let checked = null;
        const taskMatch = content.match(/^\[([ xX])\]\s+(.*)$/);
        if (taskMatch) {
          checked = taskMatch[1].toLowerCase() === 'x';
          content = taskMatch[2];
        }
        stack.at(-1).list.items.push({ isOrdered: ordered, checked, content, children: [] });
        i++;
      }
      blocks.push(...lists);
      continue;
    }

    // 8. Paragraph
    const paraLines = [line];
    i++;
    while (
      i < rawLines.length &&
      rawLines[i].trim() &&
      !rawLines[i].trim().startsWith('#') &&
      !/^(`{3,})([^`]*)$/.test(rawLines[i].trim()) &&
      !rawLines[i].trim().startsWith('>') &&
      !(rawLines[i].trim().startsWith('|') && rawLines[i].trim().endsWith('|') && i + 1 < rawLines.length && isTableSeparatorRow(rawLines[i + 1])) &&
      !rawLines[i].match(/^(\s*)([-*+]|\d+\.)\s+/)
    ) {
      paraLines.push(rawLines[i]);
      i++;
    }
    blocks.push({
      type: 'paragraph',
      content: paraLines.join('\n'),
    });
  }

  return blocks;
}

function MarkdownList({ block }) {
  const ListTag = block.ordered ? 'ol' : 'ul';
  return (
    <ListTag start={block.ordered ? block.start : undefined} className={`md-list ${block.ordered ? 'md-ol' : 'md-ul'}`}>
      {block.items.map((item, index) => (
        <li key={index} className={item.checked !== null ? 'md-task-item' : 'md-list-item'}>
          {item.checked !== null && (
            <input type="checkbox" checked={item.checked} readOnly className="md-task-checkbox" />
          )}
          <div>
            <span className={item.checked ? 'md-task-done' : undefined}>
              {renderInlineMarkdown(item.content)}
            </span>
            {item.children.map((child, childIndex) => child.type === 'continuation'
              ? <div key={childIndex}>{renderInlineMarkdown(child.content)}</div>
              : <MarkdownList key={childIndex} block={child} />)}
          </div>
        </li>
      ))}
    </ListTag>
  );
}

function CodeBlock({ lang, content }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore clipboard error
    }
  };

  return (
    <div className="md-code-block">
      <div className="md-code-header">
        <span className="md-code-lang">{lang || 'text'}</span>
        <button
          type="button"
          className="md-code-copy-btn"
          onClick={handleCopy}
          title="Copy code"
        >
          {copied ? <Check size={13} color="#34d399" /> : <Copy size={13} />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <pre className="md-code-content">
        <code>{content}</code>
      </pre>
    </div>
  );
}

export default function MarkdownView({ content, className = '', interactive = false, language = 'en' }) {
  const blocks = useMemo(() => parseMarkdownBlocks(content), [content]);

  if (!content) return null;

  return (
    <div className={`md-renderer ${className}`}>
      {blocks.map((block, idx) => {
        switch (block.type) {
          case 'heading': {
            const Tag = `h${block.level}`;
            return (
              <Tag key={idx} className={`md-h${block.level}`}>
                {renderInlineMarkdown(block.content)}
              </Tag>
            );
          }
          case 'paragraph': {
            return (
              <p key={idx} className="md-p">
                {renderInlineMarkdown(block.content)}
              </p>
            );
          }
          case 'code': {
            const rich = interactive && block.lang === 'goodteam-ui' ? parseChatRichContent(block.content) : null;
            if (rich) return <ChatRichContent key={`${idx}:${block.content}`} value={rich} language={language} renderCell={renderInlineMarkdown} />;
            return <CodeBlock key={idx} lang={block.lang} content={block.content} />;
          }
          case 'blockquote': {
            return (
              <blockquote key={idx} className="md-blockquote">
                {renderInlineMarkdown(block.content)}
              </blockquote>
            );
          }
          case 'table': {
            if (interactive) return <InteractiveTable key={`${idx}:${JSON.stringify(block)}`} headers={block.headers} rows={block.rows} language={language} renderCell={renderInlineMarkdown} />;
            return (
              <div key={idx} className="md-table-wrap">
                <table className="md-table">
                  <thead>
                    <tr>
                      {block.headers.map((h, hIdx) => (
                        <th key={hIdx}>{renderInlineMarkdown(h)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, rIdx) => (
                      <tr key={rIdx}>
                        {row.map((cell, cIdx) => (
                          <td key={cIdx}>{renderInlineMarkdown(cell)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          }
          case 'list': {
            return <MarkdownList key={idx} block={block} />;
          }
          case 'hr': {
            return <hr key={idx} className="md-hr" />;
          }
          default:
            return null;
        }
      })}
    </div>
  );
}
