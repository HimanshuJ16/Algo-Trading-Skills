import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Instrument_Serif } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";
import { CommandPalette } from "@/components/command-palette";
import { SITE, withBase } from "@/lib/site";

const sans = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
  display: "swap",
});

const mono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

const serif = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  style: ["italic"],
  variable: "--font-instrument",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(SITE.url),
  title: {
    default: `${SITE.name} | ${SITE.tagline}`,
    template: `%s · ${SITE.name}`,
  },
  description:
    "A browsable catalog of 501 algorithmic-trading skills for AI agents: broker integration, risk controls, backtesting method, execution algorithms, global market and regulatory coverage.",
  applicationName: SITE.name,
  authors: [{ name: "algo-trading-skills-contributors" }],
  keywords: [
    "algorithmic trading",
    "AI agents",
    "agent skills",
    "quant infrastructure",
    "backtesting",
    "risk management",
    "broker API",
  ],
  openGraph: {
    type: "website",
    url: "/",
    title: `${SITE.name} — ${SITE.tagline}`,
    description:
      "501 algorithmic-trading skills, 16 engineering domains, 501 reference implementations. Browse the library.",
    siteName: SITE.name,
  },
  robots: { index: true, follow: true },
  icons: { icon: withBase("/icon.svg") },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f2ec" },
    { media: "(prefers-color-scheme: dark)", color: "#0c0c0e" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${sans.variable} ${mono.variable} ${serif.variable}`}
    >
      <body className="min-h-dvh antialiased" suppressHydrationWarning>
        <Providers>
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-100 focus:rounded-full focus:bg-fg focus:px-4 focus:py-2 focus:text-sm focus:text-bg"
          >
            Skip to content
          </a>
          <SiteHeader />
          <main id="main">{children}</main>
          <SiteFooter />
          <CommandPalette />
        </Providers>
      </body>
    </html>
  );
}
