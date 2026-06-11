import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { authApi } from "../lib/api";

export default function Register() {
  const nav = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (form.password.length < 8) { setError("Le mot de passe doit comporter au moins 8 caractères."); return; }
    setError(""); setLoading(true);
    try {
      const { data } = await authApi.register(form);
      localStorage.setItem("ll_token", data.access_token);
      localStorage.setItem("ll_name", data.name);
      nav("/onboarding", { replace: true });
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Échec de l'inscription.");
    } finally { setLoading(false); }
  }

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }));

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
          <h1 className="font-heading text-xl font-bold text-white mb-1">Créez votre compte</h1>
          <p className="text-xs text-slate-500 mb-6">Gratuit — sans carte bancaire.</p>

          <form onSubmit={submit} className="space-y-4">
            {[
              { label: "Votre nom", key: "name", type: "text", ph: "Alex Coach" },
              { label: "E-mail", key: "email", type: "email", ph: "vous@exemple.com" },
              { label: "Mot de passe", key: "password", type: "password", ph: "Min. 8 caractères" },
            ].map(({ label, key, type, ph }) => (
              <div key={key}>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">{label}</label>
                <input type={type} required value={(form as any)[key]} onChange={set(key)}
                  className={inputCls} placeholder={ph} />
              </div>
            ))}

            {error && (
              <p className="text-red-400 text-xs bg-red-950/40 border border-red-900/40 rounded-xl px-3 py-2.5">
                {error}
              </p>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-50 disabled:shadow-none rounded-xl text-sm font-semibold transition-all duration-150 mt-2">
              {loading ? "Création du compte…" : "Créer un compte →"}
            </button>
          </form>

          <p className="text-center text-xs text-slate-600 mt-6">
            Déjà un compte ?{" "}
            <Link to="/login" className="text-brand-400 hover:text-brand-300 transition-colors">Se connecter</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
