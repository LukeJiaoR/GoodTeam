import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { expect, it } from 'vitest';
import WorkWorkspaceRoute from './WorkWorkspaceShell';

function LocalDraft() {
  const [draft, setDraft] = useState('');
  return <label>Local draft<input value={draft} onChange={(event) => setDraft(event.target.value)} /></label>;
}

it('does not carry draft or navigation state into a different project', () => {
  render(<MemoryRouter initialEntries={['/projects/A/work']}>
    <Link to="/projects/B/work">Project B</Link>
    <Routes><Route path="/projects/:projectKey/work" element={<WorkWorkspaceRoute Component={LocalDraft} />} /></Routes>
  </MemoryRouter>);
  fireEvent.change(screen.getByRole('textbox', { name: 'Local draft' }), { target: { value: 'Only for project A' } });
  fireEvent.click(screen.getByRole('link', { name: 'Project B' }));
  expect(screen.getByRole('textbox', { name: 'Local draft' })).toHaveValue('');
});
