/** @type {import('next').NextConfig} */

// Set NEXT_PUBLIC_BASE_PATH="/Algo-Trading-Skills" when deploying to GitHub Pages as a
// project site. Leave it unset for Vercel/Netlify or a custom domain at the root.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const nextConfig = {
  output: "export",
  basePath,
  trailingSlash: true,
  images: { unoptimized: true },
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: true },
  // The catalog reads ../index.json and ../skills/**/SKILL.md at build time.
  outputFileTracingRoot: process.cwd(),
  // The first page each export worker renders pays for parsing and highlighting all 500+
  // SKILL.md files; every page after it is served from that in-process cache. The 60s
  // default counts that one-off against the first page and fails it.
  staticPageGenerationTimeout: 600,
};

export default nextConfig;
