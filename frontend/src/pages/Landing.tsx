import { Link } from "react-router-dom";

// ── Decorative background orbs ───────────────────────────────────────────────
function Orb({ className }: { className: string }) {
  return <div className={`absolute rounded-full blur-[120px] pointer-events-none ${className}`} />;
}

// ── Navigation ───────────────────────────────────────────────────────────────
function Nav() {
  return (
    <nav className="sticky top-0 z-50 border-b border-white/[0.04] bg-[#0a0a0a]/90 backdrop-blur-xl">
      <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-4">
        <span className="font-black text-xl tracking-tight">
          Lean<span className="text-brand-500">Lead</span>
        </span>
        <div className="hidden md:flex items-center gap-8 text-sm text-slate-400 font-medium">
          <a href="#how" className="hover:text-white transition-colors">Comment ça marche</a>
          <a href="#features" className="hover:text-white transition-colors">Fonctionnalités</a>
          <a href="#pricing" className="hover:text-white transition-colors">Tarifs</a>
        </div>
        <div className="flex items-center gap-3">
          <Link to="/login" className="hidden sm:block text-sm text-slate-400 hover:text-white transition-colors font-medium px-2">
            Connexion
          </Link>
          <Link to="/register" className="text-sm bg-brand-500 hover:bg-brand-400 text-white px-5 py-2.5 rounded-xl font-semibold transition-all shadow-glow-brand hover:shadow-glow-brand-lg">
            Commencer →
          </Link>
        </div>
      </div>
    </nav>
  );
}

// ── Avatar stack for social proof ────────────────────────────────────────────
function AvatarStack() {
  const avatars = [
    { i: "ML", c: "bg-purple-600" },
    { i: "SC", c: "bg-emerald-600" },
    { i: "JB", c: "bg-sky-600" },
    { i: "AR", c: "bg-amber-600" },
    { i: "TK", c: "bg-rose-600" },
  ];
  return (
    <div className="flex items-center gap-3">
      <div className="flex -space-x-2.5">
        {avatars.map(a => (
          <div key={a.i} className={`w-9 h-9 rounded-full ${a.c} border-2 border-[#0a0a0a] flex items-center justify-center text-[10px] font-bold text-white flex-shrink-0`}>
            {a.i}
          </div>
        ))}
      </div>
      <div>
        <div className="flex gap-0.5 mb-0.5">
          {Array(5).fill(0).map((_, i) => <span key={i} className="text-amber-400 text-xs">★</span>)}
        </div>
        <p className="text-xs text-slate-400 font-medium">
          <span className="text-white font-semibold">500+ coachs</span> utilisent déjà LeanLead
        </p>
      </div>
    </div>
  );
}

