"use client";

// Administración: versiones de modelo (activar / volver atrás), datasets y entrenamientos.
// Solo administradores: el backend lo exige en cada endpoint (403 para cualquier otro rol).
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorBox, RequireAuth, formatDate } from "@/components/ui";
import { api } from "@/lib/http";
import type { Dataset, Job, ModelVersionSummary } from "@/lib/types";

const ACTIVATABLE = new Set(["approved", "archived"]);

function AdminPanel() {
  const [models, setModels] = useState<ModelVersionSummary[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [configs, setConfigs] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [fileName, setFileName] = useState("clean_v1.parquet");
  const [jobDataset, setJobDataset] = useState("");
  const [jobConfig, setJobConfig] = useState("lightgbm");

  const reload = useCallback(async () => {
    try {
      const [m, d, j, c] = await Promise.all([
        api<ModelVersionSummary[]>("/api/admin/models"),
        api<Dataset[]>("/retrain/datasets"),
        api<Job[]>("/retrain/jobs"),
        api<string[]>("/retrain/configs"),
      ]);
      setModels(m);
      setDatasets(d);
      setJobs(j);
      setConfigs(c);
      setJobDataset((current) => current || d[0]?.version || "");
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  async function run(action: () => Promise<string>) {
    setNotice(null);
    setError(null);
    try {
      setNotice(await action());
      await reload();
    } catch (err) {
      setError(err);
    }
  }

  const activate = (version: string) =>
    run(async () => {
      const r = await api<{ changed: boolean; previous: string | null }>(
        `/api/admin/models/${encodeURIComponent(version)}/activate`, { method: "POST" });
      return r.changed ? `Versión ${version} activa (antes: ${r.previous ?? "ninguna"}).` : `${version} ya estaba activa.`;
    });

  const registerDataset = (event: FormEvent) => {
    event.preventDefault();
    run(async () => {
      const d = await api<Dataset>("/retrain/datasets", { method: "POST", body: { file_name: fileName } });
      return `Dataset registrado como ${d.version} (${d.row_count.toLocaleString("es")} filas).`;
    });
  };

  const createJob = (event: FormEvent) => {
    event.preventDefault();
    run(async () => {
      await api<Job>("/retrain/jobs", {
        method: "POST",
        body: { dataset_version: jobDataset, config_name: jobConfig },
      });
      return "Entrenamiento encolado. El worker lo tomará en unos segundos.";
    });
  };

  return (
    <>
      <ErrorBox error={error} />
      {notice && <div className="alert ok" role="status">{notice}</div>}

      <section className="card">
        <h2>Versiones de modelo</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Versión</th><th>Estado</th><th>Algoritmo</th><th>Dataset</th><th className="num">F1 macro (val.)</th><th>Calidad</th><th>Creado</th><th /></tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.version}>
                  <td>{m.version}</td>
                  <td><span className={`pill ${m.status === "active" ? "good" : m.status === "rejected" ? "bad" : ""}`}>{m.status}</span></td>
                  <td>{m.algorithm}</td>
                  <td>{m.dataset_version}</td>
                  <td className="num">{m.f1_macro_validation === null ? "—" : m.f1_macro_validation.toFixed(3)}</td>
                  <td>{m.gate_passed === null ? "—" : m.gate_passed ? "aprobado" : "rechazado"}</td>
                  <td>{formatDate(m.created_at)}</td>
                  <td>
                    {ACTIVATABLE.has(m.status) && (
                      <button className="secondary" onClick={() => activate(m.version)}>
                        {m.status === "archived" ? "Volver a esta" : "Activar"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <h2>Entrenar</h2>
        <form className="grid" onSubmit={createJob}>
          <div className="field">
            <label htmlFor="job-dataset">Dataset</label>
            <select id="job-dataset" value={jobDataset} onChange={(e) => setJobDataset(e.target.value)} required>
              {datasets.map((d) => <option key={d.version} value={d.version}>{d.version} · {d.file_name}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="job-config">Configuración</label>
            <select id="job-config" value={jobConfig} onChange={(e) => setJobConfig(e.target.value)}>
              {configs.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="field" style={{ alignSelf: "end" }}>
            <button type="submit" disabled={!jobDataset}>Encolar entrenamiento</button>
          </div>
        </form>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Creado</th><th>Dataset</th><th>Config.</th><th>Estado</th><th>Modelo</th><th>Activación</th><th>Error</th></tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>{formatDate(j.created_at)}</td>
                  <td>{j.dataset_version}</td>
                  <td>{j.config_name}</td>
                  <td>{j.status}</td>
                  <td>{j.model_version ?? "—"}</td>
                  <td>{j.activation}</td>
                  <td style={{ whiteSpace: "normal" }}>{j.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="actions"><button className="secondary" onClick={reload}>Actualizar</button></div>
      </section>

      <section className="card">
        <h2>Registrar dataset</h2>
        <p className="muted">El archivo .parquet debe estar en el volumen de datasets (lo deja ahí el comando bootstrap).</p>
        <form className="actions" onSubmit={registerDataset}>
          <input aria-label="Archivo" value={fileName} onChange={(e) => setFileName(e.target.value)} style={{ maxWidth: 280 }} />
          <button type="submit">Registrar</button>
        </form>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Versión</th><th>Archivo</th><th className="num">Filas</th><th>SHA-256</th><th>Registrado</th></tr></thead>
            <tbody>
              {datasets.map((d) => (
                <tr key={d.version}>
                  <td>{d.version}</td><td>{d.file_name}</td>
                  <td className="num">{d.row_count.toLocaleString("es")}</td>
                  <td><code>{d.sha256.slice(0, 12)}…</code></td>
                  <td>{formatDate(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

export default function AdminPage() {
  return (
    <RequireAuth role="admin">
      <h1>Administración</h1>
      <p className="lead">Solo se activan versiones aprobadas por la política de calidad; la activación la verifica ia_backend.</p>
      <AdminPanel />
    </RequireAuth>
  );
}
