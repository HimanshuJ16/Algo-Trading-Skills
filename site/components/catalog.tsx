"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode, CSSProperties } from "react";
import { EMPTY_FILTERS, isFiltered, search, type Filters } from "@/lib/search";
import { useSearchIndex } from "@/lib/use-search-index";
import { domainMeta } from "@/lib/domains";
import { SkillRow } from "@/components/skill-row";
import { SpotlightGrid } from "@/components/spotlight-grid";
import { CloseIcon, FilterIcon, GraphIcon, ListIcon, SearchIcon } from "@/components/icons";

const GraphCanvas = dynamic(() => import("@/components/graph-canvas"), {
  ssr: false,
  loading: () => <div className="aspect-square w-full" aria-hidden="true" />,
});

export type Facet = { value: string; label: string; count: number; hue?: number };

const PAGE = 80;

/**
 * The catalog: a ledger, not a card grid.
 *
 * Five hundred rows scan faster as a list grouped by domain than as a wall of cards, and
 * the slug column lines up so the eye can run down it. Server-rendered first (the opening
 * page arrives as HTML), then the client owns the list once the index has loaded. Filters
 * live in the address bar, so a narrowed view is a link. A map view renders the same
 * library as the graph, for the reader who would rather find a skill by its neighbours.
 */
