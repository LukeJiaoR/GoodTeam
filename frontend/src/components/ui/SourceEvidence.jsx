/**
 * [INPUT]: 依赖WorkspacePanel、MarkdownView 与 React portal
 * [OUTPUT]: 提供SourceEvidence、SourceEvidenceContent
 * [POS]: 按点击打开来源侧栏，复用模态焦点与关闭行为
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { useState } from 'react';
import { createPortal } from 'react-dom';
import { FileText } from 'lucide-react';
import WorkspacePanel from './WorkspacePanel';
import MarkdownView from './MarkdownView';

export function SourceEvidenceContent({ sections }) {
  return sections.map((section, index) => <section className="product-source-evidence" key={index}>
    <h3>{section.title}</h3>
    {section.literal ? <pre>{section.content}</pre> : <MarkdownView content={section.content} />}
  </section>);
}

// Source records are mounted outside the conversation, only on an explicit click.
export default function SourceEvidence({ sections = [], children, label, title, compact = false, t }) {
  const [host, setHost] = useState(null);
  if (!sections.length && !children) return null;
  return <>
    <button type="button" className={compact ? 'source-evidence-action' : 'product-button product-button--secondary'} aria-haspopup="dialog"
      onClick={event => setHost(event.currentTarget.closest('.product-shell') || document.body)}>
      {compact ? <><FileText size={14} aria-hidden="true" /><span className="sr-only">{label || t('product.evidence.open')}</span></>
        : label || t('product.evidence.open')}
    </button>
    {host ? createPortal(<WorkspacePanel title={title || t('product.evidence.title')}
      closeLabel={t('product.evidence.close')} onClose={() => setHost(null)}>
      <SourceEvidenceContent sections={sections} />
      {children}
    </WorkspacePanel>, host) : null}
  </>;
}
