"use client";

import { useCallback, useEffect, useState } from "react";

import { ErrorBox, GradeBadge, RequireAuth, formatDate } from "@/components/ui";
import { api } from "@/lib/http";
import type { History, Prediction } from "@/lib/types";

const PAGE = 20;
const SHOWN = ["energy_100g", "sugars_100g", "saturated_fat_100g", "salt_100g", "proteins_100g"];

function HistoryTable() {
  const [items, setItems] = useState<Prediction[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (offset: number) => {
    setBusy(true);
    try {
      const page = await api<History>(`/api/predictions?limit=${PAGE}&offset=${offset}`);
      setItems((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
      setDone(page.items.length < PAGE);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load(0);
  }, [load]);

  if (error) return <ErrorBox error={error} />;
  if (!busy && items.length === 0) return <p className="muted">Todavía no hiciste predicciones.</p>;

  return (
    <div className="card">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Grado</th>
              <th className="num">Calorías/Energía (kJ)</th>
              <th className="num">Azúcares</th>
              <th className="num">Grasas sat.</th>
              <th className="num">Sal</th>
              <th className="num">Proteínas</th>
              <th>Modelo</th>
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td>{formatDate(p.created_at)}</td>
                <td><GradeBadge grade={p.grade} size="small" /></td>
                {SHOWN.map((name) => (
                  <td className="num" key={name}>{p.input[name] ?? "—"}</td>
                ))}
                <td>{p.model_version}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!done && (
        <div className="actions">
          <button className="secondary" disabled={busy} onClick={() => load(items.length)}>
            {busy ? "Cargando…" : "Cargar más"}
          </button>
        </div>
      )}
    </div>
  );
}

export default function HistoryPage() {
  return (
    <RequireAuth>
      <h1>Mi historial</h1>
      <p className="lead">Cada predicción guarda la versión exacta del modelo que la produjo.</p>
      <HistoryTable />
    </RequireAuth>
  );
}
