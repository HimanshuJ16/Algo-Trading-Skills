export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** Prefix a root-relative path with the deployment base path. */
export function withBase(path: string): string {
  if (!path.startsWith("/")) return path;
  return `${BASE_PATH}${path}`;
}

export const SITE = {
  /** Public origin, for canonical links, OpenGraph and the sitemap. Override per deployment. */
  url: (process.env.NEXT_PUBLIC_SITE_URL ?? "https://skills.himanshujangir.com").replace(/\/$/, ""),
  name: "Algo-Trading-Skills",
  tagline: "An open-source algorithmic trading skills library for AI agents",
  repo: "https://github.com/HimanshuJ16/Algo-Trading-Skills",
  standard: "https://agentskills.io",
  license: "Apache-2.0",
} as const;

/** The person behind the library, credited in the footer. */
export const AUTHOR = {
  name: "Himanshu Jangir",
  github: "https://github.com/HimanshuJ16",
  linkedin: "https://www.linkedin.com/in/himanshujangir16",
  site: "https://himanshujangir.com",
} as const;

/** Raw file on the default branch, for "view source" links. */
export function repoBlob(path: string): string {
  return `${SITE.repo}/blob/main/${path}`;
}
