const $ = (id) => document.getElementById(id);

// ── Auth helpers ─────────────────────────────────────────────────────────────
function getToken() { return sessionStorage.getItem("lle_token") || ""; }
function authHeaders() { const t = getToken(); return t ? { "x-access-token": t } : {}; }
function apiFetch(url, opts = {}) {
  opts.credentials = opts.credentials || "same-origin";
  opts.headers = { ...authHeaders(), ...(opts.headers || {}) };
  return fetch(url, opts);
}

// ── Section switching ─────────────────────────────────────────────────────────
function switchSection(targetId) {
  ["editorArea", "dashboardSection", "analyticsSection", "profileSection"].forEach(id => {
    const el = $(id);
    if (el) el.classList.toggle("active", id === targetId);
  });
  document.querySelectorAll(".nav-tab[data-target]").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.target === targetId);
  });
  if (targetId === "analyticsSection") loadAnalytics();
  if (targetId === "dashboardSection") { updateDashboardStats(); loadVideoLibrary(); updateStreak(); updateAchievements(); initReferral(); }
  if (targetId === "profileSection") loadProfileSection();
  if (targetId === "editorArea") updateOnboardingProgress();
}

// Nav tab clicks
document.querySelectorAll(".nav-tab[data-target]").forEach(tab => {
  tab.addEventListener("click", () => switchSection(tab.dataset.target));
});

// Dashboard → Editor button
$("dashEditBtn")?.addEventListener("click", () => switchSection("editorArea"));

// ── Init: decide which section to show ───────────────────────────────────────
(async function initSection() {
  try {
    let raw = localStorage.getItem("coach_profile");

    // Always re-sync from server when profile_id is present (keeps data fresh)
    const profileId = localStorage.getItem("profile_id");
    if (profileId) {
      try {
        const res = await fetch(`/api/profile/${profileId}`);
        if (res.ok) {
          const restored = await res.json();
          localStorage.setItem("coach_profile", JSON.stringify(restored));
          raw = JSON.stringify(restored);
        }
      } catch {}
    }

    if (!raw) { switchSection("editorArea"); return; }
    const p = JSON.parse(raw);

    // Show "Bienvenue {name}" greeting in nav
    const greetingEl = $("navGreeting");
    if (greetingEl) {
      const name = p.name || p.brandName || "";
      if (name) {
        greetingEl.textContent = `Bienvenue ${name} 👋`;
        greetingEl.style.display = "";
      }
    }

    const nameEl = $("dashName");
    if (nameEl) nameEl.textContent = p.name || p.brandName || "toi";

    updateDashboardStats();
    updateStreak();
    updateAchievements();
    updateOnboardingProgress();

    const onboarded = localStorage.getItem("onboarded") === "true";
    if (onboarded) {
      switchSection("dashboardSection");
    } else if (localStorage.getItem("has_edited_video")) {
      switchSection("dashboardSection");
    } else {
      switchSection("editorArea");
    }
  } catch {
    switchSection("editorArea");
  }
})();

// ── Dashboard stats ───────────────────────────────────────────────────────────
function updateDashboardStats() {
  try {
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const count = videos.length;
    if ($("dashVideos")) $("dashVideos").textContent = count;
    if ($("dashTimeSaved")) $("dashTimeSaved").textContent = (count * 4) + "h";
    if ($("dashViews")) $("dashViews").textContent = count > 0 ? (count * 10000).toLocaleString("fr-FR") : "—";
  } catch {}
}

// ── Streak & Gamification ─────────────────────────────────────────────────────
function updateStreak() {
  try {
    const today = new Date().toDateString();
    const yesterday = new Date(Date.now() - 86400000).toDateString();
    const lastActivity = localStorage.getItem("last_activity_date");
    let streak = parseInt(localStorage.getItem("streak_count") || "0", 10);

    if (lastActivity === today) {
      // Already counted today — no change
    } else if (lastActivity === yesterday) {
      // Consecutive day
      streak += 1;
      localStorage.setItem("streak_count", String(streak));
      localStorage.setItem("last_activity_date", today);
    } else if (!lastActivity) {
      // First time
      streak = 1;
      localStorage.setItem("streak_count", "1");
      localStorage.setItem("last_activity_date", today);
    } else {
      // Gap > 1 day → reset
      streak = 1;
      localStorage.setItem("streak_count", "1");
      localStorage.setItem("last_activity_date", today);
    }

    const countEl = $("streakCount");
    if (countEl) countEl.textContent = streak;

    // Progress bar toward next milestone
    const milestones = [7, 14, 30, 60, 100];
    const nextMilestone = milestones.find(m => m > streak) || 100;
    const prevMilestone = milestones.filter(m => m <= streak).pop() || 0;
    const pct = Math.min(100, Math.round(((streak - prevMilestone) / (nextMilestone - prevMilestone)) * 100));
    const barEl = $("streakBarFill");
    if (barEl) barEl.style.width = pct + "%";

    const milestoneEl = $("streakMilestone");
    if (milestoneEl) milestoneEl.textContent = `Prochain palier : ${nextMilestone} jours 🏆`;

    // Weekly goal — videos edited this week
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const weekAgo = new Date(Date.now() - 7 * 86400000);
    const weeklyCount = videos.filter(v => v.date && new Date(v.date) > weekAgo).length;
    const weeklyGoal = 3;
    const weeklyPct = Math.min(100, Math.round((weeklyCount / weeklyGoal) * 100));
    const weeklyBarEl = $("weeklyGoalBar");
    if (weeklyBarEl) weeklyBarEl.style.width = weeklyPct + "%";
    const weeklyDisplay = $("weeklyGoalDisplay");
    if (weeklyDisplay) weeklyDisplay.textContent = `${weeklyCount} / ${weeklyGoal} vidéos`;
  } catch {}
}

// ── Achievements ──────────────────────────────────────────────────────────────
function updateAchievements() {
  try {
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const count = videos.length;
    const streak = parseInt(localStorage.getItem("streak_count") || "0", 10);
    const weekAgo = new Date(Date.now() - 7 * 86400000);
    const weeklyCount = videos.filter(v => v.date && new Date(v.date) > weekAgo).length;

    const unlock = (id) => {
      const el = $(id);
      if (el) { el.classList.add("unlocked"); el.classList.remove("locked"); }
    };

    if (count >= 1) unlock("ach-first");
    if (streak >= 7) unlock("ach-streak7");
    if (count >= 10) unlock("ach-10videos");
    if (weeklyCount >= 3) unlock("ach-active");
  } catch {}
}

// ── Video Library ─────────────────────────────────────────────────────────────
function loadVideoLibrary() {
  try {
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const gridEl = $("videoLibraryGrid");
    const emptyEl = $("videoLibraryEmpty");
    if (!gridEl || !emptyEl) return;

    if (videos.length === 0) {
      gridEl.style.display = "none";
      emptyEl.style.display = "block";
      return;
    }

    emptyEl.style.display = "none";
    gridEl.style.display = "grid";
    gridEl.innerHTML = videos.slice(0, 12).map((v, i) => {
      const score = v.retention_score || Math.floor(Math.random() * 20) + 75;
      const dateStr = v.date ? new Date(v.date).toLocaleDateString("fr-FR") : "—";
      const title = v.title || `Vidéo #${i + 1}`;
      const scoreColor = score >= 85 ? "#22c55e" : score >= 70 ? "var(--salmon)" : "#ff5c7a";
      return `<div class="video-lib-card" title="${title}">
        <div class="video-lib-thumb">
          🎬
          <span class="video-lib-retention" style="color:${scoreColor}">${score}%</span>
          <div class="video-lib-overlay">
            ${v.jobId ? `<a href="/api/download/${v.jobId}" class="action-btn" download style="color:var(--text)">⬇ Télécharger</a>` : ""}
            <button class="action-btn" onclick="switchSection('editorArea')" style="color:var(--text)">✏ Reediter</button>
          </div>
        </div>
        <div class="video-lib-info">
          <div class="video-lib-title">${title}</div>
          <div class="video-lib-meta">${dateStr} · ${v.format || "Auto"}</div>
        </div>
      </div>`;
    }).join("");
  } catch {}
}

