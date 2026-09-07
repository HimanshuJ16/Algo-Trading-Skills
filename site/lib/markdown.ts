import { unified, type Processor } from "unified";
import { VFile } from "vfile";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeSlug from "rehype-slug";
import rehypeStringify from "rehype-stringify";
import rehypeShikiFromHighlighter from "@shikijs/rehype/core";
import { createHighlighter, type Highlighter } from "shiki";
import { visit } from "unist-util-visit";
import type { Root as MdastRoot, Parent as MdastParent } from "mdast";
import type { Root as HastRoot, Element } from "hast";
import { BASE_PATH, repoBlob } from "./site";

export type TocEntry = { id: string; title: string };

type DocData = {
  skillNames: ReadonlySet<string>;
  currentSkill?: string;
  docDir?: string;
  toc: TocEntry[];
  found: Set<string>;
};

/**
 * One processor and one Shiki highlighter for the whole build.
 *
 * Constructing them per document turns a 500-page export into a multi-minute one, because
 * each `unified()` chain would load and compile the syntax grammars again. Per-document
 * inputs travel on the VFile instead of in plugin options.
 */

// Only what the library actually fences: 49 bash blocks, 6 text, 4 python.
const LANGUAGES = ["bash", "python", "text"];

let highlighterPromise: Promise<Highlighter> | null = null;

function getHighlighter(): Promise<Highlighter> {
  highlighterPromise ??= createHighlighter({
    themes: ["github-light", "github-dark-default"],
    langs: LANGUAGES,
  });
  return highlighterPromise;
}

function data(file: VFile): DocData {
  return file.data.doc as DocData;
}

/**
 * Skill documents refer to each other as backticked slugs rather than links, because the
 * Markdown is written for agents reading the raw file. On the site those slugs become real
 * navigation, which is most of the point of a browsable catalog.
 */
function remarkLinkSkillRefs() {
  return (tree: MdastRoot, file: VFile) => {
    const { skillNames, currentSkill, found } = data(file);
    visit(tree, "inlineCode", (node, index, parent) => {
      if (!parent || index === null || index === undefined) return;
      if ((parent as { type: string }).type === "link") return;
      const slug = node.value.trim();
      if (!skillNames.has(slug)) return;
      found.add(slug);
      if (slug === currentSkill) return;
      (parent as MdastParent).children[index] = {
        type: "link",
        url: `${BASE_PATH}/skills/${slug}/`,
        children: [{ type: "inlineCode", value: slug }],
        data: { hProperties: { className: ["xref"] } },
      } as MdastParent["children"][number];
    });
  };
}

