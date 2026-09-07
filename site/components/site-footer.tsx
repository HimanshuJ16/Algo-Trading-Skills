import Link from "next/link";
import { SITE } from "@/lib/site";
import { GitHubMark } from "@/components/icons";

/**
 * A colophon rather than a sitemap. The footer states where every page came from, because
 * that provenance is the site's one real claim.
 */
export function SiteFooter({ generatedFrom }: { generatedFrom?: string }) {
  return (
    <footer className="mt-(--section) border-t border-line">
      <div className="container-x py-14">
        <div className="grid gap-10 lg:grid-cols-12">
          <div className="lg:col-span-6">
            <p className="mono text-[0.8125rem] text-fg">algo-trading-skills</p>
            <p className="mt-3 max-w-md text-[0.875rem] leading-relaxed text-muted text-pretty">
              {SITE.tagline}. Every page here is generated at build time from{" "}
              <code className="mono text-fg">index.json</code> and the{" "}
              <code className="mono text-fg">SKILL.md</code> files in the repository
              {generatedFrom ? ` (${generatedFrom})` : ""}. The site is a view of the library;
              it cannot say anything the library does not.
            </p>
          </div>

          <div className="lg:col-span-3">
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
                <a
                  href={`${SITE.repo}/blob/main/docs/ROADMAP_500.md`}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline-grow hover:text-fg"
                >
                  Roadmap
                </a>
              </li>
            </ul>
          </div>

          <div className="lg:col-span-3">
            <p className="eyebrow">Project</p>
            <ul className="mt-3 space-y-2 text-[0.875rem] text-muted">
              <li>
                <a
                  href={SITE.repo}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline-grow inline-flex items-center gap-1.5 hover:text-fg"
                >
                  <GitHubMark className="size-3.5" />
                  GitHub
                </a>
              </li>
              <li>
                <a
                  href={`${SITE.repo}/blob/main/CONTRIBUTING.md`}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline-grow hover:text-fg"
                >
                  Contributing
                </a>
              </li>
              <li>
                <a
                  href={SITE.standard}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline-grow hover:text-fg"
                >
                  agentskills.io standard
                </a>
              </li>
            </ul>
          </div>
        </div>

        <div className="mt-12 grid gap-4 border-t border-line pt-8 text-[0.75rem] leading-relaxed text-subtle lg:grid-cols-12">
          <p className="lg:col-span-6">
            <span className="text-muted">Community project.</span> Independent and
            community-created. Not affiliated with Anthropic PBC or any broker, exchange, or
            vendor referenced in this library.
          </p>
          <p className="lg:col-span-5">
            <span className="text-muted">Engineering guidance, not financial, legal, or
            compliance advice.</span>{" "}
            These skills encode engineering practices for trading infrastructure. They do
            not guarantee strategy profitability and do not eliminate the risk of capital
            loss in live trading.
          </p>
          <p className="mono lg:col-span-1 lg:text-right">Apache-2.0</p>
        </div>
      </div>
    </footer>
  );
}
