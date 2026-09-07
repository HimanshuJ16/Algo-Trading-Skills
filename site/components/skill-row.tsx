import { Link } from "next-view-transitions";
import type { CSSProperties } from "react";
import { domainMeta } from "@/lib/domains";
import { highlight } from "@/lib/search";

export type SkillRowData = {
  name: string;
  description: string;
  subdomain: string;
  tags?: string[];
};

function Marked({ text, query }: { text: string; query?: string }) {
  if (!query) return <>{text}</>;
  return (
    <>
      {highlight(text, query).map((part, i) =>
        part.hit ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
      )}
    </>
  );
}

/**
 * The ledger row. Slug first, because engineers grep by slug; the situation second, because
 * that is what they are matching; the domain last, in its tint, as a place marker.
 */
export function SkillRow({
  skill,
  query,
  showDomain = true,
  index,
}: {
  skill: SkillRowData;
  query?: string;
  showDomain?: boolean;
  index?: number;
}) {
  const meta = domainMeta(skill.subdomain);

  return (
    <Link prefetch={false}
      href={`/skills/${skill.name}/`}
      data-spot
      className="tint row rise"
      style={
        {
          "--hue": meta.hue,
          "--d": index !== undefined ? `${Math.min(index, 12) * 40}ms` : undefined,
        } as CSSProperties
      }
    >
      <span className="row-slug" data-vt>
        <Marked text={skill.name} query={query} />
      </span>
      <span className="row-desc">
        <Marked text={skill.description} query={query} />
      </span>
      {showDomain ? (
        <span className="row-domain">{meta.short}</span>
      ) : skill.tags && skill.tags.length > 1 ? (
        <span className="row-domain text-subtle!">{skill.tags[1]}</span>
      ) : (
        <span />
      )}
    </Link>
  );
}
