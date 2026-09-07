import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";
import { renderMarkdown, type TocEntry } from "./markdown";
import { DOMAINS, domainMeta } from "./domains";

/**
 * Build-time content layer.
 *
 * Everything the site renders is derived from the repository itself: index.json for
 * metadata and skills/<name>/SKILL.md for prose. No per-skill copy is authored here, so a
 * skill added to the library appears on the site with no site-side edit, and a skill that
 * breaks the repository contract fails this build.
 *
 * Reading is split in two on purpose:
 *
 *  - `getLibrary()` reads index.json and scans the raw Markdown for cross-references. It
 *    touches every skill but renders none, so it costs about a second.
 *  - `getSkillDoc()` runs the full Markdown pipeline for one skill, memoized.
 *
 * Collapsing the two would make every page — the landing page, a domain page, one skill —
 * pay to syntax-highlight all 500+ documents before it could render anything.
 */

const REPO_ROOT = path.resolve(process.cwd(), "..");

export type IndexEntry = {
  name: string;
  description: string;
  domain: string;
  subdomain: string;
  tags: string[];
  brokers_frameworks: string[];
  version: string;
  author: string;
  license: string;
  path: string;
  skill_md: string;
};

export type SkillFiles = {
  references: string[];
  scripts: string[];
  assets: string[];
};

/** Everything about a skill that does not require rendering its Markdown. */
export type Skill = IndexEntry & {
  /** Skills this one names anywhere in its body. */
  crossRefs: string[];
  /** Skills listed under "## Related Skills", in document order. */
  related: string[];
  /** Skills that name this one. */
  referencedBy: string[];
  files: SkillFiles;
  testCommand: string | null;
  words: number;
  readingMinutes: number;
};

/** A skill plus its rendered body. */
export type SkillDoc = Skill & {
  html: string;
  toc: TocEntry[];
};

export type DomainSummary = {
  slug: string;
  label: string;
  short: string;
  blurb: string;
  hue: number;
  count: number;
  skills: string[];
};

export type Library = {
  version: string;
  totalSkills: number;
  domains: DomainSummary[];
  skills: Skill[];
  bySlug: Map<string, Skill>;
  stats: {
    skills: number;
    domains: number;
    helperModules: number;
    testSuites: number;
    crossReferences: number;
    /** Parsed from the README badge so the site never carries its own copy of the number. */
    testsPassing: string | null;
    brokersFrameworks: number;
    /** Words across every SKILL.md, references/*.md and assets/*.md in the library. */
    markdownWords: number;
    /** Words in SKILL.md files alone: what an agent reads when it loads one skill. */
    skillWords: number;
    /** Lines across every scripts/*.py helper and test. */
    pythonLines: number;
  };
  /** Aggregated cross-references between domains, for the constellation on the landing page. */
  domainLinks: { source: string; target: string; count: number }[];
  brokers: { name: string; count: number }[];
  tags: { name: string; count: number }[];
};

function readJson<T>(relative: string): T {
  return JSON.parse(fs.readFileSync(path.join(REPO_ROOT, relative), "utf8")) as T;
}

function countWords(relative: string): number {
  const file = path.join(REPO_ROOT, relative);
  if (!fs.existsSync(file)) return 0;
  return fs.readFileSync(file, "utf8").split(/\s+/).filter(Boolean).length;
}

function countLines(relative: string): number {
  const file = path.join(REPO_ROOT, relative);
  if (!fs.existsSync(file)) return 0;
  return fs.readFileSync(file, "utf8").split("\n").length;
}

function listDir(relative: string): string[] {
  const dir = path.join(REPO_ROOT, relative);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isFile() && !e.name.startsWith("."))
    .map((e) => e.name)
    .sort();
}

/** Body with fenced code removed, so a slug inside a shell example is not a reference. */
function prose(body: string): string {
  return body.replace(/^```[\s\S]*?^```/gm, "");
}

