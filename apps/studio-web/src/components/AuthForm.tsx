import { useState } from "react";
import { api, type User } from "../api";
import { Btn, Card, Notice, PrimaryBtn, Spinner, TextInput } from "./ui";

export default function AuthForm({
  mode,
  onMode,
  onDone,
}: {
  mode: "login" | "register";
  onMode: (mode: "login" | "register") => void;
  onDone: (user: User) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (busy || !email || !password) return;
    setBusy(true);
    setError("");
    try {
      const user =
        mode === "login"
          ? await api.login({ email, password })
          : await api.register({ email, password, name });
      onDone(user);
    } catch (e) {
      setError(
        mode === "login"
          ? "Invalid email or password."
          : (e as Error).message.includes("409")
            ? "That email is already registered — sign in instead."
            : "Registration failed. Use a valid email and 8+ character password.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card style={{ maxWidth: 400, margin: "48px auto", padding: 32 }}>
      <h1 style={{ margin: "0 0 4px", fontSize: 22 }}>
        {mode === "login" ? "Welcome back" : "Create your studio"}
      </h1>
      <p style={{ margin: "0 0 20px", fontSize: 13, color: "var(--kv-muted)" }}>
        {mode === "login"
          ? "Sign in to your projects."
          : "One account, all your anime projects."}
      </p>
      {error && (
        <div style={{ marginBottom: 12 }} className="kv-notice-in">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        style={{ display: "flex", flexDirection: "column", gap: 10 }}
      >
        {mode === "register" && (
          <label style={label}>
            <span style={labelText}>Name (optional)</span>
            <TextInput
              placeholder="Ada"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
              disabled={busy}
              style={field}
            />
          </label>
        )}
        <label style={label}>
          <span style={labelText}>Email</span>
          <TextInput
            placeholder="you@studio.com"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            autoFocus
            disabled={busy}
            style={field}
          />
        </label>
        <label style={label}>
          <span style={labelText}>Password (8+ characters)</span>
          <TextInput
            placeholder="••••••••"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            disabled={busy}
            style={field}
          />
        </label>
        <PrimaryBtn type="submit" disabled={busy || !email || !password}>
          {busy ? (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <Spinner delayMs={0} /> Please wait…
            </span>
          ) : mode === "login" ? (
            "Sign in"
          ) : (
            "Create account"
          )}
        </PrimaryBtn>
        {mode === "login" && (
          <Btn
            type="button"
            disabled={busy}
            onClick={() => {
              setEmail("demo@komaverse.local");
              setPassword("demo1234");
              setError("");
            }}
          >
            Fill demo credentials
          </Btn>
        )}
        <button
          type="button"
          style={{
            background: "none",
            border: "none",
            color: "var(--kv-accent-text)",
            fontSize: 13,
            cursor: "pointer",
          }}
          onClick={() => onMode(mode === "login" ? "register" : "login")}
        >
          {mode === "login"
            ? "New here? Create an account"
            : "Have an account? Sign in"}
        </button>
      </form>
    </Card>
  );
}

const label: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const labelText: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 700,
  color: "var(--kv-muted)",
};

const field: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  fontSize: 14,
};
