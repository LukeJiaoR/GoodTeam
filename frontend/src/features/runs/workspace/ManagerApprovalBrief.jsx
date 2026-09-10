import FileLocation from '../../../components/ui/FileLocation';
import { fileName, isAbsoluteFilePath } from '../../../lib/presentation/filePresentation';
import {
  recommendationReasonText,
  reviewGateName,
  translated,
} from './presentationUtils';

function approvalBriefDecision(brief, t) {
  const decision = brief?.decision || {};
  const gate = brief?.approval || '';
  return translated(
    t,
    `work.manager.approvalBrief.decision.${gate}`,
    t('work.manager.approvalBrief.decision.general', {
      gate: reviewGateName(gate, t),
      subject: decision.subject || reviewGateName(gate, t),
    }),
    { gate: reviewGateName(gate, t), subject: decision.subject || reviewGateName(gate, t) },
  );
}

function approvalBriefConsequence(brief, t) {
  const consequence = brief?.consequence || {};
  const nextStage = consequence.next_stage || '';
  return translated(
    t,
    `work.manager.approvalBrief.after.${nextStage}`,
    t('work.manager.approvalBrief.after.general'),
    { task: consequence.task_title || '' },
  );
}

function briefEvidenceLabel(item, t) {
  if (item.label) return item.label;
  return translated(t, `work.manager.approvalBrief.evidenceKinds.${item.kind}`, item.kind || '—');
}

function briefStatusLabel(status, t) {
  if (!status) return '';
  const value = String(status);
  return translated(t, `work.manager.approvalBrief.status.${value}`, value.replaceAll('_', ' '));
}

function shortEvidenceRef(value) {
  const text = String(value || '');
  if (isAbsoluteFilePath(text)) return fileName(text);
  if (text.startsWith('sha256:') && text.length > 22) return `${text.slice(0, 19)}…`;
  if (/^[a-f0-9]{20,}$/i.test(text)) return `${text.slice(0, 12)}…`;
  return text;
}


function BriefFactGroups({ emptyKey, groups = [], t }) {
  if (!groups.length) return <p className="team-approval-brief__empty">{t(emptyKey)}</p>;
  return groups.map((group, index) => (
    <div className="team-approval-brief__fact-group" key={`${group.heading || group.source}-${index}`}>
      <span>
        {group.heading || translated(
          t,
          `work.manager.approvalBrief.sources.${group.source}`,
          t('work.manager.approvalBrief.sources.task_contract'),
        )}
      </span>
      <ul>
        {(group.items || []).map((item, itemIndex) => <li key={`${item}-${itemIndex}`}>{item}</li>)}
      </ul>
    </div>
  ));
}

function briefRiskText(risk, t) {
  if (risk.text) return risk.text;
  if (risk.kind === 'evidence_gap') {
    return t('work.manager.approvalBrief.riskEvidenceGap', {
      evidence: translated(
        t,
        `work.manager.approvalBrief.evidenceKinds.${risk.source}`,
        risk.source || t('work.manager.approvalBrief.evidence'),
      ),
    });
  }
  if (risk.kind === 'blockers') {
    return t('work.manager.approvalBrief.riskBlockers', { count: risk.count || 0 });
  }
  if (risk.kind === 'reviewer_aggregate') {
    return t('work.manager.approvalBrief.riskReviewerAggregate');
  }
  return briefStatusLabel(risk.status || risk.severity, t);
}

