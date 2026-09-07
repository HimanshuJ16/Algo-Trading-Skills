import type { Metadata } from "next";
import { Link } from "next-view-transitions";
import { notFound } from "next/navigation";
import type { CSSProperties } from "react";
import { getLibrary, getSkillDoc } from "@/lib/content";
import { domainMeta } from "@/lib/domains";
import { repoBlob, SITE } from "@/lib/site";
import { titleize } from "@/lib/titleize";
import { Toc } from "@/components/toc";
import { DocEnhancer } from "@/components/doc-enhancer";
import { CopyButton, CommandLine } from "@/components/copy-button";
import { Handoffs } from "@/components/handoffs";
import { ArrowRight, ArrowUpRight, GitHubMark } from "@/components/icons";

type Params = { slug: string };

export async function generateStaticParams(): Promise<Params[]> {
  const library = await getLibrary();
  return library.skills.map((skill) => ({ slug: skill.name }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { slug } = await params;
  const library = await getLibrary();
  const skill = library.bySlug.get(slug);
  if (!skill) return {};
  return {
    title: titleize(skill.name),
    description: skill.description,
    alternates: { canonical: `/skills/${skill.name}/` },
    openGraph: {
      title: `${titleize(skill.name)} · ${SITE.name}`,
      description: skill.description,
      type: "article",
      url: `/skills/${skill.name}/`,
    },
  };
}

function FileList({ label, files, dir, skillPath }: { label: string; files: string[]; dir: string; skillPath: string }) {
  if (files.length === 0) return null;
  return (
    <div>
      <p className="eyebrow mb-1.5">{label}</p>
      <ul className="space-y-0.5">
        {files.map((file) => (
          <li key={file}>
            <a
              href={repoBlob(`${skillPath}/${dir}/${file}`)}
              target="_blank"
              rel="noreferrer noopener"
              className="mono group flex items-center gap-1.5 py-0.5 text-[0.75rem] text-muted transition-colors duration-(--t-quick) hover:text-fg"
            >
              <span className="truncate">{file}</span>
              <ArrowUpRight className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The document tier. Reference material an engineer opens mid-incident: a reading column,
 * seven numbered sections, a contents rail that knows its positions in advance, and
 * nothing that animates on scroll. Entrances are CSS, complete within 400ms, and move
 * nothing that affects layout.
 */
export default async function SkillPage({ params }: { params: Promise<Params> }) {
  const { slug } = await params;
  const library = await getLibrary();
  if (!library.bySlug.has(slug)) notFound();
  const skill = await getSkillDoc(slug);

  const meta = domainMeta(skill.subdomain);
  const domain = library.domains.find((d) => d.slug === skill.subdomain)!;
  const position = domain.skills.indexOf(skill.name);
  const previous = position > 0 ? library.bySlug.get(domain.skills[position - 1]) : undefined;
  const next =
    position < domain.skills.length - 1 ? library.bySlug.get(domain.skills[position + 1]) : undefined;

  const ledger = [
    { label: "Domain", value: meta.label, href: `/domains/${meta.slug}/` },
    { label: "Version", value: skill.version },
    { label: "Reading", value: `${skill.readingMinutes} min` },
    { label: "Hands off to", value: String(skill.crossRefs.length) },
    { label: "Handed off from", value: String(skill.referencedBy.length) },
    { label: "License", value: skill.license },
  ];

  return (
    <div className="tint" style={{ "--hue": meta.hue } as CSSProperties}>
      {/* Head */}
      <header className="border-b border-line">
        <div className="container-x pb-10 pt-8 sm:pt-12">
          <nav
            aria-label="Breadcrumb"
            className="mono rise flex flex-wrap items-center gap-2 text-[0.6875rem] uppercase tracking-[0.1em] text-subtle"
            style={{ "--d": "0ms" } as CSSProperties}
          >
            <Link prefetch={false} href="/skills" className="transition-colors hover:text-fg">
              Catalog
            </Link>
            <span aria-hidden="true">/</span>
            <Link prefetch={false} href={`/domains/${meta.slug}/`} className="transition-colors hover:text-fg" style={{ color: "var(--tint)" }}>
              {meta.label}
            </Link>
            <span aria-hidden="true">/</span>
            <span className="text-fg">
              {String(position + 1).padStart(2, "0")} of {domain.count}
            </span>
          </nav>

          <div className="mt-8 grid gap-8 lg:grid-cols-12 lg:gap-12">
            <div className="lg:col-span-8">
              <h1 className="display display-md rise text-fg" style={{ "--d": "40ms" } as CSSProperties}>
                {titleize(skill.name)}
              </h1>

              <div className="rise mt-4 flex flex-wrap items-center gap-2" style={{ "--d": "80ms" } as CSSProperties}>
                <code className="vt-title mono text-[0.8125rem] text-muted">{skill.name}</code>
                <CopyButton value={skill.name} label="copy slug" />
                <a
                  href={repoBlob(skill.skill_md)}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="chip"
                >
                  <GitHubMark className="size-3" />
                  source
                </a>
              </div>

              <p className="situation rise mt-7 max-w-[34ch] text-fg" style={{ "--d": "120ms" } as CSSProperties}>
                {skill.description}
              </p>
            </div>

            <dl
              className="rise grid grid-cols-2 gap-x-6 gap-y-5 self-end text-[0.75rem] sm:grid-cols-3 lg:col-span-4 lg:grid-cols-2"
              style={{ "--d": "160ms" } as CSSProperties}
            >
              {ledger.map((item) => (
                <div key={item.label} className="border-t border-line pt-2.5">
                  <dt className="eyebrow">{item.label}</dt>
                  <dd className="mono mt-1 text-fg">
                    {item.href ? (
                      <Link prefetch={false} href={item.href} className="underline-grow" style={{ color: "var(--tint)" }}>
                        {item.value}
                      </Link>
                    ) : (
                      item.value
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          {skill.brokers_frameworks.length > 0 && (
            <div className="rise mt-8 flex flex-wrap items-center gap-1.5" style={{ "--d": "200ms" } as CSSProperties}>
              <span className="eyebrow mr-2">Covers</span>
              {skill.brokers_frameworks.map((broker) => (
                <span key={broker} className="chip max-w-full whitespace-normal [overflow-wrap:anywhere]">
                  {broker}
                </span>
              ))}
            </div>
          )}
        </div>
      </header>

      {/* Compact contents for narrow viewports */}
      <div className="sticky-label border-b border-line lg:hidden">
        <div className="container-x py-2">
          <Toc entries={skill.toc} horizontal />
        </div>
      </div>

      {/* Body */}
      <div className="container-x">
        <div className="grid gap-10 pt-10 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-16 lg:pt-14">
          <aside className="hidden lg:block">
            <div className="sticky top-[calc(var(--header-h)+1.5rem)] max-h-[calc(100dvh-var(--header-h)-3rem)] space-y-8 overflow-y-auto pb-6 pr-2">
              <Toc entries={skill.toc} />
              <div className="space-y-5">
                <FileList label="References" files={skill.files.references} dir="references" skillPath={skill.path} />
                <FileList label="Scripts" files={skill.files.scripts} dir="scripts" skillPath={skill.path} />
                <FileList label="Assets" files={skill.files.assets} dir="assets" skillPath={skill.path} />
              </div>
            </div>
          </aside>

          <div className="min-w-0">
            <article
              id="doc"
              className="doc fade"
              style={{ "--d": "120ms" } as CSSProperties}
              dangerouslySetInnerHTML={{ __html: skill.html }}
            />
            <DocEnhancer target="doc" />

            {skill.testCommand && (
              <section className="mt-14 max-w-[70ch] border-t border-line pt-8">
                <p className="eyebrow mb-3">Verify it, from the repository root</p>
                <CommandLine command={skill.testCommand} />
              </section>
            )}

            <div className="mt-14 grid gap-12 border-t border-line pt-10 lg:grid-cols-2">
              <Handoffs
                title="Hands off to"
                lede="Skills this document names, usually in When NOT to Use, as the owner of a case it excludes."
                slugs={skill.crossRefs}
                all={library.bySlug}
                emptyText="This skill names no other skill."
              />
              <Handoffs
                title="Handed off from"
                lede="Skills that name this one as the place a case belongs. The reverse edges of the graph."
                slugs={skill.referencedBy}
                all={library.bySlug}
                emptyText="No other skill hands off to this one yet."
              />
            </div>

            {/* Prev / next within the domain */}
            <nav aria-label="Within this domain" className="mt-14 grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2">
              {previous ? (
                <Link prefetch={false} href={`/skills/${previous.name}/`} className="group bg-bg p-5 transition-colors duration-(--t-quick) hover:bg-panel-2">
                  <span className="eyebrow flex items-center gap-1.5">
                    <ArrowRight className="size-3 rotate-180" />
                    Previous in {meta.label}
                  </span>
                  <span className="mono mt-2 block text-[0.8125rem] text-fg">{previous.name}</span>
                </Link>
              ) : (
                <span className="bg-bg p-5" />
              )}
              {next ? (
                <Link prefetch={false} href={`/skills/${next.name}/`} className="group bg-bg p-5 transition-colors duration-(--t-quick) hover:bg-panel-2 sm:text-right">
                  <span className="eyebrow flex items-center gap-1.5 sm:justify-end">
                    Next in {meta.label}
                    <ArrowRight className="size-3" />
                  </span>
                  <span className="mono mt-2 block text-[0.8125rem] text-fg">{next.name}</span>
                </Link>
              ) : (
                <span className="bg-bg p-5" />
              )}
            </nav>
          </div>
        </div>
      </div>
    </div>
  );
}
