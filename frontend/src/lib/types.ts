// Tipos del contrato HTTP de los servicios (espejo de sus esquemas Pydantic).
// Fuente de verdad: el OpenAPI de cada servicio (/openapi.json). Si cambia un esquema en el
// backend, se actualiza aquí; tests/contracts fija las rutas públicas de cada servicio.

export type Grade = "A" | "B" | "C" | "D" | "E";
export type Role = "user" | "admin";

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id: string | null;
  details?: Array<Record<string, unknown>> | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface User {
  id: string;
  email: string;
  role: Role;
}

export interface NumericFeatureInfo {
  name: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  required: boolean;
  in_formula: boolean;
}

export interface CategoricalFeatureInfo {
  name: string;
  label: string;
  max_length: number;
}

export interface ModelSchema {
  grades: Grade[];
  numeric: NumericFeatureInfo[];
  categorical: CategoricalFeatureInfo[];
}

export interface Prediction {
  id: string;
  grade: Grade;
  probabilities: Record<string, number>;
  model_version: string;
  inference_ms: number;
  input: Record<string, number | string>;
  created_at: string;
}

export interface History {
  items: Prediction[];
  limit: number;
  offset: number;
}

export interface ModelInfo {
  version: string;
  algorithm: string;
  dataset_version: string;
  quality_policy_version: string | null;
  trained_at: string | null;
  activated_at: string;
}

export interface ClassMetrics {
  precision: number;
  recall: number;
  f1: number;
  support: number;
}

export interface EvaluationReport {
  n: number;
  accuracy: number;
  f1_macro: number;
  precision_macro: number;
  recall_macro: number;
  per_class: Record<string, ClassMetrics>;
  confusion_matrix: { labels: string[]; matrix: number[][] };
}

export interface GateCheck {
  name: string;
  passed: boolean;
  blocking: boolean;
  value: number | null;
  threshold: number | null;
  detail: string;
}

export interface ModelMetrics {
  version: string;
  quality_policy_version: string | null;
  metrics: { validation?: EvaluationReport; test?: EvaluationReport | null };
  gate: { policy_version: string; passed: boolean; checks: GateCheck[] } | null;
}

export interface ModelVersionSummary {
  version: string;
  status: "candidate" | "evaluating" | "approved" | "active" | "rejected" | "archived";
  algorithm: string;
  dataset_version: string;
  f1_macro_validation: number | null;
  gate_passed: boolean | null;
  created_at: string;
  updated_at: string;
}

export interface Dataset {
  version: string;
  file_name: string;
  sha256: string;
  row_count: number;
  class_distribution: Record<string, number>;
  created_at: string;
}

export interface Job {
  id: string;
  dataset_version: string;
  config_name: string;
  status: "queued" | "running" | "succeeded" | "failed";
  model_version: string | null;
  activation: "not_requested" | "succeeded" | "failed" | "skipped";
  error: string | null;
  attempts: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
