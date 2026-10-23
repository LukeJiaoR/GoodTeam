import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import EvidenceSummary from './EvidenceSummary';
import { translate } from '../i18n';

const t = (key, params) => translate('en', key, params);
const report = { job_id: 'job-review-1', profile_id: 'correctness-security', artifact_version: 'sha256:artifact',
  report_status: 'available', report_sha256: 'sha256:report', content: 'High: QA BLOCKED. exit 1; zero tests.\nMedium: patch identity missing.' };

test('findings and exact attribution are readable without expanding JSON, while QA stays unknown', () => {
  render(<EvidenceSummary evidence={{ qa_status: 'unknown', blocker_count: 0, reviewer_reports: [report] }} t={t} />);
  expect(screen.getByRole('region', { name: 'Independent reviewer reports' })).toHaveTextContent('High: QA BLOCKED');
  expect(screen.getByText(/Medium: patch identity missing/)).toBeVisible();
  expect(screen.getByText(/Job \/ reviewed artifact version/)).toHaveTextContent('sha256:artifact');
  expect(screen.getByText(/Report SHA-256/)).toHaveTextContent('sha256:report');
  expect(screen.getByText('QA: Unknown')).toBeVisible();
  expect(screen.getByText(/Worker completion is not test success/)).toBeVisible();
});

test('missing current report is explicit and never shows supplied stale text', () => {
  render(<EvidenceSummary evidence={{ qa_status: 'unknown', reviewer_reports: [{ ...report, report_status: 'unavailable' }] }} t={t} />);
  expect(screen.getByRole('status')).toHaveTextContent('exact report is unavailable');
  expect(screen.queryByText(/High: QA BLOCKED/)).not.toBeInTheDocument();
});

test('report content is inert text, not a link or executable markup', () => {
  render(<EvidenceSummary evidence={{ qa_status: 'unknown', reviewer_reports: [{ ...report, content: '<img src=x onerror=alert(1)> [open](file:///private)' }] }} t={t} />);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(screen.getByText(/onerror=alert/)).toBeVisible();
});
