import type { NextConfig } from "next";

const RAILWAY_API = "https://web-production-5f6e1.up.railway.app";

const nextConfig: NextConfig = {
  rewrites: async () => [
    {
      source: "/api/:path*",
      destination: `${RAILWAY_API}/api/:path*`,
    },
  ],
};

export default nextConfig;
