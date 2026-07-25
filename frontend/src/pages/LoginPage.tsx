import { FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { APP_LEAD, APP_TAGLINE } from "../brand";
import { roleHome, useAuth } from "../auth/AuthContext";
import { BrandWordmark } from "../components/Brand";
import { ThemeToggle } from "../components/ThemeToggle";
import { useToast } from "../components/Toast";
import { WaveField } from "../components/WaveField";

const DEMOS = [
  { label: "Patient · Asha", email: "asha.patient@example.com" },
  { label: "Patient · Ravi", email: "ravi.patient@example.com" },
  { label: "Staff · Sam", email: "sam.staff@example.com" },
  { label: "Admin · Ada", email: "ada.admin@example.com" },
];

export function LoginPage() {
  const { user, loading, login, register } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedDemo, setSelectedDemo] = useState<string | null>(null);

  if (!loading && user) {
    return <Navigate to={roleHome(user.role)} replace />;
  }

  async function onLogin(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const me = await login(email.trim(), password);
      toast.push(`Signed in as ${me.name}`, "success");
      navigate(roleHome(me.role), { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  async function onRegister(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const me = await register({
        name: name.trim(),
        email: email.trim(),
        password,
        phone: phone.trim() || undefined,
      });
      toast.push("Account created", "success");
      navigate(roleHome(me.role), { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-auth">
      <div className="auth-layout">
        <aside className="auth-brand">
          <WaveField />
          <div className="auth-brand-content">
            <p className="auth-eyebrow">{APP_TAGLINE}</p>
            <h1 className="brand-hero">
              <BrandWordmark size="lg" />
            </h1>
            <p className="auth-lead">{APP_LEAD}</p>
            <ul className="auth-points">
              <li>Patients submit admin requests in plain language</li>
              <li>Staff handle escalations and approvals</li>
              <li>Not for diagnosis or prescriptions</li>
            </ul>
          </div>
        </aside>

        <main className="auth-main">
          <div className="auth-card auth-card-enter">
            <div className="auth-theme-bar">
              <ThemeToggle compact />
            </div>
            <div className="auth-tabs">
              <button
                type="button"
                className={mode === "login" ? "active" : undefined}
                onClick={() => {
                  setMode("login");
                  setError("");
                }}
              >
                Sign in
              </button>
              <button
                type="button"
                className={mode === "register" ? "active" : undefined}
                onClick={() => {
                  setMode("register");
                  setError("");
                  setSelectedDemo(null);
                }}
              >
                Create account
              </button>
            </div>

            {mode === "login" ? (
              <form className="stack" onSubmit={onLogin}>
                <h2 className="auth-card-title">Welcome back</h2>
                <p className="auth-card-sub">Use a demo account below, or your own credentials.</p>
                <label>
                  Email
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    autoComplete="username"
                    placeholder="you@example.com"
                  />
                </label>
                <label>
                  Password
                  <input
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    placeholder="••••••••"
                  />
                </label>
                <button type="submit" className="btn-block" disabled={busy}>
                  {busy && <span className="spinner" />}
                  Sign in
                </button>
                {error && <p className="err">{error}</p>}
              </form>
            ) : (
              <form className="stack" onSubmit={onRegister}>
                <h2 className="auth-card-title">New patient</h2>
                <p className="auth-card-sub">Creates a patient account and signs you in.</p>
                <label>
                  Full name
                  <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Ada Patient" />
                </label>
                <label>
                  Email
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    autoComplete="email"
                    placeholder="you@example.com"
                  />
                </label>
                <label>
                  Password
                  <input
                    type="password"
                    required
                    minLength={8}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="new-password"
                    placeholder="At least 8 characters"
                  />
                </label>
                <label>
                  Phone <span className="optional">(optional)</span>
                  <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 …" />
                </label>
                <button type="submit" className="btn-block" disabled={busy}>
                  {busy && <span className="spinner" />}
                  Create account
                </button>
                {error && <p className="err">{error}</p>}
              </form>
            )}

            {mode === "login" && (
              <div className="demo-box">
                <div className="demo-box-head">
                  <strong>Try a demo account</strong>
                  <span>
                    password: <code>password123</code>
                  </span>
                </div>
                <div className="demo-chips">
                  {DEMOS.map((d) => (
                    <button
                      key={d.email}
                      type="button"
                      className={`chip${selectedDemo === d.email ? " chip-selected" : ""}`}
                      onClick={() => {
                        setEmail(d.email);
                        setPassword("password123");
                        setSelectedDemo(d.email);
                        setError("");
                      }}
                    >
                      {d.label}
                    </button>
                  ))}
                </div>
                <p className="muted" style={{ margin: "0.65rem 0 0", fontSize: "0.85rem" }}>
                  Click a chip to fill the form, then Sign in.
                </p>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
