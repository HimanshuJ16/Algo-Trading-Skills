import { Link } from "next-view-transitions";
import { getLibrary } from "@/lib/content";
import { domainMeta } from "@/lib/domains";
import { HandoffMarquee, type Edge } from "@/components/home/marquee";
import { ExperienceMount } from "@/components/experience-mount";
import { PaletteButton } from "@/components/palette-button";
import { ArrowRight } from "@/components/icons";

/**
 * A missing address, in the library's own terms: the case is out of scope here, and these
 * are the places it might be handed to.
 */
export default async function NotFound() {
  const library = await getLibrary();
  const edges: Edge[] = library.domains.slice(0, 16).flatMap((domain) => {
    const skill = domain.skills.map((n) => library.bySlug.get(n)!).find((s) => s.crossRefs.length > 0);
    if (!skill) return [];
    return [{ from: skill.name, to: skill.crossRefs[0], hue: domainMeta(skill.subdomain).hue }];
  });

  return (
    <>
      <ExperienceMount />
      <div className="container-x flex min-h-[calc(100svh-var(--header-h))] flex-col justify-center py-16">
        <p className="eyebrow rise">When NOT to use this address</p>
        <h1 className="display display-xl rise mt-6 text-fg" style={{ "--d": "60ms" } as React.CSSProperties}>
          No skill
          <br />
          <span className="serif font-normal italic text-muted">stops here.</span>
        </h1>
        <p className="rise mt-8 max-w-[44ch] text-[1.0625rem] leading-relaxed text-muted text-pretty" style={{ "--d": "120ms" } as React.CSSProperties}>
          Every page on this site exists only while its skill does, so this one is either
          renamed or never was. Describe the situation instead; the library is indexed that
          way.
        </p>
        <div className="rise mt-9 flex flex-wrap gap-3" style={{ "--d": "180ms" } as React.CSSProperties}>
          <PaletteButton />
          <Link prefetch={false} href="/skills" className="btn">
            Browse the catalog
            <ArrowRight className="size-4" />
          </Link>
        </div>
      </div>
      <HandoffMarquee edges={edges} label="Handoffs from each domain" />
    </>
  );
}
