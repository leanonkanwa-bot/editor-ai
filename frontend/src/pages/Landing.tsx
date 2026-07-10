import { Link } from "react-router-dom";
import { useLanguage } from "../lib/useLanguage";
import { t as tr, type Lang } from "../lib/i18n";

// ── Helpers ───────────────────────────────────────────────────────────────────
function Orb({ className }: { className: string }) {
  return <div className={`absolute rounded-full blur-[120px] pointer-events-none ${className}`} />;
}

// ── Language toggle ───────────────────────────────────────────────────────────
function LangToggle({ lang, setLang }: { lang: Lang; setLang: (l: Lang) => void }) {
  return (
    <button
      onClick={() => setLang(lang === "fr" ? "en" : "fr")}
      className="flex items-center gap-1 text-xs font-bold text-slate-400 hover:text-white border border-white/10 hover:border-white/25 px-3 py-1.5 rounded-lg transition-all"
      aria-label="Switch language"
    >
      <span className={lang === "fr" ? "text-white" : "text-slate-600"}>FR</span>
      <span className="text-slate-700">/</span>
      <span className={lang === "en" ? "text-white" : "text-slate-600"}>EN</span>
    </button>
  );
}

// ── Navigation ────────────────────────────────────────────────────────────────
function Nav({ lang, setLang, t }: { lang: Lang; setLang: (l: Lang) => void; t: (k: Parameters<typeof tr>[1]) => string }) {
  return (
    <nav className="sticky top-0 z-50 border-b border-white/[0.04] bg-[#0a0a0a]/90 backdrop-blur-xl">
      <div className="max-w-6xl mx-auto flex items-center justify-between px-6 py-4">
        <span className="font-black text-xl tracking-tight">
          Lean<span className="text-brand-500">Retention</span>
        </span>
        <div className="hidden md:flex items-center gap-8 text-sm text-slate-400 font-medium">
          <a href="#how" className="hover:text-white transition-colors">{t("nav_how")}</a>
          <a href="#features" className="hover:text-white transition-colors">{t("nav_features")}</a>
          <a href="#pricing" className="hover:text-white transition-colors">{t("nav_pricing")}</a>
        </div>
        <div className="flex items-center gap-3">
          <LangToggle lang={lang} setLang={setLang} />
          <Link to="/login" className="hidden sm:block text-sm text-slate-400 hover:text-white transition-colors font-medium px-2">
            {t("nav_login")}
          </Link>
          <Link to="/register" className="text-sm bg-brand-500 hover:bg-brand-400 text-white px-5 py-2.5 rounded-xl font-semibold transition-all shadow-glow-brand hover:shadow-glow-brand-lg">
            {t("nav_cta")}
          </Link>
        </div>
      </div>
    </nav>
  );
}

// ── Avatar stack ──────────────────────────────────────────────────────────────
function AvatarStack({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
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
          <span className="text-white font-semibold">{t("social_proof_count")}</span> {t("social_proof_label")}
        </p>
      </div>
    </div>
  );
}

