"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { MoonIcon, SunIcon } from "@/components/icons";

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const dark = resolvedTheme === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? "light" : "dark")}
      className="icon-btn relative overflow-hidden"
      aria-label={!mounted ? "Toggle theme" : dark ? "Switch to light theme" : "Switch to dark theme"}
    >
      {/* Nothing renders until mounted: the server cannot know the stored preference. */}
      {mounted && (
        <span
          key={dark ? "moon" : "sun"}
          className="rise grid place-items-center"
          style={{ "--d": "0ms" } as React.CSSProperties}
        >
          {dark ? <MoonIcon className="size-[1.05rem]" /> : <SunIcon className="size-[1.05rem]" />}
        </span>
      )}
    </button>
  );
}
