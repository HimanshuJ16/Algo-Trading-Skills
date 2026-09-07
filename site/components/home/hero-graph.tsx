"use client";

import dynamic from "next/dynamic";

/** The canvas is the only client code in the hero, and it arrives after hydration. */
const GraphCanvas = dynamic(() => import("@/components/graph-canvas"), {
  ssr: false,
  loading: () => <div className="aspect-square w-full" aria-hidden="true" />,
});

export function HeroGraph() {
  return <GraphCanvas mode="hero" />;
}