function ManagerApprovalBrief({ brief, t }) {
  const decision = brief.decision || {};
  const artifact = decision.artifact || {};
  const scope = brief.scope || {};
  const evidence = brief.evidence || [];
  const missionBlockers = brief.mission_blockers || [];
  const risks = brief.risks || [];
  const consequence = brief.consequence || {};
  const recovery = consequence.recovery || [];
  const recommendation = brief.recommendation || {};
  const recommendationLabel = recommendation.verdict
    ? translated(
      t,
      `work.manager.approvalBrief.recommendations.${recommendation.verdict}`,
      String(recommendation.verdict).replaceAll('_', ' '),
    )
    : t('work.manager.approvalBrief.recommendations.none');

  return (
    <div className="team-approval-brief" data-approval-type={brief.approval_type || 'general'}>
      <section className="team-approval-brief__decision">
        <span className="team-approval-brief__kicker">{t('work.manager.approvalBrief.decisionLabel')}</span>
        <p>{approvalBriefDecision(brief, t)}</p>
        {decision.summary ? <blockquote>{decision.summary}</blockquote> : null}
        {artifact.label || artifact.version ? (
          <code title={artifact.version || artifact.label}>
            {[artifact.label, shortEvidenceRef(artifact.version)].filter(Boolean).join(' · ')}
          </code>
        ) : null}
      </section>

      <section className="team-approval-brief__section">
        <h4>{t('work.manager.approvalBrief.scope')}</h4>
        <div className="team-approval-brief__scope-block">
          <span>{t('work.manager.approvalBrief.inScope')}</span>
          <BriefFactGroups
            emptyKey="work.manager.approvalBrief.noScope"
            groups={scope.included || []}
            t={t}
          />
        </div>
        <div className="team-approval-brief__scope-block">
          <span>{t('work.manager.approvalBrief.outOfScope')}</span>
          <BriefFactGroups
            emptyKey="work.manager.approvalBrief.noExclusions"
            groups={scope.excluded || []}
            t={t}
          />
        </div>
      </section>

      <section className="team-approval-brief__section">
        <h4>{t('work.manager.approvalBrief.evidence')}</h4>
        {evidence.length ? (
          <ul className="team-approval-brief__evidence-list">
            {evidence.map((item, index) => {
              const deliveryRefs = [
                item.pr ? t('work.manager.approvalBrief.pullRequest', { value: item.pr }) : '',
                item.branch ? t('work.manager.approvalBrief.branch', { value: item.branch }) : '',
                item.commit ? t('work.manager.approvalBrief.commit', { value: shortEvidenceRef(item.commit) }) : '',
              ].filter(Boolean);
              return (
                <li data-status={item.status || 'recorded'} key={`${item.kind}-${item.label}-${index}`}>
                  <div>
                    <span>{briefEvidenceLabel(item, t)}</span>
                    {item.status ? <strong>{briefStatusLabel(item.status, t)}</strong> : null}
                  </div>
                  {item.detail ? (
                    item.kind === 'artifact' ? (
                      isAbsoluteFilePath(item.detail) ? <FileLocation path={item.detail} t={t} /> : <code title={item.detail}>{shortEvidenceRef(item.detail)}</code>
                    ) : <p>{item.detail}</p>
                  ) : null}
                  {deliveryRefs.length ? <code>{deliveryRefs.join(' · ')}</code> : null}
                  {item.count ? <p>{t('work.manager.approvalBrief.itemCount', { count: item.count })}</p> : null}
                </li>
              );
            })}
          </ul>
        ) : <p className="team-approval-brief__empty">{t('work.manager.approvalBrief.noEvidence')}</p>}
      </section>

      {missionBlockers.length ? (
        <section className="team-approval-brief__section team-approval-brief__mission-blockers">
          <h4>{t('work.manager.approvalBrief.missionBlockers')}</h4>
          <p>{t('work.manager.approvalBrief.missionBlockersNote')}</p>
          <ul>
            {missionBlockers.map((blocker, index) => <li key={`${blocker}-${index}`}>{blocker}</li>)}
          </ul>
        </section>
      ) : null}

      <section className="team-approval-brief__section">
        <h4>{t('work.manager.approvalBrief.risk')}</h4>
        {risks.length ? (
          <ul className="team-approval-brief__risk-list">
            {risks.map((risk, index) => (
              <li key={`${risk.kind}-${risk.text || risk.source}-${index}`}>
                {risk.heading ? <span>{risk.heading}</span> : null}
                <p>{briefRiskText(risk, t)}</p>
              </li>
            ))}
          </ul>
        ) : <p className="team-approval-brief__empty">{t('work.manager.approvalBrief.noRisk')}</p>}
      </section>

      <section className="team-approval-brief__section">
        <h4>{t('work.manager.approvalBrief.afterApproval')}</h4>
        <p>{approvalBriefConsequence(brief, t)}</p>
        {consequence.task_title ? (
          <p className="team-approval-brief__next-task">
            {t('work.manager.approvalBrief.nextTask', { task: consequence.task_title })}
          </p>
        ) : null}
        {brief.approval_type === 'release' ? (
          <div className="team-approval-brief__recovery">
            <span>{t('work.manager.approvalBrief.recovery')}</span>
            <BriefFactGroups
              emptyKey="work.manager.approvalBrief.noRecovery"
              groups={recovery}
              t={t}
            />
          </div>
        ) : null}
      </section>

      <section
        className="team-approval-brief__recommendation"
        data-verdict={recommendation.verdict || 'none'}
      >
        <span className="team-approval-brief__kicker">{t('work.manager.approvalBrief.recommendation')}</span>
        <strong>{recommendationLabel}</strong>
        {recommendation.confidence ? (
          <small>
            {t('work.manager.approvalBrief.confidence', {
              confidence: briefStatusLabel(recommendation.confidence, t),
            })}
          </small>
        ) : null}
        {(recommendation.reasons || []).length ? (
          <ul>
            {recommendation.reasons.map((reason, index) => (
              <li key={`${reason.kind}-${index}`}>{recommendationReasonText(reason, t)}</li>
            ))}
          </ul>
        ) : <p>{t('work.manager.approvalBrief.noRecommendationReason')}</p>}
      </section>
    </div>
  );
}


export default ManagerApprovalBrief;
