// Emits public/search-index.json, the compact projection the client-side search loads once
// and caches. Kept out of the page payloads on purpose: inlining ~400 KB of records into
// each of 500+ statically exported pages would produce a site an order of magnitude larger
// than the library it describes.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const siteRoot = path.resolve(here, "..");
const repoRoot = path.resolve(siteRoot, "..");

const index = JSON.parse(fs.readFileSync(path.join(repoRoot, "index.json"), "utf8"));

const records = index.skills.map((skill) => ({
  n: skill.name,
  d: skill.description,
  s: skill.subdomain,
  t: skill.tags ?? [],
  b: skill.brokers_frameworks ?? [],
}));

const outDir = path.join(siteRoot, "public");
fs.mkdirSync(outDir, { recursive: true });
const outFile = path.join(outDir, "search-index.json");
fs.writeFileSync(outFile, JSON.stringify({ version: index.version, records }));

const kb = (fs.statSync(outFile).size / 1024).toFixed(0);
console.log(`search-index.json  ${records.length} records  ${kb} KB`);
