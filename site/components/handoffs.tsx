import { Link } from "next-view-transitions";
import type { CSSProperties } from "react";
import type { Skill } from "@/lib/content";
import { DOMAINS, domainMeta } from "@/lib/domains";

/**
 * One node's edges, grouped by the domain on the other end. Server-rendered: a list of
 * anchors, no hydration.
 */
export function Handoffs({
  title,
  lede,
  slugs,
  all,
  emptyText,
}: {
  title: string;
  lede: string;
  slugs: string[];
  all: Map<string, Skill>;
  emptyText: string;
}) {
  const groups = new Map<string, string[]>();
  for (const slug of slugs) {
    const skill = all.get(slug);
    if (!skill) continue;
    const list = groups.get(skill.subdomain) ?? [];
    list.push(slug);
    groups.set(skill.subdomain, list);
  }
  const ordered = DOMAINS.filter((d) => groups.has(d.slug)).sort(
    (a, b) => groups.get(b.slug)!.length - groups.get(a.slug)!.length,
  );

  return (
    <section className="min-w-0">
      <h2 className="text-[1.0625rem] font-medium tracking-tight text-fg">
        {title}{" "}
        <span className="mono text-[0.75rem] font-normal text-subtle">{slugs.length}</span>
      </h2>
      <p className="mt-1.5 max-w-lg text-[0.8125rem] leading-relaxed text-muted">{lede}</p>

      {ordered.length === 0 ? (
        <p className="mono mt-5 text-[0.75rem] text-subtle">{emptyText}</p>
      ) : (
        <div className="mt-5 space-y-4">
          {ordered.map((domain) => {
            const meta = domainMeta(domain.slug);
            return (
              <div
                key={domain.slug}
                className="tint grid gap-2 sm:grid-cols-[11rem_minmax(0,1fr)]"
                style={{ "--hue": meta.hue } as CSSProperties}
              >
                <p className="mono flex items-start gap-2 pt-1 text-[0.6875rem] uppercase tracking-[0.1em]">
                  <span
                    className="mt-1 size-1.5 shrink-0 rounded-full"
                    style={{ background: "var(--tint)" }}
                    aria-hidden="true"
                  />
                  <span style={{ color: "var(--tint)" }}>{meta.short}</span>
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {groups.get(domain.slug)!.map((slug) => (
                    <li key={slug}>
                      <Link prefetch={false} href={`/skills/${slug}/`} className="chip max-w-full whitespace-normal text-left [overflow-wrap:anywhere]">
                        {slug}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