// ── Before/After Demo ─────────────────────────────────────────────────────────
function BeforeAfter({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  return (
    <div className="relative max-w-4xl mx-auto mt-16 grid grid-cols-2 gap-3">
      {/* Raw */}
      <div className="rounded-xl border border-white/[0.06] bg-[#111]/80 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-white/[0.05] flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-rose-500/60" />
          <span className="text-[10px] font-bold text-slate-500 uppercase tracking-[0.12em]">{t("before_label")}</span>
        </div>
        <div className="p-5 space-y-3">
          <div className="space-y-2">
            <p className="text-xs text-slate-500 leading-relaxed font-mono">
              "{t("ba_raw_hook")}"
            </p>
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <div className="flex-1 h-1.5 bg-[#1f1f1f] rounded-full overflow-hidden">
                <div className="w-[37%] h-full bg-rose-500/60 rounded-full" />
              </div>
              <span className="text-[10px] text-rose-400 font-mono whitespace-nowrap">37%</span>
            </div>
            <p className="text-[10px] text-slate-600 font-medium">{t("ba_raw_tag")}</p>
          </div>
        </div>
      </div>

      {/* Edited */}
      <div className="rounded-xl border border-brand-500/20 bg-[#111]/80 overflow-hidden shadow-[0_0_30px_rgba(255,117,31,0.06)]">
        <div className="px-4 py-2.5 border-b border-brand-500/15 flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-brand-500" />
          <span className="text-[10px] font-bold text-brand-400 uppercase tracking-[0.12em]">{t("after_label")}</span>
        </div>
        <div className="p-5 space-y-3">
          <div className="space-y-2">
            <p className="text-xs text-white leading-relaxed font-semibold">
              "{t("ba_edited_hook")}"
            </p>
          </div>
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <div className="flex-1 h-1.5 bg-[#1f1f1f] rounded-full overflow-hidden">
                <div className="w-[84%] h-full bg-brand-500/80 rounded-full" />
              </div>
              <span className="text-[10px] text-brand-400 font-mono whitespace-nowrap">84%</span>
            </div>
            <div className="flex gap-2">
              <span className="text-[9px] font-semibold text-brand-400 bg-brand-500/10 border border-brand-500/20 px-2 py-0.5 rounded-full">{t("ba_edited_tag_1")}</span>
              <span className="text-[9px] font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded-full">{t("ba_edited_tag_2")}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Arrow between */}
      <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-10">
        <div className="w-8 h-8 rounded-full bg-brand-500 flex items-center justify-center shadow-glow-brand text-white text-sm font-black">
          →
        </div>
      </div>
    </div>
  );
}

