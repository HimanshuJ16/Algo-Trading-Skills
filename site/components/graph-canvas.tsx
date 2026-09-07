"use client";

import { useTransitionRouter } from "next-view-transitions";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { DOMAINS, tintFor } from "@/lib/domains";
import { withBase } from "@/lib/site";

/**
 * The library, drawn from the library.
 *
 * 501 nodes on a ring, grouped into sixteen arcs by domain; 3,317 cross-references as
 * quadratic curves. A reference that stays inside a domain hugs the rim; one that crosses
 * a boundary is pulled through the centre, so the picture reads as what the data is: a
 * graph of handoffs, dense in the middle.
 *
 * Canvas 2D, not WebGL. The edges are drawn once into an offscreen bitmap and the frame
 * loop only rotates and blits it, then draws 501 dots and, when a node is under the
 * pointer, that node's own edges at full strength. Nothing here allocates per frame, the
 * loop stops when the canvas leaves the viewport or the tab is hidden, and it never starts
 * under prefers-reduced-motion: the still frame is the design.
 */

type GraphData = {
  version: string;
  domains: string[];
  /** [slug, domain index, SKILL.md word count] */
  nodes: [string, number, number][];
  edges: [number, number][];
};

let cache: Promise<GraphData> | null = null;
export function loadGraph(): Promise<GraphData> {
  cache ??= fetch(withBase("/graph.json"))
    .then((r) => {
      if (!r.ok) throw new Error(`graph: ${r.status}`);
      return r.json() as Promise<GraphData>;
    })
    .catch((e) => {
      cache = null;
      throw e;
    });
  return cache;
}

type Layout = {
  angle: Float32Array; // per node, radians, unrotated
  order: Int32Array; // node indices sorted by angle
  arcs: { domain: number; start: number; end: number }[];
  hue: Int16Array; // per node
  adjacency: Int32Array[]; // per node, edge indices
};

const GAP_SLOTS = 3;

function layout(data: GraphData): Layout {
  const n = data.nodes.length;
  const byDomain = new Map<number, number[]>();
  data.nodes.forEach(([, d], i) => byDomain.set(d, [...(byDomain.get(d) ?? []), i]));

  // Arcs in the hand-curated DOMAINS order, so the picture is stable across builds.
  const domainRank = new Map(DOMAINS.map((d, i) => [d.slug, i]));
  const domains = [...byDomain.keys()].sort(
    (a, b) => (domainRank.get(data.domains[a]) ?? 99) - (domainRank.get(data.domains[b]) ?? 99),
  );

  const slots = n + domains.length * GAP_SLOTS;
  const step = (Math.PI * 2) / slots;
  const angle = new Float32Array(n);
  const hue = new Int16Array(n);
  const arcs: Layout["arcs"] = [];
  const order: number[] = [];

  let slot = 0;
  for (const d of domains) {
    const members = byDomain.get(d)!.sort((a, b) => data.nodes[a][0].localeCompare(data.nodes[b][0]));
    const meta = DOMAINS.find((m) => m.slug === data.domains[d]);
    const h = meta?.hue ?? 0;
    const start = -Math.PI / 2 + slot * step;
    for (const i of members) {
      angle[i] = -Math.PI / 2 + slot * step;
      hue[i] = h;
      order.push(i);
      slot += 1;
    }
    arcs.push({ domain: d, start, end: -Math.PI / 2 + (slot - 1) * step });
    slot += GAP_SLOTS;
  }

  const adjacency: number[][] = Array.from({ length: n }, () => []);
  data.edges.forEach(([a, b], e) => {
    adjacency[a].push(e);
    adjacency[b].push(e);
  });

  return {
    angle,
    order: Int32Array.from(order),
    arcs,
    hue,
    adjacency: adjacency.map((list) => Int32Array.from(list)),
  };
}

function isDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

export type GraphCanvasProps = {
  /** hero: slow rotation, no labels. map: static, labelled, a navigator. */
  mode?: "hero" | "map";
  className?: string;
  /** Emphasise one domain's arc and edges (map mode). */
  focus?: string | null;
};

