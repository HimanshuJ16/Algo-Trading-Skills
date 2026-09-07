import type { Metadata } from "next";
import { getLibrary } from "@/lib/content";
import { Catalog, type Facet } from "@/components/catalog";
import { SkillRow } from "@/components/skill-row";
import { ExperienceMount } from "@/components/experience-mount";

export const metadata: Metadata = {
  title: "Catalog",
  description:
    "Search and filter every skill in the library by situation, domain, broker or framework, and tag.",
  alternates: { canonical: "/skills/" },
};

/** How many rows ship in the HTML before the client index takes over. */
const SERVER_PAGE = 80;

export default async function SkillsPage() {
  const library = await getLibrary();

  const domainFacets: Facet[] = library.domains
    .slice()
    .sort((a, b) => a.label.localeCompare(b.label))
    .map((domain) => ({ value: domain.slug, label: domain.label, count: domain.count, hue: domain.hue }));

  const brokerFacets: Facet[] = library.brokers.slice(0, 40).map((b) => ({ value: b.name, label: b.name, count: b.count }));

  // The first tag on every skill repeats its subdomain, which the domain facet already covers.
  const domainSlugs = new Set(library.domains.map((d) => d.slug));
  const tagFacets: Facet[] = library.tags
    .filter((t) => !domainSlugs.has(t.name))
    .slice(0, 44)
    .map((t) => ({ value: t.name, label: t.name, count: t.count }));

  const alphabetical = library.skills.slice().sort((a, b) => a.name.localeCompare(b.name));

  return (
    <>
      <ExperienceMount smooth={false} />
      <Catalog domains={domainFacets} brokers={brokerFacets} tags={tagFacets} total={library.stats.skills}>
        {alphabetical.slice(0, SERVER_PAGE).map((skill, i) => (
          <SkillRow
            key={skill.name}
            index={i}
            skill={{ name: skill.name, description: skill.description, subdomain: skill.subdomain, tags: skill.tags }}
          />
        ))}
      </Catalog>
    </>
  );
}
