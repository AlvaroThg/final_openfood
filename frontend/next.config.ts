import type { NextConfig } from "next";

/**
 * El frontend es el ÚNICO punto expuesto: el navegador habla solo con localhost:3000 y este
 * servidor reenvía rutas EXPLÍCITAS a los servicios por la red interna de Docker.
 *
 * - Un solo origen: no hace falta CORS y la cookie del refresh token es de primera parte.
 * - Lista blanca: nunca se reenvían /internal/* (activación por servicio) ni /auth/token
 *   (credenciales de servicio), aunque el navegador las pida.
 * - Los nombres de host son los de docker-compose (red interna), fijos al compilar.
 */
const AUTH = process.env.AUTH_SERVICE_URL ?? "http://auth_service:8000";
const IA = process.env.IA_BACKEND_URL ?? "http://ia_backend:8000";
const RETRAIN = process.env.RETRAIN_API_URL ?? "http://retrain_api:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    const auth = ["register", "login", "refresh", "logout", "me"].map((name) => ({
      source: `/auth/${name}`,
      destination: `${AUTH}/auth/${name}`,
    }));
    return [
      ...auth,
      { source: "/api/predict", destination: `${IA}/predict` },
      { source: "/api/predictions", destination: `${IA}/predictions` },
      { source: "/api/model/:part(schema|info|metrics)", destination: `${IA}/model/:part` },
      { source: "/api/admin/models", destination: `${IA}/admin/models` },
      {
        source: "/api/admin/models/:version/activate",
        destination: `${IA}/admin/models/:version/activate`,
      },
      { source: "/retrain/:part(configs|datasets|jobs)", destination: `${RETRAIN}/retrain/:part` },
      { source: "/retrain/jobs/:id", destination: `${RETRAIN}/retrain/jobs/:id` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
