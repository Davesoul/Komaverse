import { beforeEach, describe, expect, it, vi } from "vitest";

import { pruneStaleProjectSession } from "./lib/studio";

function seedLocal(entries: Record<string, string>) {
  const store = new Map(Object.entries(entries));
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  } as unknown as Storage);
  return store;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("pruneStaleProjectSession", () => {
  it("drops dead project session keys but keeps live ones", () => {
    const store = seedLocal({
      "komaverse.projectId": "dead",
      "komaverse.drawFrame.dead": "f1",
      "komaverse.scene.live": "s1",
      "komaverse.sequence.live": "q1",
    });
    pruneStaleProjectSession(["live"]);
    expect(store.has("komaverse.projectId")).toBe(false);
    expect(store.has("komaverse.drawFrame.dead")).toBe(false);
    expect(store.get("komaverse.scene.live")).toBe("s1");
    expect(store.get("komaverse.sequence.live")).toBe("q1");
  });
});
