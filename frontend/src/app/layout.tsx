import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Nav } from "@/components/ui";
import { AuthProvider } from "@/lib/auth";

import "./globals.css";

export const metadata: Metadata = {
  title: "NutriScore",
  description: "Predicción del Nutri-Score a partir de los nutrientes de un producto",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body>
        <AuthProvider>
          <Nav />
          <main>{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
