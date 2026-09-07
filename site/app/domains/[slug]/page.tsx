import type { Metadata } from "next";
import { Link } from "next-view-transitions";
import { notFound } from "next/navigation";
import type { CSSProperties } from "react";
import { getLibrary } from "@/lib/content";
import { DOMAINS, domainMeta } from "@/lib/domains";
import { DomainGlyph } from "@/components/domain-glyph";
import { SkillRow } from "@/components/skill-row";
import { SpotlightGrid } from "@/components/spotlight-grid";
import { ArrowRight } from "@/components/icons";

type Params = { slug: string };

export function generateStaticParams(): Params[] {
  return DOMAINS.map((domain) => ({ slug: domain.slug }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const library = await getLibrary();
  const domain = library.domains.find((d) => d.slug === slug);
  if (!domain) return {};
  return {
    title: domain.label,
    description: `${domain.count} skills covering ${domain.blurb.charAt(0).toLowerCase()}${domain.blurb.slice(1)}`,
    alternates: { canonical: `/domains/${domain.slug}/` },
  };
}

/**
 * A domain page shares the document tier's budget: no motion library, CSS entrances only,
 * every skill listed in full so the whole library stays crawlable from sixteen pages.
 */
export default async function DomainPage({ params }: { params: Promise<Params> }) {
  const { slug } = await params;
  const library = await getLibrary();
  const domain = library.domains.find((d) => d.slug === slug);
  if (!domain) notFound();

  const rank = library.domains.findIndex((d) => d.slug === slug) + 1;
  const skills = domain.skills
    .map((name) => library.bySlug.get(name)!)
    .sort((a, b) => a.name.localeCompare(b.name));

  // Which other domains this one hands off to most often.
  const neighbours = library.domainLinks
    .filter((link) => link.source === slug || link.target === slug)
    .slice(0, 6)
    .map((link) => {
      const other = link.source === slug ? link.target : link.source;
      return { domain: library.domains.find((d) => d.slug === other)!, count: link.count };
    })
    .filter((n) => n.domain);
  const maxNeighbour = neighbours[0]?.count ?? 1;

  const brokers = new Map<string, number>();
  for (const skill of skills) {
    for (const broker of skill.brokers_frameworks) brokers.set(broker, (brokers.get(broker) ?? 0) + 1);
  }
  const topBrokers = [...brokers.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 14);

  const outgoing = skills.reduce((n, s) => n + s.crossRefs.length, 0);
  const incoming = skills.reduce((n, s) => n + s.referencedBy.length, 0);

  // Group by first letter for a scannable index of 16 to 44 rows.
  const letters = new Map<string, typeof skills>();
  for (const skill of skills) {
    const key = skill.name.charAt(0).toUpperCase();
    letters.set(key, [...(letters.get(key) ?? []), skill]);
  }

  return (
    <div className="tint" style={{ "--hue": domain.hue } as CSSProperties}>
      <header className="border-b border-line">
        <div className="container-x pb-12 pt-8 sm:pt-12">
          <nav
            aria-label="Breadcrumb"
            className="mono rise flex items-center gap-2 text-[0.6875rem] uppercase tracking-[0.1em] text-subtle"
            style={{ "--d": "0ms" } as CSSProperties}
          >
            <Link prefetch={false} href="/domains" className="transition-colors hover:text-fg">
              Domains
            </Link>
            <span aria-hidden="true">/</span>
            <span className="text-fg">
              {String(rank).padStart(2, "0")} of {library.domains.length}
            </span>
          </nav>

          <div className="mt-8 grid gap-10 lg:grid-cols-12 lg:gap-12">
            <div className="lg:col-span-7">
              <div className="rise flex items-start gap-5" style={{ "--d": "40ms" } as CSSProperties}>
                <DomainGlyph slug={domain.slug} size={52} className="mt-1.5 text-fg" />
                <h1 className="display display-lg text-fg">{domain.label}</h1>
              </div>
              <p className="situation rise mt-7 max-w-[38ch] text-muted" style={{ "--d": "100ms" } as CSSProperties}>
                {domain.blurb}
              </p>
            </div>

            <dl
              className="rise grid grid-cols-3 gap-x-6 gap-y-5 self-end text-[0.75rem] lg:col-span-5"
              style={{ "--d": "140ms" } as CSSProperties}
            >
              <div className="border-t border-line pt-2.5">
                <dt className="eyebrow">Skills</dt>
                <dd className="numeral mt-2 text-4xl text-fg" style={{ color: "var(--tint)" }}>
                  {domain.count}
                </dd>
              </div>
              <div className="border-t border-line pt-2.5">
                <dt className="eyebrow">Hand off</dt>
                <dd className="numeral mt-2 text-4xl text-fg">{outgoing}</dd>
              </div>
              <div className="border-t border-line pt-2.5">
                <dt className="eyebrow">Handed off from</dt>
                <dd className="numeral mt-2 text-4xl text-fg">{incoming}</dd>
              </div>
            </dl>
          </div>
        </div>
      </header>

      <div className="container-x">
        {(neighbours.length > 0 || topBrokers.length > 0) && (
          <div className="fade grid gap-10 border-b border-line py-10 lg:grid-cols-12" style={{ "--d": "180ms" } as CSSProperties}>
            {neighbours.length > 0 && (
              <div className="min-w-0 lg:col-span-6">
                <p className="eyebrow mb-4">Hands off most often to</p>
                <ol className="space-y-2.5">
                  {neighbours.map(({ domain: other, count }) => {
                    const m = domainMeta(other.slug);
                    return (
                      <li key={other.slug} className="tint" style={{ "--hue": m.hue } as CSSProperties}>
                        <Link prefetch={false}
                          href={`/domains/${other.slug}/`}
                          className="group grid grid-cols-[10rem_minmax(0,1fr)_2.5rem] items-center gap-3 text-[0.8125rem] text-muted transition-colors duration-(--t-quick) hover:text-fg sm:grid-cols-[13rem_minmax(0,1fr)_2.5rem]"
                        >
                          <span className="truncate">{other.label}</span>
                          <span className="h-px w-full bg-line">
                            <span
                              className="block h-full origin-left"
                              style={{ background: "var(--tint)", width: `${Math.round((count / maxNeighbour) * 100)}%` }}
                            />
                          </span>
                          <span className="mono text-right text-[0.6875rem] tabular-nums text-subtle">{count}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ol>
              </div>
            )}

            {topBrokers.length > 0 && (
              <div className="min-w-0 lg:col-span-6">
                <p className="eyebrow mb-4">Brokers, venues and frameworks named here</p>
                <ul className="flex flex-wrap gap-1.5">
                  {topBrokers.map(([name, count]) => (
                    <li key={name}>
                      <Link prefetch={false} href={`/skills/?broker=${encodeURIComponent(name)}`} className="chip max-w-full whitespace-normal [overflow-wrap:anywhere]">
                        {name}
                        <span className="text-subtle">{count}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        <div className="grid gap-8 pt-10 lg:grid-cols-12">
          <div className="lg:col-span-3">
            <div className="lg:sticky lg:top-[calc(var(--header-h)+1.5rem)]">
              <p className="eyebrow">Index</p>
              <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted">
                All {domain.count} skills in this domain, alphabetically. Hover a row for its
                situation; open it for the full playbook.
              </p>
              <ul className="mono mt-5 hidden flex-wrap gap-x-2.5 gap-y-1 text-[0.6875rem] text-subtle lg:flex">
                {[...letters.keys()].map((letter) => (
                  <li key={letter}>
                    <a href={`#letter-${letter}`} className="hover:text-fg">
                      {letter}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <SpotlightGrid className="lg:col-span-9">
            {[...letters.entries()].map(([letter, group]) => (
              <section key={letter} aria-labelledby={`letter-${letter}`} className="mb-8">
                <h2 id={`letter-${letter}`} className="rule-label mono py-2 text-[0.6875rem] uppercase tracking-[0.14em] text-subtle">
                  {letter}
                </h2>
                {group.map((skill, i) => (
                  <SkillRow
                    key={skill.name}
                    index={i}
                    skill={{ name: skill.name, description: skill.description, subdomain: skill.subdomain, tags: skill.tags }}
                    showDomain={false}
                  />
                ))}
              </section>
            ))}
          </SpotlightGrid>
        </div>

        <div className="mt-6 flex justify-end">
          <Link prefetch={false} href="/skills" className="btn btn-sm">
            Search the whole catalog
            <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </div>
    </div>
  );
}
