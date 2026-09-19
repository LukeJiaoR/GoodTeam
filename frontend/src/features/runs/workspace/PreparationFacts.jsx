export default function PreparationFacts({ requirements, t }) {
  if (!requirements || typeof requirements !== 'object') return null;
  if (requirements.kind === 'constraint_confirmation') {
    const constraints = Array.isArray(requirements.constraints)
      ? requirements.constraints.filter((value) => typeof value === 'string' && value.trim()) : [];
    return constraints.length ? (
      <ul className="team-preparation-facts">{constraints.map((value, index) => <li key={index}>{value}</li>)}</ul>
    ) : <p>{t('work.taskAssistant.noConstraints')}</p>;
  }
  if (requirements.kind === 'worker_preflight_confirmation') {
    const message = typeof requirements.message === 'string' && requirements.message.trim()
      ? requirements.message : t('work.taskAssistant.unknownPreflight');
    return <p role="status" className="team-preparation-facts">{message}</p>;
  }
  return null;
}
