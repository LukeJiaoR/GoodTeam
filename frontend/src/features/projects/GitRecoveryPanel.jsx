import { useEffect, useRef, useState } from 'react';
import Button from '../../components/ui/Button';
import { gitRecoveryApi } from './gitRecoveryApi';

const copy = {
  en: {
    title: 'Git baseline and recovery', inspect: 'Check Git baseline', refresh: 'Refresh Git status',
    valid_head: 'Valid Git commit', unborn: 'Initial commit missing', non_git: 'Git is not initialized',
    invalid: 'Git baseline is invalid', unavailable: 'Git status unavailable',
    files: 'Select exact relative file paths, one per line',
    help: 'Only these files will enter the initial commit. Review their contents first. Existing unrelated staged files are preserved. No remote is created.',
    review: 'Review selected files', confirm: 'Confirm initial commit', cancel: 'Cancel',
    invalidFiles: 'Enter regular relative file paths. Absolute paths, parent traversal, Git metadata and GoodTeam state are not allowed.',
    pending: 'Checking Git status…', checkFailed: 'Could not read current Git status. Refresh before trying again.',
    outcome: 'Recovery response', changed: 'Check the current Git state below. A successful request alone does not prove a valid baseline.',
    scope: 'This checks repository initialization only; task-bound execution readiness is separate.',
  },
  zh: {
    title: 'Git 基线与恢复', inspect: '检查 Git 基线', refresh: '刷新 Git 状态',
    valid_head: '已有有效 Git 提交', unborn: '缺少初始提交', non_git: '尚未初始化 Git',
    invalid: 'Git 基线无效', unavailable: 'Git 状态不可用',
    files: '明确选择相对文件路径，每行一个',
    help: '只有这些文件会进入初始提交，请先核对内容。其他已暂存文件保持不变，不创建远端仓库。',
    review: '核对所选文件', confirm: '确认创建初始提交', cancel: '取消',
    invalidFiles: '请输入普通相对文件路径，不允许绝对路径、上级目录、Git 元数据或 GoodTeam 状态目录。',
    pending: '正在检查 Git 状态…', checkFailed: '无法读取当前 Git 状态，请刷新后再试。',
    outcome: '恢复响应', changed: '请核对下方当前 Git 状态，请求成功不代表基线已有效。',
    scope: '此处只检查仓库初始化，任务绑定的执行就绪状态需另行验证。',
  },
};

function selectedPaths(value) {
  const paths = [...new Set(value.split('\n').map((path) => path.trim()).filter(Boolean))];
  return paths.length && paths.every((path) => !/^[A-Za-z]:|^\/|\\/.test(path)
    && ![...path].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
    && path.split('/').every((part) => part && part !== '.' && part !== '..')
    && !['.git', '.goodteam'].includes(path.split('/')[0])) ? paths : null;
}

// Keyed inner component discards selections, confirmations and late responses on project changes.
export default function GitRecoveryPanel(props) {
  return <GitRecoverySession key={props.projectKey} {...props} />;
}

function GitRecoverySession({ projectKey, language = 'en', api = gitRecoveryApi }) {
  const t = copy[language.startsWith('zh') ? 'zh' : 'en'];
  const [open, setOpen] = useState(false);
  const [baseline, setBaseline] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [value, setValue] = useState('');
  const [selection, setSelection] = useState(null);
  const alive = useRef(true);
  const locked = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  async function inspect() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setSelection(null);
    setError('');
    setBaseline(null);
    try {
      const payload = await api.read(projectKey);
      if (alive.current) setBaseline(payload?.git_baseline || null);
    } catch (caught) {
      if (alive.current) setError(caught.message || t.checkFailed);
    } finally {
      locked.current = false;
      if (alive.current) setBusy(false);
    }
  }

  const state = ['valid_head', 'unborn', 'non_git', 'invalid', 'unavailable'].includes(baseline?.state)
    ? baseline.state : 'unavailable';
  const recoverable = ['unborn', 'non_git'].includes(state) && baseline?.baseline_required === true;
  async function recover() {
    if (locked.current || !selection || !recoverable) return;
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    const files = [...selection];
    setSelection(null);
    try {
      const result = await api.recover(projectKey, files);
      if (!alive.current) return;
      setNotice([t.changed, result?.error, ...(Array.isArray(result?.warnings) ? result.warnings : [])].filter(Boolean).join(' '));
    } catch (caught) {
      if (!alive.current) return;
      setError(caught.message || t.checkFailed);
    } finally {
      // A failed/uncertain POST can still have changed Git; never auto-retry it.
      if (alive.current) {
        setBaseline(null);
        try {
          const payload = await api.read(projectKey);
          if (alive.current) setBaseline(payload?.git_baseline || null);
        } catch {
          if (alive.current) setNotice((previous) => [previous, t.checkFailed].filter(Boolean).join(' '));
        }
        if (alive.current) setBusy(false);
      }
      locked.current = false;
    }
  }

  return <section className="work-context-disclosure" aria-label={t.title}>
    <Button variant="secondary" disabled={busy} onClick={() => {
      setOpen(!open); setSelection(null);
      if (!open) void inspect();
    }}>{t.title}</Button>
    {open && <div className="work-context-drawer">
      <p>{t.scope}</p>
      <p role="status">{busy ? t.pending : t[state]}</p>
      {baseline?.head_sha && <code>{baseline.head_sha}</code>}
      {baseline?.error && <p role="alert">{baseline.error}</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p aria-label={t.outcome}>{notice}</p>}
      <Button variant="secondary" disabled={busy} onClick={inspect}>{t.refresh}</Button>
      {recoverable && !busy && <div>
        <label>{t.files}<textarea value={value} onChange={(event) => {
          setValue(event.target.value); setSelection(null); setError('');
        }} /></label>
        <p>{t.help}</p>
        {!selection ? <Button onClick={() => {
          const paths = selectedPaths(value);
          if (!paths) setError(t.invalidFiles);
          else { setError(''); setSelection(paths); }
        }}>{t.review}</Button> : <div>
          <ul>{selection.map((path) => <li key={path}>{path}</li>)}</ul>
          <Button onClick={recover}>{t.confirm}</Button>
          <Button variant="secondary" onClick={() => setSelection(null)}>{t.cancel}</Button>
        </div>}
      </div>}
    </div>}
  </section>;
}
