import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { authApi, leadMagnetsApi, keywordTriggersApi, type Testimonial, type LeadMagnet, type KeywordTrigger } from "../lib/api";

const Field = ({ label, children, connected }: { label: string; children: React.ReactNode; connected?: boolean }) => (
  <div>
    <div className="flex items-center gap-2 mb-1.5">
      <label className="text-xs font-medium text-slate-400 tracking-wide">{label}</label>
      {connected && (
        <span className="text-[10px] text-emerald-400 bg-emerald-950/40 border border-emerald-900/50 px-1.5 py-0.5 rounded-full">
          Compte connecté ✓
        </span>
      )}
    </div>
    {children}
  </div>
);

const MAGNET_TYPE_LABELS: Record<string, string> = {
  pdf: "📄 PDF / Guide",
  video: "🎥 Vidéo",
  ebook: "📚 Ebook",
  call: "📞 Appel gratuit",
  course: "🎓 Mini-formation",
  other: "🔗 Autre",
};

function LeadMagnetSection() {
  const qc = useQueryClient();
  const { data: magnets = [] } = useQuery({
    queryKey: ["lead-magnets"],
    queryFn: () => leadMagnetsApi.list().then(r => r.data),
  });
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", type: "pdf" as LeadMagnet["type"], link: "" });

  const create = useMutation({
    mutationFn: () => leadMagnetsApi.create(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-magnets"] });
      setShowForm(false);
      setForm({ title: "", description: "", type: "pdf", link: "" });
    },
  });

  const del = useMutation({
    mutationFn: (id: number) => leadMagnetsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-magnets"] }),
  });

  const inputCls = "w-full bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-brand-500 transition-all duration-150";

  return (
    <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-6">
      <div className="flex items-center justify-between mb-1">
        <p className="font-heading text-sm font-semibold text-white">Bibliothèque de lead magnets</p>
        <button onClick={() => setShowForm(v => !v)}
          className="text-xs px-3 py-1.5 bg-brand-500/20 hover:bg-brand-500/30 border border-brand-500/30 text-brand-400 rounded-lg transition-colors">
          + Ajouter
        </button>
      </div>
      <p className="text-[11px] text-slate-500 mb-4">
        L'IA sélectionne automatiquement le lead magnet le plus adapté au profil de chaque prospect.
      </p>

      {magnets.length > 0 && (
        <div className="space-y-2 mb-4">
          {magnets.map(m => (
            <div key={m.id} className="flex items-center gap-3 bg-slate-900 border border-[#2a2a2a] rounded-xl px-4 py-3">
              <span className="text-lg">{MAGNET_TYPE_LABELS[m.type]?.split(" ")[0] || "🔗"}</span>
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-slate-200 truncate">{m.title}</p>
                {m.link && <p className="text-[10px] text-slate-500 truncate">{m.link}</p>}
              </div>
              <span className="text-[10px] text-slate-500 bg-slate-800 px-2 py-0.5 rounded-full">{MAGNET_TYPE_LABELS[m.type]?.split(" ").slice(1).join(" ")}</span>
              <button onClick={() => del.mutate(m.id)}
                className="text-slate-600 hover:text-red-400 text-lg leading-none transition-colors">×</button>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <div className="border border-dashed border-slate-700/50 rounded-xl p-4 space-y-3">
          <input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
            placeholder="Titre (ex: Guide Gratuit — 5 Erreurs à Éviter)"
            className={inputCls} />
          <select value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value as any }))}
            className={inputCls}>
            {Object.entries(MAGNET_TYPE_LABELS).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          <input value={form.link} onChange={e => setForm(f => ({ ...f, link: e.target.value }))}
            placeholder="Lien (Google Drive, Calendly, YouTube…)"
            className={inputCls} />
          <input value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="Description courte (optionnel)"
            className={inputCls} />
          <div className="flex gap-2">
            <button onClick={() => create.mutate()} disabled={!form.title || create.isPending}
              className="flex-1 py-2 bg-brand-500 hover:bg-brand-400 disabled:opacity-40 rounded-xl text-xs font-semibold transition-colors">
              {create.isPending ? "Ajout…" : "Ajouter le lead magnet"}
            </button>
            <button onClick={() => setShowForm(false)}
              className="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-xs text-slate-400 transition-colors">
              Annuler
            </button>
          </div>
        </div>
      )}

      {magnets.length === 0 && !showForm && (
        <div className="text-center py-6 text-slate-600 text-xs">
          Aucun lead magnet — ajoutez-en un pour automatiser vos DMs
        </div>
      )}
    </div>
  );
}

