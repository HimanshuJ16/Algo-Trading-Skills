"use client";

import { useTransitionRouter } from "next-view-transitions";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { loadSearchIndex } from "@/lib/use-search-index";
import type { SearchRecord } from "@/lib/content";
import { EMPTY_FILTERS, highlight, search } from "@/lib/search";
import { domainMeta } from "@/lib/domains";
import { SearchIcon } from "@/components/icons";

const OPEN_EVENT = "palette:open";

export function openPalette() {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

/** Situations rather than subjects, because that is how every description is written. */
const SUGGESTIONS = [
  "duplicate order after timeout",
  "websocket reconnect",
  "lookahead bias",
  "daylight saving",
  "kill switch",
  "wash sale",
];

/**
 * Global search on a native <dialog>: the browser supplies the focus trap, the Escape key,
 * the top layer and inert backdrop. Results are a listbox driven by aria-activedescendant,
 * so arrow keys move a selection without moving focus off the input.
 */
export function CommandPalette() {
  const router = useTransitionRouter();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [records, setRecords] = useState<SearchRecord[] | null>(null);

  useEffect(() => {
    const onOpen = () => setOpen(true);
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((v) => !v);
        return;
      }
      if (event.key === "/" && !typing) {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(OPEN_EVENT, onOpen);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  // Warm the index while the browser is idle so the first keystroke is never the fetch.
  useEffect(() => {
    const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
    if (connection?.saveData) return;
    const warm = () => loadSearchIndex().then(setRecords, () => undefined);
    const idle = window.requestIdleCallback?.(warm, { timeout: 2500 });
    const timer = idle === undefined ? window.setTimeout(warm, 1200) : undefined;
    return () => {
      if (idle !== undefined) window.cancelIdleCallback?.(idle);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      loadSearchIndex().then(setRecords, () => undefined);
      setActive(0);
      window.setTimeout(() => inputRef.current?.focus(), 10);
      document.documentElement.style.overflow = "hidden";
      window.dispatchEvent(new Event("scroll:lock"));
    } else if (!open && dialog.open) {
      dialog.close();
    }
    if (!open) {
      document.documentElement.style.overflow = "";
      window.dispatchEvent(new Event("scroll:unlock"));
    }
  }, [open]);

  const results = useMemo(() => {
    if (!records || query.trim().length === 0) return [];
    return search(records, { ...EMPTY_FILTERS, query }).slice(0, 8);
  }, [records, query]);

  const go = useCallback(
    (slug: string) => {
      setOpen(false);
      setQuery("");
      router.push(`/skills/${slug}/`);
    },
    [router],
  );

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (results.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i - 1 + results.length) % results.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const hit = results[active];
      if (hit) go(hit.record.n);
    }
  };

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <dialog
      ref={dialogRef}
      onClose={() => setOpen(false)}
      onClick={(event) => {
        // Clicks on the backdrop land on the dialog element itself.
        if (event.target === event.currentTarget) setOpen(false);
      }}
      aria-label="Search skills"
      className="m-0 h-dvh max-h-none w-screen max-w-none bg-transparent p-0 backdrop:bg-(--c-scrim) backdrop:backdrop-blur-md open:flex open:items-start open:justify-center"
    >
      {open && (
        <div
          className="panel rise relative mx-4 mt-[10vh] w-full max-w-2xl overflow-hidden shadow-2xl sm:mt-[14vh]"
          style={{ boxShadow: "var(--c-shadow)", "--d": "0ms" } as CSSProperties}
          onKeyDown={onKeyDown}
        >
          <div className="flex items-center gap-3 border-b border-line px-4">
            <SearchIcon className="size-[1.05rem] shrink-0 text-subtle" />
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActive(0);
              }}
              placeholder="Describe the situation, not the subject"
              className="h-14 w-full bg-transparent text-[0.95rem] outline-none placeholder:text-subtle"
              autoComplete="off"
              spellCheck={false}
              role="combobox"
              aria-expanded={results.length > 0}
              aria-controls="palette-results"
              aria-activedescendant={results.length > 0 ? `palette-item-${active}` : undefined}
              aria-autocomplete="list"
            />
            <kbd className="hidden rounded-full border border-line px-1.5 py-0.5 text-[0.625rem] text-subtle sm:block">
              ESC
            </kbd>
          </div>

          <div
            ref={listRef}
            id="palette-results"
            role="listbox"
            aria-label="Results"
            className="max-h-[52vh] overflow-y-auto overscroll-contain p-2"
            data-lenis-prevent
          >
            {query.trim().length === 0 && (
              <div className="px-2 py-3">
                <p className="eyebrow mb-3">Try</p>
                <div className="flex flex-wrap gap-1.5">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => {
                        setQuery(s);
                        inputRef.current?.focus();
                      }}
                      className="chip cursor-pointer"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {query.trim().length > 0 && results.length === 0 && (
              <p className="px-3 py-8 text-center text-sm text-subtle">
                {records ? "No skill matches that." : "Loading the index…"}
              </p>
            )}

            {results.map((hit, index) => {
              const meta = domainMeta(hit.record.s);
              const selected = index === active;
              return (
                <button
                  key={hit.record.n}
                  type="button"
                  id={`palette-item-${index}`}
                  role="option"
                  aria-selected={selected}
                  data-index={index}
                  tabIndex={-1}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => go(hit.record.n)}
                  className={`tint flex w-full gap-3 rounded-md px-3 py-2.5 text-left transition-colors duration-(--t-tap) ${
                    selected ? "bg-panel-2" : "hover:bg-panel-2"
                  }`}
                  style={{ "--hue": meta.hue } as CSSProperties}
                >
                  <span
                    className="mt-1.5 size-1.5 shrink-0 rounded-full"
                    style={{ background: "var(--tint)" }}
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="mono block truncate text-[0.8125rem] text-fg">
                      {highlight(hit.record.n, query).map((part, i) =>
                        part.hit ? <mark key={i}>{part.text}</mark> : part.text,
                      )}
                    </span>
                    <span className="mt-0.5 block line-clamp-2 text-[0.8rem] leading-snug text-muted">
                      {highlight(hit.record.d, query).map((part, i) =>
                        part.hit ? <mark key={i}>{part.text}</mark> : part.text,
                      )}
                    </span>
                  </span>
                  <span
                    className="mono mt-0.5 hidden shrink-0 text-[0.625rem] uppercase tracking-wider sm:block"
                    style={{ color: "var(--tint)" }}
                  >
                    {meta.short}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="mono flex items-center gap-4 border-t border-line px-4 py-2.5 text-[0.6875rem] text-subtle">
            <span>↑↓ navigate</span>
            <span>↵ open</span>
            <span className="ml-auto">
              {records ? `${records.length} skills indexed` : "indexing…"}
            </span>
          </div>
        </div>
      )}
    </dialog>
  );
}
