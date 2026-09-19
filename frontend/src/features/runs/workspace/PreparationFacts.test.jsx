import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import PreparationFacts from './PreparationFacts';

const t = (key) => key;
describe('Preparation confirmation facts', () => {
  it('renders the exact constraint and its source before confirmation', () => {
    render(<PreparationFacts requirements={{ kind: 'constraint_confirmation', constraints: ['AGENTS.md: Do not push.'] }} t={t} />);
    expect(screen.getByText('AGENTS.md: Do not push.')).toBeInTheDocument();
  });
  it('does not claim constraints were found when the list is empty', () => {
    render(<PreparationFacts requirements={{ kind: 'constraint_confirmation', constraints: [] }} t={t} />);
    expect(screen.getByText('work.taskAssistant.noConstraints')).toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });
});

it('shows the exact preflight message, with an honest fallback for malformed input', () => {
  const view = render(<PreparationFacts requirements={{ kind: 'worker_preflight_confirmation', message: 'Codex is not authenticated.' }} t={t} />);
  expect(screen.getByRole('status')).toHaveTextContent('Codex is not authenticated.');
  view.rerender(<PreparationFacts requirements={{ kind: 'worker_preflight_confirmation', message: {} }} t={t} />);
  expect(screen.getByRole('status')).toHaveTextContent('work.taskAssistant.unknownPreflight');
});

it('handles absent requirements and ignores non-text constraint entries', () => {
  const view = render(<PreparationFacts requirements={null} t={t} />);
  expect(screen.queryByRole('list')).not.toBeInTheDocument();
  view.rerender(<PreparationFacts requirements={{ kind: 'constraint_confirmation', constraints: [null, {}, '', 'AGENTS.md: Keep the schema.'] }} t={t} />);
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
  expect(screen.getByRole('listitem').textContent).toBe('AGENTS.md: Keep the schema.');
});
