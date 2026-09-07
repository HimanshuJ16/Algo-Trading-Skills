"use client";

import { ThemeProvider } from "next-themes";
import { ViewTransitions } from "next-view-transitions";
import type { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
      <ViewTransitions>{children}</ViewTransitions>
    </ThemeProvider>
  );
}
