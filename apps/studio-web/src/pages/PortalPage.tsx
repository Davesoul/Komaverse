import { useEffect, useState } from "react";
import { api, type LedgerOut, type PortalOut } from "../api";
import { Btn, Card, LoadingLine } from "../components/ui";

const COST_LABELS: Record<string, string> = {
  image: "Image generation",
  video: "Video, per second (rounded up)",
  chat: "AI chat turn (cloud brain)",
  voice: "Voiceover line",
  vision: "Vision read / cloud detection",
};

const fmtUSD = (cents: number) => `$${(cents / 100).toFixed(2)}`;

export default function PortalPage() {
  const [portal, setPortal] = useState<PortalOut | null>(null);
  const [ledger, setLedger] = useState<LedgerOut | null>(null);
  const [notice, setNotice] = useState("");
  const [busyPack, setBusyPack] = useState("");
  const [busyPlan, setBusyPlan] = useState("");
  const [armedCancel, setArmedCancel] = useState(false);

  const refresh = async () => {
    try {
      const [p, l] = await Promise.all([api.billingPortal(), api.billingLedger()]);
      setPortal(p);
      setLedger(l);
    } catch (e) {
      setNotice(`Portal unavailable: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    const topup = qs.get("topup");
    if (topup === "ok") {
      setNotice("Top-up complete — credits land as soon as Stripe confirms (usually seconds).");
      window.history.replaceState(null, "", window.location.pathname);
    } else if (topup === "cancelled") {
      setNotice("Checkout cancelled — no charge, balance unchanged.");
      window.history.replaceState(null, "", window.location.pathname);
    }
    const sub = qs.get("sub");
    if (sub === "ok") {
      setNotice("Subscription started — this month's credits land as soon as Stripe confirms (usually seconds).");
      window.history.replaceState(null, "", window.location.pathname);
    } else if (sub === "cancelled") {
      setNotice("Subscription checkout cancelled — no charge, nothing changed.");
      window.history.replaceState(null, "", window.location.pathname);
    }
    void refresh();
  }, []);

  const topup = async (packId: string) => {
    setBusyPack(packId);
    setNotice("");
    try {
      const { url } = await api.checkoutTopup(packId);
      window.location.href = url;
    } catch (e) {
      setNotice(`Checkout failed: ${(e as Error).message}`);
      setBusyPack("");
    }
  };

  const subscribe = async (planId: string) => {
    setBusyPlan(planId);
    setNotice("");
    try {
      const { url } = await api.subscribePlan(planId);
      window.location.href = url;
    } catch (e) {
      setNotice(`Subscription failed: ${(e as Error).message}`);
      setBusyPlan("");
    }
  };

  const cancelSub = async () => {
    if (!armedCancel) {
      setArmedCancel(true);
      return;
    }
    setArmedCancel(false);
    setNotice("");
    try {
      await api.cancelSubscription();
      setNotice("Subscription cancelled — credits already granted stay on your balance.");
      await refresh();
    } catch (e) {
      setNotice(`Cancel failed: ${(e as Error).message}`);
    }
  };

  if (!portal) {
    return (
      <div style={{ display: "flex", gap: 8, padding: 32 }}>
        <LoadingLine label="Loading Koma Portal…" />
      </div>
    );
  }

  const recent = [...(ledger?.entries ?? [])].slice(-10).reverse();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card>
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Koma Portal</h2>
        <p style={{ fontSize: 13, opacity: 0.8, margin: "0 0 12px" }}>
          Credits pay the model providers, so you never paste API keys.
          Local models (YOLO, heuristic, XTTS, Kokoro) are always free.
        </p>
        <div style={{ display: "flex", gap: 16, alignItems: "baseline", flexWrap: "wrap" }}>
          <div className="kv-nums" style={{ fontSize: 32, fontWeight: 700 }}>
            {portal.balance} <span style={{ fontSize: 14, fontWeight: 400 }}>credits</span>
          </div>
          <div style={{ fontSize: 13 }}>
            {portal.paywall_enabled
              ? portal.free_remaining > 0
                ? `Includes ${portal.free_remaining} free credits this month.`
                : "Free monthly credits used up — top up to keep generating."
              : "Paywall is off on this server — everything cloud is free for now."}
          </div>
        </div>
        {notice && (
          <p style={{ fontSize: 13, margin: "8px 0 0", color: "var(--kv-accent)" }}>{notice}</p>
        )}
      </Card>

      <Card>
        <h3 style={{ fontSize: 14, margin: "0 0 8px" }}>Top up (from $5)</h3>
        {!portal.publishable_key && (
          <p style={{ fontSize: 13, opacity: 0.8 }}>
            Card top-ups aren't configured on this server yet — freemium credits still apply.
          </p>
        )}
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {portal.packs.map((p) => (
            <div
              key={p.id}
              style={{
                border: "1px solid var(--kv-border)",
                borderRadius: 10,
                padding: "12px 16px",
                minWidth: 150,
              }}
            >
              <div className="kv-nums" style={{ fontSize: 20, fontWeight: 700 }}>
                {fmtUSD(p.usd_cents)}
              </div>
              <div className="kv-nums" style={{ fontSize: 13, margin: "4px 0 8px" }}>
                → {p.credits} credits
              </div>
              <Btn
                disabled={!portal.publishable_key || busyPack !== ""}
                onClick={() => void topup(p.id)}
              >
                {busyPack === p.id ? "Opening…" : "Top up"}
              </Btn>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 12, opacity: 0.7, margin: "8px 0 0" }}>
          $5 = 500 credits ≈ 160 images, or 20 video-seconds, or 500 chat turns.
          Paid securely by Stripe — the app never sees your card.
        </p>
      </Card>

      <Card>
        <h3 style={{ fontSize: 14, margin: "0 0 8px" }}>Subscribe — credits every month</h3>
        {!portal.publishable_key && (
          <p style={{ fontSize: 13, opacity: 0.8 }}>
            Subscriptions aren't configured on this server yet — top-ups and freemium still apply.
          </p>
        )}
        {portal.subscription ? (
          <div
            style={{
              border: "1px solid var(--kv-accent)",
              borderRadius: 10,
              padding: "12px 16px",
              display: "flex",
              gap: 12,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <div style={{ fontSize: 14 }}>
              <strong>
                {portal.plans.find((p) => p.id === portal.subscription?.plan_id)?.name ??
                  portal.subscription.plan_id}
              </strong>{" "}
              ·{" "}
              {(
                portal.plans.find((p) => p.id === portal.subscription?.plan_id)?.credits ?? 0
              ).toLocaleString()}{" "}
              credits/month
            </div>
            <div style={{ fontSize: 13, opacity: 0.8 }}>
              {portal.subscription.renews
                ? `Renews ${portal.subscription.renews.slice(0, 10)}`
                : "Renews monthly"}
            </div>
            <Btn
              disabled={!portal.publishable_key}
              onClick={() => void cancelSub()}
              title="Stop renewal — granted credits stay on your balance"
            >
              {armedCancel ? "Click again to confirm cancel" : "Cancel subscription"}
            </Btn>
          </div>
        ) : (
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {portal.plans.map((plan) => (
              <div
                key={plan.id}
                style={{
                  border: "1px solid var(--kv-border)",
                  borderRadius: 10,
                  padding: "12px 16px",
                  minWidth: 190,
                  flex: "1 1 190px",
                }}
              >
                <div style={{ fontSize: 14, fontWeight: 700 }}>
                  {plan.name}
                  {plan.id === "studio" && (
                    <span style={{ fontSize: 11, fontWeight: 400, opacity: 0.75 }}>
                      {" "}· most popular
                    </span>
                  )}
                </div>
                <div className="kv-nums" style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>
                  {fmtUSD(plan.usd_cents)}
                  <span style={{ fontSize: 12, fontWeight: 400 }}>/mo</span>
                </div>
                <div className="kv-nums" style={{ fontSize: 13, margin: "4px 0" }}>
                  → {plan.credits.toLocaleString()} credits/mo
                </div>
                <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 8 }}>{plan.blurb}</div>
                <Btn
                  disabled={!portal.publishable_key || busyPlan !== ""}
                  onClick={() => void subscribe(plan.id)}
                >
                  {busyPlan === plan.id ? "Opening…" : "Subscribe"}
                </Btn>
              </div>
            ))}
          </div>
        )}
        <p style={{ fontSize: 12, opacity: 0.7, margin: "8px 0 0" }}>
          Monthly plans beat pay-as-you-go per credit ($5 = 500). Renewals
          re-grant automatically; cancel anytime, granted credits stay.
        </p>
      </Card>

      <Card>
        <h3 style={{ fontSize: 14, margin: "0 0 8px" }}>What things cost</h3>
        <table style={{ fontSize: 13, borderCollapse: "collapse" }}>
          <tbody>
            {Object.entries(portal.costs).map(([kind, cost]) => (
              <tr key={kind}>
                <td style={{ padding: "4px 16px 4px 0" }}>
                  {COST_LABELS[kind] ?? kind}
                </td>
                <td className="kv-nums" style={{ padding: "4px 0" }}>
                  {cost} credit{cost === 1 ? "" : "s"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card>
        <h3 style={{ fontSize: 14, margin: "0 0 8px" }}>Purchases</h3>
        {portal.purchases.length === 0 ? (
          <p style={{ fontSize: 13, opacity: 0.8, margin: 0 }}>No top-ups yet.</p>
        ) : (
          <table style={{ fontSize: 13, borderCollapse: "collapse" }}>
            <tbody>
              {portal.purchases.map((t) => (
                <tr key={t.id}>
                  <td className="kv-nums" style={{ padding: "4px 16px 4px 0" }}>
                    {fmtUSD(t.amount_usd_cents)} → {t.credits}
                  </td>
                  <td style={{ padding: "4px 0", opacity: 0.8 }}>
                    {t.status}
                    {t.created_at ? ` · ${t.created_at.slice(0, 10)}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card>
        <h3 style={{ fontSize: 14, margin: "0 0 8px" }}>Recent activity</h3>
        {recent.length === 0 ? (
          <p style={{ fontSize: 13, opacity: 0.8, margin: 0 }}>Nothing spent or granted yet.</p>
        ) : (
          <table style={{ fontSize: 13, borderCollapse: "collapse" }}>
            <tbody>
              {recent.map((e) => (
                <tr key={e.id}>
                  <td
                    className="kv-nums"
                    style={{
                      padding: "4px 16px 4px 0",
                      color: e.delta < 0 ? "inherit" : "var(--kv-accent)",
                    }}
                  >
                    {e.delta > 0 ? `+${e.delta}` : e.delta}
                  </td>
                  <td style={{ padding: "4px 0", opacity: 0.8 }}>{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
