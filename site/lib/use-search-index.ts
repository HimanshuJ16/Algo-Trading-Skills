"use client";

import { useEffect, useState } from "react";
import type { SearchRecord } from "./content";
import { withBase } from "./site";

/**
 * The 501-record search projection is a single cacheable asset fetched once per visit and
 * shared by the catalog and the command palette. Module-level memoization means navigating
 * between them never refetches.
 */
let cache: Promise<SearchRecord[]> | null = null;

export function loadSearchIndex(): Promise<SearchRecord[]> {
  cache ??= fetch(withBase("/search-index.json"))
    .then((response) => {
      if (!response.ok) throw new Error(`search index: ${response.status}`);
      return response.json();
    })
    .then((json: { records: SearchRecord[] }) => json.records)
    .catch((error) => {
      cache = null;
      throw error;
    });
  return cache;
}

export function useSearchIndex(enabled = true) {
  const [records, setRecords] = useState<SearchRecord[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    loadSearchIndex().then(
      (data) => live && setRecords(data),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [enabled]);

  return { records, ready: records !== null, failed };
}
