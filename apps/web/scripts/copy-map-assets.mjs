// Copies MapLibre's worker and its shared chunk to public/maplibre/ so the map worker is loaded from
// the app's own origin (CSP worker-src 'self'; no blob: workers, no CDN). Run before dev/build.
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const dist = dirname(require.resolve("maplibre-gl/package.json")) + "/dist";
const target = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "maplibre");
mkdirSync(target, { recursive: true });
for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  copyFileSync(join(dist, file), join(target, file));
}
process.stdout.write(`map assets copied to ${target}\n`);
