"use client";

import { useEffect, useRef, useState } from "react";
import type { TocEntry } from "@/lib/markdown";

/**
 * The contents rail for a document whose headings are known in advance.
 *
 * Scroll-spy tracks the heading nearest a reading line a third of the way down the
 * viewport, so the marker never jumps back on a long section. Heading positions are
 * measured once per resize and cached; the scroll handler does arithmetic only.
 */
export function Toc({ entries, horizontal = false }: { entries: TocEntry[]; horizontal?: boolean }) {
  const [active, setActive] = useState(entries[0]?.id ?? "");
  const listRef = useRef<HTMLUListElement>(null);
  const markerRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const headings = entries
      .map((entry) => document.getElementById(entry.id))
      .filter((el): el is HTMLElement => el !== null);
    if (headings.length === 0) return;

    let tops: number[] = [];
    let ticking = false;

    const measure = () => {
      const scroll = window.scrollY;
      tops = headings.map((h) => h.getBoundingClientRect().top + scroll);
    };

    const update = () => {
      ticking = false;
      const line = window.scrollY + window.innerHeight * 0.3;
      let current = headings[0].id;
      for (let i = 0; i < tops.length; i += 1) {
        if (tops[i] <= line) current = headings[i].id;
      }
      // At the very bottom the last section is the one being read, whatever its height.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) {
        current = headings[headings.length - 1].id;
      }
      setActive((prev) => (prev === current ? prev : current));
    };

    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(update);
    };
    const onResize = () => {
      measure();
      onScroll();
    };

    measure();
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onResize);
    // Fonts and images settling after load move the headings.
    const settle = window.setTimeout(onResize, 800);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onResize);
      window.clearTimeout(settle);
    };
  }, [entries]);

  // Move the marker to the active item, measured relative to the list.
  useEffect(() => {
    if (horizontal) return;
    const list = listRef.current;
    const marker = markerRef.current;
    if (!list || !marker) return;
    const item = list.querySelector<HTMLElement>(`[data-id="${CSS.escape(active)}"]`);
    if (!item) return;
    marker.style.top = `${item.offsetTop}px`;
    marker.style.height = `${item.offsetHeight}px`;
  }, [active, horizontal]);

  // Keep the active chip visible in the horizontal rail. Scrolls the rail only: scrollIntoView
  // would also nudge the page vertically on load.
  useEffect(() => {
    if (!horizontal) return;
    const list = listRef.current;
    const rail = list?.parentElement;
    const item = list?.querySelector<HTMLElement>(`[data-id="${CSS.escape(active)}"]`);
    if (!rail || !item) return;
    const target = item.offsetLeft - rail.clientWidth / 2 + item.offsetWidth / 2;
    rail.scrollTo({ left: Math.max(0, target), behavior: "smooth" });
  }, [active, horizontal]);

  if (entries.length === 0) return null;

  if (horizontal) {
    return (
      <nav aria-label="On this page" className="spine-h">
        <ul ref={listRef} className="flex gap-1">
          {entries.map((entry, i) => (
            <li key={entry.id} data-id={entry.id}>
              <a href={`#${entry.id}`} aria-current={entry.id === active ? "true" : undefined}>
                {String(i + 1).padStart(2, "0")} {entry.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    );
  }

  return (
    <nav aria-label="On this page">
      <p className="eyebrow mb-3">Contents</p>
      <div className="spine">
        <span ref={markerRef} className="spine-marker" aria-hidden="true" />
        <ul ref={listRef}>
          {entries.map((entry, i) => (
            <li key={entry.id} data-id={entry.id}>
              <a href={`#${entry.id}`} aria-current={entry.id === active ? "true" : undefined}>
                <span>{String(i + 1).padStart(2, "0")}</span>
                <span>{entry.title}</span>
              </a>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
