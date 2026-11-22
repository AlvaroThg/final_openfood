"use client";

import { useEffect, useState } from "react";

import { ErrorBox, RequireAuth, formatDate } from "@/components/ui";
import { api } from "@/lib/http";
import type { EvaluationReport, ModelInfo, ModelMetrics } from "@/lib/types";

const pct = (value: number) => `${(value * 100).toFixed(1)} %`;

function Report({ title, report }: { title: string; report: EvaluationReport }) {
  const labels = report.confusion_matrix.labels;
  return (
    <section className="card">
      <h2>{title}</h2>
      <p className="muted">
        {report.n.toLocaleString("es")} productos · accuracy {pct(report.accuracy)} · F1 macro{" "}
        {pct(report.f1_macro)}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Grado</th>
              <th className="num">Precisión</th>
              <th className="num">Recall</th>
              <th className="num">F1</th>
              <th className="num">Soporte</th>
            </tr>
          </thead>
          <tbody>
            {labels.map((grade) => {
              const m = report.per_class[grade];
              return (
                <tr key={grade}>
                  <td>{grade}</td>
                  <td className="num">{pct(m.precision)}</td>
                  <td className="num">{pct(m.recall)}</td>
                  <td className="num">{pct(m.f1)}</td>
                  <td className="num">{m.support.toLocaleString("es")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <h2>Matriz de confusión</h2>
      <p className="muted">Filas: grado real · columnas: grado predicho.</p>
      <div className="table-wrap">
        <table className="matrix">
          <thead>
            <tr>
              <th />
              {labels.map((l) => <th key={l}>{l}</th>)}
            </tr>
          </thead>
          <tbody>
            {report.confusion_matrix.matrix.map((row, i) => (
              <tr key={labels[i]}>
                <th>{labels[i]}</th>
                {row.map((count, j) => (
                  <td key={labels[j]} style={i === j ? { fontWeight: 700 } : undefined}>
                    {count.toLocaleString("es")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ModelDetails() {
  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [metrics, setMetrics] = useState<ModelMetrics | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([api<ModelInfo>("/api/model/info"), api<ModelMetrics>("/api/model/metrics")])
      .then(([i, m]) => {
        setInfo(i);
        setMetrics(m);
      })
      .catch(setError);
  }, []);

  if (error) return <ErrorBox error={error} />;
  if (!info || !metrics) return <p className="muted">Cargando…</p>;

  return (
    <>
      <section className="card">
        <h2>Versión activa {info.version}</h2>
        <p>
          Algoritmo <strong>{info.algorithm}</strong> · dataset {info.dataset_version} · política{" "}
          {info.quality_policy_version ?? "—"}
        </p>
        <p className="muted">
          Entrenado: {formatDate(info.trained_at)} · activo desde: {formatDate(info.activated_at)}
        </p>
      </section>

      {metrics.gate && (
        <section className="card">
          <h2>Política de calidad {metrics.gate.policy_version}</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Criterio</th><th>Resultado</th><th className="num">Valor</th><th className="num">Umbral</th><th>Tipo</th></tr>
              </thead>
              <tbody>
                {metrics.gate.checks.map((c) => (
                  <tr key={c.name}>
                    <td>{c.name}</td>
                    <td><span className={`pill ${c.passed ? "good" : "bad"}`}>{c.passed ? "cumple" : "no cumple"}</span></td>
                    <td className="num">{c.value === null ? "—" : Number(c.value).toFixed(3)}</td>
                    <td className="num">{c.threshold === null ? "—" : Number(c.threshold).toFixed(3)}</td>
                    <td>{c.blocking ? "bloqueante" : "advertencia"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {metrics.metrics.validation && <Report title="Validación (decide la aprobación)" report={metrics.metrics.validation} />}
      {metrics.metrics.test && <Report title="Test (congelado, solo se reporta)" report={metrics.metrics.test} />}
    </>
  );
}

export default function ModelPage() {
  return (
    <RequireAuth>
      <h1>Modelo</h1>
      <p className="lead">Qué modelo responde tus predicciones y cómo se evaluó.</p>
      <ModelDetails />
    </RequireAuth>
  );
}
