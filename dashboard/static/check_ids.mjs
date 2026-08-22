// Guard: ogni id="..." in index.html deve comparire almeno una volta in app.js
// (writer o listener). Un id orfano = KPI che non verrà mai popolato.
// Uso: node check_ids.mjs  (dalla directory static/)
import { readFileSync } from "node:fs";
const html = readFileSync("index.html", "utf8");
const js = readFileSync("app.js", "utf8");
const ids = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);
// Legittimi senza writer letterale: sezioni tab (usate come "tab-" + name)
// e kpi-strip (solo contenitore di stile).
const dynamic = id => id.startsWith("tab-") || id === "kpi-strip";
const orphan = ids.filter(id => !dynamic(id) && !js.includes(id));
