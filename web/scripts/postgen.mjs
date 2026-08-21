/**
 * Post-processing for the generated API client.
 *
 * openapi-generator's typescript-angular templates still emit the NgModule
 * variant and a couple of scaffolding files this project does not use. The
 * NgModule is not merely unused — it is the pattern CLAUDE.md forbids — and
 * index.ts re-exports it, so removing the file alone breaks the build.
 *
 * This runs after every generation so the committed output is a pure function
 * of openapi.json. The Phase 6 drift check depends on that being true.
 */
import { readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const API_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'app', 'api');

const DROP_FILES = ['api.module.ts', 'git_push.sh', 'README.md', '.gitignore'];
const DROP_EXPORTS = ["export * from './api.module';"];

let removed = 0;
for (const name of DROP_FILES) {
  const path = join(API_DIR, name);
  if (existsSync(path)) {
    rmSync(path, { force: true });
    removed += 1;
  }
}

const indexPath = join(API_DIR, 'index.ts');
const before = readFileSync(indexPath, 'utf8');
const after = before
  .split(/\r?\n/)
  .filter((line) => !DROP_EXPORTS.includes(line.trim()))
  .join('\n');

if (after !== before) {
  writeFileSync(indexPath, after, 'utf8');
}

console.log(
  `postgen: removed ${removed} file(s), index.ts ${after === before ? 'unchanged' : 'rewritten'}`,
);
