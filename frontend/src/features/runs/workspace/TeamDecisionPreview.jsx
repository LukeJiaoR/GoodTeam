function fields(value, prefix = '') {
  return Object.entries(value || {}).flatMap(([key, item]) => {
    const name = prefix ? `${prefix}.${key}` : key;
    return item && typeof item === 'object' ? fields(item, name) : [[name, item]];
  });
}

function display(value, field, t) {
  if (value == null || value === '') return t('assistant.preview.empty');
  if (field === 'lifecycle') return t(`team.lifecycle.${value}`);
  if (field === 'state') return t(`team.state.${value}`);
  return String(value);
}

export default function TeamDecisionPreview({ preview, commands, t }) {
  if (!preview.changes?.length) return <p>{t('assistant.preview.unchanged')}</p>;
  return <ul className="assistant-team-preview">
    {preview.changes.map((change, index) => {
      const before = Object.fromEntries(fields(change.before));
      const after = Object.fromEntries(fields(change.after));
      const changed = [...new Set([...Object.keys(before), ...Object.keys(after)])]
        .filter((key) => before[key] !== after[key]);
      const target = change.target;
      return <li key={index}>
        <strong>{target.label}{target.name ? ` / ${target.name}` : ''}</strong>
        <small>{target.id} · {t(`assistant.command.${commands[change.command_index]?.kind}`)}</small>
        <table>
          <thead><tr><th scope="col">{t('assistant.preview.field')}</th><th scope="col">{t('assistant.preview.before')}</th><th scope="col">{t('assistant.preview.after')}</th></tr></thead>
          <tbody>{changed.map((key) => <tr key={key}>
            <th scope="row">{t(`assistant.preview.${key.split('.').at(-1)}`)}</th>
            <td>{display(before[key], key, t)}</td><td>{display(after[key], key, t)}</td>
          </tr>)}</tbody>
        </table>
      </li>;
    })}
  </ul>;
}
