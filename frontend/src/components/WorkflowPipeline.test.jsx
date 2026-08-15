import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, test } from 'vitest';
import { translate } from '../i18n';
import WorkflowPipeline from './WorkflowPipeline';

function renderPipeline(steps) {
  render(
    <MemoryRouter>
      <WorkflowPipeline projectKey="DEMO" steps={steps} t={(key, params) => translate('en', key, params)} />
    </MemoryRouter>,
  );
}

const STATUSES = ['done', 'active', 'review-needed', 'blocked', 'ready', 'not-started'];

test('renders nothing without steps', () => {
  const { container } = render(
    <MemoryRouter>
      <WorkflowPipeline projectKey="DEMO" steps={[]} t={(key) => key} />
    </MemoryRouter>,
  );
  expect(container.firstChild).toBeNull();
});

test('renders every pipeline status with its stage data-status', () => {
  renderPipeline(STATUSES.map((status, index) => ({
    key: `step-${status}`,
    number: String(index + 1),
    title: `Stage ${index + 1}`,
    status,
    status_label: status,
  })));
  for (const status of STATUSES) {
    expect(screen.getByText(`Stage ${STATUSES.indexOf(status) + 1}`, { selector: 'strong' })).toBeVisible();
    expect(document.querySelector(`[data-status="${status}"]`)).not.toBeNull();
  }
});

test('links a review-needed gate straight to its review card', () => {
  renderPipeline([
    { key: 'prd', number: '2', title: 'Product / Requirement', status: 'review-needed', approval: 'prd', status_label: 'Needs review' },
  ]);
  const link = screen.getByRole('link');
  expect(link).toHaveAttribute('href', '/projects/DEMO/review/prd');
  expect(screen.getByText('Review PRD')).toBeVisible();
});

test('links a completed stage with an artifact to the knowledge artifact view', () => {
  renderPipeline([
    { key: 'rfc', number: '3', title: 'Architecture', status: 'done', artifact: 'rfc', artifact_path: '/runs/x/rfc.md', status_label: 'Done' },
  ]);
  const link = screen.getByRole('link');
  expect(link.getAttribute('href')).toContain('/projects/DEMO/knowledge');
  expect(link.getAttribute('href')).toContain('view=artifacts');
  expect(link.getAttribute('href')).toContain('artifact=rfc');
});

test('falls back to the Work page for stages without a review or artifact target', () => {
  renderPipeline([
    { key: 'impl', number: '9', title: 'Implementation', status: 'ready', status_label: 'Ready' },
  ]);
  expect(screen.getByRole('link')).toHaveAttribute('href', '/projects/DEMO/work');
});

test('marks the takeover prefix stage and separates it from the main chain', () => {
  renderPipeline([
    { key: 'takeover', number: '1', title: 'Takeover Context', status: 'done', approval: 'takeover', status_label: 'Done' },
    { key: 'prd', number: '2', title: 'Product / Requirement', status: 'review-needed', approval: 'prd', status_label: 'Needs review' },
  ]);
  const stages = document.querySelectorAll('.workflow-pipeline__stage');
  expect(stages[0].classList.contains('workflow-pipeline__stage--takeover')).toBe(true);
  expect(stages[1].classList.contains('workflow-pipeline__stage--boundary')).toBe(true);
  expect(screen.getByText('Takeover', { selector: 'strong' })).toBeVisible();
});
