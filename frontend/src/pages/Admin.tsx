import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { adminApi } from "../lib/api";

export default function Admin() {
  const [key, setKey] = useState(localStorage.getItem("ll_admin_key") || "");
  const [inputKey, setInputKey] = useState("");
  const [error, setError] = useState("");

  const { data: stats, isError: statsError } = useQuery({
    queryKey: ["admin-stats", key],
    queryFn: () => adminApi.stats(key).then(r => r.data),
    enabled: !!key,
    retry: false,
  });

  const { data: emailsData } = useQuery({
    queryKey: ["admin-emails", key],
    queryFn: () => adminApi.emails(key).then(r => r.data),
    enabled: !!key && !!stats,
    retry: false,
  });

  function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    localStorage.setItem("ll_admin_key", inputKey);
    setKey(inputKey);
  }

  if (statsError) {
    localStorage.removeItem("ll_admin_key");
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <form onSubmit={handleLogin}
          className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-8 w-full max-w-sm space-y-4">
          <h1 className="font-heading text-xl font-bold text-white text-center">Admin LeanLead</h1>
          {error && <p className="text-xs text-red-400 text-center">{error}</p>}
          <input type="password" value={inputKey} onChange={e => setInputKey(e.target.value)}
            placeholder="Clé admin"
            className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-brand-500" />
          <button type="submit"
            className="w-full px-4 py-3 bg-brand-500 hover:bg-brand-400 rounded-xl text-sm font-semibold transition-colors">
            Accéder
          </button>
        </form>
      </div>
    );
  }

  if (!key) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <form onSubmit={handleLogin}
          className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-8 w-full max-w-sm space-y-4">
          <h1 className="font-heading text-xl font-bold text-white text-center">Admin LeanLead</h1>
          <input type="password" value={inputKey} onChange={e => setInputKey(e.target.value)}
            placeholder="Clé admin"
            className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-brand-500" />
          <button type="submit"
            className="w-full px-4 py-3 bg-brand-500 hover:bg-brand-400 rounded-xl text-sm font-semibold transition-colors">
            Accéder
          </button>
        </form>
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <p className="text-slate-400 text-sm">Chargement…</p>
      </div>
    );
  }

  const coaches = emailsData?.coaches ?? [];

  return (
    <div className="min-h-screen bg-slate-950 text-white p-6">
      <div className="max-w-5xl mx-auto space-y-8">
        <div className="flex items-center justify-between">
          <h1 className="font-heading text-2xl font-bold">
            Lean<span className="text-brand-400">Lead</span> Admin
          </h1>
          <button onClick={() => { localStorage.removeItem("ll_admin_key"); setKey(""); }}
            className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
            Déconnexion
          </button>
        </div>

        {/* Stats cards */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
          {[
            { label: "Inscriptions totales", value: stats.total_coaches, color: "text-white" },
            { label: "Essais actifs", value: stats.active_trials, color: "text-brand-400" },
            { label: "Essais expirés", value: stats.expired_trials, color: "text-amber-400" },
            { label: "Leads totaux", value: stats.total_leads, color: "text-emerald-400" },
            {
              label: "Plans",
              value: Object.entries(stats.plan_breakdown).map(([p, n]) => `${p}: ${n}`).join(" · "),
              color: "text-slate-300",
            },
          ].map(s => (
            <div key={s.label}
              className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-4">
              <p className="text-xs text-slate-500 mb-1">{s.label}</p>
              <p className={`text-xl font-bold ${s.color}`}>{s.value}</p>
            </div>
          ))}
        </div>

        {/* Email table */}
        <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-[#2a2a2a]">
            <h2 className="font-semibold text-sm">Utilisateurs ({coaches.length})</h2>
            <button onClick={() => {
              const csv = [
                "id,name,email,plan,signup_date,trial_end_date,trial_active,trial_days_left,onboarded,email_verified",
                ...coaches.map(c =>
                  [c.id, c.name, c.email, c.plan, c.signup_date, c.trial_end_date,
                   c.trial_active, c.trial_days_left, c.onboarded, c.email_verified].join(",")
                ),
              ].join("\n");
              const a = document.createElement("a");
              a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
              a.download = `leanlead-users-${new Date().toISOString().slice(0, 10)}.csv`;
              a.click();
            }}
              className="text-xs text-slate-400 hover:text-white border border-[#2a2a2a] hover:border-slate-600 px-3 py-1.5 rounded-lg transition-colors">
              Exporter CSV
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 border-b border-[#2a2a2a]">
                  {["Nom", "Email", "Plan", "Inscription", "Fin essai", "Essai", "Jours restants", "Status"].map(h => (
                    <th key={h} className="text-left px-4 py-3 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {coaches.map(c => (
                  <tr key={c.id} className="border-b border-[#1f1f1f] hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-3 font-medium text-white">{c.name}</td>
                    <td className="px-4 py-3 text-slate-300">{c.email}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                        c.plan === "agency" ? "bg-brand-500/20 text-brand-300" :
                        c.plan === "growth" ? "bg-emerald-500/20 text-emerald-300" :
                        "bg-slate-700 text-slate-300"
                      }`}>
                        {c.plan}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-400">
                      {c.signup_date ? new Date(c.signup_date).toLocaleDateString("fr-FR") : "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-400">
                      {c.trial_end_date ? new Date(c.trial_end_date).toLocaleDateString("fr-FR") : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                        c.trial_active ? "bg-green-500/20 text-green-300" : "bg-slate-700 text-slate-400"
                      }`}>
                        {c.trial_active ? "Actif" : c.trial_end_date ? "Expiré" : "Non"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-300">
                      {c.trial_days_left !== null ? `${c.trial_days_left}j` : "—"}
                    </td>
                    <td className="px-4 py-3 flex gap-1.5">
                      {c.onboarded && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] bg-slate-700 text-slate-300">Onboardé</span>
                      )}
                      {c.email_verified && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] bg-blue-900/40 text-blue-300">✓ Email</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {coaches.length === 0 && (
              <p className="text-center text-slate-500 py-8 text-xs">Aucun utilisateur</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