export default function GraphCanvas({ mode = "hero", className = "", focus = null }: GraphCanvasProps) {
  const router = useTransitionRouter();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelRef = useRef<HTMLAnchorElement>(null);
  const [data, setData] = useState<GraphData | null>(null);
  const [ready, setReady] = useState(false);
  const [labels, setLabels] = useState<{ slug: string; label: string; hue: number; x: number; y: number; anchor: "start" | "end" | "middle" }[]>([]);

  useEffect(() => {
    let live = true;
    loadGraph().then((d) => live && setData(d), () => undefined);
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    const label = labelRef.current;
    if (!canvas || !wrap || !label || !data) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const rotating = mode === "hero" && !reduced;
    const L = layout(data);
    const n = data.nodes.length;

    let size = 0;
    let dpr = 1;
    let radius = 0;
    let dark = isDark();
    let theta = 0;
    let scrollTheta = 0;
    let hovered = -1;
    let pointer: { x: number; y: number } | null = null;
    let dirty = true;
    let running = false;
    let inView = true;
    let tabVisible = document.visibilityState === "visible";
    let visible = true;
    const syncVisible = () => {
      visible = inView && tabVisible;
      if (visible) kick();
    };
    let frame = 0;
    let lastTime = 0;

    const layer = document.createElement("canvas");
    const lctx = layer.getContext("2d")!;
    const cos = new Float32Array(n);
    const sin = new Float32Array(n);
    for (let i = 0; i < n; i += 1) {
      cos[i] = Math.cos(L.angle[i]);
      sin[i] = Math.sin(L.angle[i]);
    }

    const colorCache = new Map<string, string>();
    const color = (hue: number, alpha: number) => {
      const key = `${hue}|${alpha}|${dark ? 1 : 0}`;
      let c = colorCache.get(key);
      if (!c) {
        c = tintFor(hue, dark, alpha);
        colorCache.set(key, c);
      }
      return c;
    };

    const edgePath = (target: CanvasRenderingContext2D, a: number, b: number, r: number) => {
      const ax = cos[a] * r;
      const ay = sin[a] * r;
      const bx = cos[b] * r;
      const by = sin[b] * r;
      const same = L.hue[a] === L.hue[b];
      // Angular distance decides the bow; a short intra-domain hop barely leaves the rim.
      const k = same ? 0.62 : 0.14;
      const mx = ((ax + bx) / 2) * k;
      const my = ((ay + by) / 2) * k;
      target.moveTo(ax, ay);
      target.quadraticCurveTo(mx, my, bx, by);
    };

    // Draw every edge once. Grouped by source hue so strokes batch into 16 paths.
    const paintLayer = () => {
      layer.width = Math.round(size * dpr);
      layer.height = Math.round(size * dpr);
      lctx.setTransform(dpr, 0, 0, dpr, (size / 2) * dpr, (size / 2) * dpr);
      lctx.clearRect(-size / 2, -size / 2, size, size);
      lctx.lineWidth = 0.7;
      lctx.lineCap = "round";
      const focusDomain = focus ? data.domains.indexOf(focus) : -1;
      const byHue = new Map<number, number[]>();
      data.edges.forEach(([a], e) => byHue.set(L.hue[a], [...(byHue.get(L.hue[a]) ?? []), e]));
      const baseAlpha = dark ? 0.1 : 0.13;
      for (const [hue, list] of byHue) {
        lctx.beginPath();
        for (const e of list) {
          const [a, b] = data.edges[e];
          if (focusDomain >= 0 && data.nodes[a][1] !== focusDomain && data.nodes[b][1] !== focusDomain) continue;
          edgePath(lctx, a, b, radius);
        }
        const dim = focusDomain >= 0 ? 1.8 : 1;
        lctx.strokeStyle = color(hue, baseAlpha * dim);
        lctx.stroke();
      }
      if (focusDomain >= 0) {
        // The rest of the graph, faint, for context.
        lctx.beginPath();
        for (const [a, b] of data.edges) {
          if (data.nodes[a][1] === focusDomain || data.nodes[b][1] === focusDomain) continue;
          edgePath(lctx, a, b, radius);
        }
        lctx.strokeStyle = dark ? "rgba(255,255,255,0.025)" : "rgba(0,0,0,0.035)";
        lctx.stroke();
      }
    };

    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      size = Math.max(1, Math.floor(rect.width));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(size * dpr);
      canvas.height = Math.round(size * dpr);
      canvas.style.width = `${size}px`;
      canvas.style.height = `${size}px`;
      radius = (size / 2) * (mode === "map" ? 0.8 : 0.9);
      paintLayer();
      if (mode === "map") {
        const r = radius + Math.max(18, size * 0.045);
        setLabels(
          L.arcs.map((arc) => {
            const mid = (arc.start + arc.end) / 2;
            const meta = DOMAINS.find((m) => m.slug === data.domains[arc.domain])!;
            const c = Math.cos(mid);
            return {
              slug: meta.slug,
              label: meta.label,
              hue: meta.hue,
              x: size / 2 + c * r,
              y: size / 2 + Math.sin(mid) * r,
              anchor: c > 0.15 ? "start" : c < -0.15 ? "end" : "middle",
            };
          }),
        );
      }
      dirty = true;
    };

    const hitTest = () => {
      if (!pointer) return -1;
      const x = pointer.x - size / 2;
      const y = pointer.y - size / 2;
      const dist = Math.hypot(x, y);
      if (Math.abs(dist - radius) > Math.max(14, size * 0.03)) return -1;
      const a = Math.atan2(y, x) - theta;
      // Nearest node by angle among the sorted order.
      let best = -1;
      let bestD = Infinity;
      for (let k = 0; k < L.order.length; k += 1) {
        const i = L.order[k];
        let d = Math.abs(((L.angle[i] - a + Math.PI) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2) - Math.PI);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      }
      const tolerance = (Math.PI * 2) / (n + 16 * GAP_SLOTS) * 0.9;
      return bestD <= tolerance ? best : -1;
    };

    const draw = (time: number) => {
      frame = 0;
      if (!visible) {
        running = false;
        return;
      }
      const dt = lastTime ? Math.min(0.05, (time - lastTime) / 1000) : 0;
      lastTime = time;
      if (rotating) {
        theta += dt * 0.02;
        dirty = true;
      }
      const t = theta + scrollTheta;

      if (dirty) {
        dirty = false;
        const next = hitTest();
        if (next !== hovered) {
          hovered = next;
          canvas.dataset.cursor = hovered >= 0 ? "node" : "";
          if (hovered >= 0) {
            label.textContent = data.nodes[hovered][0];
            label.href = withBase(`/skills/${data.nodes[hovered][0]}/`);
            label.hidden = false;
          } else {
            label.hidden = true;
          }
        }
        if (hovered >= 0) {
          const rot = L.angle[hovered] + t;
          label.style.left = `${size / 2 + Math.cos(rot) * radius}px`;
          label.style.top = `${size / 2 + Math.sin(rot) * radius}px`;
        }

        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, size, size);
        ctx.save();
        ctx.translate(size / 2, size / 2);
        ctx.rotate(t);
        ctx.drawImage(layer, -size / 2, -size / 2, size, size);

        // Nodes, batched by hue.
        const dotR = Math.max(1.3, size / 420);
        const groups = new Map<number, number[]>();
        for (let i = 0; i < n; i += 1) groups.set(L.hue[i], [...(groups.get(L.hue[i]) ?? []), i]);
        const focusDomain = focus ? data.domains.indexOf(focus) : -1;
        for (const [hue, list] of groups) {
          ctx.beginPath();
          for (const i of list) {
            const dim = hovered >= 0 && i !== hovered && !isNeighbour(i);
            if (dim) continue;
            if (focusDomain >= 0 && data.nodes[i][1] !== focusDomain) continue;
            ctx.moveTo(cos[i] * radius + dotR, sin[i] * radius);
            ctx.arc(cos[i] * radius, sin[i] * radius, dotR, 0, Math.PI * 2);
          }
          ctx.fillStyle = color(hue, 0.95);
          ctx.fill();
        }
        if (hovered >= 0 || focusDomain >= 0) {
          ctx.beginPath();
          for (let i = 0; i < n; i += 1) {
            const dimmed = hovered >= 0 ? i !== hovered && !isNeighbour(i) : data.nodes[i][1] !== focusDomain;
            if (!dimmed) continue;
            ctx.moveTo(cos[i] * radius + dotR, sin[i] * radius);
            ctx.arc(cos[i] * radius, sin[i] * radius, dotR, 0, Math.PI * 2);
          }
          ctx.fillStyle = dark ? "rgba(255,255,255,0.18)" : "rgba(0,0,0,0.18)";
          ctx.fill();
        }

        // The hovered node's own edges, at full strength.
        if (hovered >= 0) {
          ctx.lineWidth = 1.25;
          ctx.lineCap = "round";
          ctx.beginPath();
          for (const e of L.adjacency[hovered]) {
            const [a, b] = data.edges[e];
            edgePath(ctx, a, b, radius);
          }
          ctx.strokeStyle = color(L.hue[hovered], 0.9);
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(cos[hovered] * radius, sin[hovered] * radius, dotR * 2.4, 0, Math.PI * 2);
          ctx.fillStyle = color(L.hue[hovered], 1);
          ctx.fill();
        }
        ctx.restore();
      }

      if (rotating || dirty) {
        frame = requestAnimationFrame(draw);
      } else {
        running = false;
      }
    };

    const neighbourSet = new Set<number>();
    let neighbourOf = -1;
    const isNeighbour = (i: number) => {
      if (neighbourOf !== hovered) {
        neighbourSet.clear();
        if (hovered >= 0) {
          for (const e of L.adjacency[hovered]) {
            const [a, b] = data.edges[e];
            neighbourSet.add(a === hovered ? b : a);
          }
        }
        neighbourOf = hovered;
      }
      return neighbourSet.has(i);
    };

    const kick = () => {
      if (running || !visible) return;
      running = true;
      lastTime = 0;
      frame = requestAnimationFrame(draw);
    };

    const onPointerMove = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      dirty = true;
      kick();
    };
    const onPointerLeave = () => {
      pointer = null;
      dirty = true;
      kick();
    };
    const onClick = () => {
      if (hovered >= 0) router.push(`/skills/${data.nodes[hovered][0]}/`);
    };
    const onScroll = () => {
      if (mode !== "hero" || reduced) return;
      scrollTheta = window.scrollY * 0.00035;
      dirty = true;
      kick();
    };

    const ro = new ResizeObserver(() => {
      resize();
      kick();
    });
    ro.observe(wrap);

    const io = new IntersectionObserver(
      ([entry]) => {
        inView = entry.isIntersecting;
        syncVisible();
      },
      { rootMargin: "80px" },
    );
    io.observe(wrap);

    const onVisibility = () => {
      tabVisible = document.visibilityState === "visible";
      syncVisible();
    };

    const mo = new MutationObserver(() => {
      const next = isDark();
      if (next === dark) return;
      dark = next;
      colorCache.clear();
      paintLayer();
      dirty = true;
      kick();
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });

    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerleave", onPointerLeave);
    canvas.addEventListener("click", onClick);
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);

    resize();
    setReady(true);
    kick();

    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      io.disconnect();
      mo.disconnect();
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerleave", onPointerLeave);
      canvas.removeEventListener("click", onClick);
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // `focus` is a dependency on purpose: a change re-lays-out and repaints, which for
    // 501 nodes costs less than a frame.
  }, [data, mode, router, focus]);

  return (
    <div
      ref={wrapRef}
      className={`relative aspect-square w-full select-none ${className}`}
      data-focus={focus ?? undefined}
    >
      <canvas
        ref={canvasRef}
        role="img"
        aria-label="Every skill in the library arranged on a ring by domain, with the cross-references between skills drawn as curves through the centre"
        data-cursor=""
        className={`block h-full w-full transition-opacity duration-(--t-reveal) ease-(--ease-out) ${ready ? "opacity-100" : "opacity-0"}`}
      />
      <a ref={labelRef} className="graph-label" hidden href={withBase("/skills/")} />
      {mode === "map" &&
        labels.map((l) => (
          <a
            key={l.slug}
            href={withBase(`/domains/${l.slug}/`)}
            className="tint absolute hidden text-[0.6875rem] leading-none text-muted transition-colors duration-(--t-quick) hover:text-fg sm:block"
            style={
              {
                "--hue": l.hue,
                left: l.x,
                top: l.y,
                translate: l.anchor === "start" ? "0 -50%" : l.anchor === "end" ? "-100% -50%" : "-50% -50%",
                color: focus === l.slug ? "var(--tint)" : undefined,
              } as CSSProperties
            }
          >
            {l.label}
          </a>
        ))}
      {!ready && (
        <p className="mono absolute inset-0 grid place-items-center text-[0.6875rem] uppercase tracking-[0.14em] text-subtle">
          drawing the graph
        </p>
      )}
    </div>
  );
}
