const KEY = "sf_api_key";
export class ApiError extends Error { constructor(status, detail) { super(typeof detail === "string" ? detail : JSON.stringify(detail)); this.status = status; this.detail = detail; } }
export const session = {
  get key() { try { return sessionStorage.getItem(KEY); } catch { return null; } },
  set key(v) { try { v ? sessionStorage.setItem(KEY, v) : sessionStorage.removeItem(KEY); } catch { /* storage unavailable: session lasts for this page only */ } },
};
let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

export async function api(method, path, body, opts = {}) {
  const headers = { Accept: "application/json" };
  if (session.key) headers.Authorization = "Bearer " + session.key;
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload, signal: opts.signal });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    if (res.status === 401 && !opts.quiet) onUnauthorized();
    const detail = data && typeof data === "object" ? (data.detail ?? data.error ?? data) : data;
    throw new ApiError(res.status, Array.isArray(detail) ? detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ") : detail);
  }
  return data;
}
export const get = (p, o) => api("GET", p, undefined, o);
export const post = (p, b, o) => api("POST", p, b ?? {}, o);
