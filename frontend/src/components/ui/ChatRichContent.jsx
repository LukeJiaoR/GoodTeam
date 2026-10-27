import { useId, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, Check, Copy, Search } from 'lucide-react';
import ChatFlowchart from './ChatFlowchart';
import './chatRichContent.css';

const labels = {
  en: { filter: 'Filter table', copy: 'Copy data', copied: 'Copied', failed: 'Copy failed; try again', empty: 'No matching rows', rows: 'rows', column: 'Column', metric: 'Metric', data: 'View data', preview: 'Preview', table: 'Data table' },
  zh: { filter: '筛选表格', copy: '复制数据', copied: '已复制', failed: '复制失败，请重试', empty: '没有匹配的数据', rows: '行', column: '列', metric: '指标', data: '查看数据', preview: '展开预览', table: '数据表格' },
};
const copyFor = language => labels[String(language).startsWith('zh') ? 'zh' : 'en'];
const numeric = value => /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(String(value).trim());
const tsvCell = value => /[\t\n\r"]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;

export function InteractiveTable({ headers, rows, language = 'en', renderCell = value => value, title }) {
  const copy = copyFor(language);
  const filterId = useId();
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState(null);
  const [copied, setCopied] = useState('');
  const width = rows.reduce((maximum, row) => Math.max(maximum, row.length), headers.length);
  const columns = Array.from({ length: width }, (_, index) => headers[index] || `${copy.column} ${index + 1}`);
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase(language);
    const filtered = rows.filter(row => !needle || row.some(cell => String(cell).toLocaleLowerCase(language).includes(needle)));
    if (!sort) return filtered;
    const collator = new Intl.Collator(language, { numeric: true, sensitivity: 'base' });
    return [...filtered].sort((a, b) => {
      const left = String(a[sort.column] ?? ''), right = String(b[sort.column] ?? '');
      const compared = numeric(left) && numeric(right) ? Number(left) - Number(right) : collator.compare(left, right);
      return sort.direction === 'ascending' ? compared : -compared;
    });
  }, [rows, query, sort, language]);
  async function copyData() {
    try {
      await navigator.clipboard.writeText([columns, ...visible.map(row => columns.map((_, index) => String(row[index] ?? '')))]
        .map(row => row.map(tsvCell).join('\t')).join('\n'));
      setCopied('copied');
    } catch { setCopied('failed'); }
  }
  return <section className="chat-rich-table" aria-label={title || copy.table}>
    {title ? <h3>{title}</h3> : null}
    <div className="chat-rich-table__toolbar">
      <label htmlFor={filterId} className="chat-rich-table__search"><Search size={15} aria-hidden="true" /><span className="sr-only">{copy.filter}</span>
        <input id={filterId} type="search" name="table-filter" autoComplete="off" placeholder={copy.filter} value={query} onChange={event => { setQuery(event.target.value); setCopied(''); }} />
      </label>
      <button type="button" onClick={copyData}>{copied === 'copied' ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}{copy.copy}</button>
    </div>
    <div className="chat-rich-table__scroll" role="region" aria-label={title || copy.table} tabIndex={0}>
      <table><thead><tr>{columns.map((column, index) => {
        const direction = sort?.column === index ? sort.direction : 'none';
        const Icon = direction === 'ascending' ? ArrowUp : direction === 'descending' ? ArrowDown : ArrowUpDown;
        return <th key={index} scope="col" aria-sort={direction}><button type="button" onClick={() => {
          setCopied('');
          setSort(direction === 'descending' ? null : { column: index, direction: direction === 'ascending' ? 'descending' : 'ascending' });
        }}>{renderCell(column)}<Icon size={14} aria-hidden="true" /></button></th>;
      })}</tr></thead>
        <tbody>{visible.map((row, index) => <tr key={index}>{columns.map((_, column) => <td key={column}>{renderCell(String(row[column] ?? ''))}</td>)}</tr>)}</tbody>
      </table>
      {!visible.length ? <p className="chat-rich-empty">{copy.empty}</p> : null}
    </div>
    <div className="chat-rich-table__status" role="status"><span>{visible.length} / {rows.length} {copy.rows}</span>{copied ? <span>{copy[copied]}</span> : null}</div>
  </section>;
}

function AnswerChart({ value, language, renderCell }) {
  const copy = copyFor(language);
  const metricId = useId();
  const [metric, setMetric] = useState(1);
  const [active, setActive] = useState(0);
  const values = value.rows.map(row => row[metric]);
  const minimum = Math.min(0, ...values), maximum = Math.max(0, ...values);
  const range = maximum - minimum || 1;
  const zero = -minimum / range * 100;
  return <section className="chat-rich-chart" aria-label={value.title}>
    <header><h3>{value.title}</h3><label htmlFor={metricId}>{copy.metric}<select id={metricId} value={metric} onChange={event => setMetric(Number(event.target.value))}>
      {value.columns.slice(1).map((column, index) => <option key={index} value={index + 1}>{column}</option>)}
    </select></label></header>
    <div className="chat-rich-chart__bars">{value.rows.map((row, index) => {
      const position = (row[metric] - minimum) / range * 100;
      return <button key={index} type="button" className="chat-rich-chart__row" aria-pressed={active === index} onPointerMove={() => setActive(index)} onFocus={() => setActive(index)} onClick={() => setActive(index)}>
        <span className="chat-rich-chart__label">{row[0]}</span>
        <span className="chat-rich-chart__plot" aria-hidden="true"><span className="chat-rich-chart__zero" style={{ left: `${zero}%` }} /><span className="chat-rich-chart__bar" style={{ left: `${Math.min(position, zero)}%`, width: `${Math.abs(position - zero)}%` }} /></span>
        <strong>{row[metric].toLocaleString(language)}</strong>
      </button>;
    })}</div>
    <output className="chat-rich-chart__detail">{value.rows[active][0]} · {value.columns[metric]}: {value.rows[active][metric].toLocaleString(language)}</output>
    <details className="chat-rich-chart__data"><summary>{copy.data}</summary><InteractiveTable headers={value.columns} rows={value.rows} language={language} renderCell={renderCell} title={value.title} /></details>
  </section>;
}

export default function ChatRichContent({ value, language = 'en', renderCell = content => content }) {
  const copy = copyFor(language);
  if (value.type === 'chart') return <AnswerChart value={value} language={language} renderCell={renderCell} />;
  if (value.type === 'flowchart') return <ChatFlowchart value={value} language={language} />;
  return <section className={`chat-rich-${value.type}`} aria-label={value.title}>
    <h3>{value.title}</h3>
    {value.type === 'steps' ? <ol>{value.items.map((item, index) => <li key={index}><details>
      <summary><span className="chat-rich-step-number" aria-hidden="true">{index + 1}</span>{item.title}</summary><div className="chat-rich-preview">{renderCell(item.body)}</div>
    </details></li>)}</ol> : <div className="chat-rich-cards__grid">{value.items.map((item, index) => <article key={index}>
      <h4>{item.title}</h4>{item.summary ? <p>{renderCell(item.summary)}</p> : null}
      <details><summary>{copy.preview}</summary><div className="chat-rich-preview">{renderCell(item.body)}</div></details>
    </article>)}</div>}
  </section>;
}
