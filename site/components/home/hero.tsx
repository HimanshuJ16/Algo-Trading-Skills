import { Link } from "next-view-transitions";
import { Counter, Words } from "@/components/motion-primitives";
import { HeroGraph } from "@/components/home/hero-graph";
import { PaletteButton } from "@/components/palette-button";
import { ArrowDown } from "@/components/icons";
import { withBase } from "@/lib/site";

type Stat = { label: string; value: number };

/**
 * The graph is the hero. It sits centred and complete behind the headline, sized to the
 * viewport so nothing is clipped at any width; a radial scrim keeps the copy legible over
 * the densest part of the ring. This is a server component: the only client code is the
 * canvas itself, loaded after hydration, and the four counters.
 */
export function Hero({ version, stats, testsPassing }: { version: string; stats: Stat[]; testsPassing: string | null }) {
  return (
    <section className="relative overflow-clip border-b border-line">
      <link rel="preload" href={withBase("/graph.json")} as="fetch" crossOrigin="anonymous" />
      <div
        className="hairline-grid pointer-events-none absolute inset-0 mask-[radial-gradient(ellipse_at_center,#000_20%,transparent_70%)]"
        aria-hidden="true"
      />

      <div className="relative flex min-h-[calc(100svh-var(--header-h)-6rem)] items-center justify-center py-14 sm:py-20">
        {/* The ring, behind everything, always whole. */}
        <div className="absolute inset-0 grid place-items-center" aria-hidden="true">
          <div
            className="w-[min(96vw,calc(100svh-var(--header-h)-3rem),64rem)] opacity-90"
            data-cursor="drag"
            data-cursor-label="hover a node"
          >
            <HeroGraph />
          </div>
        </div>

        <div className="hero-scrim container-x pointer-events-none relative z-10 flex max-w-4xl flex-col items-center text-center">
          <p className="mono flex flex-wrap items-center justify-center gap-x-4 gap-y-2 text-[0.6875rem] uppercase tracking-[0.14em] text-subtle">
            <span className="flex items-center gap-2 text-fg">
              <span className="pulse size-1.5 rounded-full bg-signal" aria-hidden="true" />v{version}
            </span>
            <span>{stats[0]?.value} skills</span>
            <span>agentskills.io</span>
            <span>Apache-2.0</span>
          </p>

          <h1 className="display mt-7 text-[clamp(2.6rem,7vw,6rem)] text-fg">
            <Words text="Every skill declares" delay={100} />
            <br />
            <span className="serif font-normal italic tracking-[-0.02em] text-muted">
              <Words text="where it stops." delay={380} />
            </span>
          </h1>

          <p className="rise mt-7 max-w-[46ch] text-[1rem] leading-[1.6] text-muted text-pretty sm:text-[1.0625rem]" style={{ "--d": "700ms" } as React.CSSProperties}>
            {stats[0]?.value} playbooks for AI agents that write trading systems, each with a
            working reference implementation and a test suite. Every one names the situations
            it covers, the ones it does not, and the skill that takes over. That graph of
            handoffs is drawn behind you, from the library itself.
          </p>

          <div className="rise pointer-events-auto mt-8 flex flex-wrap items-center justify-center gap-3" style={{ "--d": "820ms" } as React.CSSProperties}>
            <PaletteButton />
            <Link prefetch={false} href="/skills" className="btn" data-cursor-label="browse">
              Browse the catalog
            </Link>
          </div>
        </div>
      </div>

      {/* The ledger */}
      <div className="relative border-t border-line bg-[color-mix(in_oklab,var(--c-bg)_80%,transparent)] backdrop-blur-sm">
        <div className="container-x grid grid-cols-2 gap-px bg-line sm:grid-cols-4 *:bg-bg">
          {stats.map((stat) => (
            <div key={stat.label} className="py-5 pr-4 sm:py-6">
              <p className="eyebrow">{stat.label}</p>
              <p className="numeral mt-2 text-[2rem] text-fg sm:text-[2.5rem]">
                <Counter value={stat.value} />
              </p>
            </div>
          ))}
        </div>
        {testsPassing && (
          <p className="container-x mono pb-5 pt-3 text-[0.6875rem] text-subtle">
            {testsPassing} unit tests passing in CI on Python 3.10, 3.12 and 3.13.
            <span className="ml-3 hidden items-center gap-1 sm:inline-flex">
              <ArrowDown className="size-3" /> scroll
            </span>
          </p>
        )}
      </div>
    </section>
  );
}