export function Catalog({
  domains,
  tags,
  brokers,
  total,
  children,
}: {
  domains: Facet[];
  tags: Facet[];
  brokers: Facet[];
  total: number;
  children: ReactNode;
}) {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [limit, setLimit] = useState(PAGE);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [view, setView] = useState<"list" | "map">("list");
  const { records, failed } = useSearchIndex();
  const inputRef = useRef<HTMLInputElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Restore a shared link, then keep the address bar in step without adding history entries.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const restored: Filters = {
      query: params.get("q") ?? "",
      domains: params.getAll("domain"),
      brokers: params.getAll("broker"),
      tags: params.getAll("tag"),
    };
    if (isFiltered(restored)) setFilters(restored);
    if (params.get("view") === "map") setView("map");
  }, []);

  useEffect(() => {
    const params = new URLSearchParams();
    if (filters.query.trim()) params.set("q", filters.query.trim());
    for (const d of filters.domains) params.append("domain", d);
    for (const b of filters.brokers) params.append("broker", b);
    for (const t of filters.tags) params.append("tag", t);
    if (view === "map") params.set("view", "map");
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [filters, view]);

  useEffect(() => setLimit(PAGE), [filters]);

  // Focus the search field with "/" when the palette is not the target.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  const toggle = useCallback((key: "domains" | "brokers" | "tags", value: string) => {
    setFilters((current) => {
      const list = current[key];
      return { ...current, [key]: list.includes(value) ? list.filter((v) => v !== value) : [...list, value] };
    });
  }, []);

  const active = isFiltered(filters);
  const results = useMemo(() => (records ? search(records, filters) : null), [records, filters]);
  const shown = results?.slice(0, limit) ?? [];
  const count = results?.length ?? total;

  // Unfiltered and unsearched, the ledger groups by domain so the shape of the library is
  // visible; a query flattens it into a ranked list.
  const grouped = useMemo(() => {
    if (!results || filters.query.trim()) return null;
    const map = new Map<string, typeof shown>();
    for (const hit of shown) map.set(hit.record.s, [...(map.get(hit.record.s) ?? []), hit]);
    return [...map.entries()].sort((a, b) => domainMeta(a[0]).label.localeCompare(domainMeta(b[0]).label));
  }, [results, shown, filters.query]);

  // Load the next page as the reader approaches the end of the list.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !results || results.length <= limit) return;
    const io = new IntersectionObserver(([entry]) => entry.isIntersecting && setLimit((n) => n + PAGE), { rootMargin: "600px" });
    io.observe(el);
    return () => io.disconnect();
  }, [results, limit]);

  const activeCount = filters.domains.length + filters.brokers.length + filters.tags.length;

  return (
    <div className="container-x">
      {/* Head */}
      <div className="grid gap-8 pb-8 pt-10 sm:pt-14 lg:grid-cols-12">
        <div className="rise lg:col-span-7">
          <p className="eyebrow">The catalog</p>
          <h1 className="display display-lg mt-4 text-fg">
            {total} skills,
            <br />
            <span className="text-muted">indexed by situation.</span>
          </h1>
        </div>
        <p className="rise self-end text-[0.95rem] leading-relaxed text-muted text-pretty lg:col-span-5" style={{ "--d": "80ms" } as CSSProperties}>
          Every description begins <span className="serif italic text-fg">Use when…</span>, so
          search for the symptom in front of you rather than the subject you think it belongs
          to. Filters stay in the address bar; a narrowed view is a link you can share.
        </p>
      </div>

      {/* Search bar */}
      <div className="sticky-label -mx-(--gutter) border-y border-line px-(--gutter) py-3">
        <div className="flex items-center gap-3">
          <SearchIcon className="size-[1.05rem] shrink-0 text-subtle" />
          <input
            ref={inputRef}
            value={filters.query}
            onChange={(event) => setFilters((current) => ({ ...current, query: event.target.value }))}
            placeholder="Describe the situation — timeout, reconnect, wash sale, DST…"
            className="h-9 w-full bg-transparent text-[0.95rem] outline-none placeholder:text-subtle"
            autoComplete="off"
            spellCheck={false}
            aria-label="Search skills"
          />
          <kbd className="hidden rounded-full border border-line px-1.5 py-0.5 text-[0.625rem] text-subtle md:block">/</kbd>
          {filters.query && (
            <button type="button" onClick={() => setFilters((current) => ({ ...current, query: "" }))} className="icon-btn size-8" aria-label="Clear search">
              <CloseIcon className="size-4" />
            </button>
          )}
          <button type="button" onClick={() => setDrawerOpen(true)} className="btn btn-sm lg:hidden" aria-haspopup="dialog">
            <FilterIcon className="size-3.5" />
            Filters
            {activeCount > 0 && <span className="mono text-signal">{activeCount}</span>}
          </button>
          <div className="hidden items-center rounded-full border border-line p-0.5 lg:flex" role="group" aria-label="View">
            <button
              type="button"
              onClick={() => setView("list")}
              aria-pressed={view === "list"}
              className={`icon-btn size-8 border-0 ${view === "list" ? "bg-fg text-bg hover:text-bg" : ""}`}
              aria-label="List view"
            >
              <ListIcon className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => setView("map")}
              aria-pressed={view === "map"}
              className={`icon-btn size-8 border-0 ${view === "map" ? "bg-fg text-bg hover:text-bg" : ""}`}
              aria-label="Map view"
            >
              <GraphIcon className="size-4" />
            </button>
          </div>
        </div>
      </div>

      <div className="grid gap-10 pb-16 pt-6 lg:grid-cols-[14rem_minmax(0,1fr)] lg:gap-14">
        {/* Facet rail */}
        <div className="hidden lg:block">
          <div className="sticky top-[calc(var(--header-h)+4.5rem)] max-h-[calc(100dvh-var(--header-h)-6rem)] space-y-8 overflow-y-auto pb-8 pr-2" data-lenis-prevent>
            <FacetGroup title="Domain" facets={domains} selected={filters.domains} onToggle={(v) => toggle("domains", v)} />
            <FacetGroup title="Broker or framework" facets={brokers} selected={filters.brokers} onToggle={(v) => toggle("brokers", v)} initial={8} />
            <FacetGroup title="Tag" facets={tags} selected={filters.tags} onToggle={(v) => toggle("tags", v)} initial={10} />
          </div>
        </div>

        <div className="min-w-0">
          {/* Status line */}
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <p className="mono text-[0.75rem] tabular-nums text-subtle" aria-live="polite">
              {!records && !failed && active ? "indexing…" : `${count.toLocaleString("en-US")} ${count === 1 ? "skill" : "skills"}`}
              {filters.query.trim() && records ? " · ranked by match" : ""}
            </p>
            {[
              ...filters.domains.map((v) => ({ key: "domains" as const, value: v })),
              ...filters.brokers.map((v) => ({ key: "brokers" as const, value: v })),
              ...filters.tags.map((v) => ({ key: "tags" as const, value: v })),
            ].map((chip) => (
              <button key={`${chip.key}-${chip.value}`} type="button" onClick={() => toggle(chip.key, chip.value)} className="chip chip-active cursor-pointer">
                {chip.key === "domains" ? domainMeta(chip.value).label : chip.value}
                <CloseIcon className="size-3" />
              </button>
            ))}
            {active && (
              <button type="button" onClick={() => setFilters(EMPTY_FILTERS)} className="mono ml-1 text-[0.75rem] text-subtle underline-offset-4 hover:text-fg hover:underline">
                reset
              </button>
            )}
          </div>

          {failed && (
            <p className="mb-5 rounded-md border border-line px-4 py-3 text-[0.8125rem] text-muted">
              The search index could not be loaded. The list below is the opening page only; the
              sixteen domain pages list every skill.
            </p>
          )}

          {view === "map" ? (
            <div className="mx-auto max-w-[44rem]" data-cursor="drag" data-cursor-label="hover a node">
              <GraphCanvas mode="map" focus={filters.domains.length === 1 ? filters.domains[0] : null} />
              <p className="mono mt-4 text-center text-[0.6875rem] uppercase tracking-[0.12em] text-subtle">
                Select one domain in the rail to isolate its arc. Click a node to open it.
              </p>
            </div>
          ) : !results ? (
            <SpotlightGrid>{children}</SpotlightGrid>
          ) : results.length === 0 ? (
            <EmptyState onReset={() => setFilters(EMPTY_FILTERS)} />
          ) : grouped ? (
            <SpotlightGrid>
              {grouped.map(([slug, hits]) => {
                const meta = domainMeta(slug);
                return (
                  <section key={slug} aria-labelledby={`group-${slug}`} className="tint mb-8" style={{ "--hue": meta.hue } as CSSProperties}>
                    <h2 id={`group-${slug}`} className="rule-label mono py-2 text-[0.6875rem] uppercase tracking-[0.14em]">
                      <span style={{ color: "var(--tint)" }}>{meta.label}</span>
                      <span className="text-subtle">{hits.length}</span>
                    </h2>
                    {hits.map((hit, i) => (
                      <SkillRow key={hit.record.n} index={i} skill={{ name: hit.record.n, description: hit.record.d, subdomain: hit.record.s, tags: hit.record.t }} showDomain={false} />
                    ))}
                  </section>
                );
              })}
            </SpotlightGrid>
          ) : (
            <SpotlightGrid>
              {shown.map((hit, i) => (
                <SkillRow key={hit.record.n} index={i} skill={{ name: hit.record.n, description: hit.record.d, subdomain: hit.record.s, tags: hit.record.t }} query={filters.query} />
              ))}
            </SpotlightGrid>
          )}

          {view === "list" && results && results.length > limit && (
            <div ref={sentinelRef} className="mt-8 flex justify-center">
              <button type="button" onClick={() => setLimit((n) => n + PAGE)} className="btn btn-sm">
                Show {Math.min(PAGE, results.length - limit)} more
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Mobile filter drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-60 lg:hidden" role="dialog" aria-modal="true" aria-label="Filters">
          <div className="fade absolute inset-0 bg-(--c-scrim) backdrop-blur-sm" onClick={() => setDrawerOpen(false)} />
          <div className="rise absolute inset-x-0 bottom-0 max-h-[82dvh] overflow-y-auto rounded-t-2xl border-t border-line bg-panel p-5" data-lenis-prevent>
            <div className="mb-5 flex items-center justify-between">
              <p className="text-sm font-medium">Filters</p>
              <button type="button" onClick={() => setDrawerOpen(false)} className="icon-btn" aria-label="Close filters">
                <CloseIcon className="size-5" />
              </button>
            </div>
            <div className="space-y-8 pb-6">
              <FacetGroup title="Domain" facets={domains} selected={filters.domains} onToggle={(v) => toggle("domains", v)} />
              <FacetGroup title="Broker or framework" facets={brokers} selected={filters.brokers} onToggle={(v) => toggle("brokers", v)} initial={8} />
              <FacetGroup title="Tag" facets={tags} selected={filters.tags} onToggle={(v) => toggle("tags", v)} initial={10} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FacetGroup({
  title,
  facets,
  selected,
  onToggle,
  initial,
}: {
  title: string;
  facets: Facet[];
  selected: string[];
  onToggle: (value: string) => void;
  initial?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded || initial === undefined ? facets : facets.slice(0, initial);

  return (
    <fieldset>
      <legend className="eyebrow mb-2.5">{title}</legend>
      <ul className="space-y-px">
        {visible.map((facet) => {
          const on = selected.includes(facet.value);
          return (
            <li key={facet.value}>
              <button
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() => onToggle(facet.value)}
                className={`tint group flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[0.8125rem] transition-colors duration-(--t-tap) ${
                  on ? "bg-panel-2 text-fg" : "text-muted hover:bg-panel-2 hover:text-fg"
                }`}
                style={facet.hue !== undefined ? ({ "--hue": facet.hue } as CSSProperties) : undefined}
              >
                {facet.hue !== undefined && (
                  <span className="size-1.5 shrink-0 rounded-full" style={{ background: "var(--tint)", opacity: on ? 1 : 0.6 }} aria-hidden="true" />
                )}
                <span className="min-w-0 flex-1 truncate">{facet.label}</span>
                <span className="mono shrink-0 text-[0.65rem] tabular-nums text-subtle">{facet.count}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {initial !== undefined && facets.length > initial && (
        <button type="button" onClick={() => setExpanded((v) => !v)} className="mono mt-2 px-2 text-[0.6875rem] text-subtle underline-offset-4 hover:text-fg hover:underline">
          {expanded ? "show fewer" : `+${facets.length - initial} more`}
        </button>
      )}
    </fieldset>
  );
}

function EmptyState({ onReset }: { onReset: () => void }) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-line px-6 py-20 text-center">
      <p className="serif text-[1.5rem] italic text-fg">No skill matches those filters.</p>
      <p className="mt-3 max-w-sm text-[0.875rem] leading-relaxed text-muted">
        Descriptions are written as situations rather than subjects. Try the symptom you are
        seeing: <span className="mono text-fg">duplicate order</span>,{" "}
        <span className="mono text-fg">stale token</span>,{" "}
        <span className="mono text-fg">missing bar</span>.
      </p>
      <button type="button" onClick={onReset} className="btn btn-sm mt-6">
        Reset filters
      </button>
    </div>
  );
}
