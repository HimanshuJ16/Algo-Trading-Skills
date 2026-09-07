// Emits public/graph.json: the cross-reference graph of the whole library as a compact,
// separately fetched asset. The landing page and the catalog render it on a canvas; keeping
// it out of the page payload means no exported page carries 3,000+ edges it never draws.
//
// Extraction mirrors lib/content.ts (backticked slugs in prose, fenced code excluded, self
// references dropped). app/page.tsx checks the node and edge counts against the library at
// build time, so the two cannot drift apart silently.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const siteRoot = path.resolve(here, "..");
const repoRoot = path.resolve(siteRoot, "..");

const index = JSON.parse(fs.readFileSync(path.join(repoRoot, "index.json"), "utf8"));
const skills = index.skills;
const valid = new Set(skills.map((s) => s.name));
const position = new Map(skills.map((s, i) => [s.name, i]));

const domainOrder = [];
for (const s of skills) if (!domainOrder.includes(s.subdomain)) domainOrder.push(s.subdomain);

function content(file) {
  const raw = fs.readFileSync(path.join(repoRoot, file), "utf8");
  // Tolerate CRLF checkouts: a Windows working copy must count the same words as CI.
  return raw.replace(/^---[\s\S]*?\r?\n---\r?\n/, "");
}

/** Prose only: fenced code stripped, so a slug inside a shell example is not an edge. */
function body(file) {
  return content(file).replace(/^```[\s\S]*?^```/gm, "");
}

// [slug, domain index, SKILL.md word count]. The word count is the same measure the
// site's loader uses, so the spectrum on the landing page agrees with the numbers beside it.
const nodes = skills.map((s) => [
  s.name,
  domainOrder.indexOf(s.subdomain),
  content(s.skill_md).split(/\s+/).filter(Boolean).length,
]);
const edges = [];
for (const s of skills) {
  const seen = new Set();
  for (const [, slug] of body(s.skill_md).matchAll(/`([a-z0-9]+(?:-[a-z0-9]+)+)`/g)) {
    if (slug === s.name || !valid.has(slug) || seen.has(slug)) continue;
    seen.add(slug);
    edges.push([position.get(s.name), position.get(slug)]);
  }
}

const outDir = path.join(siteRoot, "public");
fs.mkdirSync(outDir, { recursive: true });
const outFile = path.join(outDir, "graph.json");
fs.writeFileSync(outFile, JSON.stringify({ version: index.version, domains: domainOrder, nodes, edges }));

const kb = (fs.statSync(outFile).size / 1024).toFixed(0);
console.log(`graph.json         ${nodes.length} nodes  ${edges.length} edges  ${kb} KB`);