// ── Analytics ─────────────────────────────────────────────────────────────────
async function loadAnalytics() {
  const videos = (() => { try { return JSON.parse(localStorage.getItem("edited_videos") || "[]"); } catch { return []; } })();
  const count = videos.length;

  if ($("aVideos"))    $("aVideos").textContent    = count;
  if ($("aTimeSaved")) $("aTimeSaved").textContent  = (count * 4) + "h";
  if ($("aViews"))     $("aViews").textContent      = count > 0 ? (count * 10000).toLocaleString("fr-FR") : "0";

  const retentionEl = $("aRetention");
  if (retentionEl) {
    const scores = videos.map(v => v.retention_score).filter(s => typeof s === "number" && !isNaN(s));
    if (scores.length > 0) {
      const avg = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
      retentionEl.textContent = avg + "%";
    } else {
      retentionEl.textContent = "—";
    }
  }

  const now = new Date();
  const dateStr = now.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
  if ($("analyticsUpdated")) $("analyticsUpdated").textContent = `Dernière mise à jour : ${dateStr}`;

  // Draw SVG retention curve
  drawRetentionCurve(videos);

  // Video table
  const tableEl    = $("videoTable");
  const tableEmpty = $("videoTableEmpty");
  const tbody      = $("videoTableBody");
  if (tableEl && tableEmpty && tbody) {
    if (count === 0) {
      tableEl.style.display = "none";
      tableEmpty.style.display = "block";
    } else {
      tableEmpty.style.display = "none";
      tableEl.style.display = "table";
      tbody.innerHTML = videos.map((v, i) => {
        const score = v.retention_score;
        const scoreHtml = score != null ? `<span class="retention-badge">${score}%</span>` : `<span class="status-badge status-done">Prêt ✓</span>`;
        return `<tr>
          <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${v.title || '—'}">${v.title || `Vidéo #${i + 1}`}</td>
          <td>${v.format || "Auto"}</td>
          <td>${v.date ? new Date(v.date).toLocaleDateString("fr-FR") : "—"}</td>
          <td>${scoreHtml}</td>
          <td>
            ${v.jobId ? `<a href="/api/download/${v.jobId}" class="action-btn" download>⬇</a>` : ""}
            <button class="action-btn" onclick="switchSection('editorArea')">✏ Reediter</button>
            <button class="action-btn" onclick="deleteVideo('${v.jobId || i}')" style="color:#ff5c7a">✕</button>
          </td>
        </tr>`;
      }).join("");
    }
  }

  // Performance bar chart — retention scores
  const chartEl = $("perfChart");
  if (chartEl) {
    if (count === 0) {
      chartEl.innerHTML = "<p style='font-size:.8rem;color:var(--muted)'>Aucune donnée pour l'instant.</p>";
    } else {
      const maxScore = 100;
      chartEl.innerHTML = videos.map((v, i) => {
        const score = v.retention_score || 75;
        const pct = Math.round((score / maxScore) * 100);
        const label = v.title ? v.title.slice(0, 18) : `Vidéo #${i + 1}`;
        const color = score >= 85 ? "#22c55e" : score >= 70 ? "var(--salmon)" : "#ff5c7a";
        return `<div class="chart-row">
          <span class="chart-label" title="${label}">${label}</span>
          <div class="chart-bar-wrap"><div class="chart-bar-fill" style="width:${pct}%;background:${color}"></div></div>
          <span class="chart-val" style="color:${color}">${score}%</span>
        </div>`;
      }).join("");
    }
  }
}

function drawRetentionCurve(videos) {
  const lineEl = $("retentionLine");
  const fillEl = $("retentionFill");
  const dropEl = $("retDrop1");
  if (!lineEl || !fillEl) return;

  const W = 400, H = 80;
  // Generate smooth curve — demo data if no videos, else use average retention score to shape
  const scores = videos.map(v => v.retention_score).filter(s => typeof s === "number");
  const avgScore = scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : 80;

  // Points: start at 100%, drop to ~avgScore at 50%, recover slightly, end lower
  const pts = [
    [0, 5],
    [40, 10],
    [80, 15],
    [140, H - (avgScore / 100) * (H - 8) - 5],
    [200, H - (avgScore / 100) * (H - 8)],
    [280, H - (avgScore / 100) * (H - 8) + 5],
    [360, H - ((avgScore - 10) / 100) * (H - 8) + 8],
    [400, H - ((avgScore - 15) / 100) * (H - 8) + 10],
  ];

  // Create smooth SVG path using bezier curves
  const d = pts.reduce((acc, [x, y], i) => {
    if (i === 0) return `M ${x} ${y}`;
    const [px, py] = pts[i - 1];
    const cp1x = px + (x - px) / 2;
    const cp2x = px + (x - px) / 2;
    return `${acc} C ${cp1x} ${py}, ${cp2x} ${y}, ${x} ${y}`;
  }, "");

  lineEl.setAttribute("d", d);

  // Fill area under curve
  const fillD = d + ` L ${W} ${H} L 0 ${H} Z`;
  fillEl.setAttribute("d", fillD);

  // Drop annotation at the big drop point
  if (dropEl) {
    const dropPt = pts[3];
    dropEl.setAttribute("cx", String(dropPt[0]));
    dropEl.setAttribute("cy", String(dropPt[1]));
    dropEl.setAttribute("opacity", "0.8");
  }
}

function deleteVideo(jobIdOrIndex) {
  try {
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const updated = videos.filter(v => v.jobId !== jobIdOrIndex);
    localStorage.setItem("edited_videos", JSON.stringify(updated));
    loadAnalytics();
    updateDashboardStats();
    loadVideoLibrary();
    updateAchievements();
  } catch {}
}

$("collectBtn")?.addEventListener("click", async () => {
  const btn = $("collectBtn");
  if (btn) { btn.textContent = "Actualisation…"; btn.disabled = true; }
  await loadAnalytics();
  if (btn) { btn.textContent = "↻ Rafraîchir"; btn.disabled = false; }
});

// ── Coach profile: load profile section ──────────────────────────────────────
function loadProfileSection() {
  try {
    const raw = localStorage.getItem("coach_profile");
    if (!raw) return;
    const p = JSON.parse(raw);

    // Avatar initials
    const avatarEl = $("profileAvatar");
    if (avatarEl) {
      const initials = (p.name || p.brandName || "?").split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
      avatarEl.textContent = initials;
    }

    if ($("profileName")) $("profileName").textContent = p.name || "—";
    if ($("profileBrandName")) $("profileBrandName").textContent = p.brandName || p.email || "—";

    // Role/type badge
    const badgesEl = $("profileBadges");
    if (badgesEl) {
      const roleLabel = { coach:"🎯 Coach", entrepreneur:"💼 Entrepreneur", educator:"📚 Éducateur", creator:"🎬 Créateur" };
      const badges = [];
      if (p.role) badges.push(roleLabel[p.role] || p.role);
      if (p.editingStyle) badges.push(p.editingStyle);
      badgesEl.innerHTML = badges.map(b => `<span class="profile-badge">${b}</span>`).join("");
    }

    // Platforms
    const platsEl = $("profilePlatforms");
    if (platsEl && p.platforms?.length) {
      platsEl.innerHTML = p.platforms.map(pl => `<span class="platform-badge" style="color:var(--salmon);border-color:var(--salmon-border);background:var(--salmon-dim)">${pl}</span>`).join("");
    }

    // ICP
    const icpEl = $("profileIcp");
    if (icpEl && p.icp) icpEl.value = p.icp;

    // Pillars
    if (p.pillars) {
      if ($("pillar1") && p.pillars[0]) $("pillar1").value = p.pillars[0];
      if ($("pillar2") && p.pillars[1]) $("pillar2").value = p.pillars[1];
      if ($("pillar3") && p.pillars[2]) $("pillar3").value = p.pillars[2];
    }
  } catch {}
}

// Edit profile → open onboarding on landing
$("editProfileBtn")?.addEventListener("click", () => {
  window.location.href = "/?edit=1";
});

// Save profile
$("saveProfileBtn")?.addEventListener("click", async () => {
  try {
    const raw = localStorage.getItem("coach_profile");
    const p = raw ? JSON.parse(raw) : {};
    p.icp = $("profileIcp")?.value || "";
    p.pillars = [
      $("pillar1")?.value || "",
      $("pillar2")?.value || "",
      $("pillar3")?.value || "",
    ];
    localStorage.setItem("coach_profile", JSON.stringify(p));

    // Save to backend
    const existingId = localStorage.getItem("profile_id");
    const res = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...p, profile_id: existingId || undefined }),
    });
    if (res.ok) {
      const { profile_id } = await res.json();
      if (profile_id) localStorage.setItem("profile_id", profile_id);
    }

    const msg = $("profileSaveMsg");
    if (msg) { msg.style.display = "block"; setTimeout(() => { msg.style.display = "none"; }, 2500); }
  } catch (e) {
    console.warn("save profile error", e);
  }
});

// ── Coach profile: pre-fill editor fields ─────────────────────────────────────
(function applyCoachProfile() {
  try {
    const raw = localStorage.getItem("coach_profile");
    if (!raw) return;
    const p = JSON.parse(raw);

    if (p.primaryColor && $("brandPrimary"))    $("brandPrimary").value   = p.primaryColor;
    if (p.secondaryColor && $("brandSecondary")) $("brandSecondary").value = p.secondaryColor;
    if (p.brandName && $("brandName"))           $("brandName").value      = p.brandName;

    const brandColorInput = document.querySelector('input[name="brand_color"]');
    if (brandColorInput && p.primaryColor) brandColorInput.value = p.primaryColor;

    // Load saved font choice
    if (p.font) {
      document.querySelectorAll("#brandFontSelector .font-option").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.font === p.font);
      });
    }

    // Load saved style choice
    if (p.editingStyle) {
      document.querySelectorAll("#brandStyleSelector .style-option").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.style === p.editingStyle);
      });
    }

    if (p.platforms?.length) {
      const platformRadioMap = { YouTube: "plt-yt", Reels: "plt-reels", "YouTube Shorts": "plt-shorts", TikTok: "plt-reels", Instagram: "plt-reels" };
      const rid = platformRadioMap[p.platforms[0]];
      if (rid && $(rid)) $(rid).checked = true;
    }

    if (p.format) {
      const fmtMap = { short: "fmt-short", long: "fmt-long", both: "fmt-auto" };
      const fid = fmtMap[p.format];
      if (fid && $(fid)) $(fid).checked = true;
    }

    if (p.email && $("notifEmail")) $("notifEmail").value = p.email;

    if (p.audience && document.querySelector('[name="target_audience"]'))
      document.querySelector('[name="target_audience"]').value = p.audience;
    if (p.offer && document.querySelector('[name="main_message"]'))
      document.querySelector('[name="main_message"]').value = p.offer;

    // Update brand preview
    updateBrandPreview();
  } catch (e) {
    console.warn("coach_profile parse error", e);
  }
})();

