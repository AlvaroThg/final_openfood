// Cliente HTTP centralizado (AGENTS.md §9).
//
// - El access token vive SOLO en memoria (nunca en localStorage). El refresh token es una
//   cookie httpOnly que JavaScript no puede leer; el navegador la envía sola a /auth/*.
// - Ante un 401 por token ausente o vencido, se renueva la sesión UNA vez y se reintenta.
// - Los refrescos simultáneos comparten una sola petición: si dos llamadas refrescaran a la
//   vez con el mismo token, el backend lo interpretaría como reutilización y cerraría la sesión.
import type { ApiErrorBody, TokenResponse } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Array<Record<string, unknown>> = [],
    readonly requestId: string | null = null,
  ) {
    super(message);
  }
}

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** Renueva el access token con la cookie de refresh. Devuelve si hay sesión. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const response = await fetch("/auth/refresh", { method: "POST" });
      if (!response.ok) {
        accessToken = null;
        return false;
      }
      accessToken = ((await response.json()) as TokenResponse).access_token;
      return true;
    } catch {
      accessToken = null;
      return false;
    }
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: ApiErrorBody };
    if (body.error) {
      return new ApiError(
        response.status,
        body.error.code,
        body.error.message,
        body.error.details ?? [],
        body.error.request_id,
      );
    }
  } catch {
    // cuerpo vacío o no JSON: se informa el estado HTTP
  }
  return new ApiError(response.status, "HTTP_ERROR", `Error ${response.status}`);
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  auth?: boolean;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true } = options;
  const send = () =>
    fetch(path, {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(auth && accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

  let response = await send();
  if (response.status === 401 && auth) {
    const error = await toApiError(response.clone());
    if (["TOKEN_EXPIRED", "TOKEN_MISSING"].includes(error.code) && (await refreshSession())) {
      response = await send();
    }
  }
  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Mensaje legible para mostrar en la interfaz. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "No se pudo conectar con el servidor.";
}
