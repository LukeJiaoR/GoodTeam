import { useId, useRef, useState } from 'react';
import { ChevronDown, MessageCircleQuestion } from 'lucide-react';
import './projectQuestionBoard.css';

const GROUPS = ['awaiting_answer', 'answered', 'closed'];
const groupFor = item => ['resolved', 'not_needed'].includes(item.status) ? 'closed' : item.status === 'answered' ? 'answered' : 'awaiting_answer';

function QuestionCard({ item, onAnswer, t }) {
  const inputId = useId();
  const submitting = useRef(false);
  const [text, setText] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const canAnswer = item.status === 'awaiting_answer' && item.can_answer === true && Boolean(onAnswer);
  const submit = async event => {
    event.preventDefault();
    if (!canAnswer || submitting.current || !text.trim()) return;
    submitting.current = true;
    setPending(true);
    setError('');
    try {
      const result = await onAnswer(item.id, text.trim());
      if (result === false) setError(t('work.questions.sendUnconfirmed'));
    } catch (caught) {
      setError(caught?.message || t('work.questions.sendUnconfirmed'));
    } finally {
      submitting.current = false;
      setPending(false);
    }
  };
  return <article className="project-question-card" aria-label={item.question} data-status={item.status}>
    <div className="project-question-card__tags">
      <span className="project-question-card__status">{t(`work.questions.status.${item.status}`)}</span>
      {!item.current ? <span>{t('work.questions.historical')}</span> : null}
    </div>
    <h4>{item.question}</h4>
    {item.impact ? <p className="project-question-card__impact"><strong>{t('work.questions.impact')}</strong> {item.impact}</p> : null}
    <p className="project-question-card__why"><strong>{t('work.questions.whyHuman')}</strong> {item.why_human}</p>
    {item.rationale ? <details className="project-question-card__context">
      <summary>{t('work.questions.context')}</summary><p>{item.rationale}</p>
    </details> : null}
    {item.answer ? <div className="project-question-card__answer">
      <strong>{t('work.questions.answer')}</strong><p>{item.answer.text}</p>
    </div> : null}
    {item.status === 'answered' ? <p className="project-question-card__pending" role="status">
      {t(item.considered ? 'work.questions.considered' : 'work.questions.waitingManager')}
    </p> : null}
    {!item.current && ['awaiting_answer', 'answered'].includes(item.status) ? <p className="project-question-card__readonly">
      {t('work.questions.historicalOpen')}
    </p> : null}
    {item.resolution?.reason ? <div className="project-question-card__resolution">
      <strong>{t('work.questions.managerResolution')}</strong><p>{item.resolution.reason}</p>
    </div> : null}
    {canAnswer ? <form className="project-question-card__form" onSubmit={submit}>
      <label htmlFor={inputId}>{t('work.questions.yourAnswer')}</label>
      <textarea id={inputId} value={text} onChange={event => setText(event.target.value)}
        placeholder={t('work.questions.answerPlaceholder')} maxLength={8000} rows={2} disabled={pending} />
      {error ? <p className="project-question-card__error" role="alert">{error}</p> : null}
      <button type="submit" disabled={pending || !text.trim()}>
        {t(pending ? 'work.questions.sending' : error ? 'work.questions.retry' : 'work.questions.submit')}
      </button>
    </form> : item.status === 'awaiting_answer' && item.current ? <p className="project-question-card__readonly">{t('work.questions.cannotAnswer')}</p> : null}
  </article>;
}

export default function ProjectQuestionBoard({ board, onAnswerQuestion, t }) {
  // A successful message receipt can precede the next refreshed projection.
  // This only shows the recorded answer; resolution always comes from Manager.
  const [submitted, setSubmitted] = useState({});
  const items = (board?.items || []).map(item => item.status === 'awaiting_answer' && submitted[item.id]
    ? { ...item, status: 'answered', answer: { text: submitted[item.id] }, considered: false, can_answer: false } : item);
  const onAnswer = onAnswerQuestion ? async (id, text) => {
    const result = await onAnswerQuestion(id, text);
    if (result === false || result == null) return false;
    setSubmitted(previous => ({ ...previous, [id]: text }));
    return result;
  } : undefined;
  const cards = rows => <ul className="project-question-board__cards">{rows.map(item => <li key={item.id}>
    <QuestionCard item={item} onAnswer={onAnswer} t={t} />
  </li>)}</ul>;
  if (!items.length) return <section className="project-question-board project-question-board--empty" aria-label={t('work.questions.title')}>
    <MessageCircleQuestion size={15} aria-hidden="true" /><strong>{t('work.questions.title')}</strong><span>{t('work.questions.empty')}</span>
  </section>;
  return <section className="project-question-board" aria-label={t('work.questions.title')}>
    <details className="project-question-board__panel" open>
      <summary className="project-question-board__heading">
        <MessageCircleQuestion size={16} aria-hidden="true" /><strong>{t('work.questions.title')}</strong>
        <span className="project-question-board__total">{items.length}</span>
        <span className="project-question-board__hint">{t('work.questions.description')}</span>
        <ChevronDown className="project-question-board__chevron" size={15} aria-hidden="true" />
      </summary>
      <div className="project-question-board__columns">
        {GROUPS.map(group => {
          const rows = items.filter(item => groupFor(item) === group);
          const current = rows.filter(item => item.current);
          const history = rows.filter(item => !item.current);
          return <section key={group} className="project-question-board__column" aria-label={t(`work.questions.column.${group}`)}>
            <header><h3>{t(`work.questions.column.${group}`)}</h3><span>{rows.length}</span></header>
            <div className="project-question-board__list">
              {current.length ? group === 'closed' ? <details className="project-question-board__closed">
                <summary>{t('work.questions.showClosed', { count: current.length })}</summary>{cards(current)}
              </details> : cards(current) : <p className="project-question-board__none">{t('work.questions.noneCurrent')}</p>}
              {history.length ? <details className="project-question-board__history">
                <summary>{t('work.questions.showHistory', { count: history.length })}</summary>{cards(history)}
              </details> : null}
            </div>
          </section>;
        })}
      </div>
    </details>
  </section>;
}
