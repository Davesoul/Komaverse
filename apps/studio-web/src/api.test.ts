/* Project cache contract: tab switches refetch through an 8s TTL with
 * in-flight dedupe; any write (or explicit invalidateProject) drops the
 * entry so the next tab mount sees fresh structure. Time-sensitive but
 * deterministic under fake timers (TTL keys on Date.now). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, invalidateProject } from "./api";

const project = (id: string) => ({ id, name: id });

function mockFetch() {
  const calls: string[] = [];
  const stub = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    const m = /\/api\/v1\/projects\/([^/?]+)/.exec(url);
    return {
      ok: true,
      status: 200,
      json: async () => (m ? project(m[1]) : []),
    } as unknown as Response;
  });
  vi.stubGlobal("fetch", stub);
  return calls;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(1_000_000);
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("project cache", () => {
  it("dedupes concurrent tab mounts into one request", async () => {
    const calls = mockFetch();
    const [a, b] = await Promise.all([
      api.getProject("p1"),
      api.getProject("p1"),
    ]);
    expect(a).toEqual(project("p1"));
    expect(b).toEqual(project("p1"));
    expect(calls).toHaveLength(1);
  });

  it("serves the cached project within the 8s TTL, refetches after", async () => {
    const calls = mockFetch();
    await api.getProject("p2");
    vi.setSystemTime(1_000_000 + 7_999);
    await api.getProject("p2");
    expect(calls).toHaveLength(1);
    vi.setSystemTime(1_000_000 + 8_001);
    await api.getProject("p2");
    expect(calls).toHaveLength(2);
  });

  it("a write invalidates the cache so the next mount refetches", async () => {
    const calls = mockFetch();
    await api.getProject("p3");
    await api.createProject("fresh");
    await api.getProject("p3");
    expect(calls.filter((c) => c.startsWith("GET"))).toHaveLength(2);
  });

  it("invalidateProject drops one entry without touching others", async () => {
    const calls = mockFetch();
    await api.getProject("p4");
    await api.getProject("p5");
    invalidateProject("p4");
    await api.getProject("p4");
    await api.getProject("p5");
    expect(calls.filter((c) => c.endsWith("/p4"))).toHaveLength(2);
    expect(calls.filter((c) => c.endsWith("/p5"))).toHaveLength(1);
  });
});

describe("koma portal", () => {
  it("checkout posts the pack and returns the Stripe URL", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method} ${url} ${init?.body}`);
        return {
          ok: true,
          status: 200,
          json: async () => ({ url: "https://checkout.stripe.test/s/1", purchase_id: "p1" }),
        };
      }) as unknown as typeof fetch,
    );
    const out = await api.checkoutTopup("topup-5");
    expect(out.url).toContain("checkout.stripe.test");
    expect(calls[0]).toContain("POST");
    expect(calls[0]).toContain("/api/v1/billing/checkout");
    expect(calls[0]).toContain("topup-5");
  });

  it("portal summary fetches in one round trip", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            balance: 100,
            free_monthly_credits: 100,
            free_remaining: 100,
            paywall_enabled: true,
            publishable_key: null,
            packs: [{ id: "topup-5", usd_cents: 500, credits: 500 }],
            costs: { image: 3 },
            purchases: [],
            plans: [{ id: "hobby", name: "Hobby", usd_cents: 900, credits: 1200, blurb: "" }],
            subscription: null,
          }),
        };
      }) as unknown as typeof fetch,
    );
    const portal = await api.billingPortal();
    expect(portal.balance).toBe(100);
    expect(portal.packs).toHaveLength(1);
    expect(portal.plans).toHaveLength(1);
    expect(portal.subscription).toBeNull();
    expect(calls).toHaveLength(1);
  });

  it("subscribe + cancel hit the subscription endpoints", async () => {
    const calls: { url: string; method?: string; body?: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, method: init?.method, body: init?.body as string });
        return {
          ok: true,
          status: 200,
          json: async () => (url.endsWith("/subscribe") ? { url: "https://checkout.stripe.test/s/sub" } : { status: "canceled" }),
        };
      }) as unknown as typeof fetch,
    );
    const started = await api.subscribePlan("hobby");
    expect(started.url).toContain("checkout.stripe.test");
    expect(JSON.parse(calls[0].body ?? "{}").plan_id).toBe("hobby");
    const ended = await api.cancelSubscription();
    expect(ended.status).toBe("canceled");
    expect(calls[1].method).toBe("POST");
  });
});

describe("project slices", () => {
  it("getProjectView fetches the combined view in one round trip", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: "p9",
            user_id: "u",
            name: "Slice",
            fps: 24,
            canvas_width: 1920,
            canvas_height: 1080,
            page_count: 1,
            scene_count: 1,
            frame_count: 0,
            sequence_count: 0,
            take_count: 0,
            pages: [{ id: "pg1", project_id: "p9" }],
          }),
        };
      }) as unknown as typeof fetch,
    );
    const view = await api.getProjectView("p9");
    expect(view.id).toBe("p9");
    expect(view.page_count).toBe(1);
    expect(view.pages).toHaveLength(1);
    expect(calls.filter((c) => c.endsWith("/view"))).toHaveLength(1);
    expect(calls).toHaveLength(1);
  });
});
