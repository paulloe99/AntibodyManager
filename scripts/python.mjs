import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

export const root = fileURLToPath(new URL('../', import.meta.url));
export function runPython(args) {
  const executable = process.env.ANTIBODY_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!existsSync(executable)) {
    throw new Error('Create the project .venv or set ANTIBODY_PYTHON to your configured Python executable.');
  }
  const result = spawnSync(executable, args, { cwd: root, stdio: 'inherit', env: { ...process.env, PYTHONUNBUFFERED: '1' } });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runPython(process.argv.slice(2));
}