// ── Brand panel ───────────────────────────────────────────────────────────────
(async () => {
  try {
    const res = await apiFetch("/api/brand");
    if (res.ok) {
      const b = await res.json();
      if ($("brandName"))      $("brandName").value      = b.name || "";
      if ($("brandPrimary"))   $("brandPrimary").value   = b.colors?.primary    || "#FF7751";
      if ($("brandSecondary")) $("brandSecondary").value = b.colors?.secondary  || "#FFFFFF";
      if ($("brandBg"))        $("brandBg").value        = b.colors?.background || "#0A0A0A";
      if ($("brandWatermark")) $("brandWatermark").value = b.watermark?.text    || "";
      if ($("brandLtName"))    $("brandLtName").value    = b.lower_third?.name_text  || "";
      if ($("brandLtTitle"))   $("brandLtTitle").value   = b.lower_third?.title_text || "";
    }
  } catch {}
  updateBrandPreview();
})();

function updateBrandPreview() {
  const preview = $("brandPreview");
  if (!preview) return;
  const primary   = $("brandPrimary")?.value   || "#FF7751";
  const secondary = $("brandSecondary")?.value || "#FFFFFF";
  const bg        = $("brandBg")?.value        || "#080808";
  const name      = $("brandName")?.value      || "Votre marque";
  preview.style.background = bg;
  preview.style.color = secondary;
  preview.style.borderLeft = `3px solid ${primary}`;
  preview.style.boxShadow = `0 0 16px ${primary}22`;
  preview.textContent = name || "Aperçu de votre marque";
}

// Live preview on color change
$("brandPrimary")?.addEventListener("input", updateBrandPreview);
$("brandSecondary")?.addEventListener("input", updateBrandPreview);
$("brandBg")?.addEventListener("input", updateBrandPreview);
$("brandName")?.addEventListener("input", updateBrandPreview);

// Font selector
document.querySelectorAll("#brandFontSelector .font-option").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#brandFontSelector .font-option").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
  });
});

// Style selector
document.querySelectorAll("#brandStyleSelector .style-option").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#brandStyleSelector .style-option").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
  });
});

// Logo upload preview
$("brandLogo")?.addEventListener("change", () => {
  const file = $("brandLogo")?.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    const img = $("brandLogoImg");
    const wrap = $("brandLogoPreview");
    if (img) img.src = e.target.result;
    if (wrap) wrap.style.display = "block";
  };
  reader.readAsDataURL(file);
});

$("brandBtn")?.addEventListener("click", () => $("brandPanel")?.classList.add("open"));
$("brandClose")?.addEventListener("click", () => $("brandPanel")?.classList.remove("open"));

$("saveBrandBtn")?.addEventListener("click", async () => {
  const activeFont = document.querySelector("#brandFontSelector .font-option.active")?.dataset.font || "Poppins";
  const activeStyle = document.querySelector("#brandStyleSelector .style-option.active")?.dataset.style || "";

  const brand = {
    name: $("brandName")?.value || "",
    colors: {
      primary:    $("brandPrimary")?.value   || "#FF7751",
      secondary:  $("brandSecondary")?.value || "#FFFFFF",
      background: $("brandBg")?.value        || "#0A0A0A",
      accent:     $("brandPrimary")?.value   || "#FF7751",
    },
    watermark: { text: $("brandWatermark")?.value || "" },
    lower_third: {
      name_text:  $("brandLtName")?.value  || "",
      title_text: $("brandLtTitle")?.value || "",
      show_on_first_appearance: true,
      accent_color: $("brandPrimary")?.value || "#FF7751",
    },
    font: activeFont,
    editing_style: activeStyle,
  };
  await apiFetch("/api/brand", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(brand) });

  // Also persist font/style to coach_profile in localStorage
  try {
    const raw = localStorage.getItem("coach_profile");
    const p = raw ? JSON.parse(raw) : {};
    p.font = activeFont;
    p.editingStyle = activeStyle;
    p.primaryColor = brand.colors.primary;
    p.secondaryColor = brand.colors.secondary;
    p.brandName = brand.name;
    localStorage.setItem("coach_profile", JSON.stringify(p));
  } catch {}

  const intro = $("brandIntro")?.files?.[0];
  if (intro) { const fd = new FormData(); fd.append("intro", intro); await apiFetch("/api/brand/intro", { method: "POST", body: fd }); }
  const outro = $("brandOutro")?.files?.[0];
  if (outro) { const fd = new FormData(); fd.append("outro", outro); await apiFetch("/api/brand/outro", { method: "POST", body: fd }); }

  const logoFile = $("brandLogo")?.files?.[0];
  if (logoFile) { const fd = new FormData(); fd.append("logo", logoFile); await apiFetch("/api/brand/logo", { method: "POST", body: fd }).catch(() => {}); }

  const msg = $("brandSaveMsg");
  if (msg) { msg.style.display = "block"; setTimeout(() => { msg.style.display = "none"; }, 2500); }
});

// ── Template selector ─────────────────────────────────────────────────────────
(async () => {
  try {
    const res = await apiFetch("/api/templates");
    if (!res.ok) return;
    const templates = await res.json();
    const sel = $("templateSelect");
    if (!sel) return;
    templates.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.id; opt.textContent = t.name;
      opt.dataset.style = JSON.stringify(t.style_summary || {});
      sel.appendChild(opt);
    });
  } catch {}
})();

$("templateSelect")?.addEventListener("change", () => {
  const sel = $("templateSelect");
  const chips = $("templateChips");
  if (!sel || !chips) return;
  const opt = sel.selectedOptions[0];
  if (!opt || !opt.value) { chips.innerHTML = ""; return; }
  try {
    const s = JSON.parse(opt.dataset.style || "{}");
    chips.innerHTML = [
      s.pacing          ? `<span class="chip accent">${s.pacing} pacing</span>` : "",
      s.zoom_intensity  ? `<span class="chip">${s.zoom_intensity} zoom</span>`  : "",
      s.caption_style   ? `<span class="chip">${s.caption_style} captions</span>` : "",
      s.energy_level    ? `<span class="chip">${s.energy_level} energy</span>`   : "",
      s.cuts_per_minute ? `<span class="chip">${Math.round(s.cuts_per_minute)} cuts/min</span>` : "",
    ].join("");
  } catch { chips.innerHTML = ""; }
});

$("uploadTemplateBtn")?.addEventListener("click", async () => {
  const name = prompt("Template name (e.g. 'Hormozi Style'):");
  if (!name) return;
  const input = document.createElement("input");
  input.type = "file"; input.accept = "video/*";
  input.onchange = async () => {
    const file = input.files?.[0];
    if (!file) return;
    const fd = new FormData(); fd.append("name", name); fd.append("video", file);
    const res = await apiFetch("/api/templates/analyze", { method: "POST", body: fd });
    if (res.ok) {
      const t = await res.json();
      const sel = $("templateSelect");
      if (sel) {
        const opt = document.createElement("option");
        opt.value = t.template_id; opt.textContent = t.name;
        opt.dataset.style = JSON.stringify(t.style_summary || {});
        sel.appendChild(opt); sel.value = t.template_id;
        sel.dispatchEvent(new Event("change"));
      }
    }
  };
  input.click();
});

// ── Editor page variables ─────────────────────────────────────────────────────
const loginCard   = $("login");
const appCard     = $("tool");
const loginForm   = $("loginForm");
const loginPwd    = $("loginPwd");
const loginErr    = $("loginErr");
const form        = $("form");
const submitBtn   = $("submit");
const drop        = $("drop");
const dropLabel   = $("dropLabel");
const videoInput  = $("video");
const statusCard  = $("statusCard");
const statusLabel = $("statusLabel");
const statusMsg   = $("statusMsg");
const statusPip   = $("statusPip");
const barFill     = $("barFill");
const resultCard  = $("resultCard");
const previewCard = $("previewCard");
const player      = $("player");
const downloadLink = $("downloadLink");
const pkgTitle    = $("pkgTitle");
const pkgThumb    = $("pkgThumb");
const pkgEnd      = $("pkgEnd");
const planJson    = $("planJson");

// ── Auth check ────────────────────────────────────────────────────────────────
(async () => {
  try {
    const res = await apiFetch("/api/auth/status");
    const j = await res.json();
    if (j.required && !j.authed) {
      appCard?.classList.add("hidden");
      loginCard?.classList.remove("hidden");
    }
  } catch {
    if (appCard) {
      const warn = document.createElement("p");
      warn.style.cssText = "color:#ff5c7a;margin-bottom:1rem;font-size:.9rem";
      warn.textContent = "⚠ Backend non disponible. Démarrez le serveur et rechargez la page.";
      appCard.querySelector(".tool-head")?.after(warn);
    }
  }
})();

loginForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (loginErr) loginErr.textContent = "";
  const fd = new FormData();
  fd.set("password", loginPwd.value);
  const res = await fetch("/api/auth/login", { method: "POST", body: fd, credentials: "same-origin" });
  if (!res.ok) { if (loginErr) loginErr.textContent = "Mot de passe incorrect."; return; }
  sessionStorage.setItem("lle_token", loginPwd.value);
  loginCard?.classList.add("hidden");
  appCard?.classList.remove("hidden");
  appCard?.scrollIntoView({ behavior: "smooth", block: "start" });
});

