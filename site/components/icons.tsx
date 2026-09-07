import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export const SearchIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.6-3.6" />
  </Icon>
);

export const ArrowRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12h13" />
    <path d="m12.5 5.5 6.5 6.5-6.5 6.5" />
  </Icon>
);

export const ArrowUpRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 17 17 7" />
    <path d="M8 7h9v9" />
  </Icon>
);

export const CopyIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M15 5.5A1.5 1.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 4 6.5v7A1.5 1.5 0 0 0 5.5 15" />
  </Icon>
);

export const CheckIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m5 12.5 4.5 4.5L19 7" />
  </Icon>
);

export const BookIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z" />
    <path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19" />
  </Icon>
);

export const CodeIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m9 8-5 4 5 4" />
    <path d="m15 8 5 4-5 4" />
  </Icon>
);

export const ChecklistIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m3.5 7 1.8 1.8L9 5" />
    <path d="m3.5 16 1.8 1.8L9 14" />
    <path d="M12 7.5h8.5" />
    <path d="M12 16.5h8.5" />
  </Icon>
);

export const SunIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" />
  </Icon>
);

export const MoonIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 13.5A8.2 8.2 0 0 1 10.5 4a8.2 8.2 0 1 0 9.5 9.5" />
  </Icon>
);

export const CloseIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m6 6 12 12M18 6 6 18" />
  </Icon>
);

export const FilterIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 6h16M7 12h10M10 18h4" />
  </Icon>
);

export const LinkIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10 13.5a3.5 3.5 0 0 0 5 0l3-3a3.54 3.54 0 0 0-5-5l-1.2 1.2" />
    <path d="M14 10.5a3.5 3.5 0 0 0-5 0l-3 3a3.54 3.54 0 0 0 5 5l1.2-1.2" />
  </Icon>
);

export const GitHubMark = (p: IconProps) => (
  <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" {...p}>
    <path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8" />
  </svg>
);

export const ListIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Icon>
);

export const GraphIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="6" cy="6" r="2" />
    <circle cx="18" cy="8" r="2" />
    <circle cx="9" cy="18" r="2" />
    <path d="m7.8 7 8.4 0.5M7 8l1.5 8M16.5 9.5 10.5 17" />
  </Icon>
);

export const ArrowDown = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 5v13" />
    <path d="m5.5 11.5 6.5 6.5 6.5-6.5" />
  </Icon>
);

export const LinkedInMark = (p: IconProps) => (
  <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" {...p}>
    <path d="M3.6 5.6H.9V15h2.7V5.6ZM2.25 1A1.55 1.55 0 1 0 2.3 4.1 1.55 1.55 0 0 0 2.25 1ZM15.1 9.85c0-2.6-1.4-4.45-3.7-4.45a3.2 3.2 0 0 0-2.9 1.6V5.6H5.9V15h2.7v-4.7c0-1.25.5-2.3 1.75-2.3 1.2 0 1.6.95 1.6 2.3V15h2.7V9.85Z" />
  </svg>
);

export const GlobeIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
  </Icon>
);
