import { useState } from "react";
import { api, type User } from "../api";
import { Btn, Card, Notice, PrimaryBtn, TextInput } from "./ui";

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
        <div style={{ marginBottom: 12 }}>
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {mode === "register" && (
          <TextInput
            placeholder="Name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="name"
          />
        )}
        <TextInput
          placeholder="Email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
        />
        <TextInput
          placeholder="Password (8+ characters)"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
        />
        <PrimaryBtn disabled={busy || !email || !password} onClick={submit}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </PrimaryBtn>
        <Btn
          onClick={() => {
            setEmail("demo@komaverse.local");
            setPassword("demo1234");
            setError("");
          }}
        >
          Fill demo credentials
        </Btn>
        <button
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
      </div>
    </Card>
  );
}
