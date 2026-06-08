import { useEffect, useState } from 'react';

const getUrlProject = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get('project') || '';
};

export function useProjectUrl() {
  const [projectKey, setProjectKey] = useState(getUrlProject);

  useEffect(() => {
    const handlePopState = () => {
      setProjectKey(getUrlProject());
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const updateUrl = (key) => {
    const normalizedKey = key || '';
    const query = new URLSearchParams({ project: normalizedKey }).toString();
    const newUrl = normalizedKey ? `${window.location.pathname}?${query}` : window.location.pathname;
    window.history.pushState({ project: normalizedKey }, '', newUrl);
    setProjectKey(normalizedKey);
  };

  return { projectKey, updateUrl };
}