function crossReferencesIn(body: string, valid: ReadonlySet<string>, self: string): string[] {
  const found = new Set<string>();
  for (const [, slug] of prose(body).matchAll(/`([a-z0-9]+(?:-[a-z0-9]+)+)`/g)) {
    if (slug !== self && valid.has(slug)) found.add(slug);
  }
  return [...found].sort();
}

/** Backticked slugs under "## Related Skills", in document order, de-duplicated. */
function relatedSkills(body: string, valid: ReadonlySet<string>): string[] {
  const start = body.search(/^## Related Skills\s*$/m);
  if (start === -1) return [];
  const rest = body.slice(start);
  const nextHeading = rest.slice(1).search(/^## /m);
  const section = nextHeading === -1 ? rest : rest.slice(0, nextHeading + 1);
  const out: string[] = [];
  for (const [, slug] of section.matchAll(/`([a-z0-9-]+)`/g)) {
    if (valid.has(slug) && !out.includes(slug)) out.push(slug);
  }
  return out;
}

function testCommandOf(body: string, name: string): string | null {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`python -m unittest discover -s skills/${escaped}/scripts(?: -v)?`);
  return body.match(re)?.[0] ?? null;
}

function testsPassingFromReadme(): string | null {
  try {
    const readme = fs.readFileSync(path.join(REPO_ROOT, "README.md"), "utf8");
    const m = readme.match(/badge\/tests-([\d%,._]+?)_passing/);
    if (!m) return null;
    return decodeURIComponent(m[1]).replace(/_/g, " ");
  } catch {
    return null;
  }
}

const bodies = new Map<string, string>();

function readBody(entry: IndexEntry): string {
  const cached = bodies.get(entry.name);
  if (cached !== undefined) return cached;
  const file = path.join(REPO_ROOT, entry.skill_md);
  if (!fs.existsSync(file)) {
    throw new Error(`Missing ${entry.skill_md} referenced by index.json`);
  }
  // Normalise line endings first: on a CRLF checkout gray-matter can fail to recognise the
  // front matter and return the whole file, which would inflate word counts and leak the
  // metadata block into the rendered page. CI checks out LF; a Windows clone must match it.
  const { content } = matter(fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n"));
  bodies.set(entry.name, content);
  return content;
}

let cached: Promise<Library> | null = null;

export function getLibrary(): Promise<Library> {
  cached ??= build();
  return cached;
}

async function build(): Promise<Library> {
  const index = readJson<{
    version: string;
    total_skills: number;
    subdomains: Record<string, number>;
    skills: IndexEntry[];
  }>("index.json");

  const entries = index.skills;
  const validNames = new Set(entries.map((e) => e.name));

  // Fail the build rather than render a site that quietly disagrees with the library.
  for (const slug of Object.keys(index.subdomains)) domainMeta(slug);
  if (entries.length !== index.total_skills) {
    throw new Error(
      `index.json says ${index.total_skills} skills but lists ${entries.length}. Run tools/build_index.py.`,
    );
  }

  const skills: Skill[] = entries.map((entry) => {
    const body = readBody(entry);
    const words = body.split(/\s+/).filter(Boolean).length;
    return {
      ...entry,
      crossRefs: crossReferencesIn(body, validNames, entry.name),
      related: relatedSkills(body, validNames),
      referencedBy: [],
      files: {
        references: listDir(`${entry.path}/references`),
        scripts: listDir(`${entry.path}/scripts`).filter((f) => f.endsWith(".py")),
        assets: listDir(`${entry.path}/assets`),
      },
      testCommand: testCommandOf(body, entry.name),
      words,
      readingMinutes: Math.max(1, Math.round(words / 220)),
    };
  });

  const bySlug = new Map(skills.map((s) => [s.name, s]));

  // Reverse edges, so a skill page shows what points at it as well as where it points.
  for (const skill of skills) {
    for (const ref of skill.crossRefs) {
      bySlug.get(ref)?.referencedBy.push(skill.name);
    }
  }
  for (const skill of skills) skill.referencedBy.sort();

  const domains: DomainSummary[] = DOMAINS.map((meta) => {
    const members = skills.filter((s) => s.subdomain === meta.slug);
    return { ...meta, count: members.length, skills: members.map((s) => s.name) };
  }).sort((a, b) => b.count - a.count);

  const linkCounts = new Map<string, number>();
  let crossReferences = 0;
  for (const skill of skills) {
    for (const ref of skill.crossRefs) {
      crossReferences += 1;
      const target = bySlug.get(ref);
      if (!target || target.subdomain === skill.subdomain) continue;
      const key = [skill.subdomain, target.subdomain].sort().join("|");
      linkCounts.set(key, (linkCounts.get(key) ?? 0) + 1);
    }
  }
  const domainLinks = [...linkCounts.entries()]
    .map(([key, count]) => {
      const [source, target] = key.split("|");
      return { source, target, count };
    })
    .sort((a, b) => b.count - a.count);

  const tally = (values: string[]) => {
    const counts = new Map<string, number>();
    for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
    return [...counts.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  };

  const brokers = tally(skills.flatMap((s) => s.brokers_frameworks));
  const tags = tally(skills.flatMap((s) => s.tags));

  // Corpus size, measured rather than quoted. Reading ~2,000 small files costs well under a
  // second and means the landing page never carries a number the library has outgrown.
  const skillWords = skills.reduce((n, s) => n + s.words, 0);
  let markdownWords = skillWords;
  let pythonLines = 0;
  for (const skill of skills) {
    for (const f of skill.files.references) {
      if (f.endsWith(".md")) markdownWords += countWords(`${skill.path}/references/${f}`);
    }
    for (const f of skill.files.assets) {
      if (f.endsWith(".md")) markdownWords += countWords(`${skill.path}/assets/${f}`);
    }
    for (const f of skill.files.scripts) pythonLines += countLines(`${skill.path}/scripts/${f}`);
  }

  return {
    version: index.version,
    totalSkills: skills.length,
    domains,
    skills,
    bySlug,
    stats: {
      skills: skills.length,
      domains: domains.length,
      helperModules: skills.reduce(
        (n, s) => n + s.files.scripts.filter((f) => !f.startsWith("test_")).length,
        0,
      ),
      testSuites: skills.reduce(
        (n, s) => n + s.files.scripts.filter((f) => f.startsWith("test_")).length,
        0,
      ),
      crossReferences,
      testsPassing: testsPassingFromReadme(),
      brokersFrameworks: brokers.length,
      markdownWords,
      skillWords,
      pythonLines,
    },
    domainLinks,
    brokers,
    tags,
  };
}

const docs = new Map<string, Promise<SkillDoc>>();

/**
 * Render one skill's Markdown. Memoized, and only ever called for a slug the caller has
 * already found in the library, so an unknown slug is a bug rather than a 404.
 */
export function getSkillDoc(slug: string): Promise<SkillDoc> {
  let promise = docs.get(slug);
  if (promise) return promise;

  promise = (async (): Promise<SkillDoc> => {
    const library = await getLibrary();
    const skill = library.bySlug.get(slug);
    if (!skill) throw new Error(`getSkillDoc called for an unknown skill: ${slug}`);

    const { html, toc } = await renderMarkdown(readBody(skill), {
      skillNames: new Set(library.bySlug.keys()),
      currentSkill: skill.name,
      docDir: skill.path,
    });
    return { ...skill, html, toc };
  })();

  docs.set(slug, promise);
  return promise;
}

/** Compact projection shipped to the client for search: descriptions included, no HTML. */
export type SearchRecord = {
  n: string;
  d: string;
  s: string;
  t: string[];
  b: string[];
};

export function toSearchRecords(skills: Skill[]): SearchRecord[] {
  return skills.map((s) => ({
    n: s.name,
    d: s.description,
    s: s.subdomain,
    t: s.tags,
    b: s.brokers_frameworks,
  }));
}
