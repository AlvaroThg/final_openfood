"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";

import { ErrorBox } from "@/components/ui";
import { useAuth } from "@/lib/auth";

const MIN_PASSWORD = 12; // misma política que auth_service (la valida el backend)

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register(email, password);
      router.replace("/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card auth-card" onSubmit={submit}>
      <h1>Crear cuenta</h1>
      <p className="lead">La contraseña debe tener al menos {MIN_PASSWORD} caracteres.</p>
      <div className="field">
        <label htmlFor="email">Email</label>
        <input id="email" type="email" autoComplete="email" required value={email}
               onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="password">Contraseña</label>
        <input id="password" type="password" autoComplete="new-password" required
               minLength={MIN_PASSWORD} maxLength={128} value={password}
               onChange={(e) => setPassword(e.target.value)} />
      </div>
      <ErrorBox error={error} />
      <div className="actions">
        <button type="submit" disabled={busy}>{busy ? "Creando…" : "Crear cuenta"}</button>
        <Link href="/login">Ya tengo cuenta</Link>
      </div>
    </form>
  );
}
