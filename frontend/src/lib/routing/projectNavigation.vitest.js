import { beforeEach, describe, expect, test } from 'vitest';
import { projectNavigationDestinations, rememberProjectLocation } from './projectNavigation';

beforeEach(() => sessionStorage.clear());

describe('project navigation continuity', () => {
  test('keeps exact selected task, review and memory source independently within a project', () => {
    rememberProjectLocation('/projects/one/work/runs/past-1', '?view=outcomes');
    rememberProjectLocation('/projects/one/knowledge', '?view=memory&role=builder&q=%E4%BB%BB%E5%8A%A1&event=mem-1&event_project=two');
    rememberProjectLocation('/projects/one/review/architecture');
    expect(projectNavigationDestinations('one', '/projects/one/team')).toEqual({
      work: '/projects/one/work/runs/past-1?view=outcomes',
      review: '/projects/one/review/architecture',
      knowledge: '/projects/one/knowledge?view=memory&role=builder&q=%E4%BB%BB%E5%8A%A1&event=mem-1&event_project=two',
      team: '/projects/one/team',
    });
  });

  test('new current task navigation replaces the remembered historical task without borrowing another project', () => {
    rememberProjectLocation('/projects/one/work/runs/past-1');
    rememberProjectLocation('/projects/two/work/runs/past-1');
    expect(projectNavigationDestinations('one', '/projects/one/work').work).toBe('/projects/one/work');
    rememberProjectLocation('/projects/one/work');
    expect(projectNavigationDestinations('one').work).toBe('/projects/one/work');
    expect(projectNavigationDestinations('two').work).toBe('/projects/two/work/runs/past-1');
    expect(projectNavigationDestinations('three').work).toBe('/projects/three/work');
  });

  test('does not accept off-site, cross-project, wrong-section or malformed stored targets', () => {
    const storage = { getItem: () => JSON.stringify([{ projectKey: 'one', pages: {
      work: 'https://example.com/projects/one/work',
      knowledge: '/projects/two/knowledge?event=other',
      review: '/projects/one/team', team: '/projects/%E0%A4%A/team',
    } }]) };
    expect(projectNavigationDestinations('one', '', '', storage)).toEqual({
      work: '/projects/one/work', review: '/projects/one/review', knowledge: '/projects/one/knowledge', team: '/projects/one/team',
    });
  });

  test.each([
    '/projects/one/review/..',
    '/projects/one/review/%2e%2e',
    String.raw`/projects/one/review/item\..\..\..\two\review`,
  ])('rejects a stored target whose browser-normalized path changes: %s', target => {
    const storage = { getItem: () => JSON.stringify([{ projectKey: 'one', pages: { review: target } }]) };
    expect(projectNavigationDestinations('one', '', '', storage).review).toBe('/projects/one/review');
  });

  test('storage failure and malformed data cannot block ordinary navigation', () => {
    for (const storage of [{ getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); } }, { getItem: () => '{' }, null]) {
      expect(() => rememberProjectLocation('/projects/one/work', '', storage)).not.toThrow();
      expect(projectNavigationDestinations('one', '', '', storage).work).toBe('/projects/one/work');
    }
  });

  test('encoded project identities survive exact matching and retained history is bounded', () => {
    rememberProjectLocation('/projects/one%20two/work/runs/a%2Fb');
    expect(projectNavigationDestinations('one two').work).toBe('/projects/one%20two/work/runs/a%2Fb');
    for (let index = 0; index < 21; index++) rememberProjectLocation(`/projects/p${index}/work/runs/a`);
    expect(projectNavigationDestinations('p0').work).toBe('/projects/p0/work');
    expect(projectNavigationDestinations('p20').work).toBe('/projects/p20/work/runs/a');
  });
});