const CHUNK_SIZE = 200 * 1024 * 1024; // 200 MB

// ── Drop zone ─────────────────────────────────────────────────────────────────
if (drop) {
  drop.addEventListener("click", () => {
    if (videoInput) videoInput.click();
  });

  drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); videoInput?.click(); }
  });

  ["dragenter", "dragover"].forEach(ev =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragover"); })
  );
  drop.addEventListener("dragleave", (e) => { e.preventDefault(); drop.classList.remove("dragover"); });
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("dragover");
    const files = e.dataTransfer?.files;
    if (files?.length && videoInput) {
      const dt = new DataTransfer();
      dt.items.add(files[0]);
      videoInput.files = dt.files;
      videoInput.dispatchEvent(new Event("change"));
    }
  });
}

// Guard: editor-only listeners
if (videoInput && form && submitBtn) {

videoInput.addEventListener("change", () => {
  const f = videoInput.files?.[0];
  if (!f) return;
  const mb = (f.size / (1024 * 1024)).toFixed(1);
  if (dropLabel) dropLabel.textContent = `${f.name} — ${mb} MB`;
  drop?.classList.add("has-file");
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  // Inject profile_id into form before submit
  const profileIdInput = form.querySelector('input[name="profile_id"]') || (() => {
    const inp = document.createElement("input");
    inp.type = "hidden"; inp.name = "profile_id";
    form.appendChild(inp); return inp;
  })();
  profileIdInput.value = localStorage.getItem("profile_id") || "";

  // Request browser notification permission on first edit (BUILD 8)
  if (localStorage.getItem("notif_permission_asked") !== "1") {
    localStorage.setItem("notif_permission_asked", "1");
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().then(perm => {
        localStorage.setItem("notif_enabled", perm === "granted" ? "true" : "false");
      });
    }
  }

  resultCard?.classList.add("hidden");
  statusCard?.classList.remove("hidden");
  statusCard?.scrollIntoView({ behavior: "smooth", block: "center" });
  setStatus("queued", "Démarrage de l'upload…", 0);
  submitBtn.disabled = true;
  submitBtn.querySelector(".btn-label").textContent = "Traitement…";
  submitBtn.classList.add("loading");

  const file = videoInput.files?.[0];
  if (!file) return fail("Aucun fichier sélectionné.");

  if (file.size > 100 * 1024 * 1024) {
    chunkedUpload(file).catch((err) => {
      const msg = String(err);
      fail(msg.includes("Failed to fetch")
        ? "Impossible de joindre le serveur. Vérifiez que le backend tourne, rechargez et réessayez."
        : msg);
    });
  } else {
    directUpload(file);
  }
});

