"use client";

import { Link } from "next-view-transitions";
import { usePathname } from "next/navigation";
import { SITE } from "@/lib/site";
import { ThemeToggle } from "@/components/theme-toggle";
import { openPalette } from "@/components/command-palette";
import { SearchIcon } from "@/components/icons";
import { StarButton } from "@/components/star-button";

const NAV = [
  { href: "/skills", label: "Catalog" },
  { href: "/domains", label: "Domains" },
];

/** A boundary mark: a square, and the line where it stops. */
function LogoMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" className="shrink-0">
      <rect x="1" y="1" width="18" height="18" rx="2" fill="none" stroke="currentColor" />
      <rect x="4.5" y="4.5" width="7" height="7" rx="1" fill="currentColor" />
      <path d="M14 4.5v11" stroke="currentColor" strokeLinecap="round" />
    </svg>
  );
}

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 border-b border-line bg-[color-mix(in_oklab,var(--c-bg)_86%,transparent)] backdrop-blur-xl" data-cursor="">
      <div className="container-x flex h-(--header-h) items-center gap-3">
        <Link prefetch={false} href="/" className="flex items-center gap-2.5 text-fg" aria-label={SITE.name}>
          <LogoMark />
          <span className="mono hidden text-[0.8125rem] tracking-tight sm:block">
            algo-trading<span className="text-subtle">-</span>skills
          </span>
        </Link>

        <nav className="ml-3 flex items-center gap-0.5" aria-label="Primary">
          {NAV.map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link prefetch={false}
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-full px-3 py-1.5 text-[0.8125rem] transition-colors duration-(--t-quick) ${
                  active ? "bg-panel-2 text-fg" : "text-muted hover:text-fg"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={openPalette}
            className="flex h-9 items-center gap-2 rounded-full border border-line py-1.5 pl-3 pr-1.5 text-[0.8125rem] text-subtle transition-colors duration-(--t-quick) hover:border-line-strong hover:text-fg"
            aria-label="Search skills"
          >
            <SearchIcon className="size-4" />
            <span className="hidden w-32 text-left md:block">Search by situation</span>
            <kbd className="hidden rounded-full border border-line px-1.5 py-0.5 text-[0.625rem] text-subtle md:block">
              ⌘K
            </kbd>
          </button>

          <ThemeToggle />

          <StarButton />
        </div>
      </div>
      <div className="progress" aria-hidden="true" />
    </header>
  );
}
