import type { CSSProperties, ReactNode } from "react";
import { Counter } from "@/components/counter";

export { Counter };

/**
 * Entrance primitives with no animation library behind them.
 *
 * `Reveal` and `Stagger` are CSS scroll-driven animations (see `.reveal` in globals.css):
 * where the browser supports scroll timelines an element rises as it enters the viewport;
 * elsewhere it is simply visible. Both are server components, so they add nothing to the
 * client bundle and nothing is ever hidden waiting for a script.
 */
export function Reveal({ children, className = "", as: Tag = "div" }: { children: ReactNode; className?: string; as?: "div" | "section" | "header" | "p" }) {
  return <Tag className={`reveal ${className}`}>{children}</Tag>;
}

export function Stagger({ children, className = "", as: Tag = "div" }: { children: ReactNode; className?: string; as?: "div" | "ol" | "ul" }) {
  return <Tag className={`reveal-stagger ${className}`}>{children}</Tag>;
}

/** A direct child of `Stagger`; `index` sets its place in the sequence. */
export function StaggerItem({ children, className = "", index = 0, as: Tag = "div" }: { children: ReactNode; className?: string; index?: number; as?: "div" | "li" }) {
  return (
    <Tag className={className} style={{ "--i": Math.min(index, 12) } as CSSProperties}>
      {children}
    </Tag>
  );
}

/**
 * Split a sentence into words for a staggered entrance, keeping the full text in the
 * accessibility tree and the spaces between words in the DOM.
 */
export function Words({ text, className = "", delay = 0 }: { text: string; className?: string; delay?: number }) {
  const words = text.split(" ");
  return (
    <span className={className}>
      <span className="sr-only">{text}</span>
      {words.map((word, i) => (
        <span key={i} aria-hidden="true">
          <span className="word-wrap">
            <span className="word" style={{ "--i": i, "--d0": `${delay}ms` } as CSSProperties}>
              {word}
            </span>
          </span>
          {i < words.length - 1 ? " " : ""}
        </span>
      ))}
    </span>
  );
}
