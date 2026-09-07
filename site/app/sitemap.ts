import type { MetadataRoute } from "next";
import { getLibrary } from "@/lib/content";
import { SITE } from "@/lib/site";

export const dynamic = "force-static";

/** Every page the export produces: the landing, the two indexes, 16 domains, 501 skills. */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const library = await getLibrary();
  const at = (path: string) => `${SITE.url}${path}`;
  return [
    { url: at("/"), changeFrequency: "weekly", priority: 1 },
    { url: at("/skills/"), changeFrequency: "weekly", priority: 0.9 },
    { url: at("/domains/"), changeFrequency: "monthly", priority: 0.7 },
    ...library.domains.map((d) => ({ url: at(`/domains/${d.slug}/`), changeFrequency: "monthly" as const, priority: 0.6 })),
    ...library.skills.map((s) => ({ url: at(`/skills/${s.name}/`), changeFrequency: "monthly" as const, priority: 0.8 })),
  ];
}
