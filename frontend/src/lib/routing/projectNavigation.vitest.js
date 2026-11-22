import { beforeEach, describe, expect, test } from 'vitest';
import { projectNavigationDestinations, rememberProjectLocation } from './projectNavigation';

beforeEach(() => sessionStorage.clear());

describe('project navigation continuity', () => {
  test('does not accept off-site, cross-project, wrong-section or malformed stored targets', () => {
    const storage = { getItem: () => JSON.stringify([{ projectKey: 'one', pages: {
      work: 'https://example.com/projects/one/work',
      knowledge: '/projects/two/knowledge?event=other',
      team: '/projects/%E0%A4%A/team',
    } }]) };
    expect(projectNavigationDestinations('one', '', '', storage)).toEqual({
      work: '/projects/one/work', knowledge: '/projects/one/knowledge', team: '/projects/one/team',
    });
  });

  test('storage failure and malformed data cannot block ordinary navigation', () => {
    for (const storage of [{ getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); } }, { getItem: () => '{' }, null]) {
      expect(() => rememberProjectLocation('/projects/one/work', '', storage)).not.toThrow();
      expect(projectNavigationDestinations('one', '', '', storage).work).toBe('/projects/one/work');
    }
  });

  test('encoded project identities survive exact matching and saved destinations are bounded', () => {
    rememberProjectLocation('/projects/one%20two/work');
    expect(projectNavigationDestinations('one two').work).toBe('/projects/one%20two/work');
    for (let index = 0; index < 21; index++) rememberProjectLocation(`/projects/p${index}/work`, '?tab=tasks');
    expect(projectNavigationDestinations('p0').work).toBe('/projects/p0/work');
    expect(projectNavigationDestinations('p20').work).toBe('/projects/p20/work?tab=tasks');
  });
});
