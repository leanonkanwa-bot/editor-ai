import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  authApi, leadsApi, pipelineApi, followupsApi, prospectingApi, analyticsApi, agentApi,
  keywordTriggersApi, contentIntelApi,
  type Lead, type Stage, type FollowupDue, type Classification, type ReplyAnalysis,
  type AnalyticsData, type AgentStatus, type Coach, type ContentAnalysis,
} from "../lib/api";
import KanbanBoard from "../components/KanbanBoard";

/* ════════════════════════════════════════════════════════════════════
   MODAL AJOUT DE LEAD
════════════════════════════════════════════════════════════════════ */
function AddLeadModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({
    name: "", handle: "", platform: "instagram",
    profile_url: "", bio: "", followers: "", posts_summary: "", notes: "",
  });
  const [autoQualify, setAutoQualify] = useState(true);

  const add = useMutation({
    mutationFn: async () => {
      const { data: lead } = await leadsApi.create({ ...f, followers: parseInt(f.followers) || 0 });
      if (autoQualify) await pipelineApi.qualify(lead.id);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["leads"] }); onClose(); },
  });

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setF(p => ({ ...p, [k]: e.target.value }));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-lg bg-slate-900 border border-[#2a2a2a] rounded-2xl overflow-hidden shadow-2xl shadow-black/60 animate-fade-in"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-5 border-b border-[#2a2a2a]">
          <h2 className="font-semibold text-white">Ajouter un lead</h2>
          <button onClick={onClose} className="text-slate-500 hover:text-white text-2xl leading-none">×</button>
        </div>

        <div className="p-5 space-y-4 overflow-y-auto max-h-[68vh]">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Nom complet</label>
              <input value={f.name} onChange={set("name")}
                className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                placeholder="Jane Smith" />
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Identifiant *</label>
              <div className="flex">
                <span className="bg-slate-700 border border-slate-600 border-r-0 rounded-l-xl px-2.5 text-slate-400 text-sm flex items-center">@</span>
                <input value={f.handle} onChange={set("handle")} required
                  className="flex-1 bg-slate-800 border border-[#2a2a2a] rounded-r-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                  placeholder="janesmith" />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Plateforme</label>
              <select value={f.platform} onChange={set("platform")}
                className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500">
                <option value="instagram">📸 Instagram</option>
                <option value="tiktok">🎵 TikTok</option>
                <option value="twitter">𝕏 Twitter / X</option>
                <option value="linkedin">💼 LinkedIn</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">Abonnés</label>
              <input type="number" value={f.followers} onChange={set("followers")}
                className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                placeholder="0" />
            </div>
          </div>

          <div>
            <label className="text-xs text-slate-400 mb-1 block">URL du profil</label>
            <input value={f.profile_url} onChange={set("profile_url")}
              className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
              placeholder="https://instagram.com/janesmith" />
          </div>

          <div>
            <label className="text-xs text-slate-400 mb-1 block">Bio <span className="text-slate-600">(collez la bio complète pour de meilleurs résultats IA)</span></label>
            <textarea value={f.bio} onChange={set("bio")} rows={3}
              className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500 resize-none"
              placeholder="J'aide les mamans à perdre 10 kg en 12 semaines | DM pour mon guide gratuit…" />
          </div>

          <div>
            <label className="text-xs text-slate-400 mb-1 block">Notes sur les publications récentes <span className="text-slate-600">(facultatif)</span></label>
            <textarea value={f.posts_summary} onChange={set("posts_summary")} rows={2}
              className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500 resize-none"
              placeholder="Publications sur la préparation des repas, 3x/semaine. Dernière légende : 'pourquoi j'ai arrêté de compter les calories'…" />
          </div>

          <label className="flex items-center gap-3 cursor-pointer">
            <input type="checkbox" checked={autoQualify} onChange={e => setAutoQualify(e.target.checked)}
              className="w-4 h-4 accent-brand-500" />
            <span className="text-sm text-slate-300">Qualifier automatiquement avec l'IA après l'ajout</span>
          </label>

          {add.isError && (
            <p className="text-red-400 text-xs bg-red-950/40 border border-red-900/40 rounded-lg px-3 py-2">
              {(add.error as any)?.response?.data?.detail || "Échec de l'ajout du lead."}
            </p>
          )}
        </div>

        <div className="p-5 border-t border-[#2a2a2a]">
          <button onClick={() => add.mutate()} disabled={add.isPending || !f.handle}
            className="w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors">
            {add.isPending
              ? (autoQualify ? "Ajout et qualification…" : "Ajout…")
              : "Ajouter le lead"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
   ONGLET PROSPECTION
════════════════════════════════════════════════════════════════════ */
const PLATFORM_CONFIG: Record<string, { icon: string; label: string; termLabel: string; termPrefix: string; placeholder: string }> = {
  instagram: { icon: "📸", label: "Instagram", termLabel: "Hashtags", termPrefix: "#", placeholder: "coachbusiness" },
  tiktok:    { icon: "🎵", label: "TikTok",    termLabel: "Hashtags", termPrefix: "#", placeholder: "entrepreneuriat" },
  twitter:   { icon: "𝕏",  label: "Twitter",   termLabel: "Mots-clés", termPrefix: "",  placeholder: "online business" },
  linkedin:  { icon: "💼", label: "LinkedIn",  termLabel: "Mots-clés", termPrefix: "",  placeholder: "scale my business" },
  reddit:    { icon: "💬", label: "Reddit",    termLabel: "Subreddits", termPrefix: "r/", placeholder: "Entrepreneur" },
};

function ProspectsTab() {
  const qc = useQueryClient();
  const [profileUrl, setProfileUrl] = useState("");
  const [autoWrite, setAutoWrite] = useState(true);
  const [urlSuccess, setUrlSuccess] = useState<string | null>(null);

  const fromUrl = useMutation({
    mutationFn: () => prospectingApi.fromUrl({ profile_url: profileUrl, auto_write: autoWrite }).then(r => r.data),
    onSuccess: (lead) => {
      qc.invalidateQueries({ queryKey: ["leads"] });
      setUrlSuccess(`@${lead.handle} ajouté au pipeline${lead.outreach_message ? " avec message généré" : ""} !`);
      setProfileUrl("");
      setTimeout(() => setUrlSuccess(null), 6000);
    },
  });

  const [platform, setPlatform] = useState("instagram");
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [suggestedTags, setSuggestedTags] = useState<string[]>([]);
  const [maxResults, setMaxResults] = useState(20);
  const [autoQualify, setAutoQualify] = useState(true);

  const { data: jobs = [] } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => prospectingApi.jobs().then(r => r.data),
    refetchInterval: 5_000,
  });

  const suggest = useMutation({
    mutationFn: () => prospectingApi.suggestHashtags(platform).then(r => r.data.hashtags),
    onSuccess: t => { setSuggestedTags(t); },
  });

  const run = useMutation({
    mutationFn: () => prospectingApi.run({ platform, hashtags: tags, max_results: maxResults, auto_qualify: autoQualify }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["jobs"] }); qc.invalidateQueries({ queryKey: ["leads"] }); },
  });

  // Auto-load suggestions when platform changes
  useEffect(() => {
    setTags([]);
    suggest.mutate();
  }, [platform]);

  /* ── Autonomous agent ── */
  const { data: agentStatus, refetch: refetchAgent } = useQuery<AgentStatus>({
    queryKey: ["agent-status"],
    queryFn: () => agentApi.status().then(r => r.data),
    refetchInterval: (query) =>
      (query.state.data as AgentStatus | undefined)?.last_run?.status === "running" ? 3_000 : 10_000,
  });

  const agentSettings = useMutation({
    mutationFn: (d: Parameters<typeof agentApi.settings>[0]) => agentApi.settings(d),
    onSuccess: () => refetchAgent(),
  });
  const agentTrigger = useMutation({
    mutationFn: () => agentApi.trigger(),
    onSuccess: () => { refetchAgent(); qc.invalidateQueries({ queryKey: ["leads"] }); },
  });

  const agentEnabled = agentStatus?.enabled ?? false;
  const agentRunning = agentStatus?.last_run?.status === "running";
  const lastRun = agentStatus?.last_run;
  const competitors = agentStatus?.competitor_accounts ?? [];

  const [competitorUrl, setCompetitorUrl] = useState("");
  const [competitorPlatform, setCompetitorPlatform] = useState("instagram");
  const [showCompetitors, setShowCompetitors] = useState(false);

  const addCompetitor = useMutation({
    mutationFn: () => agentApi.addCompetitor({ url: competitorUrl, platform: competitorPlatform }),
    onSuccess: () => { refetchAgent(); setCompetitorUrl(""); },
  });
  const removeCompetitor = useMutation({
    mutationFn: (handle: string) => agentApi.removeCompetitor(handle),
    onSuccess: () => refetchAgent(),
  });

  function formatNextRun(iso?: string): string {
    if (!iso) return "—";
    const diff = new Date(iso).getTime() - Date.now();
    if (diff <= 0) return "imminent";
    const h = Math.floor(diff / 3_600_000);
    const m = Math.floor((diff % 3_600_000) / 60_000);
    return h > 0 ? `dans ${h}h ${m}m` : `dans ${m}m`;
  }

  function addTag() {
    const t = tagInput.replace(/^#/, "").trim().toLowerCase();
    if (t && !tags.includes(t)) setTags(p => [...p, t]);
    setTagInput("");
  }

  const running = jobs.some(j => j.status === "running" || j.status === "pending");
  const cfg = PLATFORM_CONFIG[platform] || PLATFORM_CONFIG.instagram;

  const statusColors: Record<string, string> = {
    pending: "text-amber-400", running: "text-brand-400", done: "text-emerald-400", error: "text-red-400",
  };
  const statusLabels: Record<string, string> = {
    pending: "en attente", running: "en cours", done: "terminé", error: "erreur",
  };

  return (
    <div className="max-w-2xl mx-auto space-y-5">

      {/* ── Agent Autonome ── */}
      <div className={`border rounded-2xl p-6 transition-colors ${
        agentEnabled
          ? "bg-gradient-to-br from-slate-900 to-brand-950/10 border-brand-900/50"
          : "bg-slate-900 border-[#2a2a2a]"
      }`}>
        <div className="flex items-start justify-between gap-4 mb-1">
          <div>
            <h2 className="font-semibold text-white flex items-center gap-2">
              🤖 Agent Autonome
              {agentRunning && (
                <span className="text-[10px] bg-brand-500/20 text-brand-400 border border-brand-500/30 px-2 py-0.5 rounded-full animate-pulse">
                  en cours…
                </span>
              )}
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Trouve et qualifie des leads 24h/24, génère les DMs — vous n'avez rien à faire.
            </p>
          </div>
          {/* Toggle */}
          <button
            onClick={() => agentSettings.mutate({ enabled: !agentEnabled })}
            disabled={agentSettings.isPending}
            aria-label="Activer/désactiver l'agent"
            className={`relative flex-shrink-0 w-12 h-6 rounded-full transition-colors disabled:opacity-50 ${
              agentEnabled ? "bg-brand-500" : "bg-slate-700"
            }`}
          >
            <span className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow transition-transform ${
              agentEnabled ? "translate-x-7" : "translate-x-1"
            }`} />
          </button>
        </div>

        {agentEnabled && (
          <>
            {/* Last run stats */}
            {lastRun ? (
              <div className="grid grid-cols-3 gap-2 mt-4 mb-4">
                <div className="bg-slate-800/70 rounded-xl p-3 text-center">
                  <div className="text-xl font-black text-white">{lastRun.leads_found}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">leads trouvés</div>
                </div>
                <div className="bg-slate-800/70 rounded-xl p-3 text-center">
                  <div className="text-xl font-black text-brand-400">{lastRun.dms_generated}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">DMs générés</div>
                </div>
                <div className="bg-slate-800/70 rounded-xl p-3 text-center">
                  <div className="text-xl font-black text-emerald-400">{lastRun.high_score_leads}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">score ≥70</div>
                </div>
              </div>
            ) : (
              <div className="mt-4 mb-4 py-4 rounded-xl bg-slate-800/40 text-center">
                <p className="text-xs text-slate-600">Aucun run encore — déclenchez le premier ci-dessous.</p>
              </div>
            )}

            {lastRun?.error_message && (
              <p className="text-[11px] text-red-400 bg-red-950/30 border border-red-900/30 rounded-lg px-3 py-2 mb-3">
                Erreur : {lastRun.error_message}
              </p>
            )}

            <div className="flex items-center justify-between gap-3">
              <p className="text-[11px] text-slate-600">
                {agentStatus?.next_run_at
                  ? `Prochain run : ${formatNextRun(agentStatus.next_run_at)}`
                  : agentRunning ? "Run en cours…" : "En attente du prochain cycle"}
              </p>
              <button
                onClick={() => agentTrigger.mutate()}
                disabled={agentRunning || agentTrigger.isPending}
                className="px-3 py-1.5 bg-brand-500/20 hover:bg-brand-500/30 border border-brand-500/30 text-brand-400 text-xs rounded-lg transition-colors disabled:opacity-40 flex-shrink-0"
              >
                {agentTrigger.isPending ? "Démarrage…" : "▶ Lancer maintenant"}
              </button>
            </div>
            {agentTrigger.isError && (
              <p className="text-red-400 text-xs mt-2">
                {(agentTrigger.error as any)?.response?.data?.detail || "Erreur"}
              </p>
            )}

            {/* ── Competitor Audience Hijack ── */}
            <div className="mt-4 pt-4 border-t border-[#2a2a2a]">
              <button
                onClick={() => setShowCompetitors(v => !v)}
                className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-200 transition-colors w-full"
              >
                <span className="text-base">🎯</span>
                <span className="font-medium">Cibler l'audience des concurrents</span>
                <span className="ml-auto text-slate-600">{showCompetitors ? "▲" : "▼"}</span>
                {competitors.length > 0 && (
                  <span className="bg-brand-500/20 text-brand-400 text-[10px] px-1.5 py-0.5 rounded-full">
                    {competitors.length}
                  </span>
                )}
              </button>

              {showCompetitors && (
                <div className="mt-3 space-y-3 animate-fade-in">
                  <p className="text-[11px] text-slate-600 leading-relaxed">
                    L'agent scrape les sections commentaires de ces comptes et contacte les personnes qui posent des questions ou interagissent.
                  </p>

                  {/* Competitor list */}
                  {competitors.length > 0 && (
                    <div className="space-y-1.5">
                      {competitors.map(c => (
                        <div key={c.handle} className="flex items-center justify-between bg-slate-800/60 rounded-lg px-3 py-2">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-xs">
                              {c.platform === "instagram" ? "📸" : c.platform === "tiktok" ? "🎵" : c.platform === "twitter" ? "𝕏" : "💼"}
                            </span>
                            <span className="text-xs text-slate-300 truncate">@{c.handle}</span>
                          </div>
                          <button
                            onClick={() => removeCompetitor.mutate(c.handle)}
                            disabled={removeCompetitor.isPending}
                            className="text-slate-600 hover:text-red-400 text-sm ml-2 transition-colors flex-shrink-0"
                          >×</button>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Add competitor */}
                  <div className="flex gap-2">
                    <select
                      value={competitorPlatform}
                      onChange={e => setCompetitorPlatform(e.target.value)}
                      className="bg-slate-800 border border-[#2a2a2a] rounded-lg px-2 py-2 text-xs focus:outline-none focus:border-brand-500 flex-shrink-0"
                    >
                      <option value="instagram">📸 IG</option>
                      <option value="tiktok">🎵 TT</option>
                      <option value="twitter">𝕏 TW</option>
                      <option value="linkedin">💼 LI</option>
                    </select>
                    <input
                      value={competitorUrl}
                      onChange={e => setCompetitorUrl(e.target.value)}
                      onKeyDown={e => e.key === "Enter" && competitorUrl && addCompetitor.mutate()}
                      placeholder="instagram.com/concurrent ou @pseudo"
                      className="flex-1 bg-slate-800 border border-[#2a2a2a] rounded-lg px-3 py-2 text-xs focus:outline-none focus:border-brand-500 min-w-0"
                    />
                    <button
                      onClick={() => addCompetitor.mutate()}
                      disabled={!competitorUrl.trim() || addCompetitor.isPending || competitors.length >= 10}
                      className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-xs transition-colors disabled:opacity-40 flex-shrink-0"
                    >
                      {addCompetitor.isPending ? "…" : "+"}
                    </button>
                  </div>
                  {addCompetitor.isError && (
                    <p className="text-red-400 text-[10px]">
                      {(addCompetitor.error as any)?.response?.data?.detail || "Erreur"}
                    </p>
                  )}
                </div>
              )}
            </div>
          </>
        )}

        {!agentEnabled && (
          <p className="text-xs text-slate-600 mt-3">
            Activez l'agent pour démarrer la prospection automatique toutes les{" "}
            {agentStatus?.frequency_hours ?? 6}h sur {agentStatus?.platforms?.length ?? 5} plateformes.
          </p>
        )}
      </div>

      {/* ── Prospection par URL ── */}
      <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl p-6">
        <h2 className="font-semibold text-white mb-1">🚀 Lancer la prospection</h2>
        <p className="text-xs text-slate-500 mb-5">Collez l'URL d'un profil TikTok ou Instagram — l'IA le qualifie et rédige le DM automatiquement.</p>

        <div className="mb-4">
          <label className="text-xs text-slate-400 mb-1.5 block">URL du profil</label>
          <input
            value={profileUrl}
            onChange={e => { setProfileUrl(e.target.value); setUrlSuccess(null); fromUrl.reset(); }}
            className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500 transition-colors"
            placeholder="https://tiktok.com/@nomduprofil ou https://instagram.com/nomduprofil"
          />
        </div>

        <label className="flex items-center gap-2 cursor-pointer mb-5">
          <input type="checkbox" checked={autoWrite} onChange={e => setAutoWrite(e.target.checked)}
            className="w-4 h-4 accent-brand-500" />
          <span className="text-sm text-slate-300">Générer le message automatiquement</span>
        </label>

        {fromUrl.isError && (
          <p className="text-red-400 text-xs mb-3 bg-red-950/40 border border-red-900/40 rounded-lg px-3 py-2">
            {(fromUrl.error as any)?.response?.data?.detail || "Échec de la prospection."}
          </p>
        )}
        {urlSuccess && (
          <p className="text-emerald-400 text-xs mb-3 bg-emerald-950/40 border border-emerald-900/40 rounded-lg px-3 py-2">
            ✓ {urlSuccess}
          </p>
        )}

        <button
          onClick={() => fromUrl.mutate()}
          disabled={fromUrl.isPending || !profileUrl.trim()}
          className="w-full py-3 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors">
          {fromUrl.isPending
            ? (autoWrite ? "Qualification + rédaction du message…" : "Qualification en cours…")
            : "🚀 Qualifier ce profil"}
        </button>
      </div>

      <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl p-6">
        <h2 className="font-semibold text-white mb-1">Trouver de nouveaux leads</h2>
        <p className="text-xs text-slate-500 mb-5">Scraper des profils publics par hashtag et les qualifier automatiquement.</p>

        {/* Platform tabs */}
        <div className="mb-5">
          <label className="text-xs text-slate-400 mb-2 block">Plateforme</label>
          <div className="grid grid-cols-5 gap-1.5">
            {Object.entries(PLATFORM_CONFIG).map(([v, c]) => (
              <button key={v} onClick={() => setPlatform(v)}
                className={`py-2 rounded-xl border text-xs font-medium transition-all flex flex-col items-center gap-0.5 ${
                  platform === v
                    ? "bg-white/[0.04] border-brand-600 text-brand-300"
                    : "bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600"}`}>
                <span className="text-base leading-none">{c.icon}</span>
                <span className="hidden sm:block">{c.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* AI suggestions — shown immediately */}
        <div className="mb-5">
          <div className="flex justify-between items-center mb-2">
            <label className="text-xs text-slate-400">{cfg.termLabel} *</label>
            <button onClick={() => suggest.mutate()} disabled={suggest.isPending}
              className="text-[10px] text-brand-400 hover:text-brand-300 transition-colors">
              {suggest.isPending ? "Génération…" : "↺ Rafraîchir les suggestions IA"}
            </button>
          </div>
          <div className="flex gap-2 mb-2">
            <div className="flex flex-1 items-center bg-slate-800 border border-[#2a2a2a] rounded-xl overflow-hidden focus-within:border-brand-500 transition-colors">
              <span className="pl-3 text-slate-500 text-sm">#</span>
              <input value={tagInput} onChange={e => setTagInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && (e.preventDefault(), addTag())}
                className="flex-1 bg-transparent px-2 py-2.5 text-sm focus:outline-none"
                placeholder={cfg.placeholder} />
            </div>
            <button onClick={() => addTag()}
              className="px-4 py-2.5 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm transition-colors">+</button>
          </div>

          {/* Selected tags */}
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {tags.map(t => (
                <span key={t} className="flex items-center gap-1 bg-white/[0.05] border border-[#2a2a2a] text-brand-400 text-xs px-2.5 py-1 rounded-full">
                  #{t}
                  <button onClick={() => setTags(p => p.filter(x => x !== t))} className="text-slate-400 hover:text-white">×</button>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Options */}
        <div className="flex gap-4 items-end mb-5">
          <div className="flex-1">
            <label className="text-xs text-slate-400 mb-1 block">Profils max</label>
            <select value={maxResults} onChange={e => setMaxResults(Number(e.target.value))}
              className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500">
              {[10,20,50,100].map(n => <option key={n} value={n}>{n} profils</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2 cursor-pointer pb-1">
            <input type="checkbox" checked={autoQualify} onChange={e => setAutoQualify(e.target.checked)}
              className="w-4 h-4 accent-brand-500" />
            <span className="text-sm text-slate-300 whitespace-nowrap">Auto-qualifier</span>
          </label>
        </div>

        {run.isError && (
          <p className="text-red-400 text-xs mb-3 bg-red-950/40 border border-red-900/40 rounded-lg px-3 py-2">
            {(run.error as any)?.response?.data?.detail || "Échec du démarrage de la tâche."}
          </p>
        )}

        <button onClick={() => run.mutate()} disabled={run.isPending || !tags.length || running}
          className="w-full py-3 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors">
          {run.isPending ? "Démarrage…" : running ? "Recherche en cours…" : `🔍 Trouver des clients sur ${cfg.icon} ${cfg.label}`}
        </button>
        {running && (
          <p className="text-center text-xs text-amber-400 mt-2">
            Recherche en arrière-plan — les leads apparaîtront dans votre pipeline une fois terminé.
          </p>
        )}
      </div>

      {/* Historique des tâches */}
      <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl p-6">
        <h3 className="font-medium text-white mb-4 text-sm">Tâches récentes</h3>
        {jobs.length === 0 ? (
          <p className="text-xs text-slate-600 text-center py-6">Aucune tâche — lancez une recherche ci-dessus.</p>
        ) : (
          <div className="space-y-3">
            {jobs.map(j => (
              <div key={j.id} className="flex items-center justify-between gap-4 py-3 border-b border-[#2a2a2a] last:border-0">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-[10px] font-bold uppercase ${statusColors[j.status]}`}>
                      {j.status === "running" ? "⟳ en cours" : statusLabels[j.status] || j.status}
                    </span>
                    <span className="text-xs text-slate-600 capitalize">{j.platform}</span>
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  <p className="text-sm font-semibold text-white">{j.leads_found} leads</p>
                  <p className="text-[10px] text-slate-600">{j.started_at ? new Date(j.started_at).toLocaleTimeString() : ""}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
   ONGLET RELANCES
════════════════════════════════════════════════════════════════════ */
const DAY_CONFIG = {
  2: { label: "J+2", sublabel: "Rappel léger",       badge: "bg-amber-950/70 border-amber-800/60 text-amber-400", ring: "ring-amber-800/20" },
  4: { label: "J+4", sublabel: "Valeur ajoutée",      badge: "bg-[#1a1a1a] border-white/[0.10] text-brand-400", ring: "ring-white/[0.03]" },
  7: { label: "J+7", sublabel: "Dernière relance",    badge: "bg-rose-950/70 border-rose-900/60 text-rose-400",    ring: "ring-rose-900/20" },
} as const;

function msgKey(day: number): keyof FollowupDue {
  return day === 2 ? "followup_d2_message" : day === 4 ? "followup_d4_message" : "followup_d7_message";
}

function daysSince(iso: string) {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return d === 1 ? "il y a 1 jour" : `il y a ${d} jours`;
}

function FollowupRow({ item }: { item: FollowupDue }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [localMessage, setLocalMessage] = useState<string | undefined>(
    item[msgKey(item.due_day)] as string | undefined
  );
  const [sent, setSent] = useState(false);

  const cfg = DAY_CONFIG[item.due_day as 2 | 4 | 7];
  const refresh = () => { qc.invalidateQueries({ queryKey: ["followups"] }); qc.invalidateQueries({ queryKey: ["leads"] }); };

  const generate = useMutation({
    mutationFn: () => followupsApi.generate(item.lead_id, item.due_day),
    onSuccess: ({ data }) => setLocalMessage(data.message),
  });

  const send = useMutation({
    mutationFn: () => followupsApi.send(item.lead_id, item.due_day),
    onSuccess: ({ data }) => {
      setLocalMessage(data.message);
      navigator.clipboard.writeText(data.message).catch(() => {});
      setSent(true);
      setTimeout(refresh, 800);
    },
  });

  return (
    <div className={`bg-slate-900 border border-[#2a2a2a] rounded-2xl overflow-hidden transition-all ${
      open ? `ring-1 ${cfg.ring}` : ""
    }`}>
      <div
        className="flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-slate-800/20 transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className={`flex-shrink-0 px-2.5 py-1 rounded-lg border text-[10px] font-bold ${cfg.badge}`}>
            {cfg.label}
            <span className="hidden sm:inline"> · {cfg.sublabel}</span>
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-100 truncate">{item.name || `@${item.handle}`}</p>
            <p className="text-[11px] text-slate-600">@{item.handle} · contacté {daysSince(item.messaged_at)}</p>
          </div>
        </div>
        <div className="flex items-center gap-3 ml-3 flex-shrink-0">
          {sent && <span className="text-[10px] text-emerald-400">✓ envoyé</span>}
          <span className="text-slate-700 text-xs">{open ? "▲" : "▼"}</span>
        </div>
      </div>

      {open && (
        <div className="border-t border-[#2a2a2a]/60 px-5 pt-4 pb-5 space-y-4 animate-fade-in">
          {/* Original DM context */}
          {item.outreach_message && (
            <div>
              <p className="text-[10px] font-medium text-slate-600 uppercase tracking-wider mb-1.5">DM original</p>
              <p className="text-xs text-slate-500 bg-slate-800/40 rounded-xl p-3 leading-relaxed line-clamp-2 italic">
                "{item.outreach_message}"
              </p>
            </div>
          )}

          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-[10px] font-medium text-slate-600 uppercase tracking-wider">Message de relance</p>
              {localMessage && !send.isPending && (
                <button
                  onClick={() => generate.mutate()}
                  disabled={generate.isPending}
                  className="text-[10px] text-slate-600 hover:text-slate-400 transition-colors"
                >
                  {generate.isPending ? "Réécriture…" : "↻ Régénérer"}
                </button>
              )}
            </div>

            {localMessage ? (
              <div className="bg-slate-800/60 border border-[#2a2a2a]/40 rounded-xl p-4 text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">
                {localMessage}
              </div>
            ) : (
              <div className="bg-slate-800/30 border border-dashed border-slate-700/40 rounded-xl px-4 py-6 text-center">
                <p className="text-xs text-slate-600">
                  {generate.isPending ? "Rédaction en cours…" : "Le message sera généré à l'envoi"}
                </p>
              </div>
            )}
          </div>

          {(send.isError || generate.isError) && (
            <p className="text-red-400 text-xs bg-red-950/30 border border-red-900/40 rounded-xl px-3 py-2">
              {((send.error || generate.error) as any)?.response?.data?.detail || "Erreur lors de la génération"}
            </p>
          )}

          {sent ? (
            <div className="flex items-center gap-2 py-2.5 px-4 bg-emerald-950/30 border border-emerald-900/40 rounded-xl">
              <span className="text-emerald-400 text-sm">✓</span>
              <span className="text-sm text-emerald-400 font-medium">Copié dans le presse-papier · Marqué comme envoyé</span>
            </div>
          ) : (
            <button
              onClick={() => send.mutate()}
              disabled={send.isPending}
              className="w-full py-3 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-all duration-150"
            >
              {send.isPending
                ? (localMessage ? "Copie + marquage…" : "Génération + envoi…")
                : (localMessage ? "📋 Copier & marquer envoyé" : "✨ Générer & envoyer")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function FollowupsTab() {
  const { data: items = [], isLoading, refetch } = useQuery({
    queryKey: ["followups"],
    queryFn: () => followupsApi.due().then(r => r.data),
    refetchInterval: 30_000,
  });

  const byDay = (d: number) => items.filter(i => i.due_day === d);
  const total = items.length;

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="font-heading font-semibold text-white text-lg">File de relances</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            {total === 0 ? "Aucune relance en attente" : `${total} relance${total > 1 ? "s" : ""} à traiter`}
          </p>
        </div>
        <button onClick={() => refetch()} className="text-xs text-slate-600 hover:text-slate-400 transition-colors mt-1">
          ↻ Actualiser
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[1,2,3].map(i => <div key={i} className="h-16 bg-slate-900 rounded-2xl animate-pulse" />)}
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 bg-slate-900 border border-[#2a2a2a] rounded-2xl">
          <p className="text-3xl mb-3">🎉</p>
          <p className="font-heading font-semibold text-white mb-1">File vide</p>
          <p className="text-xs text-slate-500 max-w-xs mx-auto">
            Aucune relance en attente. Les leads apparaissent ici automatiquement J+2, J+4 et J+7 après votre premier contact.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3">
            {([2, 4, 7] as const).map(day => {
              const cfg = DAY_CONFIG[day];
              const count = byDay(day).length;
              return (
                <div key={day} className="bg-slate-900 border border-[#2a2a2a] rounded-xl px-4 py-3 text-center">
                  <p className={`font-heading text-2xl font-black ${count > 0 ? cfg.badge.split(" ").find(c => c.startsWith("text-")) : "text-slate-700"}`}>
                    {count}
                  </p>
                  <p className="text-[10px] text-slate-600 mt-0.5">{cfg.label} · {cfg.sublabel}</p>
                </div>
              );
            })}
          </div>

          <div className="space-y-3">
            {[7, 4, 2].flatMap(day => byDay(day)).map(item => (
              <FollowupRow key={`${item.lead_id}-${item.due_day}`} item={item} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
   ONGLET RÉPONSES
════════════════════════════════════════════════════════════════════ */
const CLASS_CONFIG: Record<Classification, { label: string; bg: string; border: string; text: string; icon: string }> = {
  POSITIF:      { label: "Positif",        bg: "bg-emerald-950/60", border: "border-emerald-800/50", text: "text-emerald-400", icon: "↗" },
  NEUTRE:       { label: "Neutre",          bg: "bg-slate-800/60",   border: "border-slate-700/50",   text: "text-slate-400",   icon: "→" },
  NEGATIF:      { label: "Négatif",         bg: "bg-rose-950/60",    border: "border-rose-900/50",    text: "text-rose-400",    icon: "✕" },
  SIGNAL_ACHAT: { label: "Signal d'achat!", bg: "bg-white/[0.05]",   border: "border-brand-500/30",    text: "text-brand-400",   icon: "🎯" },
};

const STAGE_LABELS: Record<string, string> = {
  replied: "Répondu", booked: "Réservé", closed: "Clôturé",
};

function RepliesTab({ leads }: { leads: Lead[] }) {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [replyText, setReplyText] = useState("");
  const [convHistory, setConvHistory] = useState("");
  const [search, setSearch] = useState("");
  const [analysis, setAnalysis] = useState<ReplyAnalysis | null>(null);
  const [copied, setCopied] = useState(false);

  const contactedLeads = leads.filter(l => l.stage === "contacted" || l.stage === "replied");
  const filteredLeads = search
    ? contactedLeads.filter(l =>
        l.name?.toLowerCase().includes(search.toLowerCase()) ||
        l.handle?.toLowerCase().includes(search.toLowerCase())
      )
    : contactedLeads;

  const selected = leads.find(l => l.id === selectedId) ?? null;

  const analyze = useMutation({
    mutationFn: () => pipelineApi.reply(selectedId!, { lead_reply: replyText, conversation_history: convHistory }),
    onSuccess: ({ data }) => {
      setAnalysis(data);
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
  });

  function selectLead(lead: Lead) {
    setSelectedId(lead.id);
    setReplyText("");
    setConvHistory("");
    setAnalysis(null);
    setCopied(false);
  }

  function copy(text: string) {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const inputCls = "w-full bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-brand-500 focus:shadow-glow-sm transition-all resize-none";

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-5">
        <h2 className="font-heading font-semibold text-white text-lg">Gestion des réponses</h2>
        <p className="text-xs text-slate-500 mt-0.5">
          Collez la réponse reçue — Claude la classifie et génère la réponse parfaite.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-5">
        {/* ── Lead selector ── */}
        <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl overflow-hidden flex flex-col" style={{ maxHeight: "72vh" }}>
          <div className="px-4 pt-4 pb-3 border-b border-[#2a2a2a]">
            <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-2">
              Leads contactés ({contactedLeads.length})
            </p>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-brand-500 transition-all"
              placeholder="Rechercher…"
            />
          </div>

          <div className="flex-1 overflow-y-auto scrollbar-thin p-2 space-y-1">
            {filteredLeads.length === 0 ? (
              <p className="text-xs text-slate-600 text-center py-8 px-4">
                {contactedLeads.length === 0
                  ? "Aucun lead contacté. Envoyez des DMs d'abord."
                  : "Aucun résultat."}
              </p>
            ) : filteredLeads.map(lead => (
              <button
                key={lead.id}
                onClick={() => selectLead(lead)}
                className={`w-full text-left px-3 py-2.5 rounded-xl transition-all ${
                  selectedId === lead.id
                    ? "bg-white/[0.04] border border-[#2a2a2a] shadow-glow-sm"
                    : "hover:bg-slate-800/50 border border-transparent"
                }`}
              >
                <p className="text-sm font-medium text-slate-200 truncate">{lead.name || `@${lead.handle}`}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  <p className="text-[11px] text-slate-600 truncate">@{lead.handle}</p>
                  {lead.reply_received && (
                    <span className="text-[9px] bg-emerald-950/60 text-emerald-500 border border-emerald-900/50 px-1.5 py-0.5 rounded-full flex-shrink-0">
                      déjà analysé
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          {!selected ? (
            <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl flex items-center justify-center" style={{ minHeight: "320px" }}>
              <div className="text-center px-8">
                <p className="text-3xl mb-3">💬</p>
                <p className="font-heading font-semibold text-slate-400 mb-1">Sélectionnez un lead</p>
                <p className="text-xs text-slate-600">Choisissez un lead contacté dans la liste pour analyser sa réponse.</p>
              </div>
            </div>
          ) : (
            <>
              {/* Lead info header */}
              <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl px-5 py-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-semibold text-white">{selected.name || `@${selected.handle}`}</p>
                    <p className="text-xs text-slate-500 mt-0.5">@{selected.handle} · {selected.platform}</p>
                  </div>
                  {selected.outreach_message && (
                    <div className="text-right hidden sm:block">
                      <p className="text-[10px] text-slate-600 mb-1">DM envoyé</p>
                      <p className="text-xs text-slate-500 max-w-[200px] truncate italic">"{selected.outreach_message}"</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Input form */}
              <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl p-5 space-y-4">
                <div>
                  <label className="text-[11px] font-medium text-slate-500 uppercase tracking-wider block mb-2">
                    Réponse reçue *
                  </label>
                  <textarea
                    value={replyText}
                    onChange={e => { setReplyText(e.target.value); setAnalysis(null); }}
                    rows={4}
                    className={inputCls}
                    placeholder="Collez ici le message reçu du lead…"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-medium text-slate-500 uppercase tracking-wider block mb-2">
                    Historique de conversation <span className="normal-case text-slate-700">(facultatif)</span>
                  </label>
                  <textarea
                    value={convHistory}
                    onChange={e => setConvHistory(e.target.value)}
                    rows={2}
                    className={inputCls}
                    placeholder={"Vous : [votre DM]\nEux : [leur réponse précédente]\n…"}
                  />
                </div>

                {analyze.isError && (
                  <p className="text-red-400 text-xs bg-red-950/30 border border-red-900/40 rounded-xl px-3 py-2">
                    {(analyze.error as any)?.response?.data?.detail || "Erreur lors de l'analyse."}
                  </p>
                )}

                <button
                  onClick={() => analyze.mutate()}
                  disabled={analyze.isPending || !replyText.trim()}
                  className="w-full py-3 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-all duration-150"
                >
                  {analyze.isPending ? "Analyse en cours…" : "🤖 Analyser et générer la réponse"}
                </button>
              </div>

              {analysis && (() => {
                const cfg = CLASS_CONFIG[analysis.classification];
                const newStage = analysis.classification === "SIGNAL_ACHAT" ? "booked"
                               : analysis.classification === "NEGATIF" ? "closed"
                               : "replied";
                return (
                  <div className={`border rounded-2xl p-5 space-y-4 animate-slide-up ${cfg.bg} ${cfg.border}`}>
                    <div className="flex items-start gap-3">
                      <div className={`flex-shrink-0 flex items-center gap-2 px-3 py-1.5 rounded-xl border ${cfg.bg} ${cfg.border}`}>
                        <span className="text-base">{cfg.icon}</span>
                        <span className={`text-sm font-bold ${cfg.text}`}>{cfg.label}</span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-slate-400 leading-relaxed">{analysis.reasoning}</p>
                        <p className="text-[10px] text-slate-600 mt-1">
                          Lead déplacé vers : <span className="text-slate-400">{STAGE_LABELS[newStage] ?? newStage}</span>
                        </p>
                      </div>
                    </div>

                    {analysis.inject_calendly && (
                      <div className="flex items-center gap-2 bg-white/[0.03] border border-white/[0.06] rounded-xl px-3 py-2">
                        <span className="text-slate-400 text-sm">📅</span>
                        <p className="text-xs text-slate-400">Lien Calendly injecté dans la réponse</p>
                      </div>
                    )}

                    <div>
                      <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-2">Réponse suggérée</p>
                      <div className="bg-slate-900/60 border border-[#2a2a2a]/40 rounded-xl p-4 text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">
                        {analysis.suggested_reply}
                      </div>
                    </div>

                    <button
                      onClick={() => copy(analysis.suggested_reply)}
                      className={`w-full py-3 rounded-xl text-sm font-semibold transition-all duration-150 ${
                        copied
                          ? "bg-emerald-900/50 border border-emerald-800/50 text-emerald-400"
                          : "bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg text-white"
                      }`}
                    >
                      {copied ? "✓ Copié dans le presse-papiers !" : "📋 Copier la réponse"}
                    </button>
                  </div>
                );
              })()}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
   ONGLET WARMING SEQUENCE
════════════════════════════════════════════════════════════════════ */
const WARMING_STEPS = [
  { key: "comment_ready", label: "Commentaire prêt",  icon: "✍️", desc: "Commentaire IA généré — à poster sur son contenu." },
  { key: "commented",     label: "Commentaire posté", icon: "💬", desc: "Commentaire posté — attendre 24-48h avant d'envoyer le DM." },
  { key: "dm_ready",      label: "Prêt pour le DM",   icon: "📩", desc: "Le lead est chauffé — envoyez le DM maintenant." },
] as const;

type WarmingKey = (typeof WARMING_STEPS)[number]["key"];

function WarmingLeadCard({ lead, onUpdate }: { lead: Lead; onUpdate: () => void }) {
  const [copied, setCopied] = useState(false);
  const [open, setOpen] = useState(false);

  const warm = useMutation({
    mutationFn: () => pipelineApi.warm(lead.id),
    onSuccess: () => onUpdate(),
  });

  const markWarmed = useMutation({
    mutationFn: (status: string) => pipelineApi.markWarmed(lead.id, status),
    onSuccess: () => onUpdate(),
  });

  function copy(text: string) {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const currentStep = WARMING_STEPS.findIndex(s => s.key === lead.warming_status);
  const nextStep = WARMING_STEPS[currentStep + 1];
  const isPending = warm.isPending || markWarmed.isPending;

  return (
    <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl overflow-hidden">
      <div
        className="flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-slate-800/20 transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <div className="flex items-center gap-3 min-w-0">
          {/* Step indicator */}
          <div className="flex gap-1 flex-shrink-0">
            {WARMING_STEPS.map((s, i) => (
              <div key={s.key} className={`w-2 h-2 rounded-full transition-colors ${
                i <= currentStep ? "bg-brand-500" : "bg-slate-700"
              }`} />
            ))}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-100 truncate">{lead.name || `@${lead.handle}`}</p>
            <p className="text-[11px] text-slate-500">@{lead.handle} ·
              <span className="ml-1 text-brand-400">
                {currentStep >= 0 ? WARMING_STEPS[currentStep].label : "En cours"}
              </span>
            </p>
          </div>
        </div>
        <span className="text-slate-700 text-xs ml-3 flex-shrink-0">{open ? "▲" : "▼"}</span>
      </div>

      {open && (
        <div className="border-t border-[#2a2a2a]/60 px-5 pt-4 pb-5 space-y-4 animate-fade-in">
          {/* Progress bar */}
          <div className="space-y-2">
            {WARMING_STEPS.map((s, i) => (
              <div key={s.key} className={`flex items-start gap-3 ${i > currentStep ? "opacity-30" : ""}`}>
                <span className={`flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs ${
                  i < currentStep ? "bg-brand-500/30 text-brand-400"
                  : i === currentStep ? "bg-brand-500/20 border border-brand-500/50 text-brand-400"
                  : "bg-slate-800 text-slate-600"
                }`}>
                  {i < currentStep ? "✓" : s.icon}
                </span>
                <div>
                  <p className="text-xs font-medium text-slate-300">{s.label}</p>
                  <p className="text-[11px] text-slate-600">{s.desc}</p>
                </div>
              </div>
            ))}
          </div>

          {/* Warming comment */}
          {lead.warming_comment ? (
            <div>
              <p className="text-[10px] font-medium text-slate-600 uppercase tracking-wider mb-2">Commentaire warming</p>
              <div className="bg-slate-800/60 border border-[#2a2a2a]/40 rounded-xl p-4 text-sm text-slate-200 leading-relaxed">
                {lead.warming_comment}
              </div>
              <button
                onClick={() => copy(lead.warming_comment!)}
                className={`mt-2 w-full py-2 rounded-xl text-xs font-semibold transition-colors ${
                  copied
                    ? "bg-emerald-900/40 border border-emerald-800/50 text-emerald-400"
                    : "bg-slate-800 hover:bg-slate-700 text-slate-300"
                }`}
              >
                {copied ? "✓ Copié !" : "📋 Copier le commentaire"}
              </button>
            </div>
          ) : (
            <button
              onClick={() => warm.mutate()}
              disabled={isPending}
              className="w-full py-2.5 bg-brand-500/20 hover:bg-brand-500/30 border border-brand-500/30 text-brand-400 text-sm rounded-xl transition-colors disabled:opacity-40"
            >
              {warm.isPending ? "Génération…" : "✍️ Générer le commentaire warming"}
            </button>
          )}

          {warm.isError && (
            <p className="text-red-400 text-xs">{(warm.error as any)?.response?.data?.detail || "Erreur"}</p>
          )}

          {/* Advance step */}
          {nextStep && (
            <button
              onClick={() => markWarmed.mutate(nextStep.key)}
              disabled={isPending}
              className="w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors"
            >
              {markWarmed.isPending ? "Mise à jour…" : `${nextStep.icon} Marquer : ${nextStep.label}`}
            </button>
          )}

          {/* Mark as DM sent (move to dm flow) */}
          {lead.warming_status === "dm_ready" && (
            <button
              onClick={() => markWarmed.mutate("none")}
              disabled={isPending}
              className="w-full py-2 bg-slate-800 hover:bg-slate-700 text-slate-400 text-xs rounded-xl transition-colors disabled:opacity-40"
            >
              ✓ DM envoyé — retirer de la file
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function WarmingTab({ leads }: { leads: Lead[] }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["leads"] });

  const warmingLeads = leads.filter(l =>
    l.warming_status && l.warming_status !== "none"
  );

  const byStatus = (key: WarmingKey) => warmingLeads.filter(l => l.warming_status === key);

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="font-heading font-semibold text-white text-lg">File de warming</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            {warmingLeads.length === 0
              ? "Aucun lead en cours de warming"
              : `${warmingLeads.length} lead${warmingLeads.length > 1 ? "s" : ""} en séquence warming`}
          </p>
        </div>
      </div>

      {/* Stats */}
      {warmingLeads.length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          {WARMING_STEPS.map(s => {
            const count = byStatus(s.key).length;
            return (
              <div key={s.key} className="bg-slate-900 border border-[#2a2a2a] rounded-xl px-4 py-3 text-center">
                <p className="text-2xl font-black text-white">{count}</p>
                <p className="text-[10px] text-slate-500 mt-0.5">{s.label}</p>
              </div>
            );
          })}
        </div>
      )}

      {warmingLeads.length === 0 ? (
        <div className="text-center py-16 bg-slate-900 border border-[#2a2a2a] rounded-2xl">
          <p className="text-3xl mb-3">🔥</p>
          <p className="font-heading font-semibold text-white mb-1">File vide</p>
          <p className="text-xs text-slate-500 max-w-xs mx-auto">
            Les leads entrent dans la file warming via le modal du lead (onglet Warming). L'agent autonome les ajoute automatiquement pour les profils à fort potentiel.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {/* Order: dm_ready first, then commented, then comment_ready */}
          {(["dm_ready", "commented", "comment_ready"] as WarmingKey[]).flatMap(key =>
            byStatus(key).map(lead => (
              <WarmingLeadCard key={lead.id} lead={lead} onUpdate={refresh} />
            ))
          )}
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
   ANALYTICS + ONBOARDING
════════════════════════════════════════════════════════════════════ */
const pct = (n: number) => `${Math.round(n * 100)} %`;

function StatCard({ label, value, sub, accent }: {
  label: string; value: string | number; sub?: string; accent?: string;
}) {
  return (
    <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl p-5 flex flex-col gap-1">
      <p className="text-xs text-slate-500 uppercase tracking-wider">{label}</p>
      <p className={`text-3xl font-black ${accent ?? "text-white"}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

function MiniBar({ label, value, max, color }: {
  label: string; value: number; max: number; color: string;
}) {
  const pctW = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-slate-400 w-24 flex-shrink-0 truncate">{label}</span>
      <div className="flex-1 bg-slate-800 rounded-full h-1.5 overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pctW}%` }} />
      </div>
      <span className="text-xs font-semibold text-slate-300 w-6 text-right">{value}</span>
    </div>
  );
}

function OnboardingChecklist({ ob }: { ob: AnalyticsData["onboarding"] }) {
  const steps = [
    { label: "Compte créé",           done: ob.account_created },
    { label: "Niche & offre définies", done: ob.niche_set },
    { label: "Premier lead prospecté", done: ob.first_lead },
    { label: "Premier DM envoyé",      done: ob.first_dm },
    { label: "Premier appel booké",    done: ob.first_booking },
  ];
  const count = steps.filter(s => s.done).length;
  const allDone = count === steps.length;

  return (
    <div className={`bg-slate-900 border rounded-2xl p-5 ${allDone ? "border-emerald-800/50" : "border-slate-800"}`}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="font-heading font-bold text-white text-sm">
            {allDone ? "🎉 Objectif atteint !" : "Checklist de démarrage"}
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">{count} / {steps.length} étapes complètes</p>
        </div>
        <div className="w-10 h-10 rounded-full border-2 border-slate-700 flex items-center justify-center text-xs font-black text-white">
          {Math.round((count / steps.length) * 100)}%
        </div>
      </div>
      <div className="space-y-2">
        {steps.map(step => (
          <div key={step.label} className="flex items-center gap-3">
            <span className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[11px] ${
              step.done ? "bg-emerald-500/20 text-emerald-400" : "bg-slate-800 text-slate-600"
            }`}>
              {step.done ? "✓" : "○"}
            </span>
            <span className={`text-sm ${step.done ? "text-slate-300" : "text-slate-500"}`}>{step.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function AnalyticsTab({ coach }: { coach: { offer_price?: number | null } }) {
  const qc = useQueryClient();
  const [priceInput, setPriceInput] = useState(
    coach.offer_price != null ? String(coach.offer_price) : ""
  );
  const [priceSaved, setPriceSaved] = useState(false);

  const { data: stats, isLoading } = useQuery({
    queryKey: ["analytics"],
    queryFn: () => analyticsApi.get().then(r => r.data),
    refetchInterval: 60_000,
  });

  const savePrice = useMutation({
    mutationFn: () => authApi.updateSettings({ offer_price: parseFloat(priceInput) || 0 }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["analytics"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      setPriceSaved(true);
      setTimeout(() => setPriceSaved(false), 2000);
    },
  });

  if (isLoading || !stats) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-600 text-sm animate-pulse">
        Calcul des statistiques…
      </div>
    );
  }

  const maxHashtag = Math.max(...stats.top_hashtags.map(h => h.leads), 1);
  const maxConv    = Math.max(...stats.followup_conversions.map(c => c.count), 1);
  const convColors = ["bg-slate-500", "bg-sky-500", "bg-brand-500", "bg-amber-500"];

  const forecast = stats.pipeline_forecast;
  const timingReady = stats.timing_ready ?? [];
  const escalations = stats.escalation_alerts ?? [];

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <h2 className="font-heading font-extrabold text-2xl text-white">Analytics</h2>

      {/* ── Intelligence alerts ── */}
      {(timingReady.length > 0 || escalations.length > 0) && (
        <div className="grid sm:grid-cols-2 gap-4">
          {/* Timing ready */}
          {timingReady.length > 0 && (
            <div className="bg-slate-900 border border-brand-900/40 rounded-2xl p-4">
              <p className="text-xs font-semibold text-brand-400 mb-2">🕐 Contacter maintenant</p>
              <p className="text-[11px] text-slate-500 mb-3">Ces leads sont actifs à cette heure-ci.</p>
              <div className="space-y-2">
                {timingReady.slice(0, 5).map(l => (
                  <div key={l.lead_id} className="flex items-center justify-between">
                    <span className="text-xs text-slate-300 truncate">{l.name}</span>
                    <span className="text-[10px] text-slate-500 ml-2 flex-shrink-0">
                      score {l.score.toFixed(0)} · {l.best_contact_time}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Escalation alerts */}
          {escalations.length > 0 && (
            <div className="bg-slate-900 border border-red-900/40 rounded-2xl p-4">
              <p className="text-xs font-semibold text-red-400 mb-2">⚡ Douleur en escalade</p>
              <p className="text-[11px] text-slate-500 mb-3">Ces leads postent plus de signaux de douleur — contacter maintenant.</p>
              <div className="space-y-2">
                {escalations.slice(0, 5).map(l => (
                  <div key={l.lead_id} className="flex items-center justify-between">
                    <span className="text-xs text-slate-300 truncate">{l.name}</span>
                    <span className="text-[10px] text-red-400 ml-2 flex-shrink-0">
                      +{l.score_delta?.toFixed(0)} pts
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── KPIs ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="Leads · semaine"  value={stats.leads_this_week}  />
        <StatCard label="Leads · mois"     value={stats.leads_this_month} />
        <StatCard
          label="Taux de réponse"
          value={pct(stats.reply_rate)}
          sub={`${(stats.by_stage.replied ?? 0) + (stats.by_stage.booked ?? 0) + (stats.by_stage.closed ?? 0)} leads ont répondu`}
          accent="text-sky-400"
        />
        <StatCard
          label="Taux de réservation"
          value={pct(stats.booking_rate)}
          sub={`${(stats.by_stage.booked ?? 0) + (stats.by_stage.closed ?? 0)} appels bookés`}
          accent="text-emerald-400"
        />
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        {/* Top hashtags */}
        <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl p-5">
          <h3 className="font-heading font-bold text-sm text-white mb-4">Top hashtags</h3>
          {stats.top_hashtags.length === 0 ? (
            <p className="text-xs text-slate-500">Aucune campagne de prospection terminée.</p>
          ) : (
            <div className="space-y-3">
              {stats.top_hashtags.map(h => (
                <MiniBar key={h.tag} label={`#${h.tag}`} value={Math.round(h.leads)} max={maxHashtag} color="bg-brand-500" />
              ))}
            </div>
          )}
        </div>

        {/* Follow-up conversions */}
        <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl p-5">
          <h3 className="font-heading font-bold text-sm text-white mb-1">Relances → réponses</h3>
          <p className="text-xs text-slate-500 mb-4">Quelle relance a déclenché la réponse ?</p>
          {stats.followup_conversions.every(c => c.count === 0) ? (
            <p className="text-xs text-slate-500">Pas encore de données de conversion.</p>
          ) : (
            <div className="space-y-3">
              {stats.followup_conversions.map((c, i) => (
                <MiniBar key={c.label} label={c.label} value={c.count} max={maxConv} color={convColors[i] ?? "bg-slate-500"} />
              ))}
            </div>
          )}
          <div className="mt-4 pt-4 border-t border-[#2a2a2a] flex gap-4 text-xs text-slate-500">
            <span>Relances envoyées :</span>
            <span className="text-slate-300">D+2 <b>{stats.followup_sent.d2}</b></span>
            <span className="text-slate-300">D+4 <b>{stats.followup_sent.d4}</b></span>
            <span className="text-slate-300">D+7 <b>{stats.followup_sent.d7}</b></span>
          </div>
        </div>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        {/* Revenue */}
        <div className="bg-slate-900 border border-[#2a2a2a] rounded-2xl p-5 space-y-4">
          <h3 className="font-heading font-bold text-sm text-white">Projection de revenus</h3>
          <div>
            <label className="text-xs text-slate-400 mb-1.5 block">Prix de votre offre (€)</label>
            <div className="flex gap-2">
              <input type="number" min="0" value={priceInput} onChange={e => setPriceInput(e.target.value)}
                placeholder="ex. 2000"
                className="flex-1 bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm
                           focus:outline-none focus:border-brand-500 transition-colors"
              />
              <button
                onClick={() => savePrice.mutate()}
                disabled={savePrice.isPending}
                className="px-4 py-2 bg-brand-500 hover:bg-brand-400 disabled:opacity-50
                           rounded-xl text-sm font-semibold transition-colors"
              >
                {priceSaved ? "✓" : "OK"}
              </button>
            </div>
          </div>
          <div className="bg-slate-800/60 border border-[#2a2a2a]/50 rounded-xl p-4 flex items-center justify-between">
            <div>
              <p className="text-xs text-slate-500">Deals closés</p>
              <p className="text-2xl font-black text-white">{stats.closed_leads}</p>
            </div>
            <div className="text-slate-700 text-2xl">×</div>
            <div>
              <p className="text-xs text-slate-500">Prix offre</p>
              <p className="text-2xl font-black text-white">
                {stats.offer_price > 0 ? `${stats.offer_price.toLocaleString("fr-FR")} €` : "—"}
              </p>
            </div>
            <div className="text-slate-700 text-2xl">=</div>
            <div>
              <p className="text-xs text-slate-500">Revenus générés</p>
              <p className={`text-2xl font-black ${stats.projected_mrr > 0 ? "text-emerald-400" : "text-slate-600"}`}>
                {stats.projected_mrr > 0 ? `${stats.projected_mrr.toLocaleString("fr-FR")} €` : "—"}
              </p>
            </div>
          </div>
        </div>

        {/* Pipeline forecast */}
        {forecast && forecast.active_leads > 0 && (
          <div className="bg-slate-900 border border-brand-900/30 rounded-2xl p-5 space-y-3 col-span-full sm:col-span-1">
            <h3 className="font-heading font-bold text-sm text-white">Prévision pipeline</h3>
            <p className="text-[11px] text-slate-500">
              Valeur pondérée des {forecast.active_leads} leads actifs selon leur score et le taux de conversion historique.
            </p>
            <div className="flex items-end gap-2">
              <p className={`text-3xl font-black ${forecast.weighted_value > 0 ? "text-brand-400" : "text-slate-600"}`}>
                {forecast.weighted_value > 0 ? `${forecast.weighted_value.toLocaleString("fr-FR")} €` : "—"}
              </p>
              <p className="text-xs text-slate-500 mb-1">potentiel estimé</p>
            </div>
            <p className="text-[10px] text-slate-600">
              Basé sur : score ≥80 → 30% · 60-79 → 15% · 40-59 → 7% · &lt;40 → 2%
            </p>
          </div>
        )}

        {/* Onboarding checklist */}
        <OnboardingChecklist ob={stats.onboarding} />
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
   ONGLET AUTOMATISATIONS
════════════════════════════════════════════════════════════════════ */
function AutomationsTab() {
  const qc = useQueryClient();
  const { data: triggers = [] } = useQuery({
    queryKey: ["keyword-triggers"],
    queryFn: () => keywordTriggersApi.list().then(r => r.data),
  });
  const { data: stats } = useQuery({
    queryKey: ["trigger-stats"],
    queryFn: () => keywordTriggersApi.stats().then(r => r.data),
    refetchInterval: 30_000,
  });

  const fire = useMutation({
    mutationFn: ({ id, handle }: { id: number; handle: string }) =>
      keywordTriggersApi.fire(id, handle),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["keyword-triggers"] });
      qc.invalidateQueries({ queryKey: ["trigger-stats"] });
      if (data.data.dm_url) {
        navigator.clipboard.writeText(data.data.message).catch(() => {});
        window.open(data.data.dm_url, "_blank", "noopener,noreferrer");
      }
    },
  });

  const [handleInputs, setHandleInputs] = useState<Record<number, string>>({});

  return (
    <div className="space-y-6">
      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-4">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">DMs envoyés via mots-clés</p>
            <p className="text-2xl font-black text-brand-400">{stats.total_triggers}</p>
          </div>
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-4">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Mots-clés actifs</p>
            <p className="text-2xl font-black text-white">{stats.active_keywords}</p>
          </div>
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-4">
            <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Top mot-clé</p>
            <p className="text-lg font-black text-emerald-400">
              {stats.top_keywords[0]?.keyword || "—"}
            </p>
          </div>
        </div>
      )}

      {/* Keyword triggers list */}
      <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#2a2a2a]">
          <h3 className="font-semibold text-sm text-white">Déclencheurs actifs</h3>
          <a href="/settings" className="text-xs text-brand-400 hover:text-brand-300 transition-colors">
            Gérer →
          </a>
        </div>

        {triggers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center px-6">
            <p className="text-3xl mb-3">⚡</p>
            <p className="text-sm font-semibold text-white mb-1">Aucun déclencheur configuré</p>
            <p className="text-xs text-slate-500 mb-4">
              Configurez des mots-clés dans les Paramètres pour automatiser vos DMs depuis les commentaires.
            </p>
            <a href="/settings"
              className="px-4 py-2 bg-brand-500 hover:bg-brand-400 rounded-xl text-xs font-semibold transition-colors text-white">
              Configurer les mots-clés →
            </a>
          </div>
        ) : (
          <div className="divide-y divide-[#1f1f1f]">
            {triggers.map(t => (
              <div key={t.id} className="px-5 py-4">
                <div className="flex items-start gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-bold text-brand-400 bg-brand-500/10 px-2.5 py-0.5 rounded-full">
                        {t.keyword}
                      </span>
                      <span className="text-xs text-slate-500">{t.platform}</span>
                      {t.active ? (
                        <span className="text-[10px] text-emerald-400 bg-emerald-950/40 border border-emerald-900/50 px-1.5 py-0.5 rounded-full">Actif</span>
                      ) : (
                        <span className="text-[10px] text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded-full">Pausé</span>
                      )}
                    </div>
                    {t.lead_magnet && (
                      <p className="text-xs text-slate-400 mb-1">Lead magnet : {t.lead_magnet.title}</p>
                    )}
                    <p className="text-xs text-slate-500">
                      {t.trigger_count} déclenchement{t.trigger_count !== 1 ? "s" : ""} total
                      {t.last_triggered_at && ` · Dernier : ${new Date(t.last_triggered_at).toLocaleDateString("fr-FR")}`}
                    </p>
                  </div>

                  {/* Manual fire section */}
                  <div className="flex items-center gap-2">
                    <input
                      value={handleInputs[t.id] || ""}
                      onChange={e => setHandleInputs(h => ({ ...h, [t.id]: e.target.value }))}
                      placeholder="@handle du commentateur"
                      className="bg-slate-900 border border-[#2a2a2a] rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-brand-500 w-44 transition-colors"
                    />
                    <button
                      onClick={() => fire.mutate({ id: t.id, handle: handleInputs[t.id] || "" })}
                      disabled={fire.isPending}
                      className="px-3 py-1.5 bg-brand-500/20 hover:bg-brand-500/30 border border-brand-500/30 text-brand-400 rounded-lg text-xs font-medium transition-colors whitespace-nowrap disabled:opacity-50">
                      ⚡ Envoyer DM
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* How it works */}
      <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5">
        <p className="text-xs font-semibold text-slate-400 mb-3">Comment ça marche</p>
        <div className="space-y-2">
          {[
            { step: "1", text: "Publiez un contenu avec un appel à l'action : \"Commente GUIDE pour recevoir ma méthode gratuite\"" },
            { step: "2", text: "Quelqu'un commente le mot-clé sur votre post" },
            { step: "3", text: "Entrez son @handle ici → LeanLead prépare le DM avec votre lead magnet" },
            { step: "4", text: "Cliquez Envoyer DM → le message est copié, la fenêtre DM s'ouvre automatiquement" },
          ].map(({ step, text }) => (
            <div key={step} className="flex gap-3 text-xs text-slate-400">
              <span className="w-5 h-5 rounded-full bg-brand-500/20 text-brand-400 flex items-center justify-center flex-shrink-0 font-bold">{step}</span>
              <span>{text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
   ONGLET CONTENT INTELLIGENCE
════════════════════════════════════════════════════════════════════ */
function ContentIntelligenceTab() {
  const qc = useQueryClient();
  const [platform, setPlatform] = useState("instagram");
  const [customHandle, setCustomHandle] = useState("");

  const { data: result, isLoading } = useQuery({
    queryKey: ["content-intel", platform],
    queryFn: () => contentIntelApi.get(platform).then(r => r.data),
    refetchInterval: 60_000,
  });

  const analyze = useMutation({
    mutationFn: () => contentIntelApi.analyze(platform, customHandle || undefined),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["content-intel"] }), 5000),
  });

  const analysis = result?.analysis;

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5">
        <p className="font-heading text-sm font-semibold text-white mb-4">Analyser votre contenu</p>
        <div className="flex gap-3">
          <select value={platform} onChange={e => setPlatform(e.target.value)}
            className="bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-brand-500 transition-colors text-slate-200">
            <option value="instagram">📸 Instagram</option>
            <option value="tiktok">🎵 TikTok</option>
            <option value="youtube">▶️ YouTube</option>
          </select>
          <input value={customHandle} onChange={e => setCustomHandle(e.target.value.replace("@", ""))}
            placeholder="@handle (optionnel — utilise votre compte par défaut)"
            className="flex-1 bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-brand-500 transition-colors text-slate-200 placeholder-slate-600" />
          <button onClick={() => analyze.mutate()} disabled={analyze.isPending}
            className="px-4 py-2 bg-brand-500 hover:bg-brand-400 rounded-xl text-sm font-semibold transition-colors disabled:opacity-50 whitespace-nowrap">
            {analyze.isPending ? "Analyse…" : "Analyser →"}
          </button>
        </div>
        {analyze.isSuccess && (
          <p className="text-xs text-emerald-400 mt-2">
            ✓ Analyse démarrée. Les résultats apparaîtront dans 30-60 secondes…
          </p>
        )}
        {result?.analyzed_at && (
          <p className="text-[10px] text-slate-600 mt-2">
            Dernière analyse : {new Date(result.analyzed_at).toLocaleString("fr-FR")}
            {result.handle && ` · @${result.handle}`}
            {analysis?._demo && " · (mode démo — ajoutez votre clé API pour une analyse réelle)"}
          </p>
        )}
      </div>

      {!analysis && !isLoading && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <p className="text-4xl mb-3">🔍</p>
          <p className="text-sm font-semibold text-white mb-1">Aucune analyse disponible</p>
          <p className="text-xs text-slate-500">
            Cliquez "Analyser" pour obtenir des insights sur votre contenu et générer plus de leads.
          </p>
        </div>
      )}

      {isLoading && !analysis && (
        <div className="flex items-center justify-center py-16">
          <div className="text-slate-500 text-sm">Chargement…</div>
        </div>
      )}

      {analysis && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {/* Content score */}
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5 sm:col-span-2">
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Score de génération de leads</p>
              <span className={`text-2xl font-black ${analysis.content_score >= 70 ? "text-emerald-400" : analysis.content_score >= 50 ? "text-amber-400" : "text-red-400"}`}>
                {analysis.content_score}/100
              </span>
            </div>
            <div className="w-full bg-slate-800 rounded-full h-2">
              <div className="h-2 rounded-full transition-all duration-500"
                style={{
                  width: `${analysis.content_score}%`,
                  background: analysis.content_score >= 70 ? "#34d399" : analysis.content_score >= 50 ? "#fbbf24" : "#f87171"
                }} />
            </div>
          </div>

          {/* Themes */}
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">🔥 Thèmes qui engagent</p>
            <ul className="space-y-1.5">
              {analysis.top_content_themes.map((t, i) => (
                <li key={i} className="text-xs text-slate-300 flex gap-2">
                  <span className="text-brand-400 font-bold">{i + 1}.</span> {t}
                </li>
              ))}
            </ul>
          </div>

          {/* Pain points */}
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">💬 Points de douleur exprimés</p>
            <ul className="space-y-1.5">
              {analysis.audience_pain_points.map((p, i) => (
                <li key={i} className="text-xs text-slate-300 flex gap-2">
                  <span className="text-red-400">•</span> {p}
                </li>
              ))}
            </ul>
          </div>

          {/* Suggested topics */}
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">🎯 Sujets à créer pour générer des leads</p>
            <ul className="space-y-2">
              {analysis.suggested_topics.map((t, i) => (
                <li key={i} className="text-xs text-slate-300 flex gap-2">
                  <span className="text-emerald-400 font-bold">{i + 1}.</span> {t}
                </li>
              ))}
            </ul>
          </div>

          {/* Lead gen hooks */}
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">⚡ Accroches qui convertissent</p>
            <ul className="space-y-2">
              {analysis.lead_gen_hooks.map((h, i) => (
                <li key={i} className="text-xs bg-slate-900 rounded-lg px-3 py-2 text-slate-200 border border-[#2a2a2a]">
                  "{h}"
                </li>
              ))}
            </ul>
          </div>

          {/* Keyword triggers suggestions */}
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">🔑 Mots-clés déclencheurs suggérés</p>
            <div className="flex flex-wrap gap-2">
              {analysis.keyword_triggers.map((k, i) => (
                <span key={i} className="text-xs font-bold text-brand-400 bg-brand-500/10 px-3 py-1 rounded-full border border-brand-500/20">
                  {k}
                </span>
              ))}
            </div>
            <p className="text-[10px] text-slate-600 mt-3">
              Configurez ces mots-clés dans l'onglet Automatisations pour les envoyer automatiquement.
            </p>
          </div>

          {/* Best time + questions */}
          <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-5">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">🕐 Meilleur moment pour poster</p>
            <p className="text-sm text-emerald-400 font-semibold">{analysis.best_posting_time}</p>
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mt-4 mb-3">❓ Questions fréquentes</p>
            <ul className="space-y-1.5">
              {analysis.audience_questions.map((q, i) => (
                <li key={i} className="text-xs text-slate-400">"{q}"</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
   TABLEAU DE BORD
════════════════════════════════════════════════════════════════════ */
type Tab = "pipeline" | "prospects" | "followups" | "warming" | "replies" | "analytics" | "automations";

function TrialBanner({ coach }: { coach: Coach }) {
  if (!coach.trial_active) return null;
  const days = coach.trial_days_left ?? 0;
  return (
    <div className="flex items-center justify-between gap-3 px-5 py-2.5 bg-brand-950/40 border-b border-brand-900/40">
      <p className="text-xs text-brand-300">
        Essai Agency gratuit — <strong>{days} jour{days > 1 ? "s" : ""} restant{days > 1 ? "s" : ""}</strong>. Accès à toutes les fonctionnalités Agency.
      </p>
      <a href="https://buy.stripe.com/agency-placeholder" target="_blank" rel="noopener noreferrer"
        className="text-[11px] font-semibold text-brand-400 hover:text-brand-300 whitespace-nowrap transition-colors">
        Passer à Agency €129/mois →
      </a>
    </div>
  );
}

function TrialExpiredModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
      <div className="w-full max-w-md bg-slate-900 border border-[#2a2a2a] rounded-2xl overflow-hidden shadow-2xl">
        <div className="p-8 text-center">
          <span className="text-5xl block mb-4">⏰</span>
          <h2 className="font-heading text-xl font-bold text-white mb-2">Votre essai Agency est terminé</h2>
          <p className="text-sm text-slate-400 mb-6">
            Continuez à générer des leads qualifiés en choisissant votre plan.
          </p>

          <div className="grid grid-cols-2 gap-4 mb-6">
            <div className="bg-slate-800 border border-[#2a2a2a] rounded-xl p-4 text-left">
              <p className="text-xs text-slate-400 mb-1">Growth</p>
              <p className="text-2xl font-bold text-white mb-3">€49<span className="text-sm font-normal text-slate-400">/mois</span></p>
              <ul className="text-xs text-slate-300 space-y-1.5">
                <li>✓ 200 leads/mois</li>
                <li>✓ 5 plateformes</li>
                <li>✓ Relances auto</li>
              </ul>
              <a href="https://buy.stripe.com/growth-placeholder" target="_blank" rel="noopener noreferrer"
                className="mt-4 block text-center px-4 py-2 border border-brand-500 text-brand-400 hover:bg-brand-500 hover:text-white rounded-xl text-sm font-semibold transition-colors">
                Choisir Growth
              </a>
            </div>
            <div className="bg-brand-950/40 border border-brand-800/50 rounded-xl p-4 text-left">
              <p className="text-xs text-brand-400 mb-1">Agency ⭐</p>
              <p className="text-2xl font-bold text-white mb-3">€129<span className="text-sm font-normal text-slate-400">/mois</span></p>
              <ul className="text-xs text-slate-300 space-y-1.5">
                <li>✓ Illimité</li>
                <li>✓ Agent autonome</li>
                <li>✓ Tout inclus</li>
              </ul>
              <a href="https://buy.stripe.com/agency-placeholder" target="_blank" rel="noopener noreferrer"
                className="mt-4 block text-center px-4 py-2 bg-brand-500 hover:bg-brand-400 text-white rounded-xl text-sm font-semibold transition-colors">
                Choisir Agency
              </a>
            </div>
          </div>

          <button onClick={onClose} className="text-xs text-slate-500 hover:text-slate-400 transition-colors">
            Continuer avec le plan Gratuit (20 leads)
          </button>
        </div>
      </div>
    </div>
  );
}

function EmailVerificationBanner({ coach }: { coach: Coach }) {
  const qc = useQueryClient();
  const [sent, setSent] = useState(false);
  const resend = useMutation({
    mutationFn: () => authApi.resendVerification(),
    onSuccess: () => { setSent(true); qc.invalidateQueries({ queryKey: ["me"] }); },
  });
  return (
    <div className="flex items-center justify-between gap-3 px-5 py-2.5 bg-blue-950/40 border-b border-blue-900/40">
      <p className="text-xs text-blue-300">
        Vérifiez votre email ({coach.email}) pour activer toutes les fonctionnalités.
      </p>
      <button
        onClick={() => resend.mutate()}
        disabled={resend.isPending || sent}
        className="text-[11px] font-semibold text-blue-400 hover:text-blue-300 whitespace-nowrap transition-colors disabled:opacity-50"
      >
        {sent ? "✓ Envoyé !" : resend.isPending ? "Envoi…" : "Renvoyer l'email →"}
      </button>
    </div>
  );
}

export default function Dashboard() {
  const nav = useNavigate();
  const [tab, setTab] = useState<Tab>("pipeline");
  const [showAdd, setShowAdd] = useState(false);
  const [search, setSearch] = useState("");
  const [showTrialExpired, setShowTrialExpired] = useState(false);

  const { data: coach } = useQuery({
    queryKey: ["me"],
    queryFn: () => authApi.me().then(r => r.data),
  });

  useEffect(() => {
    if (coach && !coach.trial_active && coach.trial_end_date && coach.plan === "free") {
      const dismissed = sessionStorage.getItem("trial_modal_dismissed");
      if (!dismissed) setShowTrialExpired(true);
    }
  }, [coach]);

  const { data: leads = [], isLoading } = useQuery({
    queryKey: ["leads"],
    queryFn: () => leadsApi.list().then(r => r.data),
    refetchInterval: 15_000,
  });

  const { data: followups = [] } = useQuery({
    queryKey: ["followups"],
    queryFn: () => followupsApi.due().then(r => r.data),
    refetchInterval: 60_000,
  });

  function logout() {
    localStorage.removeItem("ll_token");
    localStorage.removeItem("ll_name");
    nav("/", { replace: true });
  }

  const filtered = search
    ? leads.filter(l =>
        l.name?.toLowerCase().includes(search.toLowerCase()) ||
        l.handle?.toLowerCase().includes(search.toLowerCase()) ||
        l.bio?.toLowerCase().includes(search.toLowerCase())
      )
    : leads;

  const stats = {
    total:     leads.length,
    contacted: leads.filter(l => l.stage === "contacted").length,
    replied:   leads.filter(l => l.stage === "replied").length,
    booked:    leads.filter(l => l.stage === "booked").length,
  };

  const warmingCount = leads.filter(l => l.warming_status && l.warming_status !== "none").length;

  const PLAN_LIMITS: Record<string, number | null> = { free: 20, growth: 200, agency: null };
  const coachPlan = (coach?.plan || "free") as string;
  const planLimit = PLAN_LIMITS[coachPlan] ?? null;
  const planLimitReached = planLimit !== null && leads.length >= planLimit;

  const TABS: { id: Tab; label: string; badge?: number }[] = [
    { id: "pipeline",    label: "Pipeline" },
    { id: "prospects",   label: "Prospection" },
    { id: "followups",   label: "Relances", badge: followups.length || undefined },
    { id: "warming",     label: "Warming",  badge: warmingCount || undefined },
    { id: "replies",     label: "Réponses" },
    { id: "analytics",   label: "Analytics" },
    { id: "automations", label: "Automatisations" },
  ];

  const initials = (coach?.name ?? "?")[0].toUpperCase();

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col">
      <nav className="sticky top-0 z-40 flex items-center justify-between gap-4 px-5 py-3
                      border-b border-[#2a2a2a] bg-[#0a0a0a]/90 backdrop-blur-md">
        <div className="flex items-center gap-4 min-w-0">
          <span className="font-heading font-extrabold text-lg flex-shrink-0">
            Lean<span className="text-brand-400">Lead</span>
          </span>

          {/* Sélecteur d'onglets */}
          <div className="hidden sm:flex items-center gap-1 bg-slate-900 border border-[#2a2a2a] rounded-xl p-1">
            {TABS.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`relative px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${
                  tab === t.id ? "bg-slate-800 text-white shadow-glow-sm" : "text-slate-500 hover:text-slate-300"}`}>
                {t.label}
                {t.badge ? (
                  <span className="absolute -top-1.5 -right-1.5 w-4 h-4 flex items-center justify-center
                                   bg-amber-500 text-slate-900 text-[9px] font-black rounded-full">
                    {t.badge > 9 ? "9+" : t.badge}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-3">
          {tab === "pipeline" && (
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Rechercher…"
              className="hidden sm:block bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-1.5
                         text-sm focus:outline-none focus:border-brand-500 w-36 transition-colors" />
          )}
          <button onClick={() => setShowAdd(true)}
            className="px-3.5 py-1.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg rounded-xl text-sm font-semibold transition-colors">
            + Ajouter un lead
          </button>

          <div className="relative group">
            <button className="w-8 h-8 rounded-full bg-[#1a1a1a] border border-[#2a2a2a] flex items-center justify-center
                               text-xs font-bold text-white hover:border-brand-500 transition-colors">
              {initials}
            </button>
            <div className="absolute right-0 mt-2 w-44 bg-slate-800 border border-[#2a2a2a] rounded-xl overflow-hidden
                            shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all">
              <div className="px-4 py-3 border-b border-[#2a2a2a]">
                <p className="text-xs font-semibold text-white truncate">{coach?.name}</p>
                <p className="text-[10px] text-slate-500 truncate">{coach?.email}</p>
              </div>
              <button onClick={() => nav("/settings")}
                className="w-full text-left px-4 py-2.5 text-xs text-slate-300 hover:bg-slate-700 transition-colors">
                Paramètres
              </button>
              <button onClick={logout}
                className="w-full text-left px-4 py-2.5 text-xs text-red-400 hover:bg-slate-700 transition-colors">
                Déconnexion
              </button>
            </div>
          </div>
        </div>
      </nav>

      {coach && coach.email_verified === false && (
        <EmailVerificationBanner coach={coach} />
      )}

      {coach && coach.trial_active && (
        <TrialBanner coach={coach} />
      )}

      {showTrialExpired && (
        <TrialExpiredModal onClose={() => {
          sessionStorage.setItem("trial_modal_dismissed", "1");
          setShowTrialExpired(false);
        }} />
      )}

      {planLimitReached && (
        <div className="flex items-center justify-between gap-3 px-5 py-2.5 bg-amber-950/40 border-b border-amber-900/40">
          <p className="text-xs text-amber-300">
            Limite du plan {coachPlan} atteinte ({planLimit} leads). Passez au plan supérieur pour continuer à prospecter.
          </p>
          <a href="/pricing" className="text-[11px] font-semibold text-amber-400 hover:text-amber-300 whitespace-nowrap transition-colors">
            Mettre à niveau →
          </a>
        </div>
      )}

      {tab === "pipeline" && (
        <div className="flex gap-6 px-6 py-3 border-b border-slate-900">
          {[
            { label: "Total",      value: stats.total,     color: "text-white" },
            { label: "Contactés",  value: stats.contacted, color: "text-brand-400" },
            { label: "Répondus",   value: stats.replied,   color: "text-brand-400" },
            { label: "Réservés",   value: stats.booked,    color: "text-emerald-400" },
            {
              label: "Taux conv.",
              value: stats.total ? `${Math.round((stats.booked / stats.total) * 100)} %` : "—",
              color: "text-amber-400",
            },
          ].map(s => (
            <div key={s.label}>
              <p className="text-[10px] text-slate-600 uppercase tracking-wider">{s.label}</p>
              <p className={`text-lg font-black ${s.color}`}>{s.value}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex-1 min-h-0">
        {tab === "pipeline" && (
          <div className="h-full px-5 py-4 overflow-auto">
            {isLoading ? (
              <div className="flex items-center justify-center h-64 text-slate-600 text-sm">Chargement du pipeline…</div>
            ) : leads.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-64 text-center">
                <p className="text-4xl mb-3">💭</p>
                <p className="text-sm font-semibold text-white mb-1">Aucun lead pour l'instant</p>
                <p className="text-xs text-slate-500 mb-4">Ajoutez des leads manuellement ou utilisez l'onglet Prospection pour scraper Instagram/TikTok.</p>
                <button onClick={() => setShowAdd(true)}
                  className="px-4 py-2 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg rounded-xl text-sm font-medium transition-colors">
                  + Ajouter votre premier lead
                </button>
              </div>
            ) : (
              <KanbanBoard leads={filtered} />
            )}
          </div>
        )}

        {tab === "prospects" && (
          <div className="px-5 py-6 overflow-auto">
            <ProspectsTab />
          </div>
        )}

        {tab === "followups" && (
          <div className="px-5 py-6 overflow-auto">
            <FollowupsTab />
          </div>
        )}

        {tab === "warming" && (
          <div className="px-5 py-6 overflow-auto">
            <WarmingTab leads={leads} />
          </div>
        )}

        {tab === "replies" && (
          <div className="px-5 py-6 overflow-auto">
            <RepliesTab leads={leads} />
          </div>
        )}

        {tab === "analytics" && (
          <div className="px-5 py-6 overflow-auto space-y-8">
            <ContentIntelligenceTab />
            <AnalyticsTab coach={coach ?? {}} />
          </div>
        )}

        {tab === "automations" && (
          <div className="px-5 py-6 overflow-auto">
            <AutomationsTab />
          </div>
        )}
      </div>

      {/* ── Navigation mobile bas ── */}
      <div className="sm:hidden flex border-t border-[#2a2a2a] bg-slate-950">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`relative flex-1 py-3 text-xs font-medium transition-colors ${
              tab === t.id ? "text-brand-400" : "text-slate-600 hover:text-slate-400"}`}>
            {t.label}
            {t.badge ? (
              <span className="absolute top-2 right-1/4 w-3.5 h-3.5 flex items-center justify-center
                               bg-amber-500 text-slate-900 text-[8px] font-black rounded-full">
                {t.badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {showAdd && <AddLeadModal onClose={() => setShowAdd(false)} />}
    </div>
  );
}
