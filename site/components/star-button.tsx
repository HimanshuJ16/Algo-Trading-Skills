"use client";

import { useEffect, useRef, useState } from "react";
import { SITE } from "@/lib/site";

const CACHE_KEY = "gh-stars";
const CACHE_TTL = 60 * 60 * 1000;

function compact(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  return `${k >= 10 ? Math.round(k) : Math.round(k * 10) / 10}k`;
}

/**
 * "Star this repository", as the one deliberately attention-seeking control on the site.
 *
 * The star draws its own outline, fills with the amber signal, throws a burst of sparks and
 * a ring, then settles; a light sweep crosses the pill every few seconds so it keeps
 * catching the eye without moving anything. Hover replays the burst. Every part of that is
 * CSS (see `.star-*` in globals.css) and all of it is off under prefers-reduced-motion,
 * where the star is simply filled.
 *
 * The count comes from the public GitHub API, cached for an hour in localStorage. If the
 * request fails or is rate-limited the pill still works; it just says "Star".
 */
export function StarButton() {
  const [stars, setStars] = useState<number | null>(null);
  const [shown, setShown] = useState<number>(0);
  const frame = useRef(0);

  useEffect(() => {
    const [, owner, repo] = SITE.repo.match(/github\.com\/([^/]+)\/([^/]+)/) ?? [];
    if (!owner || !repo) return;
    let live = true;
    try {
      const cached = JSON.parse(localStorage.getItem(CACHE_KEY) ?? "null") as { n: number; t: number } | null;
      if (cached && Date.now() - cached.t < CACHE_TTL) {
        setStars(cached.n);
        return;
      }
    } catch {
      /* private mode or blocked storage: fetch anyway */
    }
    fetch(`https://api.github.com/repos/${owner}/${repo}`, { headers: { Accept: "application/vnd.github+json" } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((json: { stargazers_count?: number }) => {
        if (!live || typeof json.stargazers_count !== "number") return;
        setStars(json.stargazers_count);
        try {
          localStorage.setItem(CACHE_KEY, JSON.stringify({ n: json.stargazers_count, t: Date.now() }));
        } catch {
          /* ignore */
        }
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  // Count up to the value once it arrives; instant under reduced motion.
  useEffect(() => {
    if (stars === null) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || stars < 20) {
      setShown(stars);
      return;
    }
    const start = performance.now();
    const duration = 900;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      setShown(Math.round(stars * (1 - Math.pow(1 - t, 3))));
      if (t < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [stars]);

  return (
    <a
      href={SITE.repo}
      target="_blank"
      rel="noreferrer noopener"
      className="star-btn"
      aria-label={stars !== null ? `Star ${SITE.name} on GitHub, ${stars.toLocaleString("en-US")} stars so far` : `Star ${SITE.name} on GitHub`}
    >
      <span className="star-ring" aria-hidden="true" />
      <span className="star-sparks" aria-hidden="true">
        {Array.from({ length: 6 }, (_, i) => (
          <i key={i} style={{ "--a": `${i * 60}deg` } as React.CSSProperties} />
        ))}
      </span>
      <svg viewBox="0 0 24 24" className="star-icon" aria-hidden="true">
        <path
          d="M12 2.6l2.9 5.9 6.5.95-4.7 4.58 1.1 6.47L12 17.45 6.2 20.5l1.1-6.47L2.6 9.45l6.5-.95z"
          fill="var(--c-signal)"
          stroke="var(--c-signal)"
          strokeWidth="1.5"
          strokeLinejoin="round"
          pathLength="1"
        />
      </svg>
      <span className="hidden sm:inline">Star</span>
      {stars !== null && (
        <span className="star-count" aria-hidden="true">
          {compact(shown)}
        </span>
      )}
    </a>
  );
}
