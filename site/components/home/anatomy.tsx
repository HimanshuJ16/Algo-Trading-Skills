"use client";

import { useRef } from "react";
import { keyframes, useSectionProgress } from "@/lib/use-section-progress";

/**
 * The seven sections every skill carries, and what each guarantees. Site copy about the
 * structure of the library, not about any skill; the headings themselves are the ones CI
 * validates in all 501 documents.
 */
const SECTIONS = [
  {
    id: "when-to-use",
    title: "When to Use",
    note: "The trigger, stated as a situation. Every description opens with “Use when…”, so an agent matches on the symptom in front of it rather than the subject it guesses.",
  },
  {
    id: "when-not-to-use",
    title: "When NOT to Use",
    note: "Where the skill stops. Each excluded case names the skill that owns it. This one section is why the library is a graph, and why a near-miss match is handed on instead of applied.",
  },
  {
    id: "prerequisites",
    title: "Prerequisites",
    note: "What must already be true before the workflow is safe: a broker field that echoes back, a durable ledger, a clock you trust.",
  },
  {
    id: "workflow",
    title: "Workflow",
    note: "The procedure, in order. The reference implementation in scripts/ follows it step for step, so the prose and the code cannot disagree without a test noticing.",
  },
  {
    id: "common-pitfalls",
    title: "Common Pitfalls",
    note: "The named failure modes: the ones that pass review and fail in production. Would following this skill have prevented a real incident is the bar for inclusion.",
  },
  {
    id: "verification",
    title: "Verification",
    note: "A command you can run. Every skill quotes its own test suite, and the sign-off checklist in assets/ is derived from this section.",
  },
  {
    id: "related-skills",
    title: "Related Skills",
    note: "Neighbours in the graph, each with the reason it is adjacent. Every slug named here must exist, or the build that validates the library fails.",
  },
];

const FILES = [
  ["SKILL.md", "the seven sections"],
  ["references/standards.md", "broker coverage, regulatory touchpoints"],
  ["references/workflows.md", "the full procedure"],
  ["scripts/<helper>.py", "a standalone reference implementation"],
  ["scripts/test_<helper>.py", "its unittest suite"],
  ["assets/checklist.md", "sign-off, derived from Verification"],
];

const N = SECTIONS.length;

/**
 * Pinned on large screens with motion allowed: a schematic SKILL.md whose marker slides
 * through the seven sections while the note for each fades in. On small screens, and under
 * reduced motion, the same content is a static grid. Both ship in the HTML; CSS picks one.
 */
