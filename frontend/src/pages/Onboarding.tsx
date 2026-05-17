import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { authApi } from "../lib/api";

const STEPS = ["Votre coaching", "Votre offre", "Calendly", "Réseaux sociaux"];

function StepBar({ step }: { step: number }) {
  return (
    <div className="flex items-center justify-center gap-3 mb-8">
      {STEPS.map((label, i) => (
        <div key={label} className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div className={`w-8 h-8 rounded-full border-2 flex items-center justify-center text-xs font-bold transition-all ${
              i < step ? "bg-brand-500 border-brand-500 text-white"
              : i === step ? "border-brand-500 text-brand-400"
              : "border-slate-700 text-slate-600"}`}>
              {i < step ? "✓" : i + 1}
            </div>
            <span className={`text-xs hidden sm:block ${i === step ? "text-white" : "text-slate-600"}`}>{label}</span>
          </div>
          {i < STEPS.length - 1 && (
            <div className={`w-10 h-px transition-all ${i < step ? "bg-brand-500" : "bg-slate-800"}`} />
          )}
        </div>
      ))}
    </div>
  );
}

function PainChip({ text, onRemove, onEdit }: { text: string; onRemove: () => void; onEdit: (v: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(text);
  const inputRef = useRef<HTMLInputElement>(null);

  function commit() {
    const trimmed = val.trim();
    if (trimmed && trimmed !== text) onEdit(trimmed);
    else setVal(text);
    setEditing(false);
  }

  if (editing) {
    return (
      <div className="flex items-center gap-1 bg-slate-700 border border-brand-500 rounded-lg px-2 py-1.5 text-sm">
        <input
          ref={inputRef}
          value={val}
          onChange={e => setVal(e.target.value)}
          onBlur={commit}
          onKeyDown={e => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setVal(text); setEditing(false); } }}
          className="bg-transparent outline-none flex-1 min-w-0 text-white"
          autoFocus
        />
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5 bg-[#111] border border-[#2a2a2a] hover:border-slate-600 rounded-lg px-2.5 py-1.5 text-sm group transition-colors">
      <span className="flex-1 text-slate-200 cursor-pointer" onClick={() => { setEditing(true); setTimeout(() => inputRef.current?.select(), 10); }}>{text}</span>
      <button
        onClick={() => { setEditing(true); setTimeout(() => inputRef.current?.select(), 10); }}
        className="text-slate-600 hover:text-slate-300 transition-colors opacity-0 group-hover:opacity-100 text-xs"
        title="Modifier"
      >✎</button>
      <button onClick={onRemove} className="text-slate-600 hover:text-red-400 transition-colors text-xs ml-0.5" title="Supprimer">✕</button>
    </div>
  );
}

export default function Onboarding() {
  const nav = useNavigate();
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Step 0 state
  const [description, setDescription] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzed, setAnalyzed] = useState(false);
  const [suggestedHashtags, setSuggestedHashtags] = useState<string[]>([]);
  const [painInput, setPainInput] = useState("");

  const [form, setForm] = useState({
    niche: "",
    target_audience: "",
    offer_description: "",
    icp_pain_points: [] as string[],
    calendly_link: "",
    instagram_handle: "",
    tiktok_handle: "",
    twitter_handle: "",
    reddit_handle: "",
    facebook_url: "",
    linkedin_url: "",
  });

  const setField = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }));

  async function analyzeNiche() {
    if (description.trim().length < 10) return;
    setAnalyzing(true);
    setError("");
    try {
      const res = await authApi.detectNiche(description);
      setForm(f => ({
        ...f,
        niche: res.data.niche,
        target_audience: res.data.target_audience,
        icp_pain_points: res.data.pain_points,
      }));
      setSuggestedHashtags(res.data.hashtags);
      setAnalyzed(true);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Impossible d'analyser votre coaching. Réessayez.");
    } finally {
      setAnalyzing(false);
    }
  }

  function addPain() {
    const trimmed = painInput.trim();
    if (!trimmed || form.icp_pain_points.includes(trimmed)) return;
    setForm(f => ({ ...f, icp_pain_points: [...f.icp_pain_points, trimmed] }));
    setPainInput("");
  }

  function removePain(pain: string) {
    setForm(f => ({ ...f, icp_pain_points: f.icp_pain_points.filter(p => p !== pain) }));
  }

  function editPain(old: string, next: string) {
    setForm(f => ({ ...f, icp_pain_points: f.icp_pain_points.map(p => p === old ? next : p) }));
  }

  async function finish() {
    setSaving(true);
    setError("");
    try {
      await authApi.onboard(form);
      nav("/dashboard", { replace: true });
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Échec de l'enregistrement. Réessayez.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4 py-12">
      <div className="w-full max-w-lg">
        <StepBar step={step} />

        <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-8 animate-fade-in">

          {/* ── Step 0 : Describe → AI detects ── */}
          {step === 0 && !analyzed && (
            <>
              <h1 className="font-heading text-lg font-semibold mb-1">Décrivez votre coaching</h1>
              <p className="text-xs text-slate-500 mb-6">
                Pas besoin de catégories — expliquez simplement ce que vous faites avec vos mots. L'IA s'occupe du reste.
              </p>
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                rows={4}
                className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500 resize-none"
                placeholder="ex. J'aide les femmes à retrouver confiance en elles après une rupture amoureuse, pour qu'elles puissent attirer la bonne personne et construire la relation dont elles rêvent."
              />
              <div className="mt-2 flex flex-wrap gap-2">
                {[
                  "J'aide les entrepreneurs à scaler leur business en ligne",
                  "J'accompagne les femmes à maigrir durablement sans régime",
                  "J'aide les hommes à développer leur leadership",
                ].map(ex => (
                  <button key={ex} onClick={() => setDescription(ex)}
                    className="text-xs text-slate-600 hover:text-slate-400 border border-[#2a2a2a] hover:border-slate-700 rounded-lg px-2 py-1 transition-colors">
                    {ex.length > 42 ? ex.slice(0, 42) + "…" : ex}
                  </button>
                ))}
              </div>
              {error && <p className="text-red-400 text-xs mt-3 bg-red-950/40 border border-red-900/40 rounded-lg px-3 py-2">{error}</p>}
              <button
                onClick={analyzeNiche}
                disabled={description.trim().length < 10 || analyzing}
                className="mt-6 w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors flex items-center justify-center gap-2"
              >
                {analyzing ? (
                  <>
                    <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                    </svg>
                    Analyse en cours…
                  </>
                ) : "Analyser avec l'IA →"}
              </button>
            </>
          )}

          {/* ── Step 0 phase 2: Confirm AI results ── */}
          {step === 0 && analyzed && (
            <>
              <div className="flex items-center justify-between mb-1">
                <h1 className="font-heading text-lg font-semibold">Voici ce que l'IA a détecté</h1>
                <button onClick={() => { setAnalyzed(false); setError(""); }}
                  className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
                  ← Modifier
                </button>
              </div>
              <p className="text-xs text-slate-500 mb-5">Vérifiez et ajustez si besoin. Cliquez sur une douleur pour la modifier.</p>

              {/* Niche badge */}
              <div className="mb-5">
                <label className="block text-xs text-slate-400 mb-1.5">Créneau détecté</label>
                <input
                  value={form.niche}
                  onChange={setField("niche")}
                  className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm font-medium text-brand-300 focus:outline-none focus:border-brand-500"
                />
              </div>

              {/* Pain points */}
              <div className="mb-5">
                <label className="block text-xs text-slate-400 mb-2">
                  Douleurs de votre client idéal
                  <span className="text-slate-600 ml-1">(cliquez pour modifier)</span>
                </label>
                <div className="space-y-2">
                  {form.icp_pain_points.map(pain => (
                    <PainChip
                      key={pain}
                      text={pain}
                      onRemove={() => removePain(pain)}
                      onEdit={next => editPain(pain, next)}
                    />
                  ))}
                </div>
                <div className="flex gap-2 mt-2">
                  <input
                    value={painInput}
                    onChange={e => setPainInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addPain(); } }}
                    className="flex-1 bg-[#111] border border-[#2a2a2a] rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-brand-500"
                    placeholder="Ajouter une douleur…"
                  />
                  <button onClick={addPain} disabled={!painInput.trim()}
                    className="px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 rounded-xl text-sm font-semibold transition-colors">
                    +
                  </button>
                </div>
              </div>

              {/* Hashtags preview */}
              {suggestedHashtags.length > 0 && (
                <div className="mb-6">
                  <label className="block text-xs text-slate-400 mb-2">
                    Hashtags ciblés pour la prospection
                    <span className="text-slate-600 ml-1">(générés automatiquement)</span>
                  </label>
                  <div className="flex flex-wrap gap-1.5">
                    {suggestedHashtags.map(tag => (
                      <span key={tag} className="text-xs bg-[#111] border border-[#2a2a2a] text-slate-400 rounded-lg px-2 py-1">
                        #{tag}
                      </span>
                    ))}
                  </div>
                  <p className="text-xs text-slate-600 mt-2">L'IA les utilisera pour trouver des personnes qui vivent ces douleurs.</p>
                </div>
              )}

              <button
                onClick={() => setStep(1)}
                disabled={!form.niche || form.icp_pain_points.length === 0}
                className="w-full py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors"
              >
                Confirmer et continuer →
              </button>
            </>
          )}

          {/* ── Step 1 : Offre ── */}
          {step === 1 && (
            <>
              <h1 className="font-heading text-lg font-semibold mb-1">Décrivez votre offre</h1>
              <p className="text-xs text-slate-500 mb-6">L'IA rédige des DMs ciblant directement cette transformation.</p>
              <div>
                <label className="block text-xs text-slate-400 mb-1.5">Quelle transformation apportez-vous ? *</label>
                <textarea
                  value={form.offer_description}
                  onChange={setField("offer_description")}
                  rows={4}
                  className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500 resize-none"
                  placeholder="ex. J'aide les femmes à reprendre confiance en elles après une rupture pour attirer la relation de leurs rêves en 90 jours."
                />
              </div>
              <div className="flex gap-3 mt-6">
                <button onClick={() => setStep(0)}
                  className="flex-1 py-2.5 border border-[#2a2a2a] hover:border-slate-600 rounded-xl text-sm transition-colors">
                  ← Retour
                </button>
                <button onClick={() => setStep(2)} disabled={!form.offer_description}
                  className="flex-1 py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-40 disabled:shadow-none rounded-xl text-sm font-semibold transition-colors">
                  Continuer →
                </button>
              </div>
            </>
          )}

          {/* ── Step 2 : Calendly ── */}
          {step === 2 && (
            <>
              <h1 className="font-heading text-lg font-semibold mb-1">Votre lien de réservation</h1>
              <p className="text-xs text-slate-500 mb-6">
                L'IA l'intègre automatiquement dans les DMs quand un lead est prêt à réserver un appel.
                Vous pouvez le modifier à tout moment dans les Paramètres.
              </p>
              <div>
                <label className="block text-xs text-slate-400 mb-1.5">Lien Calendly</label>
                <input
                  value={form.calendly_link}
                  onChange={setField("calendly_link")}
                  className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                  placeholder="https://calendly.com/yourname/30min"
                />
                <p className="text-xs text-slate-600 mt-1.5">Facultatif — vous pouvez ajouter le lien plus tard.</p>
              </div>
              {error && <p className="text-red-400 text-xs mt-4 bg-red-950/40 border border-red-900/40 rounded-lg px-3 py-2">{error}</p>}
              <div className="flex gap-3 mt-6">
                <button onClick={() => setStep(1)}
                  className="flex-1 py-2.5 border border-[#2a2a2a] hover:border-slate-600 rounded-xl text-sm transition-colors">
                  ← Retour
                </button>
                <button onClick={() => setStep(3)}
                  className="flex-1 py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg rounded-xl text-sm font-semibold transition-colors">
                  Continuer →
                </button>
              </div>
              <button onClick={finish} disabled={saving}
                className="w-full text-center text-xs text-slate-600 hover:text-slate-400 mt-3 transition-colors">
                Passer pour l'instant
              </button>
            </>
          )}

          {/* ── Step 3 : Réseaux sociaux ── */}
          {step === 3 && (
            <>
              <h1 className="font-heading text-lg font-semibold mb-1">Vos réseaux sociaux</h1>
              <p className="text-xs text-slate-500 mb-6">
                L'IA utilise vos profils pour trouver des audiences similaires et personnaliser vos DMs.
                Tous les champs sont facultatifs.
              </p>
              <div className="space-y-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Instagram</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm select-none">@</span>
                    <input
                      value={form.instagram_handle}
                      onChange={setField("instagram_handle")}
                      className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl pl-7 pr-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                      placeholder="votre_pseudo"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">TikTok</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm select-none">@</span>
                    <input
                      value={form.tiktok_handle}
                      onChange={setField("tiktok_handle")}
                      className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl pl-7 pr-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                      placeholder="votre_pseudo"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Twitter / X <span className="text-slate-600">(optionnel)</span></label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm select-none">@</span>
                    <input
                      value={form.twitter_handle}
                      onChange={setField("twitter_handle")}
                      className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl pl-7 pr-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                      placeholder="votre_pseudo"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Reddit <span className="text-slate-600">(optionnel)</span></label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm select-none">u/</span>
                    <input
                      value={form.reddit_handle}
                      onChange={setField("reddit_handle")}
                      className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl pl-8 pr-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                      placeholder="votre_pseudo"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Page Facebook <span className="text-slate-600">(optionnel)</span></label>
                  <input
                    value={form.facebook_url}
                    onChange={setField("facebook_url")}
                    className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                    placeholder="https://facebook.com/votrepageprofessionnelle"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">LinkedIn <span className="text-slate-600">(optionnel)</span></label>
                  <input
                    value={form.linkedin_url}
                    onChange={setField("linkedin_url")}
                    className="w-full bg-[#111] border border-[#2a2a2a] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-brand-500"
                    placeholder="https://linkedin.com/in/votre-profil"
                  />
                </div>
              </div>
              {error && <p className="text-red-400 text-xs mt-4 bg-red-950/40 border border-red-900/40 rounded-lg px-3 py-2">{error}</p>}
              <div className="flex gap-3 mt-6">
                <button onClick={() => setStep(2)}
                  className="flex-1 py-2.5 border border-[#2a2a2a] hover:border-slate-600 rounded-xl text-sm transition-colors">
                  ← Retour
                </button>
                <button onClick={finish} disabled={saving}
                  className="flex-1 py-2.5 bg-brand-500 hover:bg-brand-400 shadow-glow-brand hover:shadow-glow-brand-lg disabled:opacity-50 rounded-xl text-sm font-semibold transition-colors">
                  {saving ? "Enregistrement…" : "Accéder au tableau de bord →"}
                </button>
              </div>
              <button onClick={finish} disabled={saving}
                className="w-full text-center text-xs text-slate-600 hover:text-slate-400 mt-3 transition-colors">
                Passer pour l'instant
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
