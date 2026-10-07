import type { NextConfig } from "next";

/** Taille maximale d'un fichier (Mo) — identique à `ANON_MAX_FILE_MB` côté serveur. */
const MAX_FILE_MB = 100;

const nextConfig: NextConfig = {
  experimental: {
    // En développement, /api/* est relayé vers Flask par Next.js (voir
    // rewrites) : sans ces réglages, le corps des requêtes est tronqué
    // au-delà de 10 Mo et une requête silencieuse plus de 30 s est coupée.
    // Marge de 5 Mo pour l'enveloppe multipart.
    proxyClientMaxBodySize: `${MAX_FILE_MB + 5}mb`,
    proxyTimeout: 10 * 60 * 1000,
  },
  async rewrites() {
    // En production, Nginx route /api/* vers Gunicorn (deploy/nginx-anonymiser.conf).
    if (process.env.NODE_ENV === "production") {
      return [];
    }
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:5328/api/:path*",
      },
    ];
  },
};

export default nextConfig;
