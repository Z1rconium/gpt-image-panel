import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const immutable = new URL('../build/_app/immutable/', import.meta.url);
const workers = readdirSync(new URL('workers/', immutable)).filter((name) => /^maskRegion\.worker-.+\.js$/.test(name));
if (workers.length !== 1) throw new Error(`Expected one emitted mask Worker, found ${workers.length}`);

const chunks = new URL('chunks/', immutable);
const references = readdirSync(chunks)
  .filter((name) => name.endsWith('.js'))
  .flatMap((name) => {
    const content = readFileSync(new URL(name, chunks), 'utf8');
    return [...content.matchAll(/new Worker\((.{0,180})/g)].map((match) => match[1]);
  });

if (!references.some((reference) => reference.includes(`../workers/${workers[0]}`))) {
  throw new Error('The production bundle does not reference its emitted module Worker');
}
if (references.some((reference) => /blob:|data:/.test(reference))) {
  throw new Error('The production bundle contains an inline Worker URL');
}
console.log(`Production module Worker verified: ${join('build/_app/immutable/workers', workers[0])}`);
