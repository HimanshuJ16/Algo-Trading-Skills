import Link from "next/link";
import { AUTHOR, SITE } from "@/lib/site";
import { GitHubMark, GlobeIcon, LinkedInMark } from "@/components/icons";

const AUTHOR_LINKS = [
  { label: "GitHub", href: AUTHOR.github, Icon: GitHubMark },
  { label: "LinkedIn", href: AUTHOR.linkedin, Icon: LinkedInMark },
  { label: "Portfolio", href: AUTHOR.site, Icon: GlobeIcon },
];

/**
 * A colophon rather than a sitemap. The footer states where every page came from, because
 * that provenance is the site's one real claim, and credits the person who built it.
 */
export function SiteFooter() {
  return (
    <footer className="mt-(--section) border-t border-line">
      <div className="container-x py-14">
        <div className="grid gap-10 md:grid-cols-2 lg:grid-cols-12">
          <div className="md:col-span-2 lg:col-span-5">
            <p className="mono text-[0.8125rem] text-fg">algo-trading-skills</p>
            <p className="mt-3 max-w-md text-[0.875rem] leading-relaxed text-muted text-pretty">
              {SITE.tagline}. Every page here is generated at build time from{" "}
              <code className="mono text-fg">index.json</code> and the{" "}
              <code className="mono text-fg">SKILL.md</code> files in the repository. The site
              is a view of the library; it cannot say anything the library does not.
            </p>
          </div>

          <div className="lg:col-span-2">
            <p className="eyebrow">Browse</p>
            <ul className="mt-3 space-y-2 text-[0.875rem] text-muted">
              <li>
                <Link prefetch={false} href="/skills" className="underline-grow hover:text-fg">
                  Catalog
                </Link>
              </li>
              <li>
                <Link prefetch={false} href="/domains" className="underline-grow hover:text-fg">
                  Domains
                </Link>
              </li>
              <li>
                <a href={`${SITE.repo}/blob/main/docs/ROADMAP_500.md`} target="_blank" rel="noreferrer noopener" className="underline-grow hover:text-fg">
                  Roadmap
                </a>
              </li>
            </ul>
          </div>

          <div className="lg:col-span-2">
            <p className="eyebrow">Project</p>
            <ul className="mt-3 space-y-2 text-[0.875rem] text-muted">
              <li>
                <a href={SITE.repo} target="_blank" rel="noreferrer noopener" className="underline-grow hover:text-fg">
                  Repository
                </a>
              </li>
              <li>
                <a href={`${SITE.repo}/blob/main/CONTRIBUTING.md`} target="_blank" rel="noreferrer noopener" className="underline-grow hover:text-fg">
                  Contributing
                </a>
              </li>
              <li>
                <a href={SITE.standard} target="_blank" rel="noreferrer noopener" className="underline-grow hover:text-fg">
                  agentskills.io standard
                </a>
              </li>
            </ul>
          </div>

          <div className="lg:col-span-3">
            <p className="eyebrow">Author</p>
            <p className="mt-3 text-[0.875rem] text-fg">{AUTHOR.name}</p>
            <ul className="mt-3 flex flex-wrap gap-2">
              {AUTHOR_LINKS.map(({ label, href, Icon }) => (
                <li key={label}>
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer noopener me"
                    className="chip"
                    aria-label={`${AUTHOR.name} on ${label}`}
                  >
                    <Icon className="size-3.5" />
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Disclaimers: one measured block, not two columns racing each other. */}
        <div className="mt-12 border-t border-line pt-8">
          <div className="max-w-3xl space-y-3 text-[0.8125rem] leading-relaxed text-subtle text-pretty">
            <p>
              <span className="text-muted">Community project.</span> Independent and
              community-created. Not affiliated with Anthropic PBC or any broker, exchange, or
              vendor referenced in this library.
            </p>
            <p>
              <span className="text-muted">Engineering guidance, not financial, legal, or compliance advice.</span>{" "}
              These skills encode engineering practices for trading infrastructure. They do
              not guarantee strategy profitability and do not eliminate the risk of capital
              loss in live trading.
            </p>
          </div>
        </div>

        <div className="mono mt-8 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-line pt-5 text-[0.6875rem] uppercase tracking-[0.12em] text-subtle">
          <p>
            Built by{" "}
            <a href={AUTHOR.site} target="_blank" rel="noreferrer noopener" className="text-fg underline-grow">
              {AUTHOR.name}
            </a>
          </p>
          <p>{SITE.license} · {new Date().getFullYear()}</p>
        </div>
      </div>
    </footer>
  );
}
