"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/http";
import type { Grade, Role } from "@/lib/types";

const GRADE_COLORS: Record<Grade, string> = {
  A: "var(--grade-a)",
  B: "var(--grade-b)",
  C: "var(--grade-c)",
  D: "var(--grade-d)",
  E: "var(--grade-e)",
};

export function GradeBadge({ grade, size = "big" }: { grade: Grade; size?: "big" | "small" }) {
  return (
    <span className={`badge ${size} grade-${grade}`} role="img" aria-label={`Nutri-Score ${grade}`}>
      {grade}
    </span>
  );
}

/** Probabilidad de cada grado: barra (color del grado) + valor en texto. */
export function ProbabilityBars({ probabilities }: { probabilities: Record<string, number> }) {
  const grades = Object.keys(GRADE_COLORS) as Grade[];
  return (
    <div className="bars" aria-label="Probabilidad por grado">
      {grades.map((grade) => {
        const value = probabilities[grade] ?? 0;
        return (
          <div className="bar-row" key={grade}>
            <strong>{grade}</strong>
            <div className="bar-track">
              <div
                className="bar-fill"
                style={{ width: `${(value * 100).toFixed(1)}%`, background: GRADE_COLORS[grade] }}
              />
            </div>
            <span className="bar-value">{(value * 100).toFixed(1)} %</span>
          </div>
        );
      })}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const apiError = error instanceof ApiError ? error : null;
  const message = apiError ? apiError.message : "No se pudo conectar con el servidor.";
  const details = (apiError?.details ?? [])
    .map((d) => (typeof d.message === "string" ? d.message : typeof d.problem === "string" ? d.problem : null))
    .filter((d): d is string => d !== null);
  return (
    <div className="alert error" role="alert">
      {message}
      {details.length > 0 && (
        <ul>
          {details.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const LINKS = [
  { href: "/", label: "Predecir" },
  { href: "/historial", label: "Historial" },
  { href: "/modelo", label: "Modelo" },
];

export function Nav() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  return (
    <nav className="nav">
      <Link href="/" className="brand">
        NutriScore
      </Link>
      {user &&
        [...LINKS, ...(user.role === "admin" ? [{ href: "/admin", label: "Administración" }] : [])].map(
          (link) => (
            <Link key={link.href} href={link.href} className={pathname === link.href ? "active" : ""}>
              {link.label}
            </Link>
          ),
        )}
      <span className="spacer" />
      {user ? (
        <>
          <span className="who">
            {user.email} · {user.role === "admin" ? "admin" : "usuario"}
          </span>
          <button
            className="secondary"
            onClick={async () => {
              await logout();
              router.replace("/login");
            }}
          >
            Salir
          </button>
        </>
      ) : (
        <>
          <Link href="/login">Ingresar</Link>
          <Link href="/registro">Crear cuenta</Link>
        </>
      )}
    </nav>
  );
}

/**
 * Solo experiencia de usuario: redirige a /login si no hay sesión. La autorización real la
 * hace cada servicio (un usuario que fuerce la URL recibe 401/403 del backend).
 */
export function RequireAuth({ children, role }: { children: ReactNode; role?: Role }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) return <p className="muted">Cargando…</p>;
  if (role && user.role !== role) {
    return <div className="alert error">Esta sección es solo para administradores.</div>;
  }
  return <>{children}</>;
}

export function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString("es") : "—";
}
