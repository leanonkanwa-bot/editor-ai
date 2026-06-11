import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { authApi } from "../lib/api";

export default function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(""); setLoading(true);
    try {
      const { data } = await authApi.login(email, password);
      localStorage.setItem("ll_token", data.access_token);
      localStorage.setItem("ll_name", data.name);
      nav(data.onboarded ? "/dashboard" : "/onboarding", { replace: true });
    } catch (err: any) {
      setError(err?.response?.data?.detail || "E-mail ou mot de passe invalide.");
    } finally { setLoading(false); }
  }

  const inputCls = "w-full bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-brand-500 focus:shadow-glow-sm transition-all duration-150";

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4"
      style={{ backgroundImage: "radial-gradient(ellipse 60% 50% at 50% 0%, rgba(255,117,31,0.07), transparent)" }}>
      <div className="w-full max-w-sm">
        <Link to="/" className="flex justify-center mb-8">
          <span className="font-heading font-extrabold text-2xl tracking-tight">
            Lean<span className="text-brand-400">Lead</span>
          </span>
        </Link>

        <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-8 shadow-2xl shadow-black/60 animate-fade-in">
          <h1 className="font-heading text-xl font-bold text-white mb-1">Bon retour</h1>
          <p className="text-xs text-slate-500 mb-6">Content de vous revoir.</p>

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">E-mail</label>
              <input type="email" required value={email} onChange={e => setEmail(e.target.value)}
                className={inputCls} placeholder="vous@exemple.com" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Mot de passe</label>
              <input type="password" required value={password} onChange={e => setPassword(e.target.value)}
                className={inputCls} placeholder="••••••••" />
            </div>

            {error && (
              <p className="text-red-400 text-xs bg-red-950/40 border border-red-900/40 rounded-xl px-3 py-2.5">
                {error}
              </p>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-50 disabled:shadow-none rounded-xl text-sm font-semibold transition-all duration-150 mt-2">
              {loading ? "Connexion…" : "Se connecter →"}
            </button>
          </form>

          <p className="text-center text-xs text-slate-600 mt-6">
            Pas de compte ?{" "}
            <Link to="/register" className="text-brand-400 hover:text-brand-300 transition-colors">Créez-en un gratuitement</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
