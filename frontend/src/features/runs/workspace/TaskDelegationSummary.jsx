/** Show the exact Manager-authored contract before execution confirmation. */
export default function TaskDelegationSummary({ delegation, labels }) {
  if (!delegation) return null;
  return <dl className="task-delegation-summary">
    {['purpose', 'context', 'scope'].map(key => <div key={key}>
      <dt>{labels[key]}</dt><dd style={{ whiteSpace: 'pre-wrap' }}>{delegation[key]}</dd>
    </div>)}
  </dl>;
}
