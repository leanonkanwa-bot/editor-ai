import { useState, useRef, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Lead } from "../lib/api";
import { leadsApi, pipelineApi, aiCloneApi, type AIConversation, type AIMessage } from "../lib/api";

const PLATFORM_DM_URL: Record<string, (handle: string) => string> = {
  instagram: (h) => `https://ig.me/m/${h}`,
  tiktok:    (h) => `https://www.tiktok.com/messages?u=${h}`,
  twitter:   (h) => `https://twitter.com/messages/compose?recipient_handle=${h}`,
  linkedin:  (h) => `https://www.linkedin.com/messaging/compose/?recipients=${h}`,
  reddit:    (h) => `https://www.reddit.com/message/compose/?to=${h}`,
};
const PLATFORM_ICON: Record<string, string> = {
  instagram: "📸", tiktok: "🎵", twitter: "𝕏", linkedin: "💼", reddit: "💬",
};

function DMSendButton({ lead, text, variant, markVariant }: {
  lead: Lead;
  text: string;
  variant: "A" | "B";
  markVariant: ReturnType<typeof useMutation<any, any, "A" | "B">>;
}) {
  const [sent, setSent] = useState(lead.dm_variant_sent === variant);
  const urlFn = PLATFORM_DM_URL[lead.platform];
  if (!urlFn) return null;

  function handleSend() {
    navigator.clipboard.writeText(text).catch(() => {});
    window.open(urlFn(lead.handle), "_blank", "noopener,noreferrer");
    if (!sent) {
      markVariant.mutate(variant);
      setSent(true);
    }
  }

  const icon = PLATFORM_ICON[lead.platform] || "📩";
  return (
    <button
      onClick={handleSend}
      className={`flex-1 py-1.5 text-xs font-medium rounded-lg transition-colors flex items-center justify-center gap-1 ${
        sent
          ? "bg-emerald-950/40 border border-emerald-900/50 text-emerald-400"
          : "bg-brand-500/20 hover:bg-brand-500/30 border border-brand-500/30 text-brand-400"
      }`}
    >
      {sent ? "✓ DM envoyé" : `${icon} Envoyer via ${lead.platform}`}
    </button>
  );
}

const EMOTION_COLORS: Record<string, string> = {
  frustration: "text-orange-400",
  fear: "text-red-400",
  hope: "text-emerald-400",
  excitement: "text-brand-400",
  shame: "text-purple-400",
  anxiety: "text-amber-400",
};
const EMOTION_LABELS: Record<string, string> = {
  frustration: "😤 Frustration",
  fear: "😟 Peur",
  hope: "🌱 Espoir",
  excitement: "⚡ Excitation",
  shame: "😔 Honte",
  anxiety: "😰 Anxiété",
};
const AWARENESS_LABELS: Record<string, string> = {
  unaware: "Inconscient du problème",
  problem_aware: "Conscient du problème",
  solution_aware: "Cherche une solution",
  product_aware: "Prêt à acheter",
};
const WARMING_LABELS: Record<string, string> = {
  none: "—",
  comment_ready: "Commentaire prêt",
  commented: "Commentaire posté ✓",
  dm_ready: "Prêt pour le DM ✓✓",
};
const SOURCE_BADGES: Record<string, { label: string; cls: string }> = {
  viral_post:          { label: "🔥 Post viral",       cls: "bg-orange-950/60 text-orange-400 border-orange-900/40" },
  competitor_audience: { label: "🎯 Concurrent",       cls: "bg-brand-950/60 text-brand-400 border-brand-900/40" },
  community:           { label: "🌐 Communauté",       cls: "bg-sky-950/60 text-sky-400 border-sky-900/40" },
  micro_influencer:    { label: "⭐ Micro-influenceur", cls: "bg-indigo-950/60 text-indigo-400 border-indigo-900/40" },
  direct:              { label: "➕ Ajout direct",     cls: "bg-slate-800 text-slate-400 border-slate-700" },
  hashtag:             { label: "# Hashtag",           cls: "bg-slate-800 text-slate-500 border-slate-700" },
};