function KeywordTriggerSection({ leadMagnets }: { leadMagnets: LeadMagnet[] }) {
  const qc = useQueryClient();
  const { data: triggers = [] } = useQuery({
    queryKey: ["keyword-triggers"],
    queryFn: () => keywordTriggersApi.list().then(r => r.data),
  });
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    keyword: "",
    platform: "instagram",
    message_template: "",
    lead_magnet_id: null as number | null,
  });

  const create = useMutation({
    mutationFn: () => keywordTriggersApi.create(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["keyword-triggers"] });
      setShowForm(false);
      setForm({ keyword: "", platform: "instagram", message_template: "", lead_magnet_id: null });
    },
  });

  const del = useMutation({
    mutationFn: (id: number) => keywordTriggersApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["keyword-triggers"] }),
  });

  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      keywordTriggersApi.update(id, { active } as any),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["keyword-triggers"] }),
  });

  const inputCls = "w-full bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-brand-500 transition-all duration-150";

  return (
    <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-6">
      <div className="flex items-center justify-between mb-1">
        <p className="font-heading text-sm font-semibold text-white">Mots-clés déclencheurs</p>
        <button onClick={() => setShowForm(v => !v)}
          className="text-xs px-3 py-1.5 bg-brand-500/20 hover:bg-brand-500/30 border border-brand-500/30 text-brand-400 rounded-lg transition-colors">
          + Ajouter
        </button>
      </div>
      <p className="text-[11px] text-slate-500 mb-4">
        Quand quelqu'un commente un mot-clé sous votre post → DM automatique avec votre lead magnet. La tactique #1 sur Instagram et TikTok.
      </p>

      {triggers.length > 0 && (
        <div className="space-y-2 mb-4">
          {triggers.map(t => (
            <div key={t.id} className="bg-slate-900 border border-[#2a2a2a] rounded-xl px-4 py-3">
              <div className="flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs font-bold text-brand-400 bg-brand-500/10 px-2 py-0.5 rounded-full">
                      {t.keyword}
                    </span>
                    <span className="text-[10px] text-slate-500">{t.platform}</span>
                    <span className="text-[10px] text-emerald-400">{t.trigger_count} déclenchements</span>
                  </div>
                  {t.lead_magnet && (
                    <p className="text-[10px] text-slate-500 truncate">→ {t.lead_magnet.title}</p>
                  )}
                </div>
                <button onClick={() => toggle.mutate({ id: t.id, active: !t.active })}
                  className={`text-[10px] px-2 py-1 rounded-full font-medium transition-colors ${
                    t.active ? "bg-emerald-950/40 text-emerald-400 border border-emerald-900/50"
                              : "bg-slate-800 text-slate-500 border border-slate-700"
                  }`}>
                  {t.active ? "Actif" : "Pausé"}
                </button>
                <button onClick={() => del.mutate(t.id)}
                  className="text-slate-600 hover:text-red-400 text-lg leading-none transition-colors">×</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <div className="border border-dashed border-slate-700/50 rounded-xl p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Mot-clé (majuscules)</label>
              <input value={form.keyword}
                onChange={e => setForm(f => ({ ...f, keyword: e.target.value.toUpperCase() }))}
                placeholder="ex: GUIDE"
                className={inputCls} />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 mb-1 block">Plateforme</label>
              <select value={form.platform}
                onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}
                className={inputCls}>
                <option value="instagram">📸 Instagram</option>
                <option value="tiktok">🎵 TikTok</option>
              </select>
            </div>
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Lead magnet à envoyer (optionnel)</label>
            <select value={form.lead_magnet_id || ""}
              onChange={e => setForm(f => ({ ...f, lead_magnet_id: e.target.value ? parseInt(e.target.value) : null }))}
              className={inputCls}>
              <option value="">— Aucun lead magnet —</option>
              {leadMagnets.map(m => (
                <option key={m.id} value={m.id}>{m.title}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Message DM (laisser vide = message auto)</label>
            <textarea value={form.message_template}
              onChange={e => setForm(f => ({ ...f, message_template: e.target.value }))}
              placeholder={`Hey ! Tu as commenté ${form.keyword || 'MOT-CLÉ'} sous mon post 😊 Voici ce que j'ai préparé pour toi : {{link}}`}
              rows={3}
              className={inputCls + " resize-none"} />
            <p className="text-[10px] text-slate-600 mt-1">Utilisez {"{{link}}"} pour insérer automatiquement le lien du lead magnet</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => create.mutate()} disabled={!form.keyword || create.isPending}
              className="flex-1 py-2 bg-brand-500 hover:bg-brand-400 disabled:opacity-40 rounded-xl text-xs font-semibold transition-colors">
              {create.isPending ? "Ajout…" : "Créer le déclencheur"}
            </button>
            <button onClick={() => setShowForm(false)}
              className="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-xs text-slate-400 transition-colors">
              Annuler
            </button>
          </div>
        </div>
      )}

      {triggers.length === 0 && !showForm && (
        <div className="text-center py-6 text-slate-600 text-xs">
          Aucun déclencheur — créez-en un pour automatiser vos DMs depuis les commentaires
        </div>
      )}
    </div>
  );
}

export default function Settings() {
  const qc = useQueryClient();
  const { data: coach, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: () => authApi.me().then(r => r.data),
  });
  const { data: leadMagnets = [] } = useQuery({
    queryKey: ["lead-magnets"],
    queryFn: () => leadMagnetsApi.list().then(r => r.data),
  });

  const [form, setForm] = useState({
    niche: "", offer_description: "", target_audience: "", calendly_link: "",
    instagram_handle: "", tiktok_handle: "", twitter_handle: "", reddit_handle: "",
    facebook_url: "", linkedin_url: "",
  });
  const [saved, setSaved] = useState(false);
  const [testimonials, setTestimonials] = useState<Testimonial[]>([]);
  const [newT, setNewT] = useState<Testimonial>({ name: "", situation: "", result: "" });
  const [testimonialSaved, setTestimonialSaved] = useState(false);

  useEffect(() => {
    if (coach) {
      setForm({
        niche:             coach.niche             || "",
        offer_description: coach.offer_description || "",
        target_audience:   coach.target_audience   || "",
        calendly_link:     coach.calendly_link     || "",
        instagram_handle:  coach.instagram_handle  || "",
        tiktok_handle:     coach.tiktok_handle     || "",
        twitter_handle:    coach.twitter_handle    || "",
        reddit_handle:     coach.reddit_handle     || "",
        facebook_url:      coach.facebook_url      || "",
        linkedin_url:      coach.linkedin_url      || "",
      });
      setTestimonials(coach.testimonials || []);
    }
  }, [coach]);

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }));

  const save = useMutation({
    mutationFn: () => authApi.updateSettings(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    },
  });

  const saveTestimonials = useMutation({
    mutationFn: () => authApi.updateSettings({ testimonials }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] });
      setTestimonialSaved(true);
      setTimeout(() => setTestimonialSaved(false), 2000);
    },
  });

  function addTestimonial() {
    if (!newT.situation && !newT.result) return;
    setTestimonials(t => [...t, { ...newT, name: newT.name || "Client anonyme" }]);
    setNewT({ name: "", situation: "", result: "" });
  }

  const inputCls = "w-full bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-brand-500 focus:shadow-glow-sm transition-all duration-150";

  return (
    <div className="min-h-screen bg-slate-950" style={{ backgroundImage: "radial-gradient(ellipse 80% 50% at 50% -20%, rgba(255,117,31,0.05), transparent)" }}>
      <div className="max-w-xl mx-auto px-4 py-10">
        {/* Header */}
        <div className="flex items-center justify-between mb-10">
          <Link to="/dashboard" className="flex items-center gap-1.5 text-slate-500 hover:text-slate-300 text-sm transition-colors">
            ← Tableau de bord
          </Link>
          <span className="font-heading font-extrabold text-lg tracking-tight">
            Lean<span className="text-brand-400">Lead</span>
          </span>
        </div>

        <h1 className="font-heading text-2xl font-bold text-white mb-1">Paramètres</h1>
        <p className="text-sm text-slate-500 mb-8">Personnalisez votre profil et vos intégrations.</p>

        {isLoading ? (
          <div className="space-y-4 animate-pulse">
            {[1,2,3].map(i => <div key={i} className="h-24 bg-slate-900 rounded-2xl" />)}
          </div>
        ) : (
          <div className="space-y-4">
            {/* Coaching info */}
            <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-6">
              <p className="font-heading text-sm font-semibold text-white mb-5">Profil de coaching</p>
              <div className="space-y-4">
                <Field label="Votre créneau">
                  <input value={form.niche} onChange={set("niche")} className={inputCls}
                    placeholder="ex. Coaching business pour entrepreneurs en ligne" />
                </Field>
                <Field label="Client idéal">
                  <textarea value={form.target_audience} onChange={set("target_audience")} rows={3}
                    className={inputCls + " resize-none"}
                    placeholder="ex. Coachs en ligne souhaitant décrocher 5 clients premium en 90 jours…" />
                </Field>
                <Field label="Transformation que vous apportez">
                  <textarea value={form.offer_description} onChange={set("offer_description")} rows={3}
                    className={inputCls + " resize-none"}
                    placeholder="ex. J'aide les coachs à décrocher leurs 5 premiers clients premium en 90 jours…" />
                </Field>
                <Field label="Lien Calendly">
                  <input value={form.calendly_link} onChange={set("calendly_link")} className={inputCls}
                    placeholder="https://calendly.com/yourname/30min" />
                </Field>
              </div>
            </div>

            {/* Social Accounts */}
            <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-6">
              <div className="flex items-center justify-between mb-1">
                <p className="font-heading text-sm font-semibold text-white">Réseaux sociaux</p>
                <span className="text-[10px] text-slate-600 flex items-center gap-1">🔒 Données chiffrées</span>
              </div>
              <p className="text-[11px] text-slate-500 mb-4">
                L'IA utilise vos profils pour trouver des audiences similaires. Vos identifiants sont stockés de façon sécurisée et chiffrée.
              </p>
              <div className="space-y-4">
                <Field label="Instagram" connected={!!coach?.instagram_handle}>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm select-none">@</span>
                    <input value={form.instagram_handle} onChange={set("instagram_handle")}
                      className={inputCls + " pl-7"} placeholder="votre_pseudo" />
                  </div>
                </Field>
                <Field label="TikTok" connected={!!coach?.tiktok_handle}>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm select-none">@</span>
                    <input value={form.tiktok_handle} onChange={set("tiktok_handle")}
                      className={inputCls + " pl-7"} placeholder="votre_pseudo" />
                  </div>
                </Field>
                <Field label="Twitter / X (optionnel)" connected={!!coach?.twitter_handle}>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm select-none">@</span>
                    <input value={form.twitter_handle} onChange={set("twitter_handle")}
                      className={inputCls + " pl-7"} placeholder="votre_pseudo" />
                  </div>
                </Field>
                <Field label="Reddit (optionnel)" connected={!!coach?.reddit_handle}>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm select-none">u/</span>
                    <input value={form.reddit_handle} onChange={set("reddit_handle")}
                      className={inputCls + " pl-8"} placeholder="votre_pseudo" />
                  </div>
                </Field>
                <Field label="Page Facebook (optionnel)" connected={!!coach?.facebook_url}>
                  <input value={form.facebook_url} onChange={set("facebook_url")} className={inputCls}
                    placeholder="https://facebook.com/votrepageprofessionnelle" />
                </Field>
                <Field label="LinkedIn (optionnel)" connected={!!coach?.linkedin_url}>
                  <input value={form.linkedin_url} onChange={set("linkedin_url")} className={inputCls}
                    placeholder="https://linkedin.com/in/votre-profil" />
                </Field>
              </div>
            </div>

            {/* Social Proof Testimonials */}
            <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-6">
              <p className="font-heading text-sm font-semibold text-white mb-1">Témoignages clients</p>
              <p className="text-[11px] text-slate-500 mb-4">
                L'IA référence naturellement le témoignage le plus pertinent dans les DMs pour augmenter la confiance.
              </p>

              {/* Existing testimonials */}
              {testimonials.length > 0 && (
                <div className="space-y-2 mb-4">
                  {testimonials.map((t, i) => (
                    <div key={i} className="bg-slate-900 border border-[#2a2a2a] rounded-xl px-4 py-3 flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-xs font-semibold text-slate-200 truncate">{t.name}</p>
                        <p className="text-[11px] text-slate-500 mt-0.5 truncate">
                          {t.situation} → {t.result}
                        </p>
                      </div>
                      <button
                        onClick={() => setTestimonials(ts => ts.filter((_, j) => j !== i))}
                        className="text-slate-600 hover:text-red-400 text-lg leading-none flex-shrink-0 transition-colors"
                      >×</button>
                    </div>
                  ))}
                </div>
              )}

              {/* Add new testimonial */}
              <div className="space-y-2 border border-dashed border-slate-700/50 rounded-xl p-3">
                <p className="text-[10px] text-slate-600 font-medium">Ajouter un témoignage</p>
                <input
                  value={newT.name}
                  onChange={e => setNewT(t => ({ ...t, name: e.target.value }))}
                  placeholder="Prénom du client (ex. Sarah)"
                  className="w-full bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2 text-xs focus:outline-none focus:border-brand-500 transition-colors text-slate-200 placeholder-slate-600"
                />
                <input
                  value={newT.situation}
                  onChange={e => setNewT(t => ({ ...t, situation: e.target.value }))}
                  placeholder="Situation initiale (ex. coach sans clients, stressée, 2 ans d'échecs)"
                  className="w-full bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2 text-xs focus:outline-none focus:border-brand-500 transition-colors text-slate-200 placeholder-slate-600"
                />
                <input
                  value={newT.result}
                  onChange={e => setNewT(t => ({ ...t, result: e.target.value }))}
                  placeholder="Résultat obtenu (ex. 5 clients premium en 60 jours, +8k€/mois)"
                  className="w-full bg-slate-900 border border-[#2a2a2a] rounded-xl px-3 py-2 text-xs focus:outline-none focus:border-brand-500 transition-colors text-slate-200 placeholder-slate-600"
                />
                <button
                  onClick={addTestimonial}
                  disabled={!newT.situation && !newT.result}
                  className="w-full py-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 rounded-xl text-xs font-medium text-slate-300 transition-colors"
                >
                  + Ajouter
                </button>
              </div>

              {testimonials.length > 0 && (
                <button
                  onClick={() => saveTestimonials.mutate()}
                  disabled={saveTestimonials.isPending}
                  className="mt-3 w-full py-2 bg-brand-500/20 hover:bg-brand-500/30 border border-brand-500/30 text-brand-400 text-xs rounded-xl transition-colors disabled:opacity-40"
                >
                  {testimonialSaved ? "✓ Sauvegardé" : saveTestimonials.isPending ? "Enregistrement…" : "Enregistrer les témoignages"}
                </button>
              )}
            </div>

            {/* Lead Magnet Library */}
            <LeadMagnetSection />

            {/* Keyword Triggers */}
            <KeywordTriggerSection leadMagnets={leadMagnets} />

            {/* Account info */}
            <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-6">
              <p className="font-heading text-sm font-semibold text-white mb-4">Compte</p>
              <div className="flex items-center justify-between py-2 border-b border-[#2a2a2a]">
                <span className="text-xs text-slate-500">Nom</span>
                <span className="text-sm text-slate-300">{coach?.name}</span>
              </div>
              <div className="flex items-center justify-between py-2">
                <span className="text-xs text-slate-500">E-mail</span>
                <span className="text-sm text-slate-300">{coach?.email}</span>
              </div>
            </div>

            {/* Save */}
            {save.isError && (
              <p className="text-red-400 text-xs bg-red-950/40 border border-red-900/40 rounded-xl px-4 py-3">
                {(save.error as any)?.response?.data?.detail || "Échec de l'enregistrement."}
              </p>
            )}
            {saved && (
              <p className="text-emerald-400 text-xs bg-emerald-950/30 border border-emerald-900/40 rounded-xl px-4 py-3">
                ✓ Paramètres enregistrés avec succès.
              </p>
            )}

            <button onClick={() => save.mutate()} disabled={save.isPending}
              className="w-full py-3 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-all duration-150">
              {save.isPending ? "Enregistrement…" : "Enregistrer les paramètres"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