/** Point relative links (references/standards.md, scripts/foo.py) at the repository. */
function remarkResolveRelativeLinks() {
  return (tree: MdastRoot, file: VFile) => {
    const { docDir } = data(file);
    if (!docDir) return;
    visit(tree, "link", (node) => {
      const url = node.url;
      if (!url || /^(https?:|mailto:|#|\/)/.test(url)) return;
      node.url = repoBlob(`${docDir}/${url}`.replace(/\/\.\//g, "/"));
    });
  };
}

/**
 * Every SKILL.md carries the same seven level-2 headings, validated in CI. Wrapping each
 * heading and its content in a <section> turns that guarantee into structure the page can
 * style and address: numbered sections, a distinct treatment for "When NOT to Use", and a
 * contents rail with a fixed set of positions. Any extra heading a skill adds is wrapped
 * the same way, so nothing depends on there being exactly seven.
 */
const KNOWN_SECTIONS = new Set([
  "when-to-use",
  "when-not-to-use",
  "prerequisites",
  "workflow",
  "common-pitfalls",
  "verification",
  "related-skills",
]);

function rehypeSections() {
  return (tree: HastRoot) => {
    const out: HastRoot["children"] = [];
    let current: Element | null = null;
    for (const node of tree.children) {
      if (node.type === "element" && node.tagName === "h2") {
        const id = String(node.properties?.id ?? "");
        current = {
          type: "element",
          tagName: "section",
          properties: {
            className: ["doc-section", KNOWN_SECTIONS.has(id) ? "doc-section-known" : "doc-section-extra"],
            dataSection: id,
            "aria-labelledby": id || undefined,
          },
          children: [node],
        };
        out.push(current);
        continue;
      }
      if (node.type === "doctype") continue;
      if (current) current.children.push(node);
      else out.push(node);
    }
    tree.children = out;

    // A slug named inside "When NOT to Use" is a handoff: the case this skill excludes and
    // the skill that owns it. It reads as a pointer, not as a citation.
    for (const section of out) {
      if (section.type !== "element" || section.properties?.dataSection !== "when-not-to-use") continue;
      visit(section, "element", (node: Element) => {
        if (node.tagName !== "a") return;
        const cls = node.properties?.className;
        if (Array.isArray(cls) && cls.includes("xref")) node.properties!.className = [...cls, "handoff"];
      });
    }
  };
}

/** Wrap each fenced block so the page can add a language label and a copy control. */
function rehypeCodeFrames() {
  return (tree: HastRoot) => {
    visit(tree, "element", (node: Element, index, parent) => {
      if (node.tagName !== "pre" || !parent || index === undefined || index === null) return;
      const code = node.children.find(
        (c): c is Element => c.type === "element" && c.tagName === "code",
      );
      if (!code) return;
      const classes = code.properties?.className;
      const lang = Array.isArray(classes)
        ? String(classes.find((c) => String(c).startsWith("language-")) ?? "").replace("language-", "")
        : "";
      const frame: Element = {
        type: "element",
        tagName: "div",
        properties: { className: ["codeframe"], dataLang: lang || "text" },
        children: [
          {
            type: "element",
            tagName: "div",
            properties: { className: ["codeframe-bar"], ariaHidden: "true" },
            children: [
              {
                type: "element",
                tagName: "span",
                properties: { className: ["codeframe-lang"] },
                children: [{ type: "text", value: lang || "text" }],
              },
            ],
          },
          {
            type: "element",
            tagName: "button",
            properties: {
              type: "button",
              className: ["codeframe-copy"],
              dataCopy: "",
              ariaLabel: "Copy code",
            },
            children: [{ type: "text", value: "copy" }],
          },
          node,
        ],
      };
      (parent as Element).children[index] = frame;
    });
  };
}

function rehypeCollectToc() {
  return (tree: HastRoot, file: VFile) => {
    const { toc } = data(file);
    visit(tree, "element", (node: Element) => {
      if (node.tagName !== "h2") return;
      const id = String(node.properties?.id ?? "");
      if (!id) return;
      toc.push({ id, title: textOf(node) });
    });
  };
}

function rehypeExternalLinks() {
  return (tree: HastRoot) => {
    visit(tree, "element", (node: Element) => {
      if (node.tagName !== "a") return;
      const href = String(node.properties?.href ?? "");
      if (!href.startsWith("http")) return;
      node.properties = {
        ...node.properties,
        target: "_blank",
        rel: ["noreferrer", "noopener"],
      };
    });
  };
}

export function textOf(node: Element | HastRoot): string {
  let out = "";
  visit(node, "text", (t) => {
    out += t.value;
  });
  return out.trim();
}

type AnyProcessor = Processor<MdastRoot, MdastRoot, HastRoot, HastRoot, string>;

let processorPromise: Promise<AnyProcessor> | null = null;

async function getProcessor(): Promise<AnyProcessor> {
  processorPromise ??= (async () => {
    const highlighter = await getHighlighter();
    return unified()
      .use(remarkParse)
      .use(remarkGfm)
      .use(remarkResolveRelativeLinks)
      .use(remarkLinkSkillRefs)
      .use(remarkRehype, { allowDangerousHtml: false })
      .use(rehypeSlug)
      .use(rehypeCollectToc)
      .use(rehypeExternalLinks)
      .use(rehypeCodeFrames)
      .use(rehypeShikiFromHighlighter, highlighter, {
        themes: { light: "github-light", dark: "github-dark-default" },
        defaultColor: "light",
        fallbackLanguage: "text",
      })
      .use(rehypeSections)
      .use(rehypeStringify)
      .freeze() as unknown as AnyProcessor;
  })();
  return processorPromise;
}

export async function renderMarkdown(
  markdown: string,
  options: { skillNames: ReadonlySet<string>; currentSkill?: string; docDir?: string },
): Promise<{ html: string; toc: TocEntry[]; crossRefs: string[] }> {
  const processor = await getProcessor();
  const doc: DocData = { ...options, toc: [], found: new Set() };
  const file = new VFile({ value: markdown, data: { doc } });

  const result = await processor.process(file);

  const crossRefs = [...doc.found].filter((s) => s !== options.currentSkill).sort();
  return { html: String(result), toc: doc.toc, crossRefs };
}