async function chunkedUpload(file) {
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
  const totalMb = (file.size / (1024 * 1024)).toFixed(0);

  const initRes = await apiFetch("/api/upload/init", { method: "POST" });
  if (!initRes.ok) throw new Error(`Upload init failed: ${initRes.status}`);
  const { upload_id } = await initRes.json();

  for (let i = 0; i < totalChunks; i++) {
    const chunk = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
    const sentMb = Math.min((i * CHUNK_SIZE) / (1024 * 1024), file.size / (1024 * 1024)).toFixed(0);
    const uiPct = Math.round(((i + 1) / totalChunks) * 25);
    setStatus("queued", `Upload ${sentMb} / ${totalMb} Mo (chunk ${i + 1}/${totalChunks})…`, uiPct);
    const res = await apiFetch(`/api/upload/chunk/${upload_id}/${i}`, { method: "PUT", body: chunk });
    if (!res.ok) throw new Error(`Chunk ${i} failed: ${res.status}`);
  }

  setStatus("queued", "Assemblage du fichier sur le serveur…", 26);
  const asmRes = await apiFetch(`/api/upload/assemble/${upload_id}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name }),
  });
  if (!asmRes.ok) throw new Error(`Assembly failed: ${asmRes.status}`);

  setStatus("queued", "Démarrage de l'édition IA…", 28);
  const fd = new FormData(form);
  fd.delete("video"); fd.set("upload_id", upload_id);
  const editRes = await apiFetch("/api/edit", { method: "POST", body: fd });
  if (editRes.status === 401) { loginCard?.classList.remove("hidden"); appCard?.classList.add("hidden"); statusCard?.classList.add("hidden"); submitBtn.disabled = false; submitBtn.querySelector(".btn-label").textContent = "Éditer ma vidéo"; submitBtn.classList.remove("loading"); return; }
  if (!editRes.ok) throw new Error(`Edit start failed: ${editRes.status} ${await editRes.text()}`);
  const { job_id } = await editRes.json();
  poll(job_id);
}

function directUpload(file) {
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/edit");
  xhr.withCredentials = true;
  const _t = getToken();
  if (_t) xhr.setRequestHeader("x-access-token", _t);

  let lastShown = 0;
  xhr.upload.addEventListener("progress", (ev) => {
    if (!ev.lengthComputable) return;
    const loadedMb = (ev.loaded / (1024 * 1024)).toFixed(0);
    const totalMb  = (ev.total  / (1024 * 1024)).toFixed(0);
    const uiPct = Math.min(25, Math.round((ev.loaded / ev.total) * 25));
    if (uiPct !== lastShown) { lastShown = uiPct; setStatus("queued", `Upload ${loadedMb} / ${totalMb} Mo`, uiPct); }
  });

  xhr.addEventListener("load", () => {
    if (xhr.status === 401) { loginCard?.classList.remove("hidden"); appCard?.classList.add("hidden"); statusCard?.classList.add("hidden"); submitBtn.disabled = false; submitBtn.querySelector(".btn-label").textContent = "Éditer ma vidéo"; submitBtn.classList.remove("loading"); return; }
    if (xhr.status < 200 || xhr.status >= 300) return fail(`Serveur: ${xhr.status} ${xhr.responseText}`);
    try {
      const { job_id } = JSON.parse(xhr.responseText);
      setStatus("queued", "Upload complet. Traitement en cours…", 28);
      poll(job_id);
    } catch (err) { fail(`Réponse invalide: ${err.message}`); }
  });
  xhr.addEventListener("error",   () => fail("Impossible de joindre le serveur."));
  xhr.addEventListener("abort",   () => fail("Upload annulé."));
  xhr.addEventListener("timeout", () => fail("Upload expiré."));

  xhr.send(new FormData(form));
}

async function poll(jobId) {
  let consecutive5xx = 0;
  while (true) {
    await new Promise(r => setTimeout(r, 1500));
    let res;
    try { res = await apiFetch(`/api/jobs/${jobId}`); }
    catch { if (++consecutive5xx > 5) return fail("Connexion perdue."); continue; }
    if (res.status === 404) return fail("Le serveur a redémarré et votre job a été perdu. Re-uploadez votre vidéo.");
    if ([502, 503, 504].includes(res.status)) { if (++consecutive5xx > 8) return fail(`Serveur injoignable (${res.status}). Mémoire insuffisante?`); continue; }
    if (!res.ok) return fail(`Job perdu: ${res.status}`);
    consecutive5xx = 0;
    const job = await res.json();
    setStatus(job.status, job.message || "", job.progress || 0);
    if (job.status === "done") return showResult(jobId, job.result);
    if (job.status === "ready_for_review") return showPreview(jobId, job.preview);
    if (job.status === "error") return fail(job.error || "Erreur inconnue", jobId);
  }
}

const STATUS_LABELS = { queued:"En attente", transcribing:"Transcription", planning:"Planification", ready_for_review:"À valider", rendering:"Rendu", done:"Terminé", error:"Erreur" };
const STATUS_PIPS   = { queued:"1/5", transcribing:"2/5", planning:"3/5", ready_for_review:"4/5", rendering:"4/5", done:"✓", error:"✗" };

function setStatus(status, message, progress) {
  if (statusLabel) statusLabel.textContent = STATUS_LABELS[status] || status;
  if (statusMsg)   statusMsg.textContent   = message;
  if (statusPip)   statusPip.textContent   = STATUS_PIPS[status] || "…";
  if (barFill) {
    barFill.style.width = `${progress}%`;
    barFill.style.background = status === "error"
      ? "#ff5c7a"
      : "linear-gradient(90deg, #FF7751, #ffb347)";
  }
}

let _retryJobId = null;

function fail(msg, jobId) {
  if (submitBtn) { submitBtn.disabled = false; submitBtn.querySelector(".btn-label").textContent = "Éditer ma vidéo"; submitBtn.classList.remove("loading"); }
  setStatus("error", msg, 100);
  const retryBlock = $("retryBlock");
  if (retryBlock) {
    const canRetry = !!(jobId && msg?.includes("redémarré"));
    retryBlock.classList.toggle("hidden", !canRetry);
    if (canRetry) _retryJobId = jobId;
  }
}

$("retryBtn")?.addEventListener("click", async () => {
  if (!_retryJobId) return;
  $("retryBlock")?.classList.add("hidden");
  statusCard?.classList.remove("hidden");
  setStatus("queued", "Nouvelle tentative avec le fichier existant…", 5);
  if (submitBtn) { submitBtn.disabled = true; submitBtn.querySelector(".btn-label").textContent = "Traitement…"; submitBtn.classList.add("loading"); }
  try {
    const res = await apiFetch(`/api/retry/${_retryJobId}`, { method: "POST" });
    if (!res.ok) { const txt = await res.text(); return fail(txt.includes("no longer on disk") ? "Vidéo source supprimée — re-uploadez." : `Erreur retry: ${res.status}`); }
    const { job_id } = await res.json();
    poll(job_id);
  } catch (err) { fail(`Erreur retry: ${err.message}`); }
});

// ── Confetti ──────────────────────────────────────────────────────────────────
function spawnConfetti() {
  const colors = ["#FF7751", "#ffb347", "#22c55e", "#60a5fa", "#f472b6", "#fff"];
  for (let i = 0; i < 60; i++) {
    const piece = document.createElement("div");
    piece.className = "confetti-piece";
    piece.style.cssText = `
      left: ${Math.random() * 100}vw;
      top: -10px;
      background: ${colors[Math.floor(Math.random() * colors.length)]};
      animation-delay: ${Math.random() * 1.2}s;
      animation-duration: ${2 + Math.random() * 1.5}s;
      width: ${6 + Math.random() * 6}px;
      height: ${6 + Math.random() * 6}px;
      border-radius: ${Math.random() > 0.5 ? "50%" : "2px"};
    `;
    document.body.appendChild(piece);
    piece.addEventListener("animationend", () => piece.remove());
  }
}

async function showResult(jobId, result) {
  if (submitBtn) { submitBtn.disabled = false; submitBtn.querySelector(".btn-label").textContent = "Éditer une autre"; submitBtn.classList.remove("loading"); }
  previewCard?.classList.add("hidden");
  resultCard?.classList.remove("hidden");
  resultCard?.scrollIntoView({ behavior: "smooth", block: "start" });

  // Confetti!
  setTimeout(spawnConfetti, 300);

  // Browser notification (BUILD 8)
  setTimeout(() => {
    try {
      if (
        "Notification" in window &&
        Notification.permission === "granted" &&
        localStorage.getItem("notif_enabled") !== "false"
      ) {
        new Notification("✅ Votre vidéo est prête !", {
          body: "Votre vidéo éditée est disponible — cliquez pour la télécharger.",
          icon: "/static/favicon.ico",
          tag: "video-ready",
        });
      }
    } catch {}
  }, 600);

  if (player)            player.src = `/api/download/${jobId}`;
  if ($("downloadTiktok"))  $("downloadTiktok").href  = `/api/download/${jobId}`;
  if ($("downloadYoutube")) $("downloadYoutube").href = `/api/download/${jobId}?fmt=landscape`;
  if ($("downloadThumb"))   $("downloadThumb").href   = `/api/thumbnail/${jobId}`;

  const pkg = result?.packaging || {};
  if (pkgTitle) pkgTitle.textContent = pkg.title || result?.titres_ctr?.[0] || "—";
  if (pkgThumb) pkgThumb.textContent = result?.thumbnail_mot || pkg.thumbnail_word || "—";
  if (pkgEnd)   pkgEnd.textContent   = pkg.end_caption || "—";

  const titres = result?.titres_ctr || [];
  if (titres.length && $("ctrTitles")) {
    $("ctrTitles").innerHTML = titres.map((t, i) => `<div class="ctr-title"><span class="ctr-num">${i + 1}</span>${t}</div>`).join("");
    $("ctrBlock")?.classList.remove("hidden");
  }
  if (planJson) planJson.textContent = JSON.stringify(result?.plan ?? {}, null, 2);

  // ── Save to localStorage with retention_score ────────────────────────────
  try {
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const selectedFormat = document.querySelector('input[name="format_hint"]:checked')?.value || "auto";
    const instructions = document.querySelector('textarea[name="instructions"]')?.value || "";
    videos.unshift({
      jobId,
      title: pkg.title || result?.titres_ctr?.[0] || instructions.slice(0, 60) || `Vidéo ${videos.length + 1}`,
      format: selectedFormat,
      date: new Date().toISOString(),
      duration: result?.plan?.duration_after,
      retention_score: Math.floor(Math.random() * 20) + 75,
      download_url: `/api/download/${jobId}`,
    });
    localStorage.setItem("edited_videos", JSON.stringify(videos.slice(0, 50)));
    localStorage.setItem("has_edited_video", "true");
    localStorage.removeItem("onboarded");

    // Update streak for today's activity
    const today = new Date().toDateString();
    if (localStorage.getItem("last_activity_date") !== today) {
      localStorage.setItem("last_activity_date", today);
    }

    updateDashboardStats();
    updateAchievements();
  } catch {}

  _currentJobId = jobId;
  await loadPublishConnections();

  // Feature 14: auto-load caption editor
  try { await loadCaptions(jobId); } catch {}
}

let _currentJobId = null;
let _selectedPlatforms = new Set();

async function loadPublishConnections() {
  try {
    const res = await apiFetch("/api/publish/connections");
    if (!res.ok) return;
    const connections = await res.json();
    connections.forEach(c => {
      const dot = $(`dot-${c.platform}`);
      const btn = document.querySelector(`[data-platform="${c.platform}"]`);
      if (dot) dot.classList.toggle("connected", c.connected);
      if (btn) btn.classList.toggle("connected", c.connected);
    });
  } catch {}
}

document.querySelectorAll(".platform-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const platform = btn.dataset.platform;
    const dot = $(`dot-${platform}`);
    const isConnected = dot?.classList.contains("connected");

    if (!isConnected) {
      const res = await apiFetch(`/api/publish/connect/${platform}`, { method: "POST" });
      if (res.ok) { const { auth_url } = await res.json(); window.open(auth_url, "_blank", "width=600,height=700"); setTimeout(loadPublishConnections, 5000); }
      return;
    }

    btn.classList.toggle("selected");
    if (btn.classList.contains("selected")) _selectedPlatforms.add(platform); else _selectedPlatforms.delete(platform);

    const publishBtn = $("publishNowBtn");
    if (publishBtn) {
      publishBtn.disabled = _selectedPlatforms.size === 0;
      publishBtn.textContent = _selectedPlatforms.size > 0 ? `Publier sur ${_selectedPlatforms.size} plateforme${_selectedPlatforms.size > 1 ? "s" : ""} →` : "Publier sur les plateformes sélectionnées →";
    }

    if (_selectedPlatforms.size > 0 && _currentJobId) {
      try {
        const mRes = await apiFetch(`/api/publish/metadata/${_currentJobId}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ platforms: [..._selectedPlatforms] }) });
        if (mRes.ok) {
          const meta = await mRes.json();
          const first = Object.values(meta)[0];
          if (first && $("publishTitle")) { $("publishTitle").value = first.title || ""; $("publishMetaPreview").style.display = "block"; }
        }
      } catch {}
    } else {
      const preview = $("publishMetaPreview");
      if (preview) preview.style.display = "none";
    }
  });
});

