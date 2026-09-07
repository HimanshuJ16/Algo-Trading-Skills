"use client";

import dynamic from "next/dynamic";

/**
 * The experience-tier runtime is loaded after hydration and only on the pages that mount
 * this component, so a document page never downloads Lenis or the cursor.
 */
const Experience = dynamic(() => import("@/components/experience"), { ssr: false });

export function ExperienceMount({ smooth = true }: { smooth?: boolean }) {
  return <Experience smooth={smooth} />;
}
