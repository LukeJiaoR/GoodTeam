import { useId } from 'react';
import { layoutFlowchart } from './flowchartLayout';

export default function ChatFlowchart({ value, language }) {
  const id = useId();
  const layout = layoutFlowchart(value);
  const labels = new Map(value.nodes.map(node => [node.id, node.label]));
  const connections = language?.startsWith('zh') ? '查看流程关系' : 'View connections';
  return <section className="chat-rich-flowchart" aria-label={value.title}>
    <h3>{value.title}</h3>
    <div className="chat-rich-flowchart__scroll" role="region" aria-label={value.title} tabIndex={0}>
      <div className="chat-rich-flowchart__canvas" style={{ width: layout.width, height: layout.height }}>
        <svg width={layout.width} height={layout.height} aria-hidden="true">
          <defs><marker id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
          {layout.edges.map((edge, index) => <g key={index}>
            <path className="chat-rich-flowchart__edge" d={edge.path} markerEnd={`url(#${id})`} />
            {edge.label ? <text x={edge.labelX} y={edge.labelY} textAnchor="middle"><title>{edge.label}</title>{[...edge.label].length > 12 ? `${[...edge.label].slice(0, 11).join('')}…` : edge.label}</text> : null}
          </g>)}
        </svg>
        {layout.nodes.map(node => <div key={node.id} className="chat-rich-flowchart__node" data-kind={node.kind || 'step'}
          style={{ left: node.x, top: node.y, width: node.width, height: node.height }}><span>{node.label}</span></div>)}
      </div>
    </div>
    <details><summary>{connections}</summary><ul>{value.edges.map((edge, index) => <li key={index}>
      {labels.get(edge.from)} → {edge.label ? `${edge.label} → ` : ''}{labels.get(edge.to)}
    </li>)}</ul></details>
  </section>;
}
