import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { Link } from "next-view-transitions";
import { getLibrary } from "@/lib/content";
import { domainMeta } from "@/lib/domains";
import { Hero } from "@/components/home/hero";
import { Failures } from "@/components/home/failures";
import { DomainIndex } from "@/components/home/domain-index";
import { Anatomy } from "@/components/home/anatomy";
import { Spectrum } from "@/components/home/spectrum";
import { HandoffMarquee, type Edge } from "@/components/home/marquee";
import { GetStarted } from "@/components/home/get-started";
import { Reveal, Counter } from "@/components/motion-primitives";
import { ExperienceMount } from "@/components/experience-mount";
import { ArrowRight } from "@/components/icons";

export const metadata: Metadata = { alternates: { canonical: "/" } };

/**
 * Four failures that look like working code. The prose is site copy; the slugs are checked
 * against the library below, so a renamed skill fails this build rather than shipping a
 * dead link on the front page.
 */
const FAILURES = [
  {
    slug: "order-placement-idempotency",
    wrong: "The request timed out, so the order failed. Retry it.",
    right:
      "A timeout means unknown, never failed. Reconcile against the broker's order book before any retry, or the position doubles.",
  },
  {
    slug: "lookahead-bias-elimination",
    wrong: "Use the bar's close to decide whether to enter on that bar.",
    right:
      "The close is not knowable until the bar completes. A backtest that reads it is predicting the present.",
  },
  {
    slug: "kill-switch-and-drawdown-circuit-breakers",
    wrong: "Check the drawdown limit at the top of the strategy function.",
    right:
      "A limit inside the function it constrains can be skipped by the same bug it exists to catch. It needs veto power from outside.",
  },
  {
    slug: "websocket-subscription-reconciliation-after-reconnect",
    wrong: "On reconnect, resubscribe to every symbol.",
    right:
      "The server may still hold the old subscriptions. Reconcile what is actually subscribed, or every tick arrives twice.",
  },
] as const;

/** The canvas draws public/graph.json; this build refuses to ship if it disagrees with the library. */
function checkGraphAsset(expectedNodes: number, expectedEdges: number) {
  const file = path.join(process.cwd(), "public", "graph.json");
  if (!fs.existsSync(file)) {
    throw new Error("public/graph.json is missing. Run node scripts/generate-graph.mjs.");
  }
  const graph = JSON.parse(fs.readFileSync(file, "utf8")) as { nodes: unknown[]; edges: unknown[] };
  if (graph.nodes.length !== expectedNodes || graph.edges.length !== expectedEdges) {
    throw new Error(
      `public/graph.json has ${graph.nodes.length} nodes and ${graph.edges.length} edges; the library has ${expectedNodes} and ${expectedEdges}. Regenerate it.`,
    );
  }
}

export default async function HomePage() {
  const library = await getLibrary();

  for (const failure of FAILURES) {
    if (!library.bySlug.has(failure.slug)) {
      throw new Error(`Front page references a skill that no longer exists: ${failure.slug}`);
    }
  }
  checkGraphAsset(library.totalSkills, library.stats.crossReferences);

  const stats = [
    { label: "Skills", value: library.stats.skills },
    { label: "Domains", value: library.stats.domains },
    { label: "Handoffs", value: library.stats.crossReferences },
    { label: "Reference implementations", value: library.stats.helperModules },
  ];

  const failures = FAILURES.map((f) => {
    const skill = library.bySlug.get(f.slug)!;
    const meta = domainMeta(skill.subdomain);
    return { ...f, hue: meta.hue, domain: meta.label };
  });

  const sortedWords = library.skills.map((s) => s.words).sort((a, b) => a - b);
  const medianWords = sortedWords[Math.floor(sortedWords.length / 2)];

  // Real handoffs for the marquee: one cross-domain edge per domain, then a second pass.
  const edges: Edge[] = [];
  const used = new Set<string>();
  for (let pass = 0; pass < 2 && edges.length < 24; pass += 1) {
    for (const domain of library.domains) {
      const skill = domain.skills
        .map((n) => library.bySlug.get(n)!)
        .find((s) => !used.has(s.name) && s.crossRefs.some((r) => library.bySlug.get(r)?.subdomain !== s.subdomain));
      if (!skill) continue;
      const to = skill.crossRefs.find((r) => library.bySlug.get(r)?.subdomain !== skill.subdomain)!;
      used.add(skill.name);
      edges.push({ from: skill.name, to, hue: domainMeta(skill.subdomain).hue });
    }
  }

  const millions = (n: number) => (n / 1_000_000).toFixed(1);

  return (
    <>
      <ExperienceMount />

      <Hero version={library.version} stats={stats} testsPassing={library.stats.testsPassing} />

      <HandoffMarquee edges={edges} label="Real handoffs between skills in the library" />

      <Failures items={failures} />

      {/* ------------------------------------------------------------------ */}
      <section className="border-b border-line">
        <div className="container-x py-(--section)">
          <div className="grid gap-10 lg:grid-cols-12">
            <Reveal className="lg:col-span-4">
              <p className="eyebrow">Sixteen domains</p>
              <h2 className="display display-lg mt-4 text-fg">
                By the part of
                <br />
                the system.
              </h2>
              <p className="mt-5 max-w-[36ch] text-[0.95rem] leading-relaxed text-muted text-pretty">
                Domains describe where a skill sits in a trading system, not what asset class
                it trades. A skill about reconnect handling belongs to real-time architecture
                whether the feed carries equities or perpetual futures.
              </p>
              <Link prefetch={false} href="/domains" className="btn btn-sm mt-8" data-cursor-label="domains">
                All sixteen, with the graph
                <ArrowRight className="size-3.5" />
              </Link>
            </Reveal>
            <Reveal className="lg:col-span-8">
              <DomainIndex domains={library.domains} total={library.totalSkills} />
            </Reveal>
          </div>
        </div>
      </section>

      <Anatomy total={library.totalSkills} />

      {/* ------------------------------------------------------------------ */}
      <section className="border-b border-line">
        <div className="container-x py-(--section)">
          <div className="grid gap-10 lg:grid-cols-12">
            <Reveal className="lg:col-span-5">
              <p className="eyebrow">The scale</p>
              <h2 className="display display-lg mt-4 text-fg">
                <Counter value={library.stats.markdownWords} divisor={1_000_000} decimals={1} />M words.
                <br />
                <span className="text-muted">Read one at a time.</span>
              </h2>
              <p className="mt-5 max-w-[40ch] text-[0.95rem] leading-relaxed text-muted text-pretty">
                About {millions(library.stats.skillWords)} million words of SKILL.md and another{" "}
                {millions(library.stats.markdownWords - library.stats.skillWords)} million in
                references and checklists, beside{" "}
                {Math.round(library.stats.pythonLines / 1000)},000 lines of Python. No context
                window holds it, and none needs to. An agent reads the index, opens one
                document, and reads its boundary before anything else.
              </p>
            </Reveal>
            <Reveal className="lg:col-span-7">
              <p className="eyebrow mb-6">Every skill, by length, in ring order. Hover for the slug.</p>
              <Spectrum median={medianWords} total={library.totalSkills} />
            </Reveal>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      <section>
        <div className="container-x py-(--section)">
          <Reveal>
            <GetStarted total={library.totalSkills} />
          </Reveal>
        </div>
      </section>
    </>
  );
}