export function Anatomy({ total }: { total: number }) {
  const ref = useRef<HTMLElement>(null);
  const marker = useRef<HTMLDivElement>(null);
  const notes = useRef<(HTMLDivElement | null)[]>([]);

  useSectionProgress(ref, (p) => {
    const i = Math.min(N - 1, Math.floor(p * N));
    if (marker.current) marker.current.style.transform = `translate3d(0, ${i * 100}%, 0)`;
    notes.current.forEach((el, index) => {
      if (!el) return;
      const start = index / N;
      const end = (index + 1) / N;
      const fadeIn = start + (end - start) * 0.12;
      const fadeOut = end - (end - start) * 0.1;
      const first = index === 0;
      const last = index === N - 1;
      const opacity = keyframes(p, [start, fadeIn, fadeOut, end], [first ? 1 : 0, 1, 1, last ? 1 : 0]);
      const y = keyframes(p, [start, fadeIn, fadeOut, end], [first ? 0 : 18, 0, 0, last ? 0 : -18]);
      el.style.opacity = String(opacity);
      el.style.transform = `translate3d(0, ${y}px, 0)`;
      el.style.visibility = opacity <= 0.001 ? "hidden" : "visible";
    });
  });

  return (
    <>
      <section className="block border-b border-line bg-panel/40 lg:hidden lg:motion-reduce:block">
        <div className="container-x py-(--section)">
          <Heading total={total} />
          <dl className="reveal-stagger mt-12 grid gap-px bg-line sm:grid-cols-2 lg:grid-cols-3 [&>*]:bg-bg">
            {SECTIONS.map((s, i) => (
              <div key={s.id} className="p-6" style={{ "--i": i } as React.CSSProperties}>
                <dt className="flex items-baseline gap-3 text-fg">
                  <span className="mono text-[0.6875rem] text-subtle">{String(i + 1).padStart(2, "0")}</span>
                  <span className="font-medium">{s.title}</span>
                </dt>
                <dd className="mt-3 text-[0.875rem] leading-relaxed text-muted">{s.note}</dd>
              </div>
            ))}
            <div className="p-6" style={{ "--i": 7 } as React.CSSProperties}>
              <dt className="eyebrow">Six files, machine-enforced</dt>
              <dd className="mono mt-3 space-y-1 text-[0.75rem] text-muted">
                {FILES.map(([f]) => (
                  <p key={f}>{f}</p>
                ))}
              </dd>
            </div>
          </dl>
        </div>
      </section>

      <section
        ref={ref}
        className="relative hidden border-b border-line bg-panel/40 lg:block lg:motion-reduce:hidden"
        style={{ height: `${N * 55 + 60}svh` }}
      >
        <div className="sticky top-0 flex h-svh items-center overflow-hidden">
          <div className="container-x grid w-full gap-10 lg:grid-cols-12 lg:gap-12">
            <div className="lg:col-span-4">
              <Heading total={total} />
            </div>

            <div className="grid gap-8 lg:col-span-8 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)] lg:gap-12">
              <div className="rounded-lg border border-line bg-bg p-1">
                <div className="mono flex items-center gap-2 border-b border-line px-3 py-2 text-[0.625rem] uppercase tracking-[0.12em] text-subtle">
                  <span className="size-1.5 rounded-full bg-line-strong" aria-hidden="true" />
                  SKILL.md
                </div>
                <div className="relative">
                  <div
                    ref={marker}
                    className="absolute inset-x-0 top-0 rounded-md bg-panel-2 transition-transform duration-(--t-settle) ease-(--ease-out)"
                    style={{ height: `${100 / N}%` }}
                    aria-hidden="true"
                  />
                  <ol className="relative">
                    {SECTIONS.map((s, i) => (
                      <li key={s.id} className="flex h-11 items-center gap-3 px-3">
                        <span className="mono w-5 text-[0.6875rem] text-subtle">{String(i + 1).padStart(2, "0")}</span>
                        <span className="text-[0.875rem] text-fg">
                          <span className="text-subtle">## </span>
                          {s.title}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>

              <div className="relative min-h-[14rem]">
                {SECTIONS.map((s, i) => (
                  <div
                    key={s.id}
                    ref={(el) => {
                      notes.current[i] = el;
                    }}
                    className="absolute inset-x-0 top-0 will-change-[opacity,transform]"
                    style={{ opacity: i === 0 ? 1 : 0, visibility: i === 0 ? "visible" : "hidden" }}
                    aria-hidden={i !== 0 || undefined}
                  >
                    <p className="eyebrow">
                      {String(i + 1).padStart(2, "0")} of {String(N).padStart(2, "0")}
                    </p>
                    <h3 className="mt-3 text-[1.5rem] font-medium tracking-tight text-fg">{s.title}</h3>
                    <p className="mt-3 max-w-[46ch] text-[1rem] leading-relaxed text-muted text-pretty">{s.note}</p>
                  </div>
                ))}
                <div className="absolute inset-x-0 bottom-0">
                  <p className="eyebrow mb-2">And six files</p>
                  <ul className="mono grid grid-cols-2 gap-x-6 gap-y-1 text-[0.6875rem] text-subtle">
                    {FILES.map(([f, note]) => (
                      <li key={f} className="truncate">
                        <span className="text-muted">{f}</span> <span className="hidden xl:inline">· {note}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}

function Heading({ total }: { total: number }) {
  return (
    <>
      <p className="eyebrow">Every skill, the same shape</p>
      <h2 className="display display-lg mt-4 text-fg">
        Seven sections.
        <br />
        <span className="text-muted">{total} times.</span>
      </h2>
      <p className="mt-5 max-w-[36ch] text-[0.95rem] leading-relaxed text-muted text-pretty">
        The structure is not a convention contributors are asked to follow. A validator in CI
        rejects a skill missing any heading, any file, or naming a skill that does not exist.
      </p>
    </>
  );
}
