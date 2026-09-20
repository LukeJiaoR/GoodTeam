import { ChevronDown, Settings2 } from 'lucide-react';
import { useId, useState } from 'react';
import { rosteredWorkerCount } from './teamModel';

export default function ResponsibilityGroup({ group, children, forceOpen, expansionContext, onConfigure, saving, t }) {
  const [expanded, setExpanded] = useState(null);
  const contentId = useId();
  const open = expanded && expanded.context === expansionContext ? expanded.open : forceOpen;
  const label = t(`team.groups.${group.key}.title`);
  return (
    <section className="team-responsibility" aria-label={label}>
      <header className="team-responsibility__header">
        <button type="button" className="team-responsibility__toggle" aria-expanded={open} aria-controls={contentId}
          aria-label={t(open ? 'team.groups.collapse' : 'team.groups.expand', { name: label })}
          onClick={() => setExpanded({ context: expansionContext, open: !open })}>
          <ChevronDown aria-hidden="true" size={18} />
          <span>
            <strong>{label}</strong>
            <span className="team-responsibility__description">{t(`team.groups.${group.key}.description`)}</span>
            <small>{t('team.groups.counts', { roles: group.roles.length, workers: rosteredWorkerCount(group.roles) })}</small>
          </span>
        </button>
        <button type="button" className="product-button product-button--secondary" disabled={saving || !group.roles.some((role) => role.state === 'enabled')}
          onClick={onConfigure} aria-label={t('team.groups.configureAria', { name: label })}>
          <Settings2 aria-hidden="true" size={15} />{t('team.groups.configure')}
        </button>
      </header>
      <div id={contentId} className="team-responsibility__roles" hidden={!open}>{open ? children : null}</div>
    </section>
  );
}
