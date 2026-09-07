import { Link } from "next-view-transitions";
import type { CSSProperties } from "react";
import { ArrowRight } from "@/components/icons";

export type Failure = {
  slug: string;
  wrong: string;
  right: string;
  hue: number;
  domain: string;
};

/**
 * Four assumptions that pass code review and fail in production.
 *
 * An editorial spread rather than a pinned sequence: the heading holds its place on the
 * left while the four cases scroll past on the right, so the screen is never empty at any
 * viewport. Each wrong line is struck through as it enters view (a CSS scroll-driven
 * animation; static and fully struck where unsupported or under reduced motion). Server
 * component: no client JavaScript at all.
 */
export function Failures({ items }: { items: Failure[] }) {
  return (
    <section className="border-b border-line">
      <div className="container-x grid gap-12 py-(--section) lg:grid-cols-12 lg:gap-8">
        <div className="lg:col-span-4">
          <div className="lg:sticky lg:top-[calc(var(--header-h)+3rem)]">
            <p className="eyebrow">Why a library and not a prompt</p>
            <h2 className="display display-lg mt-4 text-fg">
              Code that reads
              <br />
              as correct.
            </h2>
            <p className="mt-5 max-w-[36ch] text-[0.95rem] leading-relaxed text-muted text-pretty">
              None of these are syntax errors, and none of them look wrong in review. They
              are the assumptions a trading system punishes. Each one is owned by a skill.
            </p>
          </div>
        </div>

        <ol className="reveal-stagger lg:col-span-8 lg:col-start-5">
          {items.map((item, i) => (
            <li
              key={item.slug}
              className="tint group border-t border-line py-10 first:border-t-0 first:pt-0 last:pb-0 sm:py-12"
              style={{ "--hue": item.hue, "--i": i } as CSSProperties}
            >
              <div className="grid gap-6 sm:grid-cols-[4rem_minmax(0,1fr)]">
                <p className="mono text-[0.6875rem] uppercase tracking-[0.14em] text-subtle">
                  <span className="block text-[1.75rem] leading-none tracking-normal text-fg">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                </p>
                <div className="min-w-0">
                  <p className="eyebrow" style={{ color: "var(--tint)" }}>
                    {item.domain}
                  </p>
                  <p className="serif mt-3 text-[clamp(1.5rem,2.6vw,2.375rem)] italic leading-[1.15] text-subtle">
                    <span className="strike">{item.wrong}</span>
                  </p>
                  <p className="mt-5 max-w-[56ch] text-[1.0625rem] leading-[1.55] text-fg sm:text-[1.125rem]">
                    {item.right}
                  </p>
                  <Link prefetch={false}
                    href={`/skills/${item.slug}/`}
                    className="chip mt-6 max-w-full whitespace-normal [overflow-wrap:anywhere]"
                    style={{ borderColor: "var(--tint-line)" }}
                    data-cursor-label="open"
                  >
                    {item.slug}
                    <ArrowRight className="size-3" />
                  </Link>
                </div>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
