import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import KnowledgeArtifacts from './KnowledgeArtifacts';
import { knowledgeApi } from './knowledgeApi';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('compact document selection retains memory search and source context', async () => {
  vi.spyOn(knowledgeApi, 'getKnowledgeArtifact').mockResolvedValue({ content: 'Original document body' });
  const setSearch = vi.fn();
  render(<KnowledgeArtifacts projectKey="DEMO" entries={[
    ['brief', '/source/brief.md'], ['plan', '/source/plan.md'],
  ]} search={new URLSearchParams('view=artifacts&role=research&memory_q=evidence&scope=OTHER&event_project=OTHER')}
    setSearch={setSearch} t={key => key} />);
  await screen.findByText('Original document body');
  fireEvent.click(screen.getByText('plan.md').closest('button'));
  const query = setSearch.mock.calls[0][0];
  expect(query.get('artifact')).toBe('plan');
  expect(query.get('view')).toBe('artifacts');
  expect(query.get('memory_q')).toBe('evidence');
  expect(query.get('scope')).toBe('OTHER');
  expect(query.get('event_project')).toBe('OTHER');
  expect(query.get('role')).toBe('research');
});