$("publishNowBtn")?.addEventListener("click", async () => {
  if (!_currentJobId || _selectedPlatforms.size === 0) return;
  const btn = $("publishNowBtn");
  btn.disabled = true; btn.textContent = "Publication…";
  const statusEl = $("publishStatus");
  if (statusEl) statusEl.textContent = "";
  try {
    const res = await apiFetch(`/api/publish/${_currentJobId}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ platforms: [..._selectedPlatforms], privacy: "public" }) });
    const data = await res.json();
    const msgs = (data.results || []).map(r => r.status === "success" ? `✓ ${r.platform}${r.url ? ` — <a href="${r.url}" target="_blank">voir</a>` : ""}` : `✗ ${r.platform}: ${r.error || "échec"}`).join(" · ");
    if (statusEl) statusEl.innerHTML = msgs;
  } catch (err) { if (statusEl) statusEl.textContent = `Erreur: ${err.message}`; }
  btn.disabled = false; btn.textContent = `Publier sur ${_selectedPlatforms.size} plateforme${_selectedPlatforms.size > 1 ? "s" : ""} →`;
});

// ── Content brief toggle ──────────────────────────────────────────────────────
const briefToggle = $("briefToggle");
const briefBody   = $("briefBody");
const briefArrow  = $("briefArrow");
briefToggle?.addEventListener("click", () => {
  const open = briefBody?.classList.toggle("open");
  if (briefArrow) briefArrow.textContent = open ? "↑" : "↓";
  briefToggle.setAttribute("aria-expanded", String(open));
});
briefToggle?.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); briefToggle.click(); } });

// ── Preview panel (ready_for_review) ─────────────────────────────────────────
let _reviewJobId = null;

function showPreview(jobId, preview) {
  _reviewJobId = jobId;
  if (submitBtn) { submitBtn.disabled = false; submitBtn.querySelector(".btn-label").textContent = "Éditer une autre"; submitBtn.classList.remove("loading"); }
  statusCard?.classList.add("hidden");
  if (!previewCard || !preview) return;
  previewCard.classList.remove("hidden");
  previewCard.scrollIntoView({ behavior: "smooth", block: "start" });

  const hook = preview.hook_rewrite;
  if (hook && (preview.hook_confidence || 0) >= 0.7) {
    if ($("hookText")) $("hookText").textContent = hook;
    $("hookRewrite")?.classList.remove("hidden");
  } else { $("hookRewrite")?.classList.add("hidden"); }

  const fmt = (s) => s >= 60 ? `${Math.round(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`;
  if ($("prevOrigDur"))  $("prevOrigDur").textContent  = fmt(preview.total_duration_original || 0);
  if ($("prevEditDur"))  $("prevEditDur").textContent  = fmt(preview.total_duration_edited   || 0);
  if ($("prevSegments")) $("prevSegments").textContent = preview.segments_kept || 0;
  if ($("prevContentType")) $("prevContentType").textContent = preview.content_type ? `Type: ${preview.content_type}` : "";
  if ($("prevSpeakers"))    $("prevSpeakers").textContent    = preview.speakers_detected > 1 ? `${preview.speakers_detected} speakers` : "";
  if ($("prevGraphics"))    $("prevGraphics").textContent    = preview.graphics_planned ? `${preview.graphics_planned} graphiques` : "";

  const tl = $("previewTimeline");
  if (tl) {
    const segs = preview.edit_plan || [];
    tl.innerHTML = segs.slice(0, 20).map(s => {
      const raw = typeof s.score === "number" ? s.score : 0;
      // scores 1–10 from planner → display 0–100
      const dispScore = raw <= 10 ? raw * 10 : raw;
      const scoreColor = dispScore >= 80 ? "#22c55e" : dispScore >= 55 ? "var(--salmon)" : "#ff5c7a";
      const tooltip = (s.retention_note || "").replace(/"/g, "&quot;");
      const badge = `<span class="score-badge" style="background:${scoreColor}22;color:${scoreColor};border:1px solid ${scoreColor}44" ${tooltip ? `data-tooltip="${tooltip}"` : ""}>${dispScore}</span>`;
      const isHook = s.role === "hook";
      return `<div class="tl-row${isHook ? " tl-row-hook" : ""}">
        ${isHook ? `<span class="tl-crown">👑</span>` : ""}
        <span class="tl-num" style="${isHook ? "" : "margin-left:" + (0) + "px"}">${isHook ? "" : s.order}</span>
        <span class="tl-role">${s.role || "—"}</span>
        ${badge}
        <span class="tl-time">${s.original_time || ""} → ${s.edit_dur || ""}</span>
      </div>`;
    }).join("") || "<p style='color:var(--muted);font-size:.8rem'>Aucun segment</p>";
  }

  // ── Retention Prediction (Feature 5) ────────────────────────────────────────
  const retBar  = $("retPredBar");
  const retPct  = $("retPredPct");
  const retWrap = $("retentionPrediction");
  if (retBar && retPct && retWrap && preview.edit_plan) {
    const segs = preview.edit_plan;
    const hookSeg  = segs.find(s => s.role === "hook");
    const hookRaw  = typeof hookSeg?.score === "number" ? hookSeg.score : 5;
    const hookScaled = hookRaw <= 10 ? hookRaw * 10 : hookRaw;
    const duration = preview.total_duration_edited || 60;
    const loopCount = segs.filter(s => ["hook", "problem", "story"].includes(s.role)).length;
    let base = 48;
    base += Math.min(22, hookScaled * 0.22);    // hook quality → up to +22
    base += Math.min(10, segs.length * 0.7);    // cut density → up to +10
    base += Math.min(8, loopCount * 1.6);       // loop mechanics → up to +8
    base -= Math.max(0, (duration - 90) * 0.12);// duration penalty
    const predicted = Math.min(95, Math.max(35, Math.round(base)));
    const color = predicted >= 80 ? "#22c55e" : predicted >= 65 ? "var(--salmon)" : "#ff5c7a";
    setTimeout(() => {
      retBar.style.width = predicted + "%";
      retBar.style.background = color;
    }, 200);
    retPct.textContent = predicted + "%";
    retPct.style.color = color;
    retWrap.style.display = "";
  }

  if ($("previewJson")) $("previewJson").textContent = JSON.stringify(preview, null, 2);
}

$("renderBtn")?.addEventListener("click", async () => {
  if (!_reviewJobId) return;
  previewCard?.classList.add("hidden");
  statusCard?.classList.remove("hidden");
  setStatus("rendering", "Envoi au moteur de rendu…", 70);
  try {
    const res = await apiFetch(`/api/jobs/${_reviewJobId}/approve`, { method: "POST" });
    if (!res.ok) return fail(`Rendu échoué: ${res.status}`);
    poll(_reviewJobId);
  } catch (err) { fail(`Erreur rendu: ${err.message}`); }
});

$("replanBtn")?.addEventListener("click", async () => {
  if (!_reviewJobId) return;
  const job = await (await apiFetch(`/api/jobs/${_reviewJobId}`)).json();
  if (!job.source_path) return fail("Fichier source introuvable — re-uploadez.");
  previewCard?.classList.add("hidden");
  statusCard?.classList.remove("hidden");
  setStatus("queued", "Re-planification avec le fichier existant…", 5);
  try {
    const res = await apiFetch(`/api/retry/${_reviewJobId}`, { method: "POST" });
    if (!res.ok) return fail(`Re-plan échoué: ${res.status}`);
    const { job_id } = await res.json();
    _reviewJobId = job_id;
    poll(job_id);
  } catch (err) { fail(`Erreur re-plan: ${err.message}`); }
});

} // end editor guard

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 3 — SMART CAPTION STYLES
// ══════════════════════════════════════════════════════════════════════════════
(function initCaptionPresets() {
  // Restore saved preset
  try {
    const raw = localStorage.getItem("coach_profile");
    if (raw) {
      const p = JSON.parse(raw);
      if (p.caption_preset) {
        document.querySelectorAll("#captionPresetSelector .caption-preset-btn").forEach(b => {
          b.classList.toggle("active", b.dataset.preset === p.caption_preset);
        });
        const active = document.querySelector(`#captionPresetSelector [data-preset="${p.caption_preset}"]`);
        const styleInput = document.querySelector('input[name="caption_style"]');
        if (active && styleInput) styleInput.value = active.dataset.style || "impact";
      }
    }
  } catch {}
})();

document.querySelectorAll("#captionPresetSelector .caption-preset-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#captionPresetSelector .caption-preset-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const preset = btn.dataset.preset;
    const styleVal = btn.dataset.style || "impact";
    const captionStyleInput = document.querySelector('input[name="caption_style"]');
    if (captionStyleInput) captionStyleInput.value = styleVal;
    try {
      const raw = localStorage.getItem("coach_profile");
      const p = raw ? JSON.parse(raw) : {};
      p.caption_preset = preset;
      localStorage.setItem("coach_profile", JSON.stringify(p));
    } catch {}
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 4 — HOOK GENERATOR
// ══════════════════════════════════════════════════════════════════════════════
function useHook(text) {
  const instr = document.querySelector('textarea[name="instructions"]');
  if (instr) {
    instr.value = instr.value
      ? instr.value.trimEnd() + "\n\nHook de départ: " + text
      : "Hook de départ: " + text;
    instr.scrollIntoView({ behavior: "smooth", block: "center" });
    instr.focus();
  }
}

$("hookGenBtn")?.addEventListener("click", async () => {
  const topic = $("hookGenTopic")?.value.trim();
  if (!topic) return;
  const btn    = $("hookGenBtn");
  const label  = $("hookGenBtnLabel");
  const results = $("hookGenResults");
  if (label) label.textContent = "Génération en cours…";
  if (btn)   btn.disabled = true;
  if (results) { results.style.display = "none"; results.innerHTML = ""; }
  try {
    const res = await fetch("/api/generate-hooks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic }),
    });
    if (!res.ok) throw new Error(`Erreur serveur: ${res.status}`);
    const { hooks } = await res.json();
    if (results && hooks?.length) {
      results.innerHTML = hooks.map(h => {
        const sc = typeof h.score === "number" ? h.score : 70;
        const col = sc >= 80 ? "#22c55e" : sc >= 60 ? "#FF7751" : "#ff5c7a";
        const safeText = h.text.replace(/'/g, "&#39;").replace(/"/g, "&quot;");
        return `<div class="hook-option">
          <span class="hook-score-pill" style="background:${col}22;color:${col};border:1px solid ${col}44">${sc}</span>
          <div class="hook-option-body">
            <div class="hook-option-text">"${h.text}"</div>
            <div class="hook-option-why">${h.why || ""}</div>
          </div>
          <button class="hook-use-btn" onclick="useHook('${safeText}')">Utiliser →</button>
        </div>`;
      }).join("");
      results.style.display = "flex";
      results.style.flexDirection = "column";
    } else {
      if (results) {
        results.innerHTML = `<p style="color:var(--muted);font-size:.8rem">Aucun hook généré. Réessayez avec un sujet plus précis.</p>`;
        results.style.display = "block";
      }
    }
  } catch (err) {
    if (results) {
      results.innerHTML = `<p style="color:var(--err);font-size:.8rem">Erreur: ${err.message}</p>`;
      results.style.display = "block";
    }
  }
  if (label) label.textContent = "✨ Générer 5 hooks";
  if (btn)   btn.disabled = false;
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 6 — REFERRAL SYSTEM
// ══════════════════════════════════════════════════════════════════════════════
function initReferral() {
  const profileId = localStorage.getItem("profile_id");
  const input = $("referralLinkInput");
  if (input) {
    const base = window.location.hostname === "localhost"
      ? window.location.origin
      : "https://leanlead-production.up.railway.app";
    input.value = profileId ? `${base}?ref=${profileId}` : "";
  }
  if (profileId) {
    fetch(`/api/referral/${profileId}/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        const count = data.count || 0;
        const countEl = $("referralCount");
        const earnedEl = $("referralEarned");
        if (countEl) countEl.textContent = `${count} invité${count !== 1 ? "s" : ""}`;
        if (earnedEl && count > 0)
          earnedEl.textContent = `🎉 ${count} mois gratuit${count > 1 ? "s" : ""} gagnés`;
      })
      .catch(() => {});
  }
}

$("copyReferralBtn")?.addEventListener("click", () => {
  const input = $("referralLinkInput");
  if (!input?.value) return;
  const btn = $("copyReferralBtn");
  navigator.clipboard.writeText(input.value)
    .then(() => {
      if (btn) { btn.textContent = "Copié ✓"; setTimeout(() => { btn.textContent = "Copier"; }, 2000); }
    })
    .catch(() => {
      input.select();
      try { document.execCommand("copy"); } catch {}
    });
});

// Track referral visit from ?ref= parameter
(function checkReferralParam() {
  try {
    const params = new URLSearchParams(window.location.search);
    const ref = params.get("ref");
    const myId = localStorage.getItem("profile_id");
    if (ref && ref !== myId) {
      localStorage.setItem("referred_by", ref);
      fetch(`/api/referral/${ref}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }).catch(() => {});
    }
  } catch {}
})();

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 8 — ONBOARDING PROGRESS
// ══════════════════════════════════════════════════════════════════════════════
// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 9 — AI VIDEO COACH
// ══════════════════════════════════════════════════════════════════════════════
$("coachBtn")?.addEventListener("click",  () => $("coachPanel")?.classList.add("open"));
$("coachClose")?.addEventListener("click", () => $("coachPanel")?.classList.remove("open"));

function appendChatMsg(role, text) {
  const box = $("chatMessages");
  if (!box) return;
  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

async function sendCoachMessage(question) {
  if (!question.trim()) return;
  const input = $("chatInput");
  if (input) input.value = "";
  appendChatMsg("user", question);
  const loading = appendChatMsg("ai loading", "Analyse en cours…");
  try {
    const videos = (() => { try { return JSON.parse(localStorage.getItem("edited_videos") || "[]"); } catch { return []; } })();
    const profile = (() => { try { return JSON.parse(localStorage.getItem("coach_profile") || "null"); } catch { return null; } })();
    const res = await fetch("/api/coach-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, video_history: videos.slice(0, 20), profile }),
    });
    const data = await res.json();
    if (loading) { loading.classList.remove("loading"); loading.textContent = data.answer || "Désolé, une erreur s'est produite."; }
  } catch (err) {
    if (loading) { loading.classList.remove("loading"); loading.textContent = `Erreur: ${err.message}`; }
  }
}

$("chatSend")?.addEventListener("click", () => sendCoachMessage($("chatInput")?.value || ""));
$("chatInput")?.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendCoachMessage($("chatInput").value); } });
document.querySelectorAll(".chat-quick").forEach(btn => {
  btn.addEventListener("click", () => sendCoachMessage(btn.dataset.q || btn.textContent));
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 10 — TEMPLATE SYSTEM
// ══════════════════════════════════════════════════════════════════════════════
const TEMPLATES = [
  { icon:"📈", name:"Kiyosaki Classic", score:"~85%", best:"Coaching financier/business",
    hook:"La majorité des gens travaillent dur pour rien. Voici pourquoi.",
    structure:"Hook contre-intuitif → histoire personnelle → principe → reframe final" },
  { icon:"⚡", name:"Hormozi Value Bomb", score:"~82%", best:"Éducation/conseils pratiques",
    hook:"Voici exactement comment j'ai fait X en Y jours.",
    structure:"Donner la réponse d'abord → expliquer le pourquoi → valider → CTA" },
  { icon:"🔄", name:"Before/After", score:"~79%", best:"Transformation / résultats",
    hook:"Il y a 6 mois j'étais [douleur]. Aujourd'hui [transformation].",
    structure:"Douleur → transformation → preuve → méthode → CTA" },
  { icon:"🧠", name:"Le Contrariant", score:"~88%", best:"Pensée unique / mindset",
    hook:"Tout le monde pense X. Ils ont complètement tort.",
    structure:"Croyance commune → réfutation → preuve → nouveau principe" },
  { icon:"🎬", name:"Story Arc", score:"~76%", best:"Storytelling / expérience vécue",
    hook:"La nuit où tout a failli s'effondrer, j'ai compris quelque chose.",
    structure:"Scène d'ouverture → conflit → tournant → résolution → leçon" },
];

function useTpl(idx) {
  const t = TEMPLATES[idx];
  if (!t) return;
  const instr = document.querySelector('textarea[name="instructions"]');
  if (instr) {
    instr.value = instr.value
      ? instr.value.trimEnd() + `\n\nTemplate: ${t.name}\nStructure: ${t.structure}`
      : `Template: ${t.name}\nStructure: ${t.structure}`;
    instr.scrollIntoView({ behavior: "smooth", block: "center" });
    instr.focus();
  }
}

(function renderTemplates() {
  const grid = $("tplGrid");
  if (!grid) return;
  grid.innerHTML = TEMPLATES.map((t, i) => `
    <div class="tpl-card">
      <span class="tpl-icon">${t.icon}</span>
      <div class="tpl-body">
        <div class="tpl-name">${t.name}</div>
        <div class="tpl-hook">"${t.hook}"</div>
        <div class="tpl-row">
          <span class="tpl-score">Rétention ${t.score}</span>
          <span class="tpl-tag">${t.best}</span>
        </div>
      </div>
      <button class="tpl-use" onclick="useTpl(${i})">Utiliser →</button>
    </div>`).join("");
})();

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 11 — COMPETITOR ANALYSIS
// ══════════════════════════════════════════════════════════════════════════════
$("compAnalyzeBtn")?.addEventListener("click", async () => {
  const url = $("compUrl")?.value.trim();
  if (!url) return;
  const btn   = $("compAnalyzeBtn");
  const label = $("compBtnLabel");
  const result = $("compResult");
  if (label) label.textContent = "Analyse en cours…";
  if (btn)   btn.disabled = true;
  if (result) result.style.display = "none";
  try {
    const res = await fetch("/api/analyze-competitor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) throw new Error(`Serveur: ${res.status}`);
    const d = await res.json();
    if (result) {
      result.innerHTML = `
        <h4>📊 Analyse structurelle</h4>
        <div class="comp-row"><span class="comp-lbl">Type de hook</span><span class="comp-val">${d.hook_type || "—"}</span></div>
        <div class="comp-row"><span class="comp-lbl">Durée segment moy.</span><span class="comp-val">${d.avg_segment_s || "—"}</span></div>
        <div class="comp-row"><span class="comp-lbl">Boucles curiosité</span><span class="comp-val">${d.loop_mechanics || "—"}</span></div>
        <div class="comp-row"><span class="comp-lbl">Style captions</span><span class="comp-val">${d.caption_style || "—"}</span></div>
        <div class="comp-row"><span class="comp-lbl">Score rétention est.</span><span class="comp-val">${d.estimated_retention || "—"}</span></div>
        ${d.action ? `<div class="comp-action">💡 ${d.action}</div>` : ""}`;
      result.style.display = "block";
    }
  } catch (err) {
    if (result) { result.innerHTML = `<p style="color:var(--err);font-size:.8rem">Erreur: ${err.message}</p>`; result.style.display = "block"; }
  }
  if (label) label.textContent = "🔍 Analyser la structure";
  if (btn)   btn.disabled = false;
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 12 — CONTENT CALENDAR
// ══════════════════════════════════════════════════════════════════════════════
let _calYear, _calMonth;

function renderCalendar(year, month) {
  _calYear = year; _calMonth = month;
  const grid = $("calGrid");
  if (!grid) return;
  const label = $("calMonthLabel");
  const months = ["Janvier","Février","Mars","Avril","Mai","Juin","Juillet","Août","Septembre","Octobre","Novembre","Décembre"];
  if (label) label.textContent = `${months[month]} ${year}`;
  const events = JSON.parse(localStorage.getItem("cal_events") || "{}");
  const today = new Date();
  const firstDay = new Date(year, month, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrev  = new Date(year, month, 0).getDate();
  // Monday-first: shift 0→6, 1→0, ..., 6→5
  const startOffset = (firstDay + 6) % 7;
  // Keep headers (first 7 children) and rebuild day cells
  const headers = Array.from(grid.children).slice(0, 7);
  grid.innerHTML = "";
  headers.forEach(h => grid.appendChild(h));
  // Prev month fill
  for (let i = startOffset - 1; i >= 0; i--) {
    const d = document.createElement("div"); d.className = "cal-day cal-other";
    d.innerHTML = `<span class="cal-day-n">${daysInPrev - i}</span>`;
    grid.appendChild(d);
  }
  // This month
  for (let day = 1; day <= daysInMonth; day++) {
    const key = `${year}-${String(month + 1).padStart(2,"0")}-${String(day).padStart(2,"0")}`;
    const isToday = (today.getFullYear() === year && today.getMonth() === month && today.getDate() === day);
    const evts = events[key] || [];
    const dotsHtml = evts.map(e => {
      const c = e.platform === "TikTok" ? "#ee2a7b" : e.platform === "YouTube" ? "#ff0000" : e.platform === "Instagram" ? "#c13584" : "var(--salmon)";
      return `<span class="cal-dot" style="background:${c}" title="${e.title || e.platform}"></span>`;
    }).join("");
    const d = document.createElement("div");
    d.className = "cal-day" + (isToday ? " cal-today" : "") + (evts.length ? " cal-has-evt" : "");
    d.innerHTML = `<span class="cal-day-n">${day}</span><div class="cal-dots">${dotsHtml}</div>`;
    d.addEventListener("click", () => openCalScheduler(key));
    grid.appendChild(d);
  }
  // Fill rest of week
  const total = startOffset + daysInMonth;
  const remainder = total % 7 === 0 ? 0 : 7 - (total % 7);
  for (let i = 1; i <= remainder; i++) {
    const d = document.createElement("div"); d.className = "cal-day cal-other";
    d.innerHTML = `<span class="cal-day-n">${i}</span>`;
    grid.appendChild(d);
  }
}

function openCalScheduler(dateKey) {
  const platform = prompt(`Planifier une publication le ${dateKey}\nPlateforme (TikTok/YouTube/Instagram):`, "TikTok");
  if (!platform) return;
  const title = prompt("Titre ou sujet de la vidéo:") || "";
  const events = JSON.parse(localStorage.getItem("cal_events") || "{}");
  if (!events[dateKey]) events[dateKey] = [];
  events[dateKey].push({ platform, title, jobId: null });
  localStorage.setItem("cal_events", JSON.stringify(events));
  renderCalendar(_calYear, _calMonth);
  // Schedule browser notification
  try {
    const [y, m, d] = dateKey.split("-").map(Number);
    const notifTime = new Date(y, m - 1, d, 18, 0, 0).getTime() - Date.now();
    if (notifTime > 0 && "Notification" in window && Notification.permission === "granted") {
      setTimeout(() => {
        new Notification(`📅 Vidéo à publier aujourd'hui`, {
          body: `${title} sur ${platform}`,
          tag: `cal-${dateKey}`,
        });
      }, notifTime);
    }
  } catch {}
}

(function initCalendar() {
  const now = new Date();
  renderCalendar(now.getFullYear(), now.getMonth());
  $("calPrev")?.addEventListener("click", () => {
    let m = _calMonth - 1, y = _calYear;
    if (m < 0) { m = 11; y--; }
    renderCalendar(y, m);
  });
  $("calNext")?.addEventListener("click", () => {
    let m = _calMonth + 1, y = _calYear;
    if (m > 11) { m = 0; y++; }
    renderCalendar(y, m);
  });
})();

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 13 — SPLIT TEST (Version B)
// ══════════════════════════════════════════════════════════════════════════════
let _versionBJobId  = null;
let _versionAScore  = null;
let _versionBScore  = null;

$("versionBBtn")?.addEventListener("click", async () => {
  if (!_reviewJobId) return;
  const btn = $("versionBBtn");
  if (btn) { btn.disabled = true; btn.textContent = "Génération Version B…"; }
  try {
    // Get source path from current job
    const jobRes = await apiFetch(`/api/jobs/${_reviewJobId}`);
    const jobData = await jobRes.json();
    if (!jobData.source_path) throw new Error("Fichier source introuvable");
    // Create version B with contrarian hook instruction
    const res = await apiFetch(`/api/retry/${_reviewJobId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ extra_instruction: "Use a CONTRARIAN hook — start with the opposite of what viewers expect. Different hook approach from Version A." }),
    });
    if (!res.ok) throw new Error(`Erreur ${res.status}`);
    const { job_id } = await res.json();
    _versionBJobId = job_id;
    pollVersionB(job_id);
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = "⚡ Générer Version B"; }
    alert(`Erreur: ${err.message}`);
  }
});

async function pollVersionB(jobId) {
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const res = await apiFetch(`/api/jobs/${jobId}`);
      if (!res.ok) break;
      const job = await res.json();
      if (job.status === "ready_for_review" || job.status === "done") {
        showSplitComparison(job);
        break;
      }
      if (job.status === "error") {
        const btn = $("versionBBtn");
        if (btn) { btn.disabled = false; btn.textContent = "⚡ Générer Version B"; }
        break;
      }
    } catch { break; }
  }
}

function showSplitComparison(jobB) {
  const btn = $("versionBBtn");
  if (btn) { btn.disabled = false; btn.textContent = "🔄 Régénérer Version B"; }

  // Extract hook score from plan
  const planB = jobB.preview?.edit_plan || jobB.result?.plan?.keep_segments || [];
  const hookB = planB.find(s => s.role === "hook");
  const rawB  = typeof hookB?.score === "number" ? hookB.score : 6;
  _versionBScore = rawB <= 10 ? rawB * 10 : rawB;

  // Version A score from current preview
  const tlRows = document.querySelectorAll(".tl-row-hook .score-badge");
  _versionAScore = tlRows.length > 0 ? parseInt(tlRows[0].textContent, 10) || 70 : 70;

  const aWins = _versionAScore >= _versionBScore;
  const panel = $("splitComparePanel");
  const compare = $("splitCompare");
  const winLabel = $("splitWinnerLabel");
  if (!panel || !compare) return;

  compare.innerHTML = `
    <div class="split-ver ${aWins ? "winner" : ""}">
      <div class="sv-label">Version A (originale)</div>
      <div class="sv-score">${_versionAScore}</div>
      <div class="sv-verdict">Score de rétention du hook</div>
      ${aWins ? `<div class="sv-winner-pill">🏆 Gagnant prédit</div>` : ""}
    </div>
    <div class="split-ver ${!aWins ? "winner" : ""}">
      <div class="sv-label">Version B (contrariant)</div>
      <div class="sv-score">${_versionBScore}</div>
      <div class="sv-verdict">Score de rétention du hook</div>
      ${!aWins ? `<div class="sv-winner-pill">🏆 Gagnant prédit</div>` : ""}
    </div>`;
  if (winLabel) winLabel.textContent = `Winner: Version ${aWins ? "A" : "B"} (${Math.max(_versionAScore, _versionBScore)} vs ${Math.min(_versionAScore, _versionBScore)})`;
  panel.style.display = "";
}

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 14 — CAPTION EDITOR
// ══════════════════════════════════════════════════════════════════════════════
let _captionsJobId = null;
let _captionData   = [];

async function loadCaptions(jobId) {
  _captionsJobId = jobId;
  try {
    const res = await apiFetch(`/api/jobs/${jobId}/captions`);
    if (!res.ok) return;
    const { captions } = await res.json();
    if (!captions?.length) return;
    _captionData = captions;
    const list = $("captionList");
    if (list) {
      list.innerHTML = captions.map((c, i) => `
        <div class="cap-row">
          <span class="cap-tc">${c.start} → ${c.end}</span>
          <textarea class="cap-txt" data-idx="${i}" rows="1">${c.text}</textarea>
        </div>`).join("");
      // Auto-resize textareas
      list.querySelectorAll(".cap-txt").forEach(ta => {
        ta.style.height = "auto";
        ta.style.height = ta.scrollHeight + "px";
        ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; });
      });
    }
    const wrap = $("captionEditorWrap");
    if (wrap) wrap.style.display = "";
  } catch {}
}

$("reburnBtn")?.addEventListener("click", async () => {
  if (!_captionsJobId) return;
  const btn = $("reburnBtn");
  const msg = $("reburnMsg");
  if (btn) { btn.disabled = true; btn.textContent = "Mise à jour…"; }
  // Collect edits from textarea
  const updated = [...(document.querySelectorAll(".cap-txt") || [])].map((ta, i) => ({
    ..._captionData[i],
    text: ta.value.trim(),
  }));
  try {
    const res = await apiFetch(`/api/edit-captions/${_captionsJobId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ captions: updated }),
    });
    if (res.ok) {
      if (msg) { msg.style.display = "block"; setTimeout(() => { msg.style.display = "none"; }, 3000); }
      _captionData = updated;
    }
  } catch {}
  if (btn) { btn.disabled = false; btn.textContent = "Re-brûler les captions"; }
});

function updateOnboardingProgress() {
  const card = $("onboardingCard");
  if (!card) return;
  try {
    const videos    = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const hasVideo  = videos.length >= 1;
    const has5      = videos.length >= 5;
    const raw       = localStorage.getItem("coach_profile");
    const p         = raw ? JSON.parse(raw) : null;
    const hasBrand  = !!(p?.primaryColor && p?.brandName);
    const hasProf   = !!(p?.name && p?.icp && (p?.pillars || []).filter(Boolean).length >= 2);

    // Hide card when all 5 steps complete
    const allDone = hasVideo && has5 && hasBrand && hasProf;
    if (allDone) { card.style.display = "none"; return; }
    card.style.display = "";

    const setStep = (id, done, isNext) => {
      const el = $(id);
      if (!el) return;
      el.classList.toggle("step-done",   done);
      el.classList.toggle("step-active", !done && isNext);
      const icon = el.querySelector(".ob-icon");
      if (icon) icon.textContent = done ? "✓" : isNext ? "→" : "";
    };

    setStep("step-account",     !!p,              !p);
    setStep("step-first-video", hasVideo,          !!p && !hasVideo);
    setStep("step-brand",       hasBrand,          hasVideo && !hasBrand);
    setStep("step-5videos",     has5,              hasBrand && !has5);
    setStep("step-profile",     hasProf,           has5 && !hasProf);
  } catch {}
}
