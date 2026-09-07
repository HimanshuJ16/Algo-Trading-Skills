"use client";

import dynamic from "next/dynamic";
import { Link } from "next-view-transitions";
import { useState } from "react";
import type { CSSProperties } from "react";
import { DomainGlyph } from "@/components/domain-glyph";
import { Reveal, Stagger } from "@/components/motion-primitives";
import { ArrowRight } from "@/components/icons";

const GraphCanvas = dynamic(() => import("@/components/graph-canvas"), {
  ssr: false,
  loading: () => <div className="aspect-square w-full" aria-hidden="true" />,
});

export type AtlasDomain = {
  slug: string;
  label: string;
  short: string;
  blurb: string;
  hue: number;
  count: number;
  outgoing: number;
  neighbours: { slug: string; label: string; count: number }[];
};

/**
 * Sixteen domains beside the graph. Hovering or focusing a domain isolates its arc and its
 * edges on the canvas, so the index and the picture are one instrument.
 */
export function DomainAtlas({ domains, total, crossReferences }: { domains: AtlasDomain[]; total: number; crossReferences: number }) {
  const [focus, setFocus] = useState<string | null>(null);
  const max = Math.max(...domains.map((d) => d.count), 1);

  return (
    <div className="container-x pb-8 pt-10 sm:pt-14">
      <Reveal className="max-w-3xl">
        <p className="eyebrow">Sixteen domains</p>
        <h1 className="display display-lg mt-4 text-fg">
          The library, by the part
          <br />
          of the system it covers.
        </h1>
        <p className="mt-5 max-w-[48ch] text-[0.95rem] leading-relaxed text-muted text-pretty">
          Every arc on the ring is a domain; every curve through the middle is one skill
          handing a case to a skill in another domain. {crossReferences.toLocaleString("en-US")}{" "}
          handoffs, {total} skills. Hover a domain to isolate its edges.
        </p>
      </Reveal>

      <div className="mt-12 grid gap-12 lg:grid-cols-12 lg:gap-8">
        <div className="lg:sticky lg:top-[calc(var(--header-h)+1rem)] lg:col-span-6 lg:self-start" data-cursor="drag" data-cursor-label="hover a node">
          <GraphCanvas mode="map" focus={focus} className="mx-auto max-w-[36rem] lg:max-w-none" />
        </div>

        <div className="lg:col-span-6">
          <Stagger as="ol" className="border-t border-line">
            {domains.map((d, i) => (
              <li
                key={d.slug}
                className="tint"
                style={{ "--hue": d.hue, "--i": Math.min(i, 12) } as CSSProperties}
                onPointerEnter={() => setFocus(d.slug)}
                onPointerLeave={() => setFocus((f) => (f === d.slug ? null : f))}
              >
                <div>
                  <Link prefetch={false}
                    href={`/domains/${d.slug}/`}
                    onFocus={() => setFocus(d.slug)}
                    onBlur={() => setFocus((f) => (f === d.slug ? null : f))}
                    className={`group grid grid-cols-[2rem_minmax(0,1fr)_auto] gap-x-4 gap-y-3 border-b border-line py-5 transition-colors duration-(--t-quick) sm:grid-cols-[2.5rem_2.5rem_minmax(0,1fr)_auto] ${
                      focus && focus !== d.slug ? "opacity-50" : ""
                    }`}
                  >
                    <span className="mono pt-1 text-[0.6875rem] text-subtle">{String(i + 1).padStart(2, "0")}</span>
                    <span className="hidden pt-0.5 sm:block">
                      <DomainGlyph slug={d.slug} size={24} className="text-fg" />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-[1.0625rem] font-medium tracking-tight text-fg">{d.label}</span>
                      <span className="mt-1 block text-[0.8125rem] leading-relaxed text-muted">{d.blurb}</span>
                      <span className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
                        <span className="h-px w-24 bg-line">
                          <span className="block h-full" style={{ background: "var(--tint)", width: `${(d.count / max) * 100}%` }} />
                        </span>
                        <span className="mono text-[0.6875rem] text-subtle">
                          hands off to{" "}
                          {d.neighbours.map((n, j) => (
                            <span key={n.slug}>
                              {j > 0 ? ", " : ""}
                              <span className="text-muted">{n.label}</span>
                            </span>
                          ))}
                        </span>
                      </span>
                    </span>
                    <span className="text-right">
                      <span className="numeral block text-[1.75rem]" style={{ color: "var(--tint)" }}>
                        {d.count}
                      </span>
                      <ArrowRight className="ml-auto mt-2 size-3.5 -translate-x-1 text-subtle opacity-0 transition-[opacity,transform] duration-(--t-quick) group-hover:translate-x-0 group-hover:opacity-100" />
                    </span>
                  </Link>
                </div>
              </li>
            ))}
          </Stagger>
        </div>
      </div>
    </div>
  );
}