// ── Hero ─────────────────────────────────────────────────────────────────────
function Hero() {
  return (
    <section className="relative px-6 pt-24 pb-16 overflow-hidden">
      <Orb className="w-[800px] h-[500px] bg-brand-500/6 top-[-100px] left-1/2 -translate-x-1/2" />
      <Orb className="w-[400px] h-[400px] bg-brand-600/5 top-[200px] right-[-100px]" />
      <Orb className="w-[300px] h-[300px] bg-brand-700/4 top-[100px] left-[-80px]" />

      <div className="relative max-w-4xl mx-auto text-center">
        {/* Badge */}
        <div className="inline-flex items-center gap-2 text-xs bg-brand-500/10 border border-brand-500/25 text-brand-400 px-4 py-2 rounded-full font-semibold tracking-widest uppercase mb-8">
          <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-pulse" />
          Propulsé par l'IA Claude d'Anthropic
        </div>

        {/* Headline */}
        <h1 className="text-5xl sm:text-6xl lg:text-7xl font-black leading-[1.05] tracking-tight mb-6">
          Remplissez votre agenda<br />
          <span className="bg-gradient-to-r from-brand-400 via-orange-300 to-brand-500 bg-clip-text text-transparent">
            de clients idéaux
          </span>
          <br />— en automatique.
        </h1>

        {/* Subheadline */}
        <p className="text-lg sm:text-xl text-slate-400 max-w-2xl mx-auto leading-relaxed mb-10 font-light">
          LeanLead trouve les personnes qui expriment vos douleurs cibles sur Instagram & TikTok,
          rédige des DMs empathiques ultra-personnalisés, et gère les relances jusqu'à la réservation.
          <span className="text-slate-300 font-medium"> Pendant que vous coachez.</span>
        </p>

        {/* CTAs */}
        <div className="flex flex-col sm:flex-row gap-4 justify-center mb-12">
          <Link
            to="/register"
            className="px-10 py-4 bg-brand-500 hover:bg-brand-400 text-white rounded-2xl font-bold text-base transition-all shadow-glow-brand hover:shadow-glow-brand-lg hover:scale-[1.02] active:scale-[0.98]"
          >
            Commencer gratuitement →
          </Link>
          <a
            href="#how"
            className="px-8 py-4 border border-white/10 hover:border-white/20 bg-white/[0.03] hover:bg-white/[0.06] text-slate-300 hover:text-white rounded-2xl font-semibold text-base transition-all"
          >
            Voir comment ça marche
          </a>
        </div>

        {/* Social proof */}
        <div className="flex justify-center">
          <AvatarStack />
        </div>

        {/* Microtext */}
        <p className="text-xs text-slate-700 mt-4 font-medium">
          Sans carte bancaire · Plan gratuit · Annulable à tout moment
        </p>
      </div>

      {/* App mockup */}
      <div className="relative max-w-5xl mx-auto mt-20">
        <div className="absolute inset-x-0 -top-10 h-20 bg-gradient-to-b from-[#0a0a0a] to-transparent z-10 pointer-events-none" />
        <div className="absolute inset-x-0 -bottom-1 h-32 bg-gradient-to-t from-[#0a0a0a] to-transparent z-10 pointer-events-none" />
        <div className="rounded-2xl border border-white/[0.06] bg-[#111]/80 overflow-hidden shadow-[0_40px_120px_rgba(0,0,0,0.6)] backdrop-blur-sm">
          {/* Window bar */}
          <div className="flex items-center gap-2 px-5 py-3.5 border-b border-white/[0.05] bg-[#0a0a0a]/60">
            <div className="flex gap-1.5">
              <div className="w-3 h-3 rounded-full bg-red-500/60" />
              <div className="w-3 h-3 rounded-full bg-amber-500/60" />
              <div className="w-3 h-3 rounded-full bg-emerald-500/60" />
            </div>
            <span className="ml-2 text-xs text-slate-600 font-mono">leanlead.app/dashboard</span>
          </div>
          {/* Mock nav */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-white/[0.04]">
            <span className="font-black text-sm text-brand-400">LeanLead</span>
            <div className="flex gap-5 text-xs font-medium">
              <span className="text-brand-400 border-b border-brand-500 pb-0.5">Pipeline</span>
              <span className="text-slate-600">Prospection</span>
              <span className="text-slate-600">Relances</span>
              <span className="text-slate-600">Analytiques</span>
            </div>
            <div className="text-xs bg-brand-500 text-white px-3 py-1.5 rounded-lg font-semibold">+ Nouveau lead</div>
          </div>
          {/* Stats row */}
          <div className="flex gap-8 px-5 py-3.5 border-b border-white/[0.04]">
            {[["127", "Total leads"], ["38", "Contactés"], ["14", "Répondus"], ["8", "Réservés"], ["11.2%", "Conversion"]].map(([v, l]) => (
              <div key={l}>
                <p className="text-[10px] text-slate-600 font-medium uppercase tracking-wider">{l}</p>
                <p className="text-lg font-black text-white">{v}</p>
              </div>
            ))}
          </div>
          {/* Kanban columns */}
          <div className="flex gap-3 p-4 overflow-x-auto">
            {[
              { label: "NOUVEAU", color: "border-slate-700", count: 67 },
              { label: "CONTACTÉ", color: "border-[#2a2a2a]", count: 38 },
              { label: "RÉPONDU", color: "border-brand-700", count: 14 },
              { label: "RÉSERVÉ", color: "border-emerald-800", count: 8 },
              { label: "CLÔTURÉ", color: "border-rose-900", count: 3 },
            ].map((col, ci) => (
              <div key={col.label} className={`flex-shrink-0 w-52 rounded-xl border ${col.color} bg-[#0d0d0d]`}>
                <div className="flex justify-between items-center px-3 py-2.5 border-b border-white/[0.04]">
                  <span className="text-[9px] font-bold text-slate-500 tracking-[0.15em]">{col.label}</span>
                  <span className="text-[9px] bg-slate-800 text-slate-500 px-1.5 py-0.5 rounded-full font-semibold">{col.count}</span>
                </div>
                <div className="p-2 space-y-2">
                  {ci === 0 ? [
                    { name: "Sophie M.", handle: "sophiefitlife", score: 91, tag: "confiance en soi" },
                    { name: "Marc D.", handle: "marcdubusiness", score: 78, tag: "business en ligne" },
                    { name: "Léa R.", handle: "learose_coach", score: 85, tag: "perte de poids" },
                  ].map(c => (
                    <div key={c.handle} className="bg-[#1a1a1a] rounded-lg p-2.5 border border-white/[0.04]">
                      <div className="flex justify-between mb-1.5">
                        <div>
                          <p className="text-xs font-semibold text-white">{c.name}</p>
                          <p className="text-[10px] text-slate-600">@{c.handle}</p>
                        </div>
                        <span className={`text-[11px] font-black ${c.score >= 80 ? "text-emerald-400" : "text-amber-400"}`}>{c.score}</span>
                      </div>
                      <span className="text-[9px] bg-[#1a1a1a] border border-white/[0.06] text-brand-400 px-1.5 py-0.5 rounded-full font-medium">{c.tag}</span>
                    </div>
                  )) : (
                    <div className="h-16 rounded-lg border border-dashed border-white/[0.05] flex items-center justify-center">
                      <span className="text-[9px] text-slate-800 font-medium">Glisser ici</span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Stats bar ─────────────────────────────────────────────────────────────────
function Stats() {
  const items = [
    { value: "500+", label: "Coachs actifs" },
    { value: "12%", label: "Taux de réponse moyen" },
    { value: "48h", label: "Premier lead qualifié" },
    { value: "4.9★", label: "Note moyenne" },
  ];
  return (
    <div className="border-y border-white/[0.04] bg-white/[0.015] py-10 px-6">
      <div className="max-w-4xl mx-auto grid grid-cols-2 sm:grid-cols-4 gap-8">
        {items.map((item, i) => (
          <div key={item.label} className={`text-center ${i < items.length - 1 ? "sm:border-r border-white/[0.06]" : ""}`}>
            <p className="text-3xl font-black text-white mb-1">{item.value}</p>
            <p className="text-xs text-slate-500 font-medium uppercase tracking-wider">{item.label}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── How it works ──────────────────────────────────────────────────────────────
function HowItWorks() {
  const steps = [
    {
      n: "01",
      icon: "✦",
      title: "Décrivez votre coaching en langage naturel",
      desc: "Pas de catégories, pas de formulaires complexes. Vous écrivez simplement ce que vous faites.",
      detail: "L'IA détecte votre niche, génère les points de douleur de vos clients et les hashtags ciblés — en 30 secondes.",
    },
    {
      n: "02",
      icon: "◎",
      title: "L'IA prospecte & qualifie pour vous",
      desc: "LeanLead scrape Instagram & TikTok pour trouver les personnes qui expriment vos douleurs cibles.",
      detail: "Chaque profil est scoré de 0 à 100 sur l'intensité de la douleur exprimée. Seuls les meilleurs arrivent dans votre pipeline.",
    },
    {
      n: "03",
      icon: "→",
      title: "DMs empathiques + relances → réservations",
      desc: "Claude rédige un message personnalisé par lead — jamais de template. Moins de 70 mots. Zéro pitch.",
      detail: "Relances automatiques J+2, J+4, J+7 avec des tons différents. Quand ils répondent, l'IA guide vers votre Calendly.",
    },
  ];

  return (
    <section id="how" className="py-32 px-6">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-20">
          <p className="text-brand-500 text-xs font-bold uppercase tracking-[0.2em] mb-4">Comment ça marche</p>
          <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-5">
            Du profil froid à l'appel réservé
            <span className="block bg-gradient-to-r from-brand-400 to-orange-300 bg-clip-text text-transparent">en 3 étapes.</span>
          </h2>
          <p className="text-slate-400 text-lg font-light max-w-xl mx-auto">Configuration en 5 minutes. Premiers leads qualifiés dans les 10 minutes suivantes.</p>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {steps.map((s) => (
            <div key={s.n} className="relative bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-8 hover:border-brand-500/20 transition-all hover:-translate-y-1 group">
              <div className="flex items-center justify-between mb-6">
                <div className="w-12 h-12 rounded-xl bg-white/[0.06] border border-white/[0.08] flex items-center justify-center text-white text-xl font-black group-hover:bg-white/[0.09] transition-colors">
                  {s.icon}
                </div>
                <span className="text-4xl font-black text-white/[0.05]">{s.n}</span>
              </div>
              <h3 className="text-lg font-bold text-white mb-3 leading-snug">{s.title}</h3>
              <p className="text-slate-300 text-sm leading-relaxed mb-3">{s.desc}</p>
              <p className="text-slate-600 text-xs leading-relaxed">{s.detail}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Features ──────────────────────────────────────────────────────────────────
function Features() {
  const features = [
    {
      icon: "🧠",
      title: "Détection IA de votre niche",
      desc: "Décrivez votre coaching en deux phrases. L'IA identifie votre créneau, vos clients idéaux et les douleurs qu'ils expriment sur les réseaux.",
    },
    {
      icon: "🔍",
      title: "Prospection Instagram & TikTok",
      desc: "Scraping automatique par hashtag ciblé sur la douleur, pas la solution. Score 0-100 basé sur l'intensité émotionnelle du contenu.",
    },
    {
      icon: "✍️",
      title: "DMs ultra-personnalisés",
      desc: "Chaque DM fait référence à la vraie bio et aux posts du lead. Empathie d'abord, question ouverte, zéro pitch. Taux de réponse 8–15%.",
    },
    {
      icon: "🔁",
      title: "Relances J+2 / J+4 / J+7",
      desc: "Trois messages automatiques : rappel doux, valeur ajoutée, dernière tentative. Tons différents, entièrement rédigés par l'IA.",
    },
    {
      icon: "💬",
      title: "Gestion des réponses IA",
      desc: "Collez une réponse de lead, obtenez en secondes une suggestion IA calibrée pour orienter la conversation vers votre Calendly.",
    },
    {
      icon: "📊",
      title: "Pipeline Kanban",
      desc: "Vue visuelle Nouveau → Contacté → Répondu → Réservé → Clôturé. Glissez-déposez vos leads d'une étape à l'autre, intégration Calendly native.",
    },
  ];

  return (
    <section id="features" className="py-32 px-6 relative">
      <Orb className="w-[500px] h-[500px] bg-brand-600/4 top-1/2 left-[-150px] -translate-y-1/2" />

      <div className="relative max-w-5xl mx-auto">
        <div className="text-center mb-20">
          <p className="text-brand-500 text-xs font-bold uppercase tracking-[0.2em] mb-4">Fonctionnalités</p>
          <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-5">Tout ce qu'il faut pour<br />remplir votre agenda.</h2>
          <p className="text-slate-400 text-lg font-light">Une seule plateforme, de la prospection à la réservation.</p>
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {features.map((f) => (
            <div key={f.title} className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-7 hover:border-brand-500/25 transition-all hover:-translate-y-0.5 group">
              <div className="w-12 h-12 bg-white/[0.06] rounded-xl flex items-center justify-center text-2xl mb-5 group-hover:scale-110 transition-transform">
                {f.icon}
              </div>
              <h3 className="text-base font-bold text-white mb-2.5">{f.title}</h3>
              <p className="text-sm text-slate-400 leading-relaxed font-light">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Testimonials ──────────────────────────────────────────────────────────────
function Testimonials() {
  const testimonials = [
    {
      quote: "Je n'aurais jamais cru qu'un DM à froid puisse sonner aussi authentique. Première semaine avec LeanLead : 6 réponses positives et 3 appels réservés. Mes clientes ont dit que c'était le message le plus « humain » qu'elles avaient reçu.",
      name: "Marie-Sophie D.",
      role: "Coach Confiance en Soi",
      location: "Paris",
      result: "3 appels / semaine 1",
      initials: "MD",
      color: "bg-purple-600",
    },
    {
      quote: "La détection de créneau m'a bluffé. J'ai décrit mon coaching en deux phrases et l'IA a trouvé exactement les bons hashtags — des gens qui VIVENT mon problème cible. Mes leads sont enfin qualifiés, je perds zéro temps sur de mauvais prospects.",
      name: "Julien B.",
      role: "Coach Business & Mindset",
      location: "Lyon",
      result: "−80% temps de prospection",
      initials: "JB",
      color: "bg-sky-600",
    },
    {
      quote: "La relance J+7 a converti une cliente qui m'avait ghostée pendant deux semaines. Elle m'a dit que ce message l'avait « touchée au bon moment ». Cette réservation s'est transformée en un accompagnement à 2 800 € sur 3 mois.",
      name: "Camille R.",
      role: "Coach Bien-être & Nutrition",
      location: "Bordeaux",
      result: "2 800 € sur 1 relance",
      initials: "CR",
      color: "bg-emerald-600",
    },
  ];

  return (
    <section className="py-32 px-6 relative">
      <Orb className="w-[500px] h-[500px] bg-brand-600/4 top-1/2 right-[-150px] -translate-y-1/2" />

      <div className="relative max-w-5xl mx-auto">
        <div className="text-center mb-20">
          <p className="text-brand-500 text-xs font-bold uppercase tracking-[0.2em] mb-4">Témoignages</p>
          <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-5">De vrais coachs.<br />De vrais résultats.</h2>
        </div>

        <div className="grid sm:grid-cols-3 gap-6">
          {testimonials.map((t) => (
            <div key={t.name} className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-7 flex flex-col">
              {/* Stars */}
              <div className="flex gap-0.5 mb-5">
                {Array(5).fill(0).map((_, i) => <span key={i} className="text-amber-400 text-sm">★</span>)}
              </div>
              {/* Quote */}
              <p className="text-sm text-slate-300 leading-relaxed font-light flex-1 mb-6">
                "{t.quote}"
              </p>
              {/* Author + result */}
              <div className="flex items-end justify-between pt-5 border-t border-white/[0.05]">
                <div className="flex items-center gap-3">
                  <div className={`w-9 h-9 rounded-full ${t.color} flex items-center justify-center text-[11px] font-bold text-white flex-shrink-0`}>
                    {t.initials}
                  </div>
                  <div>
                    <p className="text-sm font-bold text-white">{t.name}</p>
                    <p className="text-xs text-slate-500">{t.role} · {t.location}</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-xs font-bold text-brand-400 bg-white/[0.05] border border-brand-500/30 px-2.5 py-1 rounded-lg whitespace-nowrap">
                    {t.result}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Pricing ───────────────────────────────────────────────────────────────────
function PricingCard({
  name, price, sub, features, hot, cta,
}: {
  name: string; price: string; sub: string; features: string[]; hot?: boolean; cta: string;
}) {
  return (
    <div className={`relative rounded-2xl p-8 flex flex-col transition-all ${
      hot
        ? "bg-brand-500 border border-brand-400 shadow-[0_0_60px_rgba(255,117,31,0.20)] scale-[1.03]"
        : "bg-[#1a1a1a] border border-[#2a2a2a] hover:border-white/[0.12]"
    }`}>
      {hot && (
        <div className="absolute -top-4 left-1/2 -translate-x-1/2 bg-amber-400 text-[#0a0a0a] text-[10px] font-black px-4 py-1.5 rounded-full tracking-widest uppercase shadow-lg">
          Le plus populaire
        </div>
      )}
      <div className="mb-6">
        <p className={`text-xs font-bold uppercase tracking-[0.15em] mb-3 ${hot ? "text-white/70" : "text-slate-500"}`}>{name}</p>
        <div className="flex items-baseline gap-1.5">
          <span className="text-5xl font-black text-white">{price}</span>
          {price !== "0€" && (
            <span className={`text-sm font-medium ${hot ? "text-white/60" : "text-slate-500"}`}>/mois</span>
          )}
        </div>
        <p className={`text-xs mt-2 font-light ${hot ? "text-white/70" : "text-slate-500"}`}>{sub}</p>
      </div>

      <ul className="space-y-3.5 flex-1 mb-8">
        {features.map(f => (
          <li key={f} className="flex items-start gap-2.5 text-sm">
            <span className={`mt-0.5 text-xs font-bold ${hot ? "text-white" : "text-brand-500"}`}>✓</span>
            <span className={`font-light leading-snug ${hot ? "text-white/90" : "text-slate-300"}`}>{f}</span>
          </li>
        ))}
      </ul>

      <Link
        to="/register"
        className={`text-center py-3.5 rounded-xl text-sm font-bold transition-all ${
          hot
            ? "bg-white text-brand-600 hover:bg-brand-50 shadow-lg"
            : "bg-white/[0.06] hover:bg-white/[0.10] text-white border border-white/[0.08]"
        }`}
      >
        {cta}
      </Link>
    </div>
  );
}

function Pricing() {
  return (
    <section id="pricing" className="py-32 px-6 relative">
      <Orb className="w-[600px] h-[400px] bg-brand-500/5 bottom-0 left-1/2 -translate-x-1/2" />

      <div className="relative max-w-5xl mx-auto">
        <div className="text-center mb-20">
          <p className="text-brand-500 text-xs font-bold uppercase tracking-[0.2em] mb-4">Tarifs</p>
          <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-5">Simple. Transparent.<br />Rentable dès le premier client.</h2>
          <p className="text-slate-400 text-lg font-light max-w-md mx-auto">Commencez gratuitement. Passez à la version supérieure quand vous signez.</p>
        </div>

        <div className="grid sm:grid-cols-3 gap-6 items-center">
          <PricingCard
            name="Gratuit"
            price="0€"
            sub="Pour tester sans risque."
            features={[
              "20 leads / mois",
              "Instagram uniquement",
              "DMs générés par l'IA",
              "Pipeline Kanban",
              "Détection de créneau IA",
            ]}
            cta="Commencer gratuitement"
          />
          <PricingCard
            name="Growth"
            price="49€"
            sub="Pour les coachs en prospection active."
            features={[
              "200 leads / mois",
              "5 plateformes (IG, TT, LI, X, Reddit)",
              "Relances automatiques J+2 / J+4 / J+7",
              "Analyse psychographique des leads",
              "Intégration Calendly native",
              "Support prioritaire",
            ]}
            hot
            cta="Démarrer le plan Growth"
          />
          <PricingCard
            name="Agency"
            price="129€"
            sub="Pour les agences et coachs à fort volume."
            features={[
              "Leads illimités",
              "Toutes les plateformes",
              "Agent autonome 24h/7j",
              "5 comptes coachs inclus",
              "Prompts IA personnalisés",
              "Support Slack dédié",
            ]}
            cta="Démarrer le plan Agency"
          />
        </div>

        <p className="text-center text-xs text-slate-600 mt-8 font-medium">
          Tous les plans incluent · SSL · Données sécurisées · Annulable à tout moment
        </p>
      </div>
    </section>
  );
}

// ── Final CTA ─────────────────────────────────────────────────────────────────
function FinalCTA() {
  return (
    <section className="py-32 px-6">
      <div className="max-w-3xl mx-auto">
        <div className="relative rounded-3xl overflow-hidden bg-gradient-to-br from-brand-600 via-brand-500 to-orange-400 p-1">
          <div className="bg-[#0a0a0a] rounded-[22px] px-10 py-16 text-center relative overflow-hidden">
            <Orb className="w-[400px] h-[300px] bg-brand-500/15 top-[-50px] left-1/2 -translate-x-1/2" />
            <div className="relative">
              <p className="text-brand-400 text-xs font-bold uppercase tracking-[0.2em] mb-6">Prêt à scaler ?</p>
              <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-6">
                Remplissez votre agenda<br />
                <span className="bg-gradient-to-r from-brand-400 to-orange-300 bg-clip-text text-transparent">
                  dès cette semaine.
                </span>
              </h2>
              <p className="text-slate-400 text-lg font-light mb-10 max-w-lg mx-auto">
                Rejoignez 500+ coachs qui utilisent LeanLead pour trouver leurs clients idéaux — sans prospecter manuellement.
              </p>
              <Link
                to="/register"
                className="inline-flex items-center gap-2 px-12 py-5 bg-brand-500 hover:bg-brand-400 text-white rounded-2xl font-bold text-lg transition-all shadow-glow-brand-lg hover:scale-[1.02] active:scale-[0.98]"
              >
                Commencer gratuitement →
              </Link>
              <p className="text-slate-700 text-xs mt-5 font-medium">Sans carte bancaire · Configuration en 5 minutes</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Footer ────────────────────────────────────────────────────────────────────
function Footer() {
  return (
    <footer className="border-t border-white/[0.04] py-10 px-6">
      <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-6">
        <span className="font-black text-lg">Lean<span className="text-brand-500">Lead</span></span>
        <div className="flex gap-6 text-xs text-slate-600 font-medium">
          <a href="#features" className="hover:text-slate-400 transition-colors">Fonctionnalités</a>
          <a href="#pricing" className="hover:text-slate-400 transition-colors">Tarifs</a>
          <Link to="/login" className="hover:text-slate-400 transition-colors">Connexion</Link>
          <Link to="/register" className="hover:text-slate-400 transition-colors">S'inscrire</Link>
        </div>
        <p className="text-xs text-slate-700 font-medium">© {new Date().getFullYear()} LeanLead. Conçu pour les coachs qui concluent.</p>
      </div>
    </footer>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function Landing() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white overflow-x-hidden">
      <Nav />
      <Hero />
      <Stats />
      <HowItWorks />
      <Features />
      <Testimonials />
      <Pricing />
      <FinalCTA />
      <Footer />
    </div>
  );
}
