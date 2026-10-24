/** Thin fetch wrapper. Errors carry the server's message (never swallowed). */

export class ApiError extends Error {
  status: number;
  details: unknown;
  constructor(status: number, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  const body = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = typeof body === "object" && body && "message" in body ? String((body as any).message) : `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, typeof body === "object" ? (body as any).details : body);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, params?: Record<string, string | number | undefined>) => {
    const q = params
      ? "?" + Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&")
      : "";
    return fetch(`${BASE}${path}${q}`).then((r) => handle<T>(r));
  },
  post: <T>(path: string, body?: unknown) =>
    fetch(`${BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) }).then((r) => handle<T>(r)),
  patch: <T>(path: string, body: unknown) =>
    fetch(`${BASE}${path}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  del: (path: string) => fetch(`${BASE}${path}`, { method: "DELETE" }).then((r) => handle<void>(r)),
  upload: <T>(path: string, form: FormData) => fetch(`${BASE}${path}`, { method: "POST", body: form }).then((r) => handle<T>(r)),
  url: (path: string) => `${BASE}${path}`,
};
