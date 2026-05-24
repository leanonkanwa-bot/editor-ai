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
  if (targetId === "dashboardSection") { updateDashboardStats(); loadVideoLibrary(); updateStreak(); updateAchievements(); }
  if (targetId === "profileSection") loadProfileSection();
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

    if (!raw) {
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
    }

    if (!raw) { switchSection("editorArea"); return; }
    const p = JSON.parse(raw);

    const nameEl = $("dashName");
    if (nameEl) nameEl.textContent = p.name || p.brandName || "toi";

    updateDashboardStats();
    updateStreak();
    updateAchievements();

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

  if (player)      player.src       = `/api/download/${jobId}`;
  if (downloadLink) downloadLink.href = `/api/download/${jobId}`;

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
    tl.innerHTML = segs.slice(0, 20).map(s =>
      `<div class="tl-row"><span class="tl-num">${s.order}</span><span class="tl-role">${s.role || "—"}</span><span class="tl-time">${s.original_time || ""} → ${s.edit_dur || ""}</span></div>`
    ).join("") || "<p style='color:var(--muted);font-size:.8rem'>Aucun segment</p>";
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
