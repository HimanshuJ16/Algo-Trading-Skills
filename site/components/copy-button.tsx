"use client";

import { useEffect, useState } from "react";
import { CheckIcon, CopyIcon } from "@/components/icons";

export function CopyButton({
  value,
  label,
  className = "",
}: {
  value: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(id);
  }, [copied]);

  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(value).then(
          () => setCopied(true),
          () => undefined,
        );
      }}
      className={`chip cursor-pointer ${copied ? "text-signal!" : ""} ${className}`}
      aria-label={copied ? "Copied" : `Copy ${label ?? "to clipboard"}`}
    >
      {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
      {label && <span>{copied ? "copied" : label}</span>}
    </button>
  );
}

/** A command line with a copy affordance, used for install and test commands. */
export function CommandLine({ command, note }: { command: string; note?: string }) {
  return (
    <div className="flex min-w-0 items-center gap-3 rounded-md border border-line bg-(--c-code-bg) py-2 pl-3 pr-2">
      <span className="mono select-none text-subtle" aria-hidden="true">
        $
      </span>
      <code className="mono min-w-0 flex-1 overflow-x-auto whitespace-pre text-[0.8125rem] text-fg">
        {command}
      </code>
      {note && <span className="mono hidden text-[0.65rem] text-subtle lg:block">{note}</span>}
      <CopyButton value={command} className="shrink-0" />
    </div>
  );
}
