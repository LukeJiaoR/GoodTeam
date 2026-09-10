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
