"use client";

// Formulario de predicción generado desde GET /api/model/schema (contrato del backend): los
// nombres, unidades, rangos y obligatorios vienen de nutriscore.schema, no se duplican aquí.
// La validación del navegador es solo ayuda; la que vale es la del backend.
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorBox, GradeBadge, ProbabilityBars, RequireAuth } from "@/components/ui";
import { api } from "@/lib/http";
import type { ModelSchema, NumericFeatureInfo, Prediction } from "@/lib/types";

function NumericField({ feature, value, onChange }: {
  feature: NumericFeatureInfo;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="field">
      <label htmlFor={feature.name}>
        {feature.label} <span className="muted">({feature.unit})</span>
        {feature.required && <span className="req"> *</span>}
      </label>
      <input id={feature.name} type="number" inputMode="decimal" step="any"
             min={feature.min} max={feature.max} required={feature.required}
             value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function PredictForm() {
  const [schema, setSchema] = useState<ModelSchema | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Prediction | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<ModelSchema>("/api/model/schema", { auth: false }).then(setSchema).catch(setError);
  }, []);

  if (!schema) return error ? <ErrorBox error={error} /> : <p className="muted">Cargando…</p>;

  const required = schema.numeric.filter((f) => f.required);
  const optional = schema.numeric.filter((f) => !f.required);
  const set = (name: string) => (value: string) => setValues((v) => ({ ...v, [name]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!schema) return;
    setBusy(true);
    setError(null);
    const body: Record<string, number | string> = {};
    for (const feature of schema.numeric) {
      const raw = values[feature.name];
      if (raw !== undefined && raw !== "") body[feature.name] = Number(raw);
    }
    for (const feature of schema.categorical) {
      const raw = values[feature.name]?.trim();
      if (raw) body[feature.name] = raw;
    }
    try {
      setResult(await api<Prediction>("/api/predict", { method: "POST", body }));
    } catch (err) {
      setResult(null);
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="card" onSubmit={submit}>
        <h2>Nutrientes por 100 g</h2>
        <div className="grid">
          {required.map((f) => (
            <NumericField key={f.name} feature={f} value={values[f.name] ?? ""} onChange={set(f.name)} />
          ))}
        </div>
        <details>
          <summary>Más datos (opcionales: mejoran la estimación)</summary>
          <div className="grid">
            {optional.map((f) => (
              <NumericField key={f.name} feature={f} value={values[f.name] ?? ""} onChange={set(f.name)} />
            ))}
            {schema.categorical.map((f) => (
              <div className="field" key={f.name}>
                <label htmlFor={f.name}>{f.label}</label>
                <input id={f.name} maxLength={f.max_length} value={values[f.name] ?? ""}
                       onChange={(e) => set(f.name)(e.target.value)} placeholder="p. ej. Sugary snacks" />
              </div>
            ))}
          </div>
        </details>
        <ErrorBox error={error} />
        <div className="actions">
          <button type="submit" disabled={busy}>{busy ? "Calculando…" : "Predecir Nutri-Score"}</button>
          <span className="muted">* obligatorio</span>
        </div>
      </form>

      {result && (
        <section className="card" aria-live="polite">
          <h2>Resultado</h2>
          <div className="result">
            <GradeBadge grade={result.grade} />
            <ProbabilityBars probabilities={result.probabilities} />
          </div>
          <p className="muted">
            Modelo {result.model_version} · {result.inference_ms.toFixed(1)} ms · guardado en tu historial
          </p>
        </section>
      )}
    </>
  );
}

export default function Home() {
  return (
    <RequireAuth>
      <h1>Predecir el Nutri-Score</h1>
      <p className="lead">
        Ingresa los valores de la etiqueta nutricional. El modelo estima el grado (A = mejor, E = peor).
      </p>
      <PredictForm />
    </RequireAuth>
  );
}