function AICloneTab({ lead }: { lead: Lead }) {
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { data: convData, isLoading } = useQuery({
    queryKey: ["ai-conv", lead.id],
    queryFn: () => aiCloneApi.get(lead.id).then(r => r.data),
  });

  const start = useMutation({
    mutationFn: () => aiCloneApi.start(lead.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai-conv", lead.id] }),
  });

  const sendMsg = useMutation({
    mutationFn: (content: string) => aiCloneApi.message(lead.id, content),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["ai-conv", lead.id] });
      if (data.data.is_hot) {
        qc.invalidateQueries({ queryKey: ["leads"] });
      }
    },
  });

  const handOff = useMutation({
    mutationFn: () => aiCloneApi.handOff(lead.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai-conv", lead.id] }),
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [convData]);

  const conv = convData?.conversation;
  const messages = conv?.messages || [];

  function handleSend() {
    if (!input.trim()) return;
    const msg = input.trim();
    setInput("");
    sendMsg.mutate(msg);
  }

  const STATUS_LABELS: Record<string, { label: string; color: string }> = {
    active:      { label: "Conversation active", color: "text-brand-400" },
    hot:         { label: "🔥 Lead chaud — Passer la main", color: "text-amber-400" },
    disqualified:{ label: "Lead non qualifié", color: "text-red-400" },
    handed_off:  { label: "✓ Transmis au coach", color: "text-emerald-400" },
  };

  if (isLoading) {
    return <div className="flex items-center justify-center py-8 text-slate-500 text-sm">Chargement…</div>;
  }

  if (!conv) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-center">
        <p className="text-3xl mb-3">🤖</p>
        <p className="text-sm font-semibold text-white mb-1">IA Clone — Qualifieur conversationnel</p>
        <p className="text-xs text-slate-400 mb-5 max-w-xs">
          L'IA pose des questions qualifiantes dans votre voix et ne vous transfère le lead que quand il atteint 80+.
        </p>
        <button
          onClick={() => start.mutate()}
          disabled={start.isPending}
          className="px-5 py-2.5 bg-brand-500 hover:bg-brand-400 rounded-xl text-sm font-semibold transition-colors disabled:opacity-50">
          {start.isPending ? "Démarrage…" : "Démarrer la qualification IA →"}
        </button>
      </div>
    );
  }

  const statusInfo = STATUS_LABELS[conv.status] || STATUS_LABELS.active;

  return (
    <div className="flex flex-col h-full">
      {/* Status bar */}
      <div className="flex items-center justify-between px-1 pb-3 border-b border-[#2a2a2a]">
        <div className="flex items-center gap-3">
          <span className={`text-xs font-semibold ${statusInfo.color}`}>{statusInfo.label}</span>
          {conv.current_score > 0 && (
            <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${
              conv.current_score >= 80 ? "bg-amber-500/20 text-amber-400" :
              conv.current_score >= 50 ? "bg-brand-500/20 text-brand-400" :
              "bg-slate-800 text-slate-400"
            }`}>
              Score {Math.round(conv.current_score)}/100
            </span>
          )}
        </div>
        {conv.status === "hot" && (
          <button
            onClick={() => handOff.mutate()}
            disabled={handOff.isPending}
            className="text-xs px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/30 text-amber-400 rounded-lg transition-colors disabled:opacity-50">
            ✓ Prendre en charge
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-3 py-3 min-h-0" style={{ maxHeight: "320px" }}>
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "assistant" ? "justify-start" : "justify-end"}`}>
            <div className={`max-w-[80%] rounded-2xl px-3 py-2 text-xs ${
              msg.role === "assistant"
                ? "bg-slate-800 text-slate-200 rounded-tl-sm"
                : "bg-brand-500/20 border border-brand-500/30 text-slate-200 rounded-tr-sm"
            }`}>
              {msg.content}
            </div>
          </div>
        ))}
        {sendMsg.isPending && (
          <div className="flex justify-start">
            <div className="bg-slate-800 rounded-2xl rounded-tl-sm px-3 py-2 text-xs text-slate-500">
              IA en train d'écrire…
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      {conv.status === "active" && (
        <div className="pt-3 border-t border-[#2a2a2a]">
          <p className="text-[10px] text-slate-600 mb-2">Entrez la réponse du lead pour que l'IA continue la conversation :</p>
          <div className="flex gap-2">
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), handleSend())}
              placeholder="Réponse du lead…"
              className="flex-1 bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2 text-xs focus:outline-none focus:border-brand-500 transition-colors text-slate-200 placeholder-slate-600"
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || sendMsg.isPending}
              className="px-4 py-2 bg-brand-500 hover:bg-brand-400 disabled:opacity-40 rounded-xl text-xs font-semibold transition-colors">
              →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function LeadModal({ lead, onClose }: { lead: Lead; onClose: () => void }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"info" | "dm" | "warming" | "reply" | "ai-clone">("info");
  const [replyText, setReplyText] = useState(lead.reply_received || "");
  const [convHistory, setConvHistory] = useState("");
  const [notes, setNotes] = useState(lead.notes || "");
  const [copied, setCopied] = useState<string | null>(null);
  const [activeVariant, setActiveVariant] = useState<"A" | "B">(lead.dm_variant_sent || "A");

  const refetch = () => qc.invalidateQueries({ queryKey: ["leads"] });

  const qualify    = useMutation({ mutationFn: () => pipelineApi.qualify(lead.id), onSuccess: refetch });
  const rescan     = useMutation({ mutationFn: () => pipelineApi.rescan(lead.id), onSuccess: refetch });
  const reengage   = useMutation({ mutationFn: () => pipelineApi.reengage(lead.id), onSuccess: refetch });
  const writeAb   = useMutation({ mutationFn: () => pipelineApi.writeAb(lead.id), onSuccess: refetch });
  const warm      = useMutation({ mutationFn: () => pipelineApi.warm(lead.id), onSuccess: refetch });
  const markWarmed = useMutation({
    mutationFn: (status: string) => pipelineApi.markWarmed(lead.id, status),
    onSuccess: refetch,
  });
  const markVariant = useMutation({
    mutationFn: (v: "A" | "B") => pipelineApi.markVariant(lead.id, v),
    onSuccess: refetch,
  });
  const reply = useMutation({
    mutationFn: () => pipelineApi.reply(lead.id, { lead_reply: replyText, conversation_history: convHistory }),
    onSuccess: refetch,
  });
  const saveNotes = useMutation({ mutationFn: () => leadsApi.update(lead.id, { notes }), onSuccess: refetch });
  const del = useMutation({ mutationFn: () => leadsApi.delete(lead.id), onSuccess: () => { refetch(); onClose(); } });

  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text);
    setCopied(key); setTimeout(() => setCopied(null), 1500);
  }

  const busy = qualify.isPending || rescan.isPending || writeAb.isPending || reply.isPending || warm.isPending || reengage.isPending;
  const psycho = lead.psychographic_profile;
  const sourceBadge = lead.source_tag ? SOURCE_BADGES[lead.source_tag] : null;

  const TABS = [
    { id: "info" as const, label: "Profil" },
    { id: "dm" as const, label: "DM A/B" },
    { id: "warming" as const, label: "Warming" },
    { id: "reply" as const, label: "Réponse" },
    { id: "ai-clone" as const, label: "IA Clone" },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-xl bg-slate-900 border border-[#2a2a2a] rounded-2xl overflow-hidden shadow-2xl shadow-black/60 animate-fade-in"
        onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-[#2a2a2a]">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-semibold text-white">{lead.name}</h2>
              {sourceBadge && (
                <span className={`text-[10px] border px-1.5 py-0.5 rounded-full ${sourceBadge.cls}`}>
                  {sourceBadge.label}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3 mt-0.5">
              <p className="text-sm text-slate-400">@{lead.handle} · {lead.platform}</p>
              {lead.response_probability != null && (
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                  lead.response_probability >= 60 ? "bg-emerald-950/60 text-emerald-400" :
                  lead.response_probability >= 40 ? "bg-amber-950/60 text-amber-400" :
                  "bg-slate-800 text-slate-500"
                }`}>
                  {lead.response_probability}% réponse
                </span>
              )}
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white text-2xl leading-none ml-3 flex-shrink-0">×</button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-[#2a2a2a]">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex-1 py-2.5 text-xs font-medium transition-colors border-b-2 ${
                tab === t.id ? "text-brand-400 border-brand-500" : "text-slate-500 border-transparent hover:text-slate-300"}`}>
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="p-5 overflow-y-auto max-h-[60vh] space-y-4">

          {/* ── PROFIL tab ── */}
          {tab === "info" && (
            <>
              {lead.bio && (
                <div>
                  <p className="text-xs text-slate-500 mb-1">Bio</p>
                  <p className="text-sm text-slate-300 leading-relaxed">{lead.bio}</p>
                </div>
              )}

              {lead.qualification_score > 0 && (
                <div className="bg-slate-800 rounded-xl p-4">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-xs text-slate-500">Score IA</span>
                    <span className="text-2xl font-black text-brand-400">{lead.qualification_score}/100</span>
                    {lead.score_delta != null && lead.score_delta !== 0 && (
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                        lead.score_delta > 0 ? "bg-red-950/60 text-red-400" : "bg-slate-800 text-slate-500"
                      }`}>
                        {lead.score_delta > 0 ? "⚡" : "▼"} {lead.score_delta > 0 ? "+" : ""}{lead.score_delta.toFixed(0)} pts
                      </span>
                    )}
                    {lead.language && (
                      <span className="ml-auto text-[10px] bg-slate-700 text-slate-400 px-2 py-0.5 rounded uppercase">
                        {lead.language}
                      </span>
                    )}
                  </div>
                  {lead.qualification_reason && (
                    <p className="text-xs text-slate-400 mb-3 leading-relaxed">{lead.qualification_reason}</p>
                  )}
                  {lead.pain_points?.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mb-3">
                      {lead.pain_points.map(p => (
                        <span key={p} className="text-[10px] bg-[#1a1a1a] text-brand-400 border border-white/[0.08] px-2 py-0.5 rounded-full">{p}</span>
                      ))}
                    </div>
                  )}
                  {lead.predicted_objection && (
                    <div className="mt-2 bg-amber-950/20 border border-amber-900/30 rounded-lg px-3 py-2">
                      <p className="text-[10px] text-amber-600 mb-0.5">Objection probable pré-emtée dans le DM</p>
                      <p className="text-xs text-amber-400 italic">"{lead.predicted_objection}"</p>
                    </div>
                  )}
                  {lead.aspiration_gap_score != null && lead.aspiration_gap_score > 0 && (
                    <div className="mt-2 bg-violet-950/20 border border-violet-900/30 rounded-lg px-3 py-2">
                      <div className="flex items-center justify-between mb-1">
                        <p className="text-[10px] text-violet-500">🌟 Gap aspiration / réalité</p>
                        <span className={`text-[11px] font-bold ${
                          lead.aspiration_gap_score >= 80 ? "text-violet-300"
                          : lead.aspiration_gap_score >= 60 ? "text-violet-400"
                          : "text-violet-500"
                        }`}>{lead.aspiration_gap_score}/100</span>
                      </div>
                      <div className="w-full bg-violet-950/60 rounded-full h-1.5">
                        <div
                          className="bg-violet-500 h-1.5 rounded-full transition-all"
                          style={{ width: `${lead.aspiration_gap_score}%` }}
                        />
                      </div>
                    </div>
                  )}
                  {/* Price tier + Trust velocity row */}
                  {(lead.price_tier || lead.trust_velocity) && (
                    <div className="mt-2 flex gap-2 flex-wrap">
                      {lead.price_tier === "premium" && (
                        <span className="text-[10px] bg-yellow-950/30 text-yellow-400 border border-yellow-900/40 px-2 py-0.5 rounded-full">
                          💎 Prospect premium — DM haut de gamme
                        </span>
                      )}
                      {lead.price_tier === "budget" && (
                        <span className="text-[10px] bg-slate-800 text-slate-500 border border-slate-700 px-2 py-0.5 rounded-full">
                          ⚠️ Signaux budget — ne pas parler de prix
                        </span>
                      )}
                      {lead.trust_velocity === "fast" && (
                        <span className="text-[10px] bg-emerald-950/30 text-emerald-400 border border-emerald-900/40 px-2 py-0.5 rounded-full">
                          ⚡ Décideur rapide — CTA direct possible
                        </span>
                      )}
                      {lead.trust_velocity === "slow" && (
                        <span className="text-[10px] bg-blue-950/30 text-blue-400 border border-blue-900/40 px-2 py-0.5 rounded-full">
                          🐢 Décideur lent — séquence nurture recommandée
                        </span>
                      )}
                      {lead.voice_tone_intensity != null && lead.voice_tone_intensity >= 60 && (
                        <span className="text-[10px] bg-orange-950/30 text-orange-400 border border-orange-900/40 px-2 py-0.5 rounded-full">
                          📣 Intensité émotionnelle {lead.voice_tone_intensity}/100
                        </span>
                      )}
                    </div>
                  )}
                  <div className="flex gap-2 mt-3">
                    <button
                      onClick={() => rescan.mutate()}
                      disabled={rescan.isPending}
                      className="text-[10px] text-slate-600 hover:text-slate-400 transition-colors disabled:opacity-40"
                    >
                      {rescan.isPending ? "⟳ Rescan…" : "⟳ Rescan douleur"}
                    </button>
                  </div>
                </div>
              )}

              {/* Churn risk + re-engagement */}
              {lead.stage === "contacted" && !lead.reply_received && (lead.churn_risk ?? 0) >= 0.5 && (
                <div className="bg-rose-950/20 border border-rose-900/30 rounded-xl p-4">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-xs text-rose-400 font-medium">🧊 Lead en train de refroidir</p>
                    <span className="text-[10px] text-rose-500">{Math.round((lead.churn_risk ?? 0) * 100)}% risque</span>
                  </div>
                  {lead.reengagement_message ? (
                    <div>
                      <p className="text-[10px] text-slate-500 mb-1.5">Message de relance IA</p>
                      <p className="text-xs text-slate-300 bg-slate-800 rounded-lg p-2.5 leading-relaxed">
                        {lead.reengagement_message}
                      </p>
                    </div>
                  ) : (
                    <button
                      onClick={() => reengage.mutate()}
                      disabled={reengage.isPending}
                      className="text-xs bg-rose-950/40 text-rose-400 border border-rose-900/50 px-3 py-1.5 rounded-lg hover:bg-rose-950/60 transition-colors disabled:opacity-40"
                    >
                      {reengage.isPending ? "⟳ Génération…" : "⟳ Générer relance IA"}
                    </button>
                  )}
                </div>
              )}

              {/* Psychographic profile */}
              {psycho && (
                <div className="bg-slate-800/60 rounded-xl p-4">
                  <p className="text-xs text-slate-500 mb-3 font-medium">Profil psychographique</p>
                  <div className="grid grid-cols-2 gap-3">
                    {psycho.dominant_emotion && (
                      <div>
                        <p className="text-[10px] text-slate-600 mb-0.5">Émotion dominante</p>
                        <p className={`text-xs font-medium ${EMOTION_COLORS[psycho.dominant_emotion] || "text-slate-300"}`}>
                          {EMOTION_LABELS[psycho.dominant_emotion] || psycho.dominant_emotion}
                        </p>
                      </div>
                    )}
                    {psycho.awareness_stage && (
                      <div>
                        <p className="text-[10px] text-slate-600 mb-0.5">Niveau de conscience</p>
                        <p className="text-xs text-slate-300">{AWARENESS_LABELS[psycho.awareness_stage] || psycho.awareness_stage}</p>
                      </div>
                    )}
                    {psycho.communication_style && (
                      <div>
                        <p className="text-[10px] text-slate-600 mb-0.5">Style de communication</p>
                        <p className="text-xs text-slate-300 capitalize">{psycho.communication_style}</p>
                      </div>
                    )}
                    {psycho.best_contact_time && (
                      <div>
                        <p className="text-[10px] text-slate-600 mb-0.5">Meilleur moment</p>
                        <p className="text-xs text-slate-300 capitalize">{psycho.best_contact_time}</p>
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div>
                <label className="text-xs text-slate-500 mb-1 block">Notes</label>
                <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3}
                  className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm resize-none focus:outline-none focus:border-brand-500" />
                <button onClick={() => saveNotes.mutate()}
                  className="text-xs text-brand-400 hover:text-brand-300 mt-1 transition-colors">
                  {saveNotes.isPending ? "Enregistrement…" : "Enregistrer les notes"}
                </button>
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                <button onClick={() => qualify.mutate()} disabled={busy}
                  className={`px-3 py-2 rounded-lg text-xs font-medium transition-colors disabled:opacity-40 ${
                    lead.qualification_score > 0
                      ? "bg-[#1a1a1a] hover:bg-[#222] border border-[#2a2a2a] text-slate-400"
                      : "bg-brand-500/20 hover:bg-brand-500/30 border border-brand-500/30 text-brand-400"
                  }`}>
                  {qualify.isPending ? "Qualification en cours…" : lead.qualification_score > 0 ? "⟳ Re-qualifier" : "🎯 Qualifier ce lead"}
                </button>
                <button onClick={() => { if (confirm("Supprimer ce lead ?")) del.mutate(); }}
                  className="ml-auto px-3 py-2 bg-red-950 hover:bg-red-900 text-red-400 rounded-lg text-xs transition-colors">
                  Supprimer
                </button>
              </div>
            </>
          )}

          {/* ── DM A/B tab ── */}
          {tab === "dm" && (
            <>
              {!lead.qualification_reason && (
                <div className="bg-amber-950/30 border border-amber-900/40 rounded-xl px-4 py-3 mb-2">
                  <p className="text-xs text-amber-400">Qualifiez ce lead d'abord (onglet Profil) pour générer des DMs personnalisés.</p>
                </div>
              )}

              {/* Both variants shown simultaneously */}
              {(lead.outreach_message || lead.dm_variant_b) && (
                <div className="space-y-3">
                  {[
                    { variant: "A" as const, text: lead.outreach_message, label: "Variante A — Empathie + preuve" },
                    { variant: "B" as const, text: lead.dm_variant_b, label: "Variante B — Curiosité + rêve" },
                  ].map(({ variant, text, label }) => text ? (
                    <div key={variant} className={`border rounded-xl p-4 transition-colors ${
                      lead.dm_variant_sent === variant
                        ? "bg-emerald-950/20 border-emerald-900/40"
                        : "bg-slate-800/60 border-[#2a2a2a]"
                    }`}>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[11px] font-semibold text-slate-400">{label}</span>
                        {lead.dm_variant_sent === variant && (
                          <span className="text-[9px] bg-emerald-950/60 text-emerald-400 border border-emerald-900/50 px-1.5 py-0.5 rounded-full">
                            DM envoyé ✓
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-slate-200 whitespace-pre-wrap leading-relaxed mb-3">{text}</p>
                      <div className="flex flex-wrap gap-2">
                        <button
                          onClick={() => copy(text, `dm-${variant}`)}
                          className="flex-1 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs font-medium rounded-lg transition-colors"
                        >
                          {copied === `dm-${variant}` ? "✓ Copié !" : "📋 Copier"}
                        </button>
                        <DMSendButton lead={lead} text={text} variant={variant} markVariant={markVariant} />
                      </div>
                    </div>
                  ) : null)}
                </div>
              )}

              <button
                onClick={() => writeAb.mutate()}
                disabled={busy || !lead.qualification_reason}
                className="w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors"
              >
                {writeAb.isPending
                  ? "Génération des variantes A/B…"
                  : lead.outreach_message
                  ? "↻ Régénérer variantes A/B"
                  : "✍️ Générer variantes A/B"}
              </button>
              {writeAb.isError && (
                <p className="text-red-400 text-xs bg-red-950/30 border border-red-900/40 rounded-lg px-3 py-2">
                  {(writeAb.error as any)?.response?.data?.detail || "Erreur lors de la génération."}
                </p>
              )}
            </>
          )}

          {/* ── WARMING tab ── */}
          {tab === "warming" && (
            <>
              <div className="bg-slate-800/60 rounded-xl p-4">
                <p className="text-xs text-slate-500 mb-2 font-medium">Séquence de warming</p>
                <p className="text-[11px] text-slate-600 leading-relaxed mb-3">
                  Postez un commentaire genuinement sur leur contenu → attendez 24-48h → envoyez le DM.
                  Augmente le taux de réponse de 3% à 25%+.
                </p>
                <div className="flex items-center gap-2 flex-wrap">
                  {(["none", "comment_ready", "commented", "dm_ready"] as const).map((s, i) => (
                    <div key={s} className="flex items-center gap-1">
                      {i > 0 && <span className="text-slate-700 text-xs">→</span>}
                      <span className={`text-[10px] px-2 py-0.5 rounded-full border ${
                        lead.warming_status === s
                          ? "bg-brand-500/20 border-brand-500/50 text-brand-400"
                          : "bg-slate-800 border-[#2a2a2a] text-slate-600"
                      }`}>
                        {WARMING_LABELS[s]}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Warming comment */}
              {lead.warming_comment ? (
                <div>
                  <p className="text-xs text-slate-500 mb-2">Commentaire IA à poster</p>
                  <div className="bg-slate-800 rounded-xl p-4 text-sm text-slate-200 italic leading-relaxed">
                    "{lead.warming_comment}"
                  </div>
                  <div className="flex gap-3 mt-2">
                    <button onClick={() => copy(lead.warming_comment!, "comment")}
                      className="text-xs text-brand-400 hover:text-brand-300 transition-colors">
                      {copied === "comment" ? "Copié !" : "📋 Copier le commentaire"}
                    </button>
                    {lead.warming_status === "comment_ready" && (
                      <button onClick={() => markWarmed.mutate("commented")}
                        className="text-xs text-emerald-400 hover:text-emerald-300 transition-colors">
                        ✓ Commentaire posté
                      </button>
                    )}
                    {lead.warming_status === "commented" && (
                      <button onClick={() => markWarmed.mutate("dm_ready")}
                        className="text-xs text-emerald-400 hover:text-emerald-300 transition-colors">
                        ✓ Prêt pour le DM
                      </button>
                    )}
                  </div>
                </div>
              ) : (
                <div className="text-center py-4">
                  <p className="text-xs text-slate-600">Générez un commentaire personnalisé ci-dessous.</p>
                </div>
              )}

              <button onClick={() => warm.mutate()} disabled={busy}
                className="w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors">
                {warm.isPending ? "Génération…" : (lead.warming_comment ? "↻ Régénérer le commentaire" : "🔥 Générer un commentaire warming")}
              </button>
              {warm.isError && (
                <p className="text-red-400 text-xs">{(warm.error as any)?.response?.data?.detail}</p>
              )}
            </>
          )}

          {/* ── IA CLONE tab ── */}
          {tab === "ai-clone" && (
            <AICloneTab lead={lead} />
          )}

          {/* ── RÉPONSE tab ── */}
          {tab === "reply" && (
            <>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">Sa réponse</label>
                <textarea value={replyText} onChange={e => setReplyText(e.target.value)} rows={3}
                  className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm resize-none focus:outline-none focus:border-brand-500"
                  placeholder="Collez leur message ici…" />
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">Historique <span className="text-slate-700">(facultatif)</span></label>
                <textarea value={convHistory} onChange={e => setConvHistory(e.target.value)} rows={3}
                  className="w-full bg-slate-800 border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm resize-none focus:outline-none focus:border-brand-500"
                  placeholder={"Vous : [DM]\nEux : [réponse]\n…"} />
              </div>
              {lead.suggested_reply && (
                <div>
                  <p className="text-xs text-slate-500 mb-2">Réponse suggérée</p>
                  <div className="bg-slate-800 rounded-xl p-4 text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">
                    {lead.suggested_reply}
                  </div>
                  <button onClick={() => copy(lead.suggested_reply!, "reply")}
                    className="text-xs text-brand-400 hover:text-brand-300 mt-2 transition-colors">
                    {copied === "reply" ? "Copié !" : "📋 Copier"}
                  </button>
                </div>
              )}
              <button onClick={() => reply.mutate()} disabled={busy || !replyText}
                className="w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors">
                {reply.isPending ? "Génération…" : "🤖 Générer une réponse"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
