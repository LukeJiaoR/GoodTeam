import { Link } from 'react-router-dom';
import { useAppearancePreferences } from '../hooks/useAppearancePreferences';

export default function NotFoundPage() {
  const { t } = useAppearancePreferences();

  return (
    <div className="product-shell">
      <main className="product-not-found">
        <p className="product-not-found__code" aria-hidden="true">404</p>
        <h1>{t('product.notFound.title')}</h1>
        <p>{t('product.notFound.description')}</p>
        <Link className="product-button product-button--primary" to="/projects">
          {t('product.notFound.action')}
        </Link>
      </main>
    </div>
  );
}
