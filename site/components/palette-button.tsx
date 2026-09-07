"use client";

import { openPalette } from "@/components/command-palette";
import { SearchIcon } from "@/components/icons";

export function PaletteButton({ label = "Search by situation" }: { label?: string }) {
  return (
    <button type="button" onClick={openPalette} className="btn btn-primary" data-cursor-label="search">
      <SearchIcon className="size-4" />
      {label}
      <kbd className="ml-1 rounded-full border border-current/30 px-1.5 py-0.5 text-[0.625rem] opacity-70">⌘K</kbd>
    </button>
  );
}