// ── Hero ──────────────────────────────────────────────────────────────────────
function Hero({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  return (
    <section className="relative px-6 pt-24 pb-16 overflow-hidden">
      <Orb className="w-[800px] h-[500px] bg-brand-500/6 top-[-100px] left-1/2 -translate-x-1/2" />
      <Orb className="w-[400px] h-[400px] bg-brand-600/5 top-[200px] right-[-100px]" />
      <Orb className="w-[300px] h-[300px] bg-brand-700/4 top-[100px] left-[-80px]" />

      <div className="relative max-w-4xl mx-auto text-center">
        <div className="inline-flex items-center gap-2 text-xs bg-brand-500/10 border border-brand-500/25 text-brand-400 px-4 py-2 rounded-full font-semibold tracking-widest uppercase mb-8">
          <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-pulse" />
          {t("hero_badge")}
        </div>

        <h1 className="text-5xl sm:text-6xl lg:text-7xl font-black leading-[1.05] tracking-tight mb-4">
          {t("hero_title_1")}<br />
          <span className="bg-gradient-to-r from-brand-400 via-orange-300 to-brand-500 bg-clip-text text-transparent">
            {t("hero_title_2")}
          </span>
        </h1>

        <p className="text-sm text-brand-400 font-semibold mb-6 tracking-wide">
          {t("hero_diff")}
        </p>

        <p className="text-lg sm:text-xl text-slate-400 max-w-2xl mx-auto leading-relaxed mb-10 font-light">
          {t("hero_subtitle")}
        </p>

        <div className="flex flex-col sm:flex-row gap-4 justify-center mb-10">
          <Link
            to="/register"
            className="px-10 py-4 bg-brand-500 hover:bg-brand-400 text-white rounded-2xl font-bold text-base transition-all shadow-glow-brand hover:shadow-glow-brand-lg hover:scale-[1.02] active:scale-[0.98]"
          >
            {t("hero_cta_primary")}
          </Link>
          <a
            href="#before-after"
            className="px-8 py-4 border border-white/10 hover:border-white/20 bg-white/[0.03] hover:bg-white/[0.06] text-slate-300 hover:text-white rounded-2xl font-semibold text-base transition-all"
          >
            {t("hero_cta_secondary")}
          </a>
        </div>

        <div className="flex justify-center mb-4">
          <AvatarStack t={t} />
        </div>

        <p className="text-xs text-slate-700 mt-4 font-medium">
          {t("hero_no_card")}
        </p>
      </div>

      <div id="before-after">
        <BeforeAfter t={t} />
      </div>
    </section>
  );
}

// ── Stats ─────────────────────────────────────────────────────────────────────
function Stats({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  const items = [
    { v: t("stat_1_v"), l: t("stat_1_l") },
    { v: t("stat_2_v"), l: t("stat_2_l") },
    { v: t("stat_3_v"), l: t("stat_3_l") },
    { v: t("stat_4_v"), l: t("stat_4_l") },
  ];
  return (
    <div className="border-y border-white/[0.04] bg-white/[0.015] py-10 px-6">
      <div className="max-w-4xl mx-auto grid grid-cols-2 sm:grid-cols-4 gap-8">
        {items.map((item, i) => (
          <div key={item.l} className={`text-center ${i < items.length - 1 ? "sm:border-r border-white/[0.06]" : ""}`}>
            <p className="text-3xl font-black text-white mb-1">{item.v}</p>
            <p className="text-xs text-slate-500 font-medium uppercase tracking-wider">{item.l}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── How it works ──────────────────────────────────────────────────────────────
function HowItWorks({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  const steps = [
    { n: t("step_1_n"), icon: t("step_1_icon"), title: t("step_1_title"), desc: t("step_1_desc"), detail: t("step_1_detail") },
    { n: t("step_2_n"), icon: t("step_2_icon"), title: t("step_2_title"), desc: t("step_2_desc"), detail: t("step_2_detail") },
    { n: t("step_3_n"), icon: t("step_3_icon"), title: t("step_3_title"), desc: t("step_3_desc"), detail: t("step_3_detail") },
  ];
  return (
    <section id="how" className="py-32 px-6">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-20">
          <p className="text-brand-500 text-xs font-bold uppercase tracking-[0.2em] mb-4">{t("how_eyebrow")}</p>
          <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-5">
            {t("how_title_1")}
            <span className="block bg-gradient-to-r from-brand-400 to-orange-300 bg-clip-text text-transparent">{t("how_title_2")}</span>
          </h2>
          <p className="text-slate-400 text-lg font-light max-w-xl mx-auto">{t("how_subtitle")}</p>
        </div>
        <div className="grid md:grid-cols-3 gap-6">
          {steps.map(s => (
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
function Features({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  const features = [
    { icon: "🎙", title: t("feat_1_title"), desc: t("feat_1_desc") },
    { icon: "✍️", title: t("feat_2_title"), desc: t("feat_2_desc") },
    { icon: "✂️", title: t("feat_3_title"), desc: t("feat_3_desc") },
    { icon: "💬", title: t("feat_4_title"), desc: t("feat_4_desc") },
    { icon: "📊", title: t("feat_5_title"), desc: t("feat_5_desc") },
    { icon: "🎯", title: t("feat_6_title"), desc: t("feat_6_desc") },
  ];
  return (
    <section id="features" className="py-32 px-6 relative">
      <Orb className="w-[500px] h-[500px] bg-brand-600/4 top-1/2 left-[-150px] -translate-y-1/2" />
      <div className="relative max-w-5xl mx-auto">
        <div className="text-center mb-20">
          <p className="text-brand-500 text-xs font-bold uppercase tracking-[0.2em] mb-4">{t("feat_eyebrow")}</p>
          <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-5 whitespace-pre-line">{t("feat_title")}</h2>
          <p className="text-slate-400 text-lg font-light">{t("feat_subtitle")}</p>
        </div>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {features.map(f => (
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

// ── Coach section ─────────────────────────────────────────────────────────────
function Coach({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  const blocks = [
    { icon: "💡", title: t("coach_1_title"), desc: t("coach_1_desc") },
    { icon: "📈", title: t("coach_2_title"), desc: t("coach_2_desc") },
    { icon: "🗓", title: t("coach_3_title"), desc: t("coach_3_desc") },
  ];
  return (
    <section className="py-32 px-6 relative">
      <Orb className="w-[600px] h-[400px] bg-brand-500/5 top-1/2 right-[-100px] -translate-y-1/2" />
      <div className="relative max-w-5xl mx-auto">
        <div className="text-center mb-16">
          <p className="text-brand-500 text-xs font-bold uppercase tracking-[0.2em] mb-4">{t("coach_eyebrow")}</p>
          <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-5">
            {t("coach_title_1")}<br />
            <span className="bg-gradient-to-r from-brand-400 to-orange-300 bg-clip-text text-transparent">
              {t("coach_title_2")}
            </span>
          </h2>
        </div>
        <div className="grid md:grid-cols-3 gap-6">
          {blocks.map(b => (
            <div key={b.title} className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-2xl p-8 hover:border-brand-500/20 transition-all hover:-translate-y-1">
              <div className="w-14 h-14 rounded-2xl bg-brand-500/10 border border-brand-500/20 flex items-center justify-center text-2xl mb-6">
                {b.icon}
              </div>
              <h3 className="text-lg font-bold text-white mb-3">{b.title}</h3>
              <p className="text-sm text-slate-400 leading-relaxed font-light">{b.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Pricing ───────────────────────────────────────────────────────────────────
function PricingCard({
  name, price, sub, features, hot, cta, popularLabel, perMonth,
}: {
  name: string; price: string; sub: string; features: string[];
  hot?: boolean; cta: string; popularLabel: string; perMonth: string;
}) {
  return (
    <div className={`relative rounded-2xl p-8 flex flex-col transition-all ${
      hot
        ? "bg-brand-500 border border-brand-400 shadow-[0_0_60px_rgba(255,117,31,0.20)] scale-[1.03]"
        : "bg-[#1a1a1a] border border-[#2a2a2a] hover:border-white/[0.12]"
    }`}>
      {hot && (
        <div className="absolute -top-4 left-1/2 -translate-x-1/2 bg-amber-400 text-[#0a0a0a] text-[10px] font-black px-4 py-1.5 rounded-full tracking-widest uppercase shadow-lg">
          {popularLabel}
        </div>
      )}
      <div className="mb-6">
        <p className={`text-xs font-bold uppercase tracking-[0.15em] mb-3 ${hot ? "text-white/70" : "text-slate-500"}`}>{name}</p>
        <div className="flex items-baseline gap-1.5">
          <span className="text-5xl font-black text-white">{price}</span>
          {price !== "0€" && price !== "$0" && (
            <span className={`text-sm font-medium ${hot ? "text-white/60" : "text-slate-500"}`}>{perMonth}</span>
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

function Pricing({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  return (
    <section id="pricing" className="py-32 px-6 relative">
      <Orb className="w-[600px] h-[400px] bg-brand-500/5 bottom-0 left-1/2 -translate-x-1/2" />
      <div className="relative max-w-5xl mx-auto">
        <div className="text-center mb-20">
          <p className="text-brand-500 text-xs font-bold uppercase tracking-[0.2em] mb-4">{t("pricing_eyebrow")}</p>
          <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-5 whitespace-pre-line">{t("pricing_title")}</h2>
          <p className="text-slate-400 text-lg font-light max-w-md mx-auto">{t("pricing_subtitle")}</p>
        </div>
        <div className="grid sm:grid-cols-3 gap-6 items-center">
          <PricingCard
            name={t("plan_free_name")}
            price={t("plan_free_price")}
            sub={t("plan_free_sub")}
            features={[t("plan_free_f1"), t("plan_free_f2"), t("plan_free_f3"), t("plan_free_f4")]}
            cta={t("plan_free_cta")}
            popularLabel={t("plan_popular")}
            perMonth={t("plan_per_month")}
          />
          <PricingCard
            name={t("plan_pro_name")}
            price={t("plan_pro_price")}
            sub={t("plan_pro_sub")}
            features={[t("plan_pro_f1"), t("plan_pro_f2"), t("plan_pro_f3"), t("plan_pro_f4"), t("plan_pro_f5"), t("plan_pro_f6")]}
            hot
            cta={t("plan_pro_cta")}
            popularLabel={t("plan_popular")}
            perMonth={t("plan_per_month")}
          />
          <PricingCard
            name={t("plan_agency_name")}
            price={t("plan_agency_price")}
            sub={t("plan_agency_sub")}
            features={[t("plan_agency_f1"), t("plan_agency_f2"), t("plan_agency_f3"), t("plan_agency_f4"), t("plan_agency_f5")]}
            cta={t("plan_agency_cta")}
            popularLabel={t("plan_popular")}
            perMonth={t("plan_per_month")}
          />
        </div>
        <p className="text-center text-xs text-slate-600 mt-8 font-medium">{t("pricing_footer")}</p>
      </div>
    </section>
  );
}

// ── Final CTA ─────────────────────────────────────────────────────────────────
function FinalCTA({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  return (
    <section className="py-32 px-6">
      <div className="max-w-3xl mx-auto">
        <div className="relative rounded-3xl overflow-hidden bg-gradient-to-br from-brand-600 via-brand-500 to-orange-400 p-1">
          <div className="bg-[#0a0a0a] rounded-[22px] px-10 py-16 text-center relative overflow-hidden">
            <Orb className="w-[400px] h-[300px] bg-brand-500/15 top-[-50px] left-1/2 -translate-x-1/2" />
            <div className="relative">
              <p className="text-brand-400 text-xs font-bold uppercase tracking-[0.2em] mb-6">{t("cta_eyebrow")}</p>
              <h2 className="text-4xl sm:text-5xl font-black leading-tight mb-6">
                {t("cta_title_1")}<br />
                <span className="bg-gradient-to-r from-brand-400 to-orange-300 bg-clip-text text-transparent">
                  {t("cta_title_2")}
                </span>
              </h2>
              <p className="text-slate-400 text-lg font-light mb-10 max-w-lg mx-auto">{t("cta_subtitle")}</p>
              <Link
                to="/register"
                className="inline-flex items-center gap-2 px-12 py-5 bg-brand-500 hover:bg-brand-400 text-white rounded-2xl font-bold text-lg transition-all shadow-glow-brand-lg hover:scale-[1.02] active:scale-[0.98]"
              >
                {t("cta_button")}
              </Link>
              <p className="text-slate-700 text-xs mt-5 font-medium">{t("cta_micro")}</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Footer ────────────────────────────────────────────────────────────────────
function Footer({ t }: { t: (k: Parameters<typeof tr>[1]) => string }) {
  return (
    <footer className="border-t border-white/[0.04] py-10 px-6">
      <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-6">
        <span className="font-black text-lg">Lean<span className="text-brand-500">Retention</span></span>
        <div className="flex gap-6 text-xs text-slate-600 font-medium">
          <a href="#features" className="hover:text-slate-400 transition-colors">{t("nav_features")}</a>
          <a href="#pricing" className="hover:text-slate-400 transition-colors">{t("nav_pricing")}</a>
          <Link to="/login" className="hover:text-slate-400 transition-colors">{t("nav_login")}</Link>
        </div>
        <p className="text-xs text-slate-700 font-medium">© {new Date().getFullYear()} LeanRetention. {t("footer_tagline")}</p>
      </div>
    </footer>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function Landing() {
  const { lang, setLang } = useLanguage();
  const bound = (key: Parameters<typeof tr>[1]) => tr(lang, key);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white overflow-x-hidden">
      <Nav lang={lang} setLang={setLang} t={bound} />
      <Hero t={bound} />
      <Stats t={bound} />
      <HowItWorks t={bound} />
      <Features t={bound} />
      <Coach t={bound} />
      <Pricing t={bound} />
      <FinalCTA t={bound} />
      <Footer t={bound} />
    </div>
  );
}
