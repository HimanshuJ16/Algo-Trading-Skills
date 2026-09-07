"use client";

import { useEffect, useRef } from "react";

/**
 * Counts up to `value` once scrolled into view. The final value is in the HTML, so the
 * number is correct before any script runs and under reduced motion nothing changes.
 */
export function Counter({
  value,
  duration = 1400,
  divisor = 1,
  decimals = 0,
  className,
}: {
  value: number;
  duration?: number;
  divisor?: number;
  decimals?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const format = (n: number) =>
    (n / divisor).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

  useEffect(() => {
    const el = ref.current;
    if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        io.disconnect();
        const start = performance.now();
        const tick = (now: number) => {
          const t = Math.min(1, (now - start) / duration);
          const eased = 1 - Math.pow(1 - t, 4);
          el.textContent = format(value * eased);
          if (t < 1) frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
      },
      { rootMargin: "-40px" },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      cancelAnimationFrame(frame);
    };
    // format is derived from props that do not change at runtime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, duration, divisor, decimals]);

  return (
    <span ref={ref} className={className}>
      {format(value)}
    </span>
  );
}
