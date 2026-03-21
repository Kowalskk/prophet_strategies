import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: "http://95.179.243.31:8000/api/v1/:path*",
      },
    ];
  },
};

export default nextConfig;
