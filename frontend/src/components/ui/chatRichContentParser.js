import { layoutFlowchart } from './flowchartLayout';

const text = value => typeof value === 'string' && value.trim().length > 0;

// Rich answers are data, never executable HTML, scripts or workflow actions.
export function parseChatRichContent(source) {
  if (typeof source !== 'string' || source.length > 100_000) return null;
  let value;
  try { value = JSON.parse(source); } catch { return null; }
  if (!value || !text(value.title) || value.title.length > 200) return null;
  if (value.type === 'flowchart') {
    if (!Array.isArray(value.nodes) || !value.nodes.length || value.nodes.length > 12
      || !value.nodes.every(node => node && typeof node.id === 'string' && /^[\w-]{1,40}$/.test(node.id)
        && text(node.label) && node.label.length <= 80
        && (node.kind === undefined || ['step', 'decision', 'terminal'].includes(node.kind)))
      || !Array.isArray(value.edges) || value.edges.length > 20) return null;
    const ids = new Set(value.nodes.map(node => node.id));
    if (ids.size !== value.nodes.length || !value.edges.every(edge => edge && ids.has(edge.from) && ids.has(edge.to)
      && (edge.label === undefined || (text(edge.label) && edge.label.length <= 40)))) return null;
    return layoutFlowchart(value) ? value : null;
  }
  if (value.type === 'chart') {
    if (!Array.isArray(value.columns) || value.columns.length < 2 || value.columns.length > 8
      || !value.columns.every(column => text(column) && column.length <= 120)
      || !Array.isArray(value.rows) || !value.rows.length || value.rows.length > 100
      || !value.rows.every(row => Array.isArray(row) && row.length === value.columns.length
        && text(row[0]) && row[0].length <= 120
        && row.slice(1).every(number => typeof number === 'number' && Number.isFinite(number)))) return null;
    return value;
  }
  if (!['steps', 'cards'].includes(value.type) || !Array.isArray(value.items)
    || !value.items.length || value.items.length > 20
    || !value.items.every(item => item && text(item.title) && item.title.length <= 200
      && text(item.body) && item.body.length <= 10_000
      && (item.summary === undefined || (typeof item.summary === 'string' && item.summary.length <= 1000)))) return null;
  return value;
}
