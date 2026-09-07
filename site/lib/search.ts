import type { SearchRecord } from "./content";

/**
 * Deliberately not fuzzy.
 *
 * Every description in the library starts with "Use when ...", so a query is almost always
 * a situation ("timeout duplicate order", "wash sale") whose words appear literally in the
 * text. Token matching over weighted fields beats edit-distance fuzziness here: it never
 * surfaces a near-miss slug, and it keeps the whole index client-side with no dependency.
 *
 * Three refinements earn their keep against the real corpus:
 *
 *  - Short tokens match on word boundaries only. Substring matching on "dst" otherwise
 *    ranks any description containing those three letters above the DST skill itself.
 *  - Tokens match their own crude stem too, because the library writes "rate limiting"
 *    and "rate limits" while people search "rate limited".
 *  - Requiring every token is the first pass, not the only one. When that finds nothing,
 *    a second pass ranks partial matches rather than showing an empty result.
 */

export type Filters = {
  query: string;
  domains: string[];
  brokers: string[];
  tags: string[];
};

export const EMPTY_FILTERS: Filters = { query: "", domains: [], brokers: [], tags: [] };

export function isFiltered(f: Filters): boolean {
  return (
    f.query.trim().length > 0 ||
    f.domains.length > 0 ||
    f.brokers.length > 0 ||
    f.tags.length > 0
  );
}

function tokenize(input: string): string[] {
  return input
    .toLowerCase()
    .split(/[^a-z0-9+.]+/)
    .filter((t) => t.length > 0);
}

/** The token plus a crude stem, so "limited" also matches "limit" and "limiting". */
function variantsOf(token: string): string[] {
  const out = [token];
  for (const suffix of ["ing", "ed", "es", "s"]) {
    if (token.length > suffix.length + 2 && token.endsWith(suffix)) {
      const stem = token.slice(0, -suffix.length);
      if (!out.includes(stem)) out.push(stem);
    }
  }
  return out;
}

const SHORT = 3;
const boundaryCache = new Map<string, RegExp>();

function boundary(token: string): RegExp {
  let re = boundaryCache.get(token);
  if (!re) {
    re = new RegExp(`(?:^|[^a-z0-9])${token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![a-z0-9])`);
    boundaryCache.set(token, re);
  }
  return re;
}

/** Index of `token` in `haystack`, or -1. Short tokens must sit on a word boundary. */
function indexOfToken(haystack: string, token: string): number {
  if (token.length > SHORT) return haystack.indexOf(token);
  const match = boundary(token).exec(haystack);
  if (!match) return -1;
  return match.index === 0 ? 0 : match.index + 1;
}

function scoreVariant(record: SearchRecord, token: string): number {
  const name = record.n;
  let score = 0;

  if (name === token) return 1200;
  if (name.startsWith(token)) score = Math.max(score, 420);
  else if (indexOfToken(name, token) !== -1) score = Math.max(score, 300);
  else if (name.split("-").some((part) => part.startsWith(token) && token.length > SHORT)) {
    score = Math.max(score, 260);
  }

  for (const tag of record.t) {
    if (tag === token) score = Math.max(score, 220);
    else if (indexOfToken(tag, token) !== -1) score = Math.max(score, 150);
  }

  for (const broker of record.b) {
    if (indexOfToken(broker.toLowerCase(), token) !== -1) score = Math.max(score, 180);
  }

  if (indexOfToken(record.s, token) !== -1) score = Math.max(score, 120);

  const at = indexOfToken(record.d.toLowerCase(), token);
  if (at !== -1) {
    // Earlier in the description means closer to the trigger clause, which is what matters.
    score = Math.max(score, 90 + Math.max(0, 40 - Math.floor(at / 6)));
  }

  return score;
}

function scoreToken(record: SearchRecord, token: string): number {
  let best = 0;
  const variants = variantsOf(token);
  for (let i = 0; i < variants.length; i += 1) {
    // A stem match is real but weaker than the word the user actually typed.
    const raw = scoreVariant(record, variants[i]);
    const value = i === 0 ? raw : Math.round(raw * 0.8);
    if (value > best) best = value;
  }
  return best;
}

export type Scored = { record: SearchRecord; score: number };

export function search(records: readonly SearchRecord[], filters: Filters): Scored[] {
  const tokens = tokenize(filters.query);
  const domains = new Set(filters.domains);
  const brokers = new Set(filters.brokers);
  const tags = new Set(filters.tags);

  const candidates: SearchRecord[] = [];
  for (const record of records) {
    if (domains.size > 0 && !domains.has(record.s)) continue;
    if (tags.size > 0 && !record.t.some((t) => tags.has(t))) continue;
    if (brokers.size > 0 && !record.b.some((b) => brokers.has(b))) continue;
    candidates.push(record);
  }

  if (tokens.length === 0) {
    return candidates
      .map((record) => ({ record, score: 0 }))
      .sort((a, b) => a.record.n.localeCompare(b.record.n));
  }

  const all: Scored[] = [];
  const partial: Scored[] = [];

  for (const record of candidates) {
    let total = 0;
    let matched = 0;
    for (const token of tokens) {
      const score = scoreToken(record, token);
      if (score > 0) {
        matched += 1;
        total += score;
      }
    }
    if (matched === 0) continue;
    if (matched === tokens.length) all.push({ record, score: total });
    // Missing a term is a real cost, not a rounding one.
    else partial.push({ record, score: Math.round((total * matched) / (tokens.length * 2)) });
  }

  const byScore = (a: Scored, b: Scored) =>
    b.score - a.score || a.record.n.localeCompare(b.record.n);

  if (all.length > 0) return all.sort(byScore);
  return partial.sort(byScore);
}

/** Split text into alternating non-match/match runs for highlighting. */
export function highlight(text: string, query: string): { text: string; hit: boolean }[] {
  const tokens = tokenize(query).filter((t) => t.length > 1);
  if (tokens.length === 0) return [{ text, hit: false }];

  const ranges: [number, number][] = [];
  const lower = text.toLowerCase();
  for (const token of tokens) {
    for (const variant of variantsOf(token)) {
      let from = 0;
      for (;;) {
        const at = indexOfToken(lower.slice(from), variant);
        if (at === -1) break;
        ranges.push([from + at, from + at + variant.length]);
        from = from + at + variant.length;
      }
    }
  }
  if (ranges.length === 0) return [{ text, hit: false }];

  ranges.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }

  const parts: { text: string; hit: boolean }[] = [];
  let cursor = 0;
  for (const [start, end] of merged) {
    if (start > cursor) parts.push({ text: text.slice(cursor, start), hit: false });
    parts.push({ text: text.slice(start, end), hit: true });
    cursor = end;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), hit: false });
  return parts;
}
