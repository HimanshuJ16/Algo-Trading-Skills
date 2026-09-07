"use client";

import type { ReactNode } from "react";

/**
 * One pointer listener and one click listener for a whole list rather than one per row.
 *
 * The catalog renders hundreds of rows. Giving each its own client component to track the
 * cursor would cost more hydration than the rest of the page, so both effects are delegated:
 *
 *  - pointermove finds the row under the pointer and writes two custom properties on it,
 *    which the `.row::before` gradient reads. No state, no re-render.
 *  - click (capture phase, so it runs before the link starts navigating) tags the clicked
 *    row's slug with the shared-element name, so the view transition morphs that one slug
 *    into the document's title. Only one element may carry the name at a time, which is
 *    why it is assigned on click rather than rendered.
 */
export function SpotlightGrid({
  children,
  className,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "ul" | "ol";
}) {
  return (
    <Tag
      className={className}
      onPointerMove={(event) => {
        if (event.pointerType !== "mouse") return;
        const card = (event.target as HTMLElement).closest<HTMLElement>("[data-spot]");
        if (!card) return;
        const rect = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${event.clientX - rect.left}px`);
        card.style.setProperty("--my", `${event.clientY - rect.top}px`);
      }}
      onClickCapture={(event) => {
        const link = (event.target as HTMLElement).closest<HTMLElement>("a[data-spot]");
        const vt = link?.querySelector<HTMLElement>("[data-vt]");
        if (!vt) return;
        document
          .querySelectorAll<HTMLElement>("[data-vt-live]")
          .forEach((el) => {
            el.style.viewTransitionName = "";
            delete el.dataset.vtLive;
          });
        vt.style.viewTransitionName = "skill-title";
        vt.dataset.vtLive = "";
      }}
    >
      {children}
    </Tag>
  );
}
