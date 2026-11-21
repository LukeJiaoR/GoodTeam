import { useState } from 'react';
import { AlertCircle } from 'lucide-react';
import Button from '../../../components/ui/Button';
import PreparationFacts from './PreparationFacts';

export function ProjectPreparationAttention({ attention, busy, onContinue, t }) {
  const requirements = attention.input_requirements || {};
  const commands = requirements.commands || [];
  const [selectedCommands, setSelectedCommands] = useState(() => commands.map((item) => item.id));
  const [goal, setGoal] = useState('');
  const [brief, setBrief] = useState('');
  const kind = requirements.kind || 'project_preparation';
  const submit = () => {
    let values = { confirmed: true };
    if (kind === 'command_confirmation') values = { confirmed_command_ids: selectedCommands };
    if (kind === 'project_initialization') values = { goal: goal.trim(), brief: brief.trim() };
    onContinue?.(attention.step_id, values);
  };
  const canContinue = kind !== 'project_initialization' || (goal.trim() && brief.trim());
  return (
    <section className="team-attention" data-attention-id={attention.id} data-kind="project_preparation" aria-label={t('work.manager.attention')}>
      <p className="team-attention__label"><AlertCircle aria-hidden="true" size={15} />{t('work.taskAssistant.preparationTitle')}</p>
      <h3>{t(`work.taskAssistant.preparation.${kind}`)}</h3>
      <PreparationFacts requirements={requirements} t={t} />
      {kind === 'command_confirmation' ? (
        <div className="team-preparation-options">
          {commands.map((command) => (
            <label key={command.id}>
              <input
                checked={selectedCommands.includes(command.id)}
                onChange={(event) => setSelectedCommands((current) => (
                  event.target.checked ? [...current, command.id] : current.filter((id) => id !== command.id)
                ))}
                type="checkbox"
              />
              <code>{command.command}</code>
            </label>
          ))}
        </div>
      ) : null}
      {kind === 'project_initialization' ? (
        <div className="team-preparation-fields">
          <label>{t('work.taskAssistant.projectGoal')}<input value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
          <label>{t('work.taskAssistant.projectBrief')}<textarea value={brief} onChange={(event) => setBrief(event.target.value)} /></label>
        </div>
      ) : null}
      <Button busy={busy === 'preparation'} disabled={!canContinue} onClick={submit}>
        {t('work.taskAssistant.continuePreparation')}
      </Button>
    </section>
  );
}
