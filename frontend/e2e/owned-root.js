import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const ownershipError = () => new Error('E2E requires its own temporary directory');

// This capability is only for a single Playwright-owned temporary tree. Never
// use it for a project workspace or relax the production reviewer input seal.
export function openOwnedE2ERoot(rootPath, owner) {
  const root = fs.realpathSync(rootPath);
  const temporaryParent = fs.realpathSync(os.tmpdir());
  if (!fs.lstatSync(rootPath).isDirectory() || path.dirname(root) !== temporaryParent
    || !path.basename(root).startsWith('goodteam-e2e-') || !owner) {
    throw ownershipError();
  }
  const identity = fs.lstatSync(root);

  function validateOwnership() {
    const current = fs.lstatSync(root);
    const marker = path.join(root, '.owner');
    if (!current.isDirectory() || current.dev !== identity.dev || current.ino !== identity.ino
      || !fs.lstatSync(marker).isFile() || fs.readFileSync(marker, 'utf8') !== owner) {
      throw ownershipError();
    }
  }
  validateOwnership();

  // Unlinking 0444 files needs write permission on their parent directories,
  // not on the files. In particular, do not chmod files or follow symlinks: a
  // fixture may link to something outside this owned tree.
  function prepareDirectories(directory) {
    const fd = fs.openSync(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
    try {
      const info = fs.fstatSync(fd);
      if (!(info.mode & 0o200)) fs.fchmodSync(fd, (info.mode & 0o7777) | 0o200);
      for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        if (entry.isDirectory()) prepareDirectories(path.join(directory, entry.name));
      }
    } finally {
      fs.closeSync(fd);
    }
  }

  return {
    root,
    cleanup() {
      // Repeated shutdown is harmless, but all other filesystem errors remain
      // visible. Recheck the token and inode before the first mutation.
      try {
        fs.lstatSync(root);
      } catch (error) {
        if (error.code === 'ENOENT') return;
        throw error;
      }
      validateOwnership();
      prepareDirectories(root);
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
}
