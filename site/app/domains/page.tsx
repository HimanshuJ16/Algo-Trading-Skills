import type { Metadata } from "next";
import { getLibrary } from "@/lib/content";
import { DomainAtlas } from "@/components/domain-atlas";
import { ExperienceMount } from "@/components/experience-mount";

export const metadata: Metadata = {
  title: "Domains",
  description:
    "The sixteen engineering domains the library covers, from broker integration and risk management to microstructure, custody and tax reporting, with the handoffs between them.",
  alternates: { canonical: "/domains/" },
};

export default async function DomainsPage() {
  const library = await getLibrary();

  const domains = library.domains.map((d) => {
    const members = d.skills.map((n) => library.bySlug.get(n)!);
    const outgoing = members.reduce((n, s) => n + s.crossRefs.length, 0);
    const neighbours = library.domainLinks
      .filter((l) => l.source === d.slug || l.target === d.slug)
      .slice(0, 3)
      .map((l) => {
        const other = l.source === d.slug ? l.target : l.source;
        return { slug: other, label: library.domains.find((x) => x.slug === other)?.label ?? other, count: l.count };
      });
    return { slug: d.slug, label: d.label, short: d.short, blurb: d.blurb, hue: d.hue, count: d.count, outgoing, neighbours };
  });

  return (
    <>
      <ExperienceMount />
      <DomainAtlas domains={domains} total={library.totalSkills} crossReferences={library.stats.crossReferences} />
    </>
  );
}
