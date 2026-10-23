import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { spawn } from 'node:child_process';
import { openOwnedE2ERoot } from './owned-root.js';

const { root, cleanup } = openOwnedE2ERoot(process.env.GOODTEAM_E2E_ROOT, process.env.GOODTEAM_E2E_OWNER);
const isolatedHome = path.join(root, 'home');
fs.mkdirSync(isolatedHome, { recursive: true });
fs.mkdirSync(path.join(root, 'workspace'), { recursive: true });
const child = spawn(process.env.GOODTEAM_E2E_PYTHON || '../.venv/bin/python', [
  '-m', 'goodteam_os.cli', 'serve', '--workspace', path.join(root, 'workspace'),
  '--host', '127.0.0.1', '--port', process.env.GOODTEAM_E2E_PORT,
], {
  stdio: 'inherit',
  env: {
    ...process.env,
    GOODTEAM_HOME: isolatedHome,
    GOODTEAM_STATE_DIR: path.join(root, 'operator-state'),
    GOODTEAM_PROJECTS_REGISTRY: path.join(isolatedHome, 'projects.json'),
    GOODTEAM_FRONTEND_DIST: path.resolve('dist'),
    GOODTEAM_DISABLE_TG_NETWORK: '1',
    GOODTEAM_TG_BOT_TOKEN: '',
    GOODTEAM_TG_CHAT_ID: '',
    GOODTEAM_SERVER_ACTIONS: '0',
    PYTHONPATH: path.resolve('../src'),
  },
});
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));
child.on('error', (error) => { console.error(error); process.exitCode = 1; });
child.on('exit', (code, signal) => {
  cleanup();
  process.exitCode = signal === 'SIGTERM' || signal === 'SIGINT' ? 0 : (code ?? 1);
});
