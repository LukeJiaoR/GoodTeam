import { Link, useLocation } from 'react-router-dom';
import { routes } from '../../lib/routing/routes';
import { stepTitle } from '../../utils/workflowHelpers';

export default function Breadcrumb({ projectKey = '', t }) {
  const { pathname } = useLocation();

  if (!t) return null;

  const items = [
    { label: t('product.navigation.projects'), to: routes.projects() },
  ];

  if (projectKey) {
    items.push({ label: projectKey, to: routes.projectWork(projectKey) });

    if (pathname.includes('/work')) {
      items.push({ label: t('product.navigation.work'), to: routes.projectWork(projectKey), current: true });
    } else if (pathname.includes('/review')) {
      const reviewItemMatch = pathname.match(/\/review\/([^/]+)/);
      if (reviewItemMatch) {
        const reviewItem = decodeURIComponent(reviewItemMatch[1]);
        const translatedStage = stepTitle({ key: reviewItem, approval: reviewItem }, t);
        items.push({ label: t('product.navigation.review'), to: routes.projectReview(projectKey) });
        items.push({ label: translatedStage || reviewItem, current: true });
      } else {
        items.push({ label: t('product.navigation.review'), to: routes.projectReview(projectKey), current: true });
      }
    } else if (pathname.includes('/knowledge')) {
      items.push({ label: t('product.navigation.knowledge'), to: routes.projectKnowledge(projectKey), current: true });
    } else if (pathname.includes('/settings')) {
      items.push({ label: t('product.navigation.projectSettings'), to: routes.projectSettings(projectKey, 'general'), current: true });
    }
  } else {
    if (pathname.startsWith('/inbox')) {
      items.push({ label: t('product.navigation.inbox'), current: true });
    } else if (pathname.startsWith('/activity')) {
      items.push({ label: t('product.navigation.activity'), current: true });
    } else if (pathname.startsWith('/settings')) {
      items.push({ label: t('product.navigation.settings'), current: true });
    }
  }

  if (items.length <= 1) return null;

  return (
    <nav className="product-breadcrumb" aria-label={t('product.navigation.breadcrumb') || 'Breadcrumb'}>
      <ol className="product-breadcrumb__list">
        {items.map((item, index) => {
          const isLast = index === items.length - 1;
          return (
            <li key={index} className="product-breadcrumb__item" aria-current={isLast ? 'page' : undefined}>
              {!isLast && item.to ? (
                <Link to={item.to} className="product-breadcrumb__link">
                  {item.label}
                </Link>
              ) : (
                <span className="product-breadcrumb__current">{item.label}</span>
              )}
              {!isLast && <span className="product-breadcrumb__separator" aria-hidden="true">/</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
