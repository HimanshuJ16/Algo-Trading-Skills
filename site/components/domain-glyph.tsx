/**
 * A deterministic 4x4 mark per domain, derived from the slug.
 *
 * Sixteen domains would otherwise need sixteen hand-drawn icons that all have to mean
 * something. A generated glyph is instead a consistent, recognizable fingerprint: the same
 * domain always draws the same pattern, and every pattern is visibly distinct from the rest.
 *
 * Rendered as two paths (lit cells, unlit cells) rather than sixteen rects, so a page that
 * lists all sixteen domains carries a few hundred bytes of markup per glyph, not several
 * kilobytes.
 */

function fingerprint(slug: string): boolean[] {
  let h = 0x811c9dc5;
  for (let i = 0; i < slug.length; i += 1) {
    h ^= slug.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  const cells: boolean[] = [];
  for (let i = 0; i < 16; i += 1) {
    h = Math.imul(h ^ (h >>> 15), 0x2545f491) >>> 0;
    cells.push((h & 0xff) > 104);
  }
  // Never render an empty or a solid mark.
  const on = cells.filter(Boolean).length;
  if (on < 5) cells[0] = cells[5] = cells[10] = cells[15] = true;
  if (on > 13) cells[3] = cells[6] = false;
  return cells;
}

const UNIT = 25;

function squares(cells: boolean[], lit: boolean): string {
  const inset = lit ? 3.5 : 9.5;
  const size = UNIT - inset * 2;
  let d = "";
  cells.forEach((on, i) => {
    if (on !== lit) return;
    const x = (i % 4) * UNIT + inset;
    const y = Math.floor(i / 4) * UNIT + inset;
    d += `M${x} ${y}h${size}v${size}h-${size}z`;
  });
  return d;
}

export function DomainGlyph({ slug, size = 34, className = "" }: { slug: string; size?: number; className?: string }) {
  const cells = fingerprint(slug);
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true" className={`shrink-0 ${className}`}>
      <path d={squares(cells, false)} fill="currentColor" opacity="0.16" />
      <path d={squares(cells, true)} fill="var(--tint, currentColor)" opacity="0.95" />
    </svg>
  );
}
