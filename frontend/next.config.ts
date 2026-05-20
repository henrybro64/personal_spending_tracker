import type { NextConfig } from "next";

// /api/* is proxied by app/api/[...path]/route.ts (not rewrites) so we can set
// long timeouts for batch-imports. Set API_PROXY_TARGET when running dev/build.
const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
