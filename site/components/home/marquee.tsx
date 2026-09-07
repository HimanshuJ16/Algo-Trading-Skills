import { Link } from "next-view-transitions";
import type { CSSProperties } from "react";

export type Edge = { from: string; to: string; hue: number };

/**
 * A marquee that carries information: real handoffs from the library, each one a pair of
 * links. Two identical halves loop seamlessly; hover pauses it because the slugs are meant
 * to be read. Under reduced motion the track wraps into a static list.
 */
export function HandoffMarquee({ edges, label }: { edges: Edge[]; label: string }) {
  const doubled = [...edges, ...edges];
  return (
    <div className="marquee relative border-y border-line py-4" aria-label={label}>
      <div className="fade-x overflow-hidden">
        <ul className="marquee-track gap-x-8 gap-y-2 px-4" style={{ "--marquee-t": `${edges.length * 4.5}s` } as CSSProperties}>
          {doubled.map((edge, i) => (
            <li
              key={`${edge.from}-${edge.to}-${i}`}
              className="tint mono flex shrink-0 items-center gap-2 text-[0.75rem] text-muted"
              style={{ "--hue": edge.hue } as CSSProperties}
              aria-hidden={i >= edges.length}
            >
              <Link prefetch={false} href={`/skills/${edge.from}/`} className="transition-colors duration-(--t-quick) hover:text-fg" tabIndex={i >= edges.length ? -1 : undefined}>
                {edge.from}
              </Link>
              <span style={{ color: "var(--tint)" }} aria-hidden="true">
                →
              </span>
              <Link prefetch={false} href={`/skills/${edge.to}/`} className="transition-colors duration-(--t-quick) hover:text-fg" tabIndex={i >= edges.length ? -1 : undefined}>
                {edge.to}
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
