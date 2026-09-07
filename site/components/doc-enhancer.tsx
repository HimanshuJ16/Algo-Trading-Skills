"use client";

import { useEffect } from "react";

/**
 * One delegated listener for every control inside a rendered document.
 *
 * The Markdown pipeline emits a copy button per code fence as plain HTML; wiring each one
 * as a React component would hydrate a node per fence and serialize the whole article into
 * the client payload. This attaches a single click handler to the article instead.
 */
export function DocEnhancer({ target }: { target: string }) {
  useEffect(() => {
    const root = document.getElementById(target);
    if (!root) return;

    const onClick = (event: MouseEvent) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-copy]");
      if (!button) return;
      const pre = button.parentElement?.querySelector("pre");
      const text = pre?.textContent ?? "";
      navigator.clipboard?.writeText(text).then(
        () => {
          button.dataset.done = "";
          button.textContent = "copied";
          button.setAttribute("aria-label", "Copied");
          window.setTimeout(() => {
            delete button.dataset.done;
            button.textContent = "copy";
            button.setAttribute("aria-label", "Copy code");
          }, 1600);
        },
        () => undefined,
      );
    };

    root.addEventListener("click", onClick);
    return () => root.removeEventListener("click", onClick);
  }, [target]);

  return null;
}
