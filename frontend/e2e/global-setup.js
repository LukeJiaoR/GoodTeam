import fs from 'node:fs';
import path from 'node:path';

export default async function globalSetup() {
  const root = '/private/tmp/goodteam-project-load-e2e';
  try {
    fs.chmodSync(path.join(root, 'targets', 'blocked'), 0o700);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  fs.rmSync(root, { recursive: true, force: true });
  fs.mkdirSync(path.join(root, 'workspace'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'empty'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'existing'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'ambiguous'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'blocked'), { recursive: true });
  fs.mkdirSync(path.join(root, 'targets', 'existing', 'src'), { recursive: true });
  fs.writeFileSync(
    path.join(root, 'targets', 'existing', 'package.json'),
    JSON.stringify({
      name: 'existing',
      scripts: {
        start: 'vite',
        test: 'vitest run',
        lint: 'eslint .',
        build: 'vite build',
      },
      dependencies: { react: '^19.0.0' },
    }, null, 2),
  );
  fs.writeFileSync(
    path.join(root, 'targets', 'existing', 'README.md'),
    '# Existing fixture\n\nA read-only fixture for GoodTeam readiness E2E.\n',
  );
  fs.writeFileSync(
    path.join(root, 'targets', 'existing', 'src', 'main.jsx'),
    "export default function App() { return 'fixture'; }\n",
  );
  fs.writeFileSync(path.join(root, 'targets', 'ambiguous', 'blob.bin'), 'asset');
  fs.chmodSync(path.join(root, 'targets', 'blocked'), 0o000);
}
