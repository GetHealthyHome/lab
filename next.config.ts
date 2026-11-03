import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    serverActions: { bodySizeLimit: "12mb" }, // phone photos uploaded from the admin form
  },
  images: {
    remotePatterns: [{ protocol: "https", hostname: new URL(process.env.NEXT_PUBLIC_SUPABASE_URL!).hostname, pathname: "/storage/v1/object/public/**" }],
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
