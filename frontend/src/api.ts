// Single API client for the frontend. Every call goes to the backend via
// the same-origin `/api/*` path. In production nginx proxies `/api/` to
// the backend; in `vite dev` the dev server does the same. The frontend
// MUST NOT call the model services directly — only the backend talks to
// `embedding-model` and `generation-model` on the internal `model_net`.

const API_BASE = "/api";

export interface Stats {
  flights: number;
  flight_logs: number;
  incidents: number;
  embedded_logs: number;
  unembedded_logs: number;
}

export interface Health {
  status: string;
  database: string;
}

export interface FlightSummary {
  id: number;
  flight_number: string;
  airline: string;
  origin: string;
  destination: string;
  scheduled_departure: string;
  status: string;
}

export interface CreateLogRequest {
  flight_id: number;
  log_type: string;
  source_system: string;
  severity: string;
  message: string;
  log_time?: string;
  metadata?: Record<string, unknown>;
}

export interface CreateLogResponse {
  id: number;
  flight_id: number;
  log_time: string;
  log_type: string;
  source_system: string;
  severity: string;
  message: string;
  embedded: boolean;
  embedding_error?: string | null;
}

export interface VectorSearchRequest {
  query: string;
  top_k: number;
  filters?: Record<string, string | number>;
}

export interface VectorSearchResult {
  id: number;
  score: number | null;
  distance: number | null;
  text: string;
  metadata: Record<string, unknown>;
}

export interface VectorSearchResponse {
  query: string;
  resource: string;
  top_k: number;
  results: VectorSearchResult[];
}

export interface QueryEvidence {
  type: string;
  id?: unknown;
  message?: string | null;
  [key: string]: unknown;
}

export interface QueryResponse {
  answer: string;
  evidence: QueryEvidence[];
  agent_trace: Record<string, unknown>;
}

export interface IngestResponse {
  scanned: number;
  embedded: number;
  errors: string[];
}

async function request<T>(
  path: string,
  init: Omit<RequestInit, "body"> & { body?: unknown } = {}
): Promise<T> {
  const { body: rawBody, headers: rawHeaders, ...rest } = init;
  const headers = new Headers(rawHeaders);
  let body: BodyInit | undefined;
  if (rawBody !== undefined && rawBody !== null) {
    if (typeof rawBody === "string" || rawBody instanceof FormData) {
      body = rawBody as BodyInit;
    } else {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(rawBody);
    }
  }
  const resp = await fetch(`${API_BASE}${path}`, { ...rest, headers, body });
  if (!resp.ok) {
    let detail: string;
    try {
      const data = await resp.json();
      detail =
        typeof data?.detail === "string"
          ? data.detail
          : JSON.stringify(data?.detail ?? data);
    } catch {
      detail = await resp.text();
    }
    throw new Error(`${resp.status} ${resp.statusText}: ${detail}`);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  stats: () => request<Stats>("/stats"),
  listFlights: (search?: string, limit = 25) => {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    params.set("limit", String(limit));
    return request<{ results: FlightSummary[] }>(`/flights?${params}`);
  },
  createLog: (req: CreateLogRequest) =>
    request<CreateLogResponse>("/logs", { method: "POST", body: req }),
  vectorSearch: (req: VectorSearchRequest) =>
    request<VectorSearchResponse>("/search/vector", {
      method: "POST",
      body: req,
    }),
  query: (question: string, top_k?: number) =>
    request<QueryResponse>("/query", {
      method: "POST",
      body: { question, top_k },
    }),
  ingest: (limit?: number) =>
    request<IngestResponse>("/ingest", {
      method: "POST",
      body: limit !== undefined ? { limit } : {},
    }),
};
