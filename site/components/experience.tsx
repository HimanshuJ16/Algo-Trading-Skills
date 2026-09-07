"use client";

import Lenis from "lenis";
import { useEffect, useRef } from "react";

/**
 * Experience-tier runtime: smooth scroll and the contextual cursor.
 *
 * Mounted only by the landing page, the catalog shell, the domain index and the 404.
 * Never by a document. Both parts yield to prefers-reduced-motion: Lenis does not
 * initialise and the cursor element never becomes visible. The cursor also stays away
 * from coarse pointers, where it would mean nothing.
 */
export default function Experience({ smooth = true }: { smooth?: boolean }) {
  const cursorRef = useRef<HTMLDivElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const fine = window.matchMedia("(pointer: fine)").matches;

    // ---- Smooth scroll --------------------------------------------------------------
    let lenis: Lenis | null = null;
    let lenisFrame = 0;
    if (smooth && !reduced) {
      lenis = new Lenis({ lerp: 0.11, smoothWheel: true, anchors: true, autoRaf: false });
      const raf = (time: number) => {
        lenis?.raf(time);
        lenisFrame = requestAnimationFrame(raf);
      };
      lenisFrame = requestAnimationFrame(raf);
    }
    const onLock = () => lenis?.stop();
    const onUnlock = () => lenis?.start();
    window.addEventListener("scroll:lock", onLock);
    window.addEventListener("scroll:unlock", onUnlock);

    // ---- Cursor ------------------------------------------------------------------
    const cursor = cursorRef.current;
    const label = labelRef.current;
    let cursorFrame = 0;
    let onMove: ((e: PointerEvent) => void) | null = null;
    let onLeave: (() => void) | null = null;
    let onDown: (() => void) | null = null;
    let onUp: (() => void) | null = null;

    if (cursor && label && fine && !reduced) {
      let tx = -100;
      let ty = -100;
      let x = tx;
      let y = ty;
      let shown = false;
      let state = "";
      let pressed = false;
      let running = false;

      const setState = (next: string, text = "") => {
        if (next !== state) {
          state = next;
          cursor.dataset.state = next;
        }
        if (label.textContent !== text) label.textContent = text;
      };

      const step = () => {
        cursorFrame = 0;
        x += (tx - x) * 0.22;
        y += (ty - y) * 0.22;
        cursor.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${pressed ? 0.85 : 1})`;
        if (Math.abs(tx - x) > 0.2 || Math.abs(ty - y) > 0.2) {
          cursorFrame = requestAnimationFrame(step);
        } else {
          running = false;
        }
      };
      const kick = () => {
        if (running) return;
        running = true;
        cursorFrame = requestAnimationFrame(step);
      };

      onMove = (event: PointerEvent) => {
        if (event.pointerType !== "mouse") return;
        tx = event.clientX;
        ty = event.clientY;
        if (!shown) {
          shown = true;
          x = tx;
          y = ty;
          cursor.style.opacity = "1";
        }
        const target = event.target as HTMLElement | null;
        const tagged = target?.closest<HTMLElement>("[data-cursor]");
        if (tagged) {
          const value = tagged.dataset.cursor ?? "";
          if (value === "node") setState("node");
          else if (value === "drag") setState("drag", tagged.dataset.cursorLabel ?? "scroll");
          else if (value) setState(value, tagged.dataset.cursorLabel ?? "");
          else setState("");
        } else if (target?.closest("a, button, [role=button], summary, label")) {
          setState("link", target.closest<HTMLElement>("[data-cursor-label]")?.dataset.cursorLabel ?? "open");
        } else if (target?.closest("input, textarea, select")) {
          setState("text");
        } else if (target?.closest("p, h1, h2, h3, h4, li, code, pre, blockquote, dd, dt, td, th")) {
          setState("text");
        } else {
          setState("");
        }
        kick();
      };
      onLeave = () => {
        shown = false;
        cursor.style.opacity = "0";
      };
      onDown = () => {
        pressed = true;
        kick();
      };
      onUp = () => {
        pressed = false;
        kick();
      };

      window.addEventListener("pointermove", onMove, { passive: true });
      document.documentElement.addEventListener("pointerleave", onLeave);
      window.addEventListener("pointerdown", onDown, { passive: true });
      window.addEventListener("pointerup", onUp, { passive: true });
    }

    return () => {
      cancelAnimationFrame(lenisFrame);
      cancelAnimationFrame(cursorFrame);
      lenis?.destroy();
      window.removeEventListener("scroll:lock", onLock);
      window.removeEventListener("scroll:unlock", onUnlock);
      if (onMove) window.removeEventListener("pointermove", onMove);
      if (onLeave) document.documentElement.removeEventListener("pointerleave", onLeave);
      if (onDown) window.removeEventListener("pointerdown", onDown);
      if (onUp) window.removeEventListener("pointerup", onUp);
    };
  }, [smooth]);

  return (
    <div ref={cursorRef} className="cursor" aria-hidden="true">
      <span ref={labelRef} className="cursor-label" />
    </div>
  );
}
