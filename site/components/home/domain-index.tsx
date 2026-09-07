import { Link } from "next-view-transitions";
import type { CSSProperties } from "react";
import type { DomainSummary } from "@/lib/content";
import { DomainGlyph } from "@/components/domain-glyph";
import { SpotlightGrid } from "@/components/spotlight-grid";
import { ArrowRight } from "@/components/icons";

/**
 * Sixteen domains as an index, not a card grid. Numbered, with a bar for the count so the
 * shape of the library is visible in one glance, and the hue swatch that colours every page
 * under it.
 */
export function DomainIndex({ domains, total }: { domains: DomainSummary[]; total: number }) {
  const max = Math.max(...domains.map((d) => d.count), 1);
  return (
    <SpotlightGrid as="ol" className="border-t border-line">
      {domains.map((domain, i) => (
        <li key={domain.slug}>
          <Link prefetch={false}
            href={`/domains/${domain.slug}/`}
            data-spot
            className="tint row grid-cols-[2rem_minmax(0,1fr)_auto] items-center md:grid-cols-[2.5rem_2.5rem_minmax(0,14rem)_minmax(0,1fr)_5rem_auto]"
            style={{ "--hue": domain.hue } as CSSProperties}
          >
            <span className="mono text-[0.6875rem] text-subtle">{String(i + 1).padStart(2, "0")}</span>
            <span className="hidden md:block">
              <DomainGlyph slug={domain.slug} size={22} className="text-fg" />
            </span>
            <span className="text-[0.95rem] font-medium text-fg">{domain.label}</span>
            <span className="col-span-full flex items-center gap-3 md:col-span-1">
              <span className="hidden h-px flex-1 bg-line md:block">
                <span className="block h-full origin-left" style={{ background: "var(--tint)", width: `${(domain.count / max) * 100}%` }} />
              </span>
              <span className="text-[0.8125rem] text-muted md:hidden">{domain.short}</span>
            </span>
            <span className="mono text-right text-[0.75rem] tabular-nums text-fg">
              {domain.count}
              <span className="text-subtle"> / {total}</span>
            </span>
            <ArrowRight className="size-3.5 -translate-x-1 text-subtle opacity-0 transition-[opacity,transform] duration-(--t-quick) ease-(--ease-out) group-hover:translate-x-0 group-hover:opacity-100 [.row:hover_&]:translate-x-0 [.row:hover_&]:opacity-100" />
          </Link>
        </li>
      ))}
    </SpotlightGrid>
  );
}
