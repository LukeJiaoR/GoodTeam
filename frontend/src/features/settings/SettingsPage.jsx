import { ArrowUpRight, Globe2, Moon, Palette, Sun, Users } from 'lucide-react';
import { Link, useOutletContext, useParams } from 'react-router-dom';
import PageHeading from '../../components/ui/PageHeading';
import { routes } from '../../lib/routing/routes';

export default function SettingsPage() {
  const { projectKey = '' } = useParams();
  const { t, preferences } = useOutletContext();
  const { darkMode, setDarkMode, languagePreference, setLanguagePreference, motionPreference, setMotionPreference } = preferences;
  return <main id="main-content" className="studio-settings-page">
    <PageHeading eyebrow={projectKey || t('product.studio.workspace')} title={t('product.studio.settings')} description={t('product.studio.settingsDescription')} />
    <section className="studio-settings-section" aria-labelledby="settings-appearance">
      <header><Palette size={19} aria-hidden="true" /><div><h2 id="settings-appearance">{t('product.appearance.label')}</h2><p>{t('product.studio.appearanceHint')}</p></div></header>
      <div className="studio-theme-options">{[[false, Sun, 'light'], [true, Moon, 'dark']].map(([dark, Icon, key]) => <button type="button" key={key} className={`studio-theme-option studio-theme-option--${key}`} aria-pressed={darkMode === dark} onClick={() => setDarkMode(dark)}><span className="studio-theme-preview" aria-hidden="true"><i /><span><b /><b /><b /></span></span><span><Icon size={16} aria-hidden="true" />{t(`product.appearance.${key}`)}</span></button>)}</div>
    </section>
    <section className="studio-settings-section studio-settings-section--row" aria-labelledby="settings-language"><header><Globe2 size={19} aria-hidden="true" /><div><h2 id="settings-language">{t('product.language.label')}</h2><p>{t('product.studio.languageHint')}</p></div></header><select aria-labelledby="settings-language" value={languagePreference} onChange={(event) => setLanguagePreference(event.target.value)}>{[['auto', 'auto'], ['en', 'en'], ['zh-CN', 'zhCN']].map(([value, key]) => <option value={value} key={value}>{t(`product.language.${key}`)}</option>)}</select></section>
    <section className="studio-settings-section studio-settings-section--row" aria-labelledby="settings-motion"><header><Palette size={19} aria-hidden="true" /><div><h2 id="settings-motion">{t('product.motion.label')}</h2><p id="settings-motion-hint">{t('product.motion.hint')}</p></div></header><select aria-labelledby="settings-motion" aria-describedby="settings-motion-hint" value={motionPreference} onChange={(event) => setMotionPreference(event.target.value)}>{['system', 'reduce'].map((value) => <option value={value} key={value}>{t(`product.motion.${value}`)}</option>)}</select></section>
    {projectKey ? <section className="studio-settings-section studio-settings-section--row"><header><Users size={19} aria-hidden="true" /><div><h2>{t('product.studio.runtimeTitle')}</h2><p>{t('product.studio.runtimeHint')}</p></div></header><Link className="product-button product-button--secondary" to={routes.projectTeam(projectKey)}>{t('product.studio.configureTeam')}<ArrowUpRight size={15} aria-hidden="true" /></Link></section> : null}
  </main>;
}
