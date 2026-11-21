import { useMemo } from 'react';
import StatusBadge from './ui/StatusBadge';

/**
 * Shared checklist editor for binding Skills with `required` vs `on_demand` mode.
 */
export default function SkillBindingsEditor({ skills = [], bindings = [], onChange, idPrefix = 'skill' }) {
  const bindingMap = useMemo(() => {
    const map = new Map();
    for (const item of bindings || []) {
      if (item?.skill_id) {
        map.set(item.skill_id, item.mode || 'required');
      }
    }
    return map;
  }, [bindings]);

  const toggleSkill = (skillId, checked) => {
    const next = (bindings || []).filter((item) => item.skill_id !== skillId);
    if (checked) {
      next.push({ skill_id: skillId, mode: 'required' });
    }
    onChange(next);
  };

  const changeMode = (skillId, mode) => {
    const next = (bindings || []).map((item) =>
      item.skill_id === skillId ? { ...item, mode } : item,
    );
    onChange(next);
  };

  if (!skills.length) {
    return (
      <p style={{ margin: 0, fontSize: '0.84rem', color: 'var(--product-text-muted)' }}>
        当前暂无可用 Skill，请先在「Skill 能力库」中安装或扫描。
      </p>
    );
  }

  return (
    <div style={{ display: 'grid', gap: '0.45rem' }}>
      {skills.map((skill) => {
        const checked = bindingMap.has(skill.id);
        const mode = bindingMap.get(skill.id) || 'required';
        const inputId = `${idPrefix}-${skill.id}`;
        return (
          <div
            key={skill.id}
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '0.6rem',
              padding: '0.5rem 0.7rem',
              borderRadius: '7px',
              border: '1px solid var(--product-border)',
              background: checked ? 'var(--product-accent-soft)' : 'var(--product-bg)',
            }}
          >
            <label
              htmlFor={inputId}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '0.55rem',
                flex: 1,
                minWidth: '180px',
                cursor: 'pointer',
              }}
            >
              <input
                id={inputId}
                type="checkbox"
                checked={checked}
                onChange={(e) => toggleSkill(skill.id, e.target.checked)}
                style={{ marginTop: '3px', width: '15px', height: '15px', cursor: 'pointer' }}
              />
              <span style={{ display: 'grid', gap: '0.15rem' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontWeight: 620, fontSize: '0.86rem' }}>
                  {skill.name}
                  <StatusBadge tone={skill.source_type === 'builtin' ? 'neutral' : 'accent'}>
                    {skill.source_type}
                  </StatusBadge>
                </span>
                {skill.description ? (
                  <span style={{ fontSize: '0.76rem', color: 'var(--product-text-muted)' }}>
                    {skill.description}
                  </span>
                ) : null}
              </span>
            </label>

            {checked ? (
              <select
                aria-label={`${skill.name} 注入模式`}
                className="v2-select"
                style={{ width: 'auto', minHeight: '32px', fontSize: '0.8rem' }}
                value={mode}
                onChange={(e) => changeMode(skill.id, e.target.value)}
              >
                <option value="required">必用 (required)</option>
                <option value="on_demand">按需 (on_demand)</option>
              </select>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
