import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../lib/api";
import { Button, ErrorText } from "../components/ui";

const MIN_PASSWORD = 10;

export default function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ organization: "", email: "", password: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (mode === "register") {
      if (form.organization.trim().length < 2) return setError(new Error("Organization name must be at least 2 characters."));
      if (form.password.length < MIN_PASSWORD) {
        return setError(new Error(`Password must be at least ${MIN_PASSWORD} characters.`));
      }
    }
    setBusy(true);
    try {
      const body = mode === "login" ? { email: form.email, password: form.password } : form;
      const res = await api.post<{ access_token: string }>(`/api/auth/${mode}`, body);
      setToken(res.access_token);
      navigate("/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const field = (key: keyof typeof form, label: string, type = "text", hint?: string) => (
    <label className="block text-sm">
      <span className="text-slate-600">{label}</span>
      <input
        type={type}
        required
        value={form[key]}
        autoComplete={key === "password" ? (mode === "login" ? "current-password" : "new-password") : key === "email" ? "email" : undefined}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
        className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
      />
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );

  return (
    <div className="min-h-screen grid place-items-center bg-slate-100 px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-white rounded-xl shadow p-6 space-y-4">
        <h1 className="text-xl font-bold">Client Intelligence Platform</h1>
        <p className="text-sm text-slate-500">
          {mode === "login" ? "Sign in to your organization" : "Create an organization (you become its admin)"}
        </p>
        {mode === "register" && field("organization", "Organization")}
        {field("email", "Email", "email")}
        {field("password", "Password", "password", mode === "register" ? `At least ${MIN_PASSWORD} characters.` : undefined)}
        <ErrorText error={error} />
        <Button type="submit" disabled={busy} className="w-full">
          {mode === "login" ? "Sign in" : "Create account"}
        </Button>
        <button
          type="button"
          className="text-sm text-indigo-600 underline"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
        >
          {mode === "login" ? "New here? Create an organization" : "Have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
