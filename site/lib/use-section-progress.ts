"use client";

import { useEffect, type RefObject } from "react";

/**
 * Progress of a tall section through the viewport, 0 when its top reaches the top of the
 * viewport and 1 when its bottom reaches the bottom. One passive scroll listener, one
 * rAF-throttled measurement per frame, no library. `apply` receives every change and
 * writes styles itself, so a pinned sequence has a single source of truth.
 */
export function useSectionProgress(ref: RefObject<HTMLElement | null>, apply: (p: number) => void) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let ticking = false;
    let last = -1;

    const measure = () => {
      ticking = false;
      const rect = el.getBoundingClientRect();
      const range = rect.height - window.innerHeight;
      const p = range <= 0 ? 1 : Math.min(1, Math.max(0, -rect.top / range));
      if (p !== last) {
        last = p;
        apply(p);
      }
    };
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(measure);
    };

    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
    // `apply` closes over refs only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref]);
}

/** Linear map of `p` across keyframes, clamped at both ends. Inputs must be ascending. */
export function keyframes(p: number, inputs: readonly number[], outputs: readonly number[]): number {
  if (p <= inputs[0]) return outputs[0];
  const last = inputs.length - 1;
  if (p >= inputs[last]) return outputs[last];
  let i = 0;
  while (p > inputs[i + 1]) i += 1;
  const span = inputs[i + 1] - inputs[i];
  const t = span === 0 ? 1 : (p - inputs[i]) / span;
  return outputs[i] + (outputs[i + 1] - outputs[i]) * t;
}
