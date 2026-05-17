import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { Lead } from "../lib/api";

const PLATFORM: Record<string, string> = {
  instagram: "📸", tiktok: "🎵", twitter: "𝕏", linkedin: "💼",
};

const SOURCE_BADGES: Record<string, { label: string; cls: string }> = {
  viral_post:          { label: "🔥 viral",        cls: "bg-orange-950/60 text-orange-400 border-orange-900/50" },
  competitor_audience: { label: "🎯 concurrent",    cls: "bg-purple-950/60 text-purple-400 border-purple-900/50" },
  community:           { label: "🌐 communauté",    cls: "bg-sky-950/60 text-sky-400 border-sky-900/50" },
  micro_influencer:    { label: "⭐ micro-inf",     cls: "bg-indigo-950/60 text-indigo-400 border-indigo-900/50" },
  hashtag:             { label: "#️⃣ hashtag",      cls: "bg-slate-800/60 text-slate-500 border-slate-700/50" },
  direct:              { label: "👤 direct",        cls: "bg-slate-800/60 text-slate-500 border-slate-700/50" },
};

function Score({ v }: { v: number }) {
  if (!v) return null;
  const cls = v >= 80 ? "text-emerald-400" : v >= 60 ? "text-amber-400" : "text-slate-500";
  const dot = v >= 80 ? "bg-emerald-500" : v >= 60 ? "bg-amber-500" : "bg-slate-600";
  return (
    <span className={`flex items-center gap-1 text-[10px] font-mono font-bold ${cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      {v.toFixed(1)}
    </span>
  );
}

const DM_STATUS: Record<string, { label: string; cls: string }> = {
  contacted: { label: "DM envoyé ✓", cls: "bg-blue-950/60 text-blue-400 border-blue-900/50" },
  replied:   { label: "Répondu ✓",   cls: "bg-emerald-950/60 text-emerald-400 border-emerald-900/50" },
};

function Avatar({ lead }: { lead: Lead }) {
  const initials = (lead.name || lead.handle).slice(0, 2).toUpperCase();
  if (lead.profile_pic_url) {
    return (
      <img
        src={lead.profile_pic_url}
        alt={lead.name || lead.handle}
        className="w-8 h-8 rounded-full object-cover flex-shrink-0 ring-1 ring-white/10"
        onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
      />
    );
  }
  return (
    <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-[10px] font-bold text-slate-400 flex-shrink-0">
      {initials}
    </div>
  );
}

export default function LeadCard({ lead, onClick }: { lead: Lead; onClick: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: lead.id });

  const followupDue = lead.stage === "contacted" && lead.messaged_at && !lead.reply_received;
  const sourceBadge = lead.source_tag ? SOURCE_BADGES[lead.source_tag] : null;
  const prob = lead.response_probability;
  const escalating = lead.escalation_alert && (lead.score_delta ?? 0) >= 10;
  const gap = lead.aspiration_gap_score;
  const churnRisk = lead.churn_risk ?? 0;
  const dmStatus = DM_STATUS[lead.stage];

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.3 : 1 }}
      {...attributes} {...listeners}
      onClick={onClick}
      className="bg-slate-800/80 border border-[#2a2a2a]/60 hover:border-slate-600 hover:bg-slate-800 rounded-xl p-3.5 cursor-pointer select-none transition-all duration-150 group"
    >
      <div className="flex items-start gap-2.5 mb-2">
        <Avatar lead={lead} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-100 truncate group-hover:text-white transition-colors">
            {lead.name || `@${lead.handle}`}
          </p>
          <p className="text-[11px] text-slate-500 truncate mt-0.5">
            {PLATFORM[lead.platform] || "👤"} @{lead.handle}
          </p>
        </div>
        <Score v={lead.qualification_score} />
      </div>

      {lead.bio && (
        <p className="text-[11px] text-slate-500 line-clamp-2 mb-2.5 leading-relaxed">{lead.bio}</p>
      )}

      {lead.pain_points?.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2.5">
          {lead.pain_points.slice(0, 2).map(p => (
            <span key={p} className="text-[9px] bg-white/[0.04] text-brand-400/80 border border-white/[0.06] px-1.5 py-0.5 rounded-full">
              {p}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between mt-0.5">
        <span className="text-[10px] text-slate-600 tabular-nums">
          {lead.followers > 0 ? `${lead.followers.toLocaleString()} abonnés` : ""}
        </span>
        <div className="flex gap-1.5 items-center flex-wrap justify-end">
          {sourceBadge && (
            <span className={`text-[9px] border px-1.5 py-0.5 rounded-full ${sourceBadge.cls}`}>
              {sourceBadge.label}
            </span>
          )}
          {prob != null && (
            <span className={`text-[9px] border px-1.5 py-0.5 rounded-full tabular-nums ${
              prob >= 70 ? "bg-emerald-950/60 text-emerald-400 border-emerald-900/50"
              : prob >= 40 ? "bg-amber-950/60 text-amber-400 border-amber-900/50"
              : "bg-slate-800/60 text-slate-500 border-slate-700/50"
            }`}>
              {prob}% rép.
            </span>
          )}
          {escalating && (
            <span className="text-[9px] bg-red-950/60 text-red-400 border border-red-900/50 px-1.5 py-0.5 rounded-full animate-pulse">
              ⚡ +{lead.score_delta?.toFixed(0)} pts
            </span>
          )}
          {followupDue && (
            <span className="text-[9px] bg-amber-950/60 text-amber-400 border border-amber-900/50 px-1.5 py-0.5 rounded-full">
              relance due
            </span>
          )}
          {gap != null && gap >= 70 && (
            <span className="text-[9px] bg-violet-950/60 text-violet-400 border border-violet-900/50 px-1.5 py-0.5 rounded-full">
              🌟 gap {gap}
            </span>
          )}
          {lead.price_tier === "premium" && (
            <span className="text-[9px] bg-yellow-950/60 text-yellow-400 border border-yellow-900/50 px-1.5 py-0.5 rounded-full">
              💎 premium
            </span>
          )}
          {lead.trust_velocity === "fast" && (
            <span className="text-[9px] bg-emerald-950/60 text-emerald-400 border border-emerald-900/50 px-1.5 py-0.5 rounded-full">
              ⚡ rapide
            </span>
          )}
          {churnRisk >= 0.7 && lead.stage === "contacted" && !lead.reply_received && (
            <span className="text-[9px] bg-rose-950/60 text-rose-400 border border-rose-900/50 px-1.5 py-0.5 rounded-full animate-pulse">
              🧊 froid
            </span>
          )}
          {dmStatus && (
            <span className={`text-[9px] border px-1.5 py-0.5 rounded-full ${dmStatus.cls}`}>
              {dmStatus.label}
            </span>
          )}
          {lead.stage === "new" && lead.outreach_message && (
            <span className="text-[9px] bg-slate-800/80 text-slate-400 border border-slate-700/50 px-1.5 py-0.5 rounded-full">
              DM prêt
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
