import { Link } from "next-view-transitions";
import { CommandLine } from "@/components/copy-button";
import { ArrowRight } from "@/components/icons";
import { SITE } from "@/lib/site";

const AGENTS = ["Claude Code", "GitHub Copilot", "Codex CLI", "Cursor", "Gemini CLI", "Aider", "Cline", "Windsurf", "Continue"];

export function GetStarted({ total }: { total: number }) {
  return (
    <div className="grid min-w-0 gap-12 lg:grid-cols-12 lg:gap-16">
      <div className="lg:col-span-6">
        <p className="eyebrow">Get started</p>
        <h2 className="display display-lg mt-4 text-fg">
          Clone it. Point
          <br />
          your agent at it.
        </h2>
        <p className="mt-6 max-w-[44ch] text-[0.95rem] leading-relaxed text-muted text-pretty">
          The library needs no runtime and no service. It is Markdown and standalone Python,
          read by any tool that understands a <code className="mono text-fg">SKILL.md</code>.
          The repository ships instruction files for each of these:
        </p>
        <ul className="mt-5 flex flex-wrap gap-1.5">
          {AGENTS.map((a) => (
            <li key={a} className="chip">
              {a}
            </li>
          ))}
        </ul>
        <div className="mt-9 flex flex-wrap gap-3">
          <Link prefetch={false} href="/skills" className="btn btn-primary">
            Browse {total} skills
            <ArrowRight className="size-4" />
          </Link>
          <a href={SITE.repo} target="_blank" rel="noreferrer noopener" className="btn">
            View on GitHub
          </a>
        </div>
      </div>

      <div className="min-w-0 space-y-2.5 self-center lg:col-span-6">
        <CommandLine command={`git clone ${SITE.repo}.git`} />
        <CommandLine command="pip install -r requirements-dev.txt" note="pyyaml, numpy, pandas, scipy, pyotp, pytest" />
        <CommandLine command="python tools/validate_skills.py" note="the structural contract" />
        <CommandLine command="python tools/run_all_tests.py" note="every suite, one subprocess each" />
      </div>
    </div>
  );
}
