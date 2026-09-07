"use client";

import { useTransitionRouter } from "next-view-transitions";
import { useEffect, useRef, useState } from "react";
import { DOMAINS, tintFor } from "@/lib/domains";
import { withBase } from "@/lib/site";
import { loadGraph } from "@/components/graph-canvas";

/**
 * The library as a spectrum: one hairline per skill, height by SKILL.md word count, in the
 * same ring order as the graph. Drawn on a canvas from the graph asset the hero already
 * fetched, so the landing page carries no per-skill markup for it. Static: it paints once
 * per resize or theme change and never runs a frame loop. The hovered bar's label is a real
 * link.
 */
export function Spectrum({ median, total }: { median: number; total: number }) {
  const router = useTransitionRouter();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelRef = useRef<HTMLAnchorElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    const label = labelRef.current;
    if (!wrap || !canvas || !label) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let live = true;
    let bars: { slug: string; words: number; hue: number }[] = [];
    let max = 1;
    let hovered = -1;
    let width = 0;
    let height = 0;
    let dpr = 1;
    const dark = () => document.documentElement.classList.contains("dark");

    const draw = () => {
      if (bars.length === 0) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const step = width / bars.length;
      const barW = Math.max(1, step * 0.6);
      const isDark = dark();
      bars.forEach((bar, i) => {
        const h = Math.max(2, (bar.words / max) * height);
        ctx.fillStyle = tintFor(bar.hue, isDark, hovered === -1 || hovered === i ? 0.95 : 0.35);
        ctx.fillRect(i * step, height - h, barW, h);
      });
      const my = height - (median / max) * height;
      ctx.strokeStyle = isDark ? "rgba(236,235,230,0.7)" : "rgba(20,19,17,0.7)";
      ctx.setLineDash([2, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, my + 0.5);
      ctx.lineTo(width, my + 0.5);
      ctx.stroke();
      ctx.setLineDash([]);
    };

    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      draw();
    };

    const onMove = (event: PointerEvent) => {
      if (bars.length === 0) return;
      const rect = canvas.getBoundingClientRect();
      const i = Math.max(0, Math.min(bars.length - 1, Math.floor(((event.clientX - rect.left) / rect.width) * bars.length)));
      if (i === hovered) return;
      hovered = i;
      const bar = bars[i];
      const x = ((i + 0.5) / bars.length) * rect.width;
      label.hidden = false;
      label.style.left = `${x}px`;
      label.style.translate = x > rect.width * 0.75 ? "-100% 0" : x < rect.width * 0.25 ? "0 0" : "-50% 0";
      label.textContent = `${bar.slug} · ${bar.words.toLocaleString("en-US")} words`;
      label.href = withBase(`/skills/${bar.slug}/`);
      draw();
    };
    const onLeave = () => {
      hovered = -1;
      label.hidden = true;
      draw();
    };
    const onClick = () => {
      if (hovered >= 0) router.push(`/skills/${bars[hovered].slug}/`);
    };

    loadGraph().then((data) => {
      if (!live) return;
      const rank = new Map(DOMAINS.map((d, i) => [d.slug, i]));
      const hueOf = new Map(DOMAINS.map((d) => [d.slug, d.hue]));
      bars = data.nodes
        .map(([slug, di, words]) => ({ slug, words: words ?? 0, domain: data.domains[di] }))
        .sort((a, b) => (rank.get(a.domain) ?? 99) - (rank.get(b.domain) ?? 99) || a.slug.localeCompare(b.slug))
        .map((b) => ({ slug: b.slug, words: b.words, hue: hueOf.get(b.domain) ?? 0 }));
      max = Math.max(...bars.map((b) => b.words), 1);
      resize();
      setReady(true);
    }, () => undefined);

    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    const mo = new MutationObserver(draw);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("click", onClick);
    return () => {
      live = false;
      ro.disconnect();
      mo.disconnect();
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("click", onClick);
    };
  }, [median, router]);

  const first = DOMAINS[0]?.label;
  const last = DOMAINS[DOMAINS.length - 1]?.label;

  return (
    <div>
      <div ref={wrapRef} className="relative h-40 w-full sm:h-48">
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={`${total} bars, one per skill, showing the length of each document. The median is ${median.toLocaleString("en-US")} words.`}
          className={`block h-full w-full cursor-crosshair transition-opacity duration-(--t-reveal) ${ready ? "opacity-100" : "opacity-0"}`}
        />
        <a ref={labelRef} hidden href={withBase("/skills/")} className="mono absolute -top-8 rounded bg-fg px-2 py-1 text-[0.6875rem] text-bg no-underline" />
        <p className="mono pointer-events-none absolute right-0 top-0 text-[0.625rem] uppercase tracking-[0.12em] text-subtle">
          median {median.toLocaleString("en-US")} words
        </p>
      </div>
      <div className="mono mt-3 flex justify-between text-[0.625rem] uppercase tracking-[0.12em] text-subtle">
        <span>{first}</span>
        <span>{last}</span>
      </div>
    </div>
  );
}
