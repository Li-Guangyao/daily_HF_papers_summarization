// Shared helpers for the read-only API (functions/api/*). Kept outside functions/ so it is not a route.

export interface Env {
  DB: D1Database;
}

export function json(data: unknown, status = 200, maxAge = 0): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": maxAge > 0 ? `public, max-age=${maxAge}` : "no-store",
    },
  });
}

// Serve from Cloudflare's edge cache when possible, so D1 is only read on a miss.
export async function cached(
  ctx: EventContext<Env, string, unknown>,
  maxAge: number,
  build: () => Promise<Response>,
): Promise<Response> {
  const { hostname } = new URL(ctx.request.url);
  if (hostname === "localhost" || hostname === "127.0.0.1") return build(); // fresh data while developing
  const cache = (caches as unknown as { default: Cache }).default;
  const key = new Request(ctx.request.url, { method: "GET" });
  const hit = await cache.match(key);
  if (hit) return hit;
  const res = await build();
  if (res.status === 200 && maxAge > 0) ctx.waitUntil(cache.put(key, res.clone()));
  return res;
}

export const isDate = (s: string | null): s is string => !!s && /^\d{4}-\d{2}-\d{2}$/.test(s);

// Recent dates can still change (late papers, upvotes); older ones are effectively immutable.
export function ttlForDate(day: string): number {
  const ageDays = (Date.now() - Date.parse(`${day}T00:00:00Z`)) / 86_400_000;
  return ageDays < 5 ? 300 : 86_400;
}

export function parseJsonArray(value: unknown): string[] {
  if (typeof value !== "string") return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
