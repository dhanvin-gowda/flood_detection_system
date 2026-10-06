import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  rewrites() {
    return [
      {
        source: "/backend/:path*",
        // The API mounts every route under /backend, so the prefix has to be
        // restated here. ":path*" on its own replaces the whole path, which
        // silently forwarded /backend/geocode to /geocode and 404'd.
        destination: "http://127.0.0.1:8000/backend/:path*",
      },
    ];
  },
};

export default nextConfig;
