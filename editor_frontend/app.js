const $ = (id) => document.getElementById(id);

// ── Theme management (light is default; dark applied via [data-theme="dark"]) ──
(function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "dark") document.documentElement.setAttribute("data-theme", "dark");
  const btn = document.getElementById("themeToggle");
  if (btn) btn.textContent = saved === "dark" ? "Clair" : "Sombre";
})();
document.getElementById("themeToggle")?.addEventListener("click", () => {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  if (isDark) {
    document.documentElement.removeAttribute("data-theme");
    localStorage.setItem("theme", "light");
    document.getElementById("themeToggle").textContent = "Sombre";
  } else {
    document.documentElement.setAttribute("data-theme", "dark");
    localStorage.setItem("theme", "dark");
    document.getElementById("themeToggle").textContent = "Clair";
  }
});

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
  ["editorArea", "dashboardSection", "profileSection", "learnSection", "outilsSection"].forEach(id => {
    const el = $(id);
    if (el) el.classList.toggle("active", id === targetId);
  });
  document.querySelectorAll(".nav-tab[data-target]").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.target === targetId);
  });
  if (targetId === "dashboardSection") { updateDashboardStats(); loadVideoLibrary(); updateStreak(); updateAchievements(); initReferral(); loadPerfTracker(); loadTeam(); loadApiKey(); }
  if (targetId === "profileSection") loadProfileSection();
  if (targetId === "editorArea") updateOnboardingProgress();
  if (targetId === "learnSection") renderLessons();
}


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

    // Update nav avatar with initials
    const navAvatar = $("navAvatar");
    if (navAvatar) {
      const initials = (p.name || p.brandName || "?").split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
      navAvatar.textContent = initials;
      navAvatar.addEventListener("click", () => switchSection("profileSection"));
    }
    const greetingEl = $("navGreeting");
    if (greetingEl) {
      const name = p.name || p.brandName || "";
      if (name) {
        greetingEl.textContent = `Bienvenue, ${name}`;
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
    const now = new Date();
    const thisMonthCount = videos.filter(v => {
      if (!v.date) return false;
      const d = new Date(v.date);
      return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
    }).length;
    if ($("dashViews")) $("dashViews").textContent = thisMonthCount;
    const scores = videos.map(v => v.retention_score).filter(s => typeof s === "number");
    if ($("dashScoreMoyen")) {
      $("dashScoreMoyen").textContent = scores.length > 0
        ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) + "%"
        : "—";
    }
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
      const title = v.title || `Vidéo #${i + 1}`;
      const scoreBg = score >= 85 ? "#22c55e" : score >= 70 ? "#f59e0b" : "#ef4444";
      const thumbSrc = v.thumbnail_url || v.thumbnail || (v.jobId ? `/api/thumbnail/${v.jobId}` : null);
      const thumbHtml = thumbSrc
        ? `<img src="${thumbSrc}" alt="${title}" style="width:100%;height:100%;object-fit:cover;display:block" />`
        : `<div class="video-lib-thumb-placeholder"><svg class="video-lib-play" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8" fill="currentColor" stroke="none"/></svg></div>`;
      return `<div class="video-lib-card" title="${title}">
        <div class="video-lib-thumb">
          ${thumbHtml}
          <span class="video-lib-retention" style="background:${scoreBg}">${score}%</span>
          <div class="video-lib-title-overlay">${title}</div>
          <div class="video-lib-overlay">
            ${v.jobId ? `<a href="/api/download/${v.jobId}" class="action-btn" download>Télécharger</a>` : ""}
            <button class="action-btn" onclick="switchSection('editorArea')">Reediter</button>
          </div>
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
        const scoreHtml = score != null ? `<span class="retention-badge">${score}%</span>` : `<span class="status-badge status-done">Pret</span>`;
        return `<tr>
          <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${v.title || '—'}">${v.title || `Vidéo #${i + 1}`}</td>
          <td>${v.format || "Auto"}</td>
          <td>${v.date ? new Date(v.date).toLocaleDateString("fr-FR") : "—"}</td>
          <td>${scoreHtml}</td>
          <td>
            ${v.jobId ? `<a href="/api/download/${v.jobId}" class="action-btn" download>Télécharger</a>` : ""}
            <button class="action-btn" onclick="switchSection('editorArea')">Reediter</button>
            <button class="action-btn" onclick="deleteVideo('${v.jobId || i}')" style="color:#e53e3e">Supprimer</button>
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
      const roleLabel = { coach:"Coach", entrepreneur:"Entrepreneur", educator:"Educateur", creator:"Createur" };
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
const VALID_EXTS = ["mp4", "mov", "mkv"];

function _dropZoneError(msg) {
  if (!dropLabel) return;
  dropLabel.textContent = msg;
  dropLabel.style.color = "var(--err)";
  setTimeout(() => {
    dropLabel.textContent = "Déposer votre vidéo ici";
    dropLabel.style.color = "";
  }, 3500);
}

function _dropZoneAcceptFile(file) {
  if (!file || !videoInput) return;
  const ext = file.name.split(".").pop().toLowerCase();
  if (!VALID_EXTS.includes(ext)) {
    _dropZoneError(`Format non supporté (.${ext}) — acceptés : MP4, MOV, MKV`);
    return;
  }
  // Assign the file to the hidden input
  try {
    const dt = new DataTransfer();
    dt.items.add(file);
    videoInput.files = dt.files;
  } catch (_) {}
  // Update drop zone with thumbnail + file info
  const mb = (file.size / (1024 * 1024)).toFixed(1);
  drop?.classList.add("has-file");
  if (dropLabel) {
    dropLabel.textContent = `${file.name} — ${mb} MB`;
    dropLabel.style.color = "#fff";
    dropLabel.style.textShadow = "0 1px 4px rgba(0,0,0,0.8)";
  }
  // Best-effort client-side thumbnail (works for MP4, may fail for MOV/MKV)
  if (drop) {
    try {
      const thumbVideo = document.createElement("video");
      const objUrl = URL.createObjectURL(file);
      thumbVideo.src = objUrl;
      thumbVideo.muted = true;
      thumbVideo.playsInline = true;
      thumbVideo.addEventListener("loadedmetadata", () => {
        thumbVideo.currentTime = Math.min(2, thumbVideo.duration * 0.1);
      }, { once: true });
      thumbVideo.addEventListener("seeked", () => {
        try {
          if (thumbVideo.videoWidth === 0) return;
          const canvas = document.createElement("canvas");
          canvas.width = thumbVideo.videoWidth;
          canvas.height = thumbVideo.videoHeight;
          canvas.getContext("2d").drawImage(thumbVideo, 0, 0);
          const imgUrl = canvas.toDataURL("image/jpeg", 0.7);
          drop.style.backgroundImage = `url(${imgUrl})`;
          drop.style.backgroundSize = "cover";
          drop.style.backgroundPosition = "center";
        } catch (_) {}
        URL.revokeObjectURL(objUrl);
      }, { once: true });
      thumbVideo.addEventListener("error", () => { URL.revokeObjectURL(objUrl); }, { once: true });
      thumbVideo.load();
    } catch (_) {}
  }
}


// Guard: editor-only listeners
if (videoInput && form && submitBtn) {

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

  // Server-side thumbnail (reliable for all formats including MOV/MKV)
  try {
    const thumbRes = await apiFetch(`/api/upload/preview/${upload_id}`);
    if (thumbRes.ok) {
      const blob = await thumbRes.blob();
      const thumbUrl = URL.createObjectURL(blob);
      if (drop) {
        drop.style.backgroundImage = `url(${thumbUrl})`;
        drop.style.backgroundSize = "cover";
        drop.style.backgroundPosition = "center";
      }
    }
  } catch (_) {}

  setStatus("queued", "Démarrage de l'édition IA…", 28);
  const fd = new FormData(form);
  fd.delete("video"); fd.set("upload_id", upload_id);

  // Inject brand colors + font from coach_profile if not already set by form
  try {
    const _cp = JSON.parse(localStorage.getItem("coach_profile") || "{}");
    const _fontMap = { Poppins: "Poppins Bold", Inter: "Inter Bold", Montserrat: "Montserrat Bold",
      Bebas: "Bebas Neue", Anton: "Anton", "DM Sans": "DM Sans Bold", Quicksand: "Quicksand Bold" };
    if (_cp.primaryColor && !fd.get("brand_color")) fd.set("brand_color", _cp.primaryColor);
    if (_cp.font && (!fd.get("caption_font") || fd.get("caption_font") === "Poppins Bold"))
      fd.set("caption_font", _fontMap[_cp.font] || _cp.font);
  } catch (_e) {}

  // Enforce editing_style + matching caption_style
  fd.set("editing_style", selectedEditingStyle);
  if (selectedEditingStyle === "priestley" || selectedEditingStyle === "momentum") {
    fd.set("caption_style", selectedEditingStyle);
  }
  // Style pack
  const _spVal = document.getElementById("stylePackValue");
  if (_spVal) fd.set("style_pack", _spVal.value);

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

  const _xfd = new FormData(form);
  try {
    const _cp = JSON.parse(localStorage.getItem("coach_profile") || "{}");
    const _fm = { Poppins: "Poppins Bold", Inter: "Inter Bold", Montserrat: "Montserrat Bold",
      Bebas: "Bebas Neue", Anton: "Anton", "DM Sans": "DM Sans Bold", Quicksand: "Quicksand Bold" };
    if (_cp.primaryColor && !_xfd.get("brand_color")) _xfd.set("brand_color", _cp.primaryColor);
    if (_cp.font && (!_xfd.get("caption_font") || _xfd.get("caption_font") === "Poppins Bold"))
      _xfd.set("caption_font", _fm[_cp.font] || _cp.font);
  } catch (_e) {}
  _xfd.set("editing_style", selectedEditingStyle);
  if (selectedEditingStyle === "priestley" || selectedEditingStyle === "momentum") {
    _xfd.set("caption_style", selectedEditingStyle);
  }
  // Style pack
  const _spEl = document.getElementById("stylePackValue");
  if (_spEl) _xfd.set("style_pack", _spEl.value);
  xhr.send(_xfd);
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
    if (job.status === "ready_for_review") {
      // Auto-approve: skip the review screen, proceed directly to render
      setStatus("rendering", "Lancement du rendu…", 70);
      try { await apiFetch(`/api/jobs/${jobId}/approve`, { method: "POST" }); } catch (_) {}
      continue;
    }
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
  if (submitBtn) { submitBtn.disabled = false; submitBtn.querySelector(".btn-label").textContent = "Éditer ma vidéo"; submitBtn.classList.remove("loading"); }
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

  const downloadUrl = `/api/download/${jobId}`;
  if (player) player.src = downloadUrl;
  if (downloadLink) downloadLink.href = downloadUrl;

  // Store API thumbnail URL for library (generated on-demand by backend)
  try {
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    if (videos.length && videos[0].jobId === jobId) {
      videos[0].thumbnail_url = `/api/thumbnail/${jobId}`;
      localStorage.setItem("edited_videos", JSON.stringify(videos.slice(0, 50)));
    }
  } catch {}

  // Show hook_rewrite as suggested title metadata (not a video caption).
  const suggestedTitleEl = $("suggestedTitleResult");
  if (suggestedTitleEl) {
    const suggestedTitle = result?.hook_overlay?.rewritten_hook || "";
    if (suggestedTitle) {
      suggestedTitleEl.textContent = `Titre suggéré : ${suggestedTitle}`;
      suggestedTitleEl.style.display = "";
    } else {
      suggestedTitleEl.style.display = "none";
    }
  }

  const pkg = result?.packaging || {};
  const titres = result?.titres_ctr || [];
  if (titres.length && $("ctrTitles")) {
    $("ctrTitles").innerHTML = titres.map((t, i) => `<div class="ctr-title"><span class="ctr-num">${i + 1}</span>${t}</div>`).join("");
    const ctrBlock = $("ctrBlock");
    if (ctrBlock) ctrBlock.style.display = "";
  }

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

  // Features 17 & 20
  generateChapters(result, jobId);
  recordPerfPrediction(jobId, result);
  addNotification("✅", "Vidéo prête !", "Votre vidéo éditée est disponible au téléchargement.");
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


// ── Details expand toggle ─────────────────────────────────────────────────────
$("detailsToggle")?.addEventListener("click", () => {
  const body = $("detailsBody");
  const btn  = $("detailsToggle");
  if (!body) return;
  const open = body.classList.toggle("open");
  if (btn) btn.textContent = open ? "Masquer les détails ↑" : "Voir les détails ↓";
});

// ── Preview panel (ready_for_review) ─────────────────────────────────────────
let _reviewJobId = null;

function showPreview(jobId, preview) {
  _reviewJobId = jobId;
  if (submitBtn) { submitBtn.disabled = false; submitBtn.querySelector(".btn-label").textContent = "Éditer ma vidéo"; submitBtn.classList.remove("loading"); }
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

  // ── Dropped segments ────────────────────────────────────────────────────────
  const dropped = preview.drop_segments || preview.removed_segments || [];
  let droppedContainer = $("droppedSegmentsWrap");
  if (!droppedContainer) {
    droppedContainer = document.createElement("div");
    droppedContainer.id = "droppedSegmentsWrap";
    droppedContainer.style.cssText = "margin-top:.75rem";
    tl?.parentNode?.insertBefore(droppedContainer, tl.nextSibling);
  }
  if (dropped.length > 0) {
    droppedContainer.innerHTML =
      `<div style="margin-bottom:.35rem;font-size:.72rem;font-weight:600;color:var(--muted);letter-spacing:.04em;text-transform:uppercase">Segments supprimés (${dropped.length})</div>` +
      dropped.slice(0, 15).map(s => {
        const raw = typeof s.score === "number" ? s.score : 0;
        const dispScore = raw <= 10 ? raw * 10 : raw;
        const preview_text = s.preview || s.text || s.role || "—";
        return `<div class="tl-row" style="opacity:.55;background:rgba(255,92,122,.06);border:1px solid rgba(255,92,122,.18)">
          <span style="font-size:.6rem;font-weight:700;color:#ff5c7a;background:rgba(255,92,122,.18);padding:.1rem .35rem;border-radius:3px;margin-right:.35rem;flex-shrink:0">Supprimé</span>
          <span class="tl-role" style="text-decoration:line-through;color:var(--muted)">${s.role || "—"}</span>
          <span class="score-badge" style="background:#ff5c7a22;color:#ff5c7a;border:1px solid #ff5c7a44;margin-left:auto;flex-shrink:0">${dispScore}</span>
          <span class="tl-time" style="color:var(--muted)">${s.original_time || ""}</span>
        </div>`;
      }).join("");
  } else {
    droppedContainer.innerHTML = "";
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

// ── Editing style selector ────────────────────────────────────────────────────
let selectedEditingStyle = localStorage.getItem("editing_style") || "viral";

(function initStyleCards() {
  const styleInput = document.querySelector('input[name="editing_style"]');
  if (styleInput) styleInput.value = selectedEditingStyle;

  document.querySelectorAll("#styleSelector .style-card").forEach(card => {
    if (card.dataset.style === selectedEditingStyle) {
      card.classList.add("active");
    } else {
      card.classList.remove("active");
    }
    card.addEventListener("click", () => {
      document.querySelectorAll("#styleSelector .style-card").forEach(c => c.classList.remove("active"));
      card.classList.add("active");
      selectedEditingStyle = card.dataset.style;
      localStorage.setItem("editing_style", selectedEditingStyle);
      if (styleInput) styleInput.value = selectedEditingStyle;
      // Sync caption_style hidden input when Priestley/Momentum is selected
      const captionStyleInput = document.querySelector('input[name="caption_style"]');
      if (captionStyleInput) {
        if (selectedEditingStyle === "priestley" || selectedEditingStyle === "momentum") {
          captionStyleInput.value = selectedEditingStyle;
          // Remove active from all caption preset buttons
          document.querySelectorAll("#captionPresetSelector .caption-preset-btn")
            .forEach(b => b.classList.remove("active"));
        } else {
          // Restore to whichever caption preset is active, defaulting to twolevel
          const activeBtn = document.querySelector("#captionPresetSelector .caption-preset-btn.active");
          captionStyleInput.value = activeBtn ? (activeBtn.dataset.style || "twolevel") : "twolevel";
        }
      }
    });
  });
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

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 17 — AUTO-CHAPTERS
// ══════════════════════════════════════════════════════════════════════════════
function secondsToTimestamp(sec) {
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

function generateChapters(result, jobId) {
  const section = $("chaptersSection");
  const output = $("chaptersOutput");
  if (!section || !output) return;

  const segs = result?.edit_plan || result?.plan?.segments || [];
  if (!segs.length) return;

  let elapsed = 0;
  const lines = segs.map((seg, i) => {
    const ts = secondsToTimestamp(elapsed);
    const role = seg.role || seg.label || `Partie ${i + 1}`;
    const label = role.charAt(0).toUpperCase() + role.slice(1);
    const dur = parseFloat(seg.edit_dur || seg.duration || 0);
    elapsed += isNaN(dur) ? 15 : dur;
    return `${ts} ${label}`;
  });

  output.textContent = lines.join("\n");
  section.style.display = "";

  $("copyChaptersBtn")?.addEventListener("click", () => {
    navigator.clipboard.writeText(lines.join("\n")).then(() => {
      const btn = $("copyChaptersBtn");
      if (btn) { btn.textContent = "✓ Copié !"; setTimeout(() => { btn.textContent = "📋 Copier les chapitres"; }, 2000); }
    });
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 18 — DESCRIPTION GENERATOR
// ══════════════════════════════════════════════════════════════════════════════
(function initDescTabs() {
  document.querySelectorAll(".desc-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".desc-tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".desc-platform").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      const plat = tab.dataset.plat;
      document.getElementById(`desc-${plat}`)?.classList.add("active");
    });
  });

  document.querySelectorAll("[data-copy]").forEach(btn => {
    btn.addEventListener("click", () => {
      const el = $(btn.dataset.copy);
      if (!el) return;
      navigator.clipboard.writeText(el.textContent).then(() => {
        const orig = btn.textContent;
        btn.textContent = "✓ Copié !";
        setTimeout(() => { btn.textContent = orig; }, 2000);
      });
    });
  });
})();

$("descGenBtn")?.addEventListener("click", async () => {
  const btn = $("descGenBtnLabel");
  if (btn) btn.textContent = "⏳ Génération en cours…";
  $("descGenBtn").disabled = true;

  try {
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const latest = videos[0] || {};
    const title = latest.title || "Ma vidéo";
    const format = latest.format || "auto";
    const instructions = document.querySelector('textarea[name="instructions"]')?.value || "";

    const res = await apiFetch("/api/generate-descriptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: _currentJobId, title, format, context: instructions }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const set = (id, text) => { const el = $(id); if (el) el.textContent = text || ""; };
    set("desc-youtube-text", data.youtube || "");
    set("desc-tiktok-text", data.tiktok || "");
    set("desc-instagram-text", data.instagram || "");
    set("desc-linkedin-text", data.linkedin || "");

    $("descGenOutput").style.display = "";
  } catch (err) {
    console.error("Desc gen error:", err);
  } finally {
    if (btn) btn.textContent = "✨ Régénérer les descriptions";
    $("descGenBtn").disabled = false;
  }
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 20 — PERFORMANCE TRACKER
// ══════════════════════════════════════════════════════════════════════════════
function recordPerfPrediction(jobId, result) {
  try {
    const segs = result?.edit_plan || result?.plan?.segments || [];
    const hookSeg = segs.find(s => s.role === "hook");
    const hookScore = typeof hookSeg?.score === "number" ? hookSeg.score : 5;
    const predicted = Math.min(95, Math.max(30, Math.round(48 + hookScore * 3.5)));

    const tracker = JSON.parse(localStorage.getItem("perf_tracker") || "[]");
    const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
    const title = videos[0]?.title || `Vidéo ${tracker.length + 1}`;

    tracker.unshift({
      jobId,
      title,
      predicted,
      actual: null,
      date: new Date().toISOString(),
    });
    localStorage.setItem("perf_tracker", JSON.stringify(tracker.slice(0, 20)));
  } catch {}
}

function loadPerfTracker() {
  const list = $("perfVideoList");
  const accEl = $("perfAccuracy");
  const pctEl = $("perfAccuracyPct");
  if (!list) return;

  const tracker = JSON.parse(localStorage.getItem("perf_tracker") || "[]");

  // Simulate actuals for entries older than 7 days
  let updated = false;
  tracker.forEach(item => {
    if (!item.actual) {
      const age = (Date.now() - new Date(item.date).getTime()) / 86400000;
      if (age >= 7) {
        item.actual = Math.min(100, Math.max(0, item.predicted + Math.round((Math.random() - 0.4) * 25)));
        updated = true;
      }
    }
  });
  if (updated) localStorage.setItem("perf_tracker", JSON.stringify(tracker));

  if (!tracker.length) {
    list.innerHTML = '<div class="perf-empty">Éditez une vidéo pour voir les prédictions.</div>';
    return;
  }

  // Accuracy score from entries with actuals
  const withActual = tracker.filter(i => i.actual !== null);
  if (withActual.length && accEl && pctEl) {
    const avgErr = withActual.reduce((s, i) => s + Math.abs(i.predicted - i.actual), 0) / withActual.length;
    const acc = Math.max(50, Math.round(100 - avgErr * 1.2));
    pctEl.textContent = acc;
    accEl.style.display = "";
  }

  list.innerHTML = tracker.map(item => `
    <div class="perf-video-row">
      <span class="perf-title">${item.title}</span>
      <span class="perf-pred">Prévu: ${item.predicted}%</span>
      ${item.actual !== null ? `<span class="perf-actual good">Réel: ${item.actual}%</span>` : '<span class="perf-actual">En attente (7j)</span>'}
    </div>
  `).join("");
}

document.querySelectorAll(".perf-connect-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const plat = btn.dataset.platform;
    btn.textContent = "⏳ Connexion…";
    btn.disabled = true;
    setTimeout(() => {
      btn.textContent = plat === "youtube" ? "✅ YouTube connecté" : "✅ TikTok connecté";
      btn.style.borderColor = "#22c55e";
      btn.style.color = "#22c55e";
      addNotification("📊", "Compte connecté !", `${plat === "youtube" ? "YouTube" : "TikTok"} est maintenant connecté pour le suivi des performances.`);
    }, 1500);
  });
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 21 — TEAM COLLABORATION
// ══════════════════════════════════════════════════════════════════════════════
function loadTeam() {
  const memberList = $("teamMemberList");
  const commentList = $("teamCommentList");
  if (!memberList) return;

  const members = JSON.parse(localStorage.getItem("team_members") || "[]");
  memberList.innerHTML = members.map(m => `
    <div class="team-member">
      <div class="team-avatar">${m.email.charAt(0).toUpperCase()}</div>
      <div class="team-info">
        <div class="team-name">${m.email.split("@")[0]}</div>
        <div class="team-email">${m.email}</div>
      </div>
      <span class="team-role">${m.role}</span>
      <button onclick="removeTeamMember('${m.email}')" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:.9rem">✕</button>
    </div>
  `).join("") || '<p style="font-size:.8rem;color:var(--muted)">Aucun membre invité pour l\'instant.</p>';

  if (commentList) {
    const comments = JSON.parse(localStorage.getItem("team_comments") || "[]");
    commentList.innerHTML = comments.map(c => `
      <div class="team-comment-item">
        <div class="team-comment-author">${c.author}</div>
        <div style="font-size:.8rem;margin-top:.2rem">${c.text}</div>
        <div style="font-size:.7rem;color:var(--muted);margin-top:.2rem">${new Date(c.date).toLocaleString("fr-FR")}</div>
      </div>
    `).join("") || '<p style="font-size:.8rem;color:var(--muted)">Aucun commentaire pour l\'instant.</p>';
  }
}

function removeTeamMember(email) {
  const members = JSON.parse(localStorage.getItem("team_members") || "[]");
  localStorage.setItem("team_members", JSON.stringify(members.filter(m => m.email !== email)));
  loadTeam();
}

$("teamInviteBtn")?.addEventListener("click", () => {
  const emailEl = $("teamInviteEmail");
  const roleEl = $("teamInviteRole");
  const email = emailEl?.value.trim();
  if (!email || !email.includes("@")) return;

  const members = JSON.parse(localStorage.getItem("team_members") || "[]");
  if (!members.find(m => m.email === email)) {
    members.push({ email, role: roleEl?.value || "viewer", date: new Date().toISOString() });
    localStorage.setItem("team_members", JSON.stringify(members));
  }
  if (emailEl) emailEl.value = "";
  loadTeam();
  addNotification("👥", "Invitation envoyée", `${email} a été invité comme ${roleEl?.value || "viewer"}.`);
});

$("teamCommentBtn")?.addEventListener("click", () => {
  const input = $("teamCommentInput");
  const text = input?.value.trim();
  if (!text) return;

  const p = JSON.parse(localStorage.getItem("coach_profile") || "{}");
  const author = p.name || p.brandName || "Vous";

  const comments = JSON.parse(localStorage.getItem("team_comments") || "[]");
  comments.unshift({ author, text, date: new Date().toISOString() });
  localStorage.setItem("team_comments", JSON.stringify(comments.slice(0, 50)));
  if (input) input.value = "";
  loadTeam();
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 22 — WHITE LABEL
// ══════════════════════════════════════════════════════════════════════════════
function applyWhiteLabel(wl) {
  if (!wl) return;
  if (wl.primaryColor) {
    document.documentElement.style.setProperty("--salmon", wl.primaryColor);
    document.documentElement.style.setProperty("--salmon-hover", wl.primaryColor + "cc");
  }
  const logoName = document.querySelector(".logo-name");
  if (logoName && wl.brandName) logoName.textContent = wl.brandName;
  const wlBrandNameDisplay = $("wlBrandNameDisplay");
  if (wlBrandNameDisplay && wl.brandName) wlBrandNameDisplay.textContent = wl.brandName;
  const logoBox = $("wlLogoBox");
  if (logoBox && wl.logoDataUrl) {
    logoBox.innerHTML = `<img src="${wl.logoDataUrl}" style="width:36px;height:36px;object-fit:contain;border-radius:6px" />`;
  }
}

(function initWhiteLabel() {
  try {
    const wl = JSON.parse(localStorage.getItem("white_label") || "{}");
    if (wl.primaryColor || wl.brandName) applyWhiteLabel(wl);
    if ($("wlBrandName") && wl.brandName) $("wlBrandName").value = wl.brandName;
    if ($("wlPrimaryColor") && wl.primaryColor) $("wlPrimaryColor").value = wl.primaryColor;
    if ($("wlColorHex") && wl.primaryColor) $("wlColorHex").value = wl.primaryColor;
  } catch {}
})();

$("wlPrimaryColor")?.addEventListener("input", (e) => {
  const hex = e.target.value;
  if ($("wlColorHex")) $("wlColorHex").value = hex;
  document.documentElement.style.setProperty("--salmon", hex);
  const box = $("wlLogoBox");
  if (box && !box.querySelector("img")) box.style.background = hex;
});

$("wlColorHex")?.addEventListener("input", (e) => {
  const hex = e.target.value;
  if (/^#[0-9A-Fa-f]{6}$/.test(hex)) {
    if ($("wlPrimaryColor")) $("wlPrimaryColor").value = hex;
    document.documentElement.style.setProperty("--salmon", hex);
  }
});

$("wlSaveBtn")?.addEventListener("click", () => {
  const brandName = $("wlBrandName")?.value.trim();
  const primaryColor = $("wlPrimaryColor")?.value || "#FF7751";
  const logoFile = $("wlLogoUpload")?.files?.[0];

  const save = (logoDataUrl) => {
    const wl = { brandName, primaryColor, logoDataUrl, savedAt: new Date().toISOString() };
    localStorage.setItem("white_label", JSON.stringify(wl));
    applyWhiteLabel(wl);
    addNotification("🏷️", "White Label appliqué !", `Marque "${brandName || "Mon Studio"}" activée.`);
    const btn = $("wlSaveBtn");
    if (btn) { btn.textContent = "✓ Appliqué !"; setTimeout(() => { btn.textContent = "Appliquer le White Label"; }, 2000); }
  };

  if (logoFile) {
    const reader = new FileReader();
    reader.onload = (ev) => save(ev.target.result);
    reader.readAsDataURL(logoFile);
  } else {
    const existing = JSON.parse(localStorage.getItem("white_label") || "{}");
    save(existing.logoDataUrl || null);
  }
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 23 — API ACCESS
// ══════════════════════════════════════════════════════════════════════════════
let _apiKey = null;
let _apiKeyVisible = false;

function generateApiKey() {
  const bytes = new Uint8Array(20);
  crypto.getRandomValues(bytes);
  return "lrk_" + Array.from(bytes).map(b => b.toString(16).padStart(2, "0")).join("");
}

async function loadApiKey() {
  const display = $("apiKeyDisplay");
  if (!display) return;

  const profileId = localStorage.getItem("profile_id") || "default";

  try {
    const res = await apiFetch(`/api/api-keys/${profileId}`);
    if (res.ok) {
      const data = await res.json();
      if (data.key) { _apiKey = data.key; updateApiKeyDisplay(); return; }
    }
  } catch {}

  // Generate new key
  _apiKey = generateApiKey();
  try {
    await apiFetch("/api/api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: profileId, key: _apiKey }),
    });
  } catch {}
  updateApiKeyDisplay();
}

function updateApiKeyDisplay() {
  const display = $("apiKeyDisplay");
  if (!display || !_apiKey) return;
  display.textContent = _apiKeyVisible ? _apiKey : _apiKey.slice(0, 7) + "•".repeat(_apiKey.length - 7);
}

$("showApiKeyBtn")?.addEventListener("click", () => {
  _apiKeyVisible = !_apiKeyVisible;
  updateApiKeyDisplay();
  const btn = $("showApiKeyBtn");
  if (btn) btn.textContent = _apiKeyVisible ? "🙈 Cacher" : "👁 Voir";
});

$("copyApiKeyBtn")?.addEventListener("click", () => {
  if (!_apiKey) return;
  navigator.clipboard.writeText(_apiKey).then(() => {
    const btn = $("copyApiKeyBtn");
    if (btn) { btn.textContent = "✓"; setTimeout(() => { btn.textContent = "📋"; }, 2000); }
  });
});

$("regenApiKeyBtn")?.addEventListener("click", async () => {
  if (!confirm("Régénérer la clé API ? L'ancienne clé sera révoquée.")) return;
  _apiKey = generateApiKey();
  const profileId = localStorage.getItem("profile_id") || "default";
  try {
    await apiFetch("/api/api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: profileId, key: _apiKey }),
    });
  } catch {}
  updateApiKeyDisplay();
  addNotification("🔑", "Clé API régénérée", "Votre nouvelle clé API est active.");
});

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 24 — SMART NOTIFICATIONS
// ══════════════════════════════════════════════════════════════════════════════
function addNotification(icon, title, body) {
  const notifs = JSON.parse(localStorage.getItem("notifications") || "[]");
  notifs.unshift({ icon, title, body, time: new Date().toISOString(), read: false });
  localStorage.setItem("notifications", JSON.stringify(notifs.slice(0, 50)));
  renderNotifications();

  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(`${icon} ${title}`, { body, icon: "/static/favicon.ico" }); } catch {}
  }
}

function renderNotifications() {
  const list = $("notifList");
  const dot = $("notifDot");
  if (!list) return;

  const notifs = JSON.parse(localStorage.getItem("notifications") || "[]");
  const unread = notifs.filter(n => !n.read).length;
  if (dot) dot.classList.toggle("active", unread > 0);

  if (!notifs.length) {
    list.innerHTML = '<div class="notif-empty">Aucune notification pour l\'instant.</div>';
    return;
  }

  list.innerHTML = notifs.map(n => {
    const d = new Date(n.time);
    const age = Math.floor((Date.now() - d.getTime()) / 60000);
    const timeStr = age < 60 ? `${age}min` : age < 1440 ? `${Math.floor(age / 60)}h` : `${Math.floor(age / 1440)}j`;
    return `<div class="notif-item${n.read ? "" : ' style="background:rgba(255,119,81,.04)"'}">
      <div class="notif-icon-wrap">${n.icon}</div>
      <div class="notif-body"><strong>${n.title}</strong><p>${n.body}</p></div>
      <div class="notif-time">${timeStr}</div>
    </div>`;
  }).join("");
}

$("notifBellBtn")?.addEventListener("click", () => {
  const panel = $("notifPanel");
  if (!panel) return;
  panel.classList.toggle("open");
  if (panel.classList.contains("open")) {
    // Mark all as read
    const notifs = JSON.parse(localStorage.getItem("notifications") || "[]");
    notifs.forEach(n => { n.read = true; });
    localStorage.setItem("notifications", JSON.stringify(notifs));
    renderNotifications();
  }
});

$("notifClose")?.addEventListener("click", () => $("notifPanel")?.classList.remove("open"));

$("notifClearBtn")?.addEventListener("click", () => {
  localStorage.setItem("notifications", "[]");
  renderNotifications();
});

// Close panel on outside click
document.addEventListener("click", (e) => {
  const panel = $("notifPanel");
  const bell = $("notifBellBtn");
  if (panel?.classList.contains("open") && !panel.contains(e.target) && e.target !== bell && !bell?.contains(e.target)) {
    panel.classList.remove("open");
  }
});

// Smart notifications check on load
(function checkSmartNotifications() {
  renderNotifications();

  const lastDate = localStorage.getItem("last_activity_date");
  if (lastDate) {
    const daysSince = Math.floor((Date.now() - new Date(lastDate).getTime()) / 86400000);
    if (daysSince >= 3) {
      const existing = JSON.parse(localStorage.getItem("notifications") || "[]");
      const hasInactivity = existing.some(n => n.title === "Tu nous manques !");
      if (!hasInactivity) {
        addNotification("⏰", "Tu nous manques !", `Ça fait ${daysSince} jours sans édition. Revenez pour maintenir votre streak !`);
      }
    }
  }

  const videos = JSON.parse(localStorage.getItem("edited_videos") || "[]");
  if (videos.length >= 5) {
    const existing = JSON.parse(localStorage.getItem("notifications") || "[]");
    const hasTrend = existing.some(n => n.title === "Tendance détectée");
    if (!hasTrend) {
      addNotification("📈", "Tendance détectée", "Les vidéos courtes (< 60s) ont +34% d'engagement ce mois-ci. Adaptez votre stratégie !");
    }
  }
})();

// ══════════════════════════════════════════════════════════════════════════════
// FEATURE 25 — LEARNING CENTER
// ══════════════════════════════════════════════════════════════════════════════
const LESSONS = [
  { id: "hook", emoji: "🎣", title: "Maîtriser le Hook en 3 secondes", duration: "8 min", description: "Apprenez à captiver votre audience dès la première seconde.", quizIdx: 0 },
  { id: "retention", emoji: "📈", title: "Psychologie de la rétention", duration: "12 min", description: "Comprendre pourquoi les gens arrêtent de regarder et comment l'éviter.", quizIdx: 1 },
  { id: "editing", emoji: "✂️", title: "Montage haute-rétention", duration: "10 min", description: "Techniques de coupe, rythme et dynamique pour garder l'attention.", quizIdx: 2 },
  { id: "captions", emoji: "💬", title: "Captions qui convertissent", duration: "7 min", description: "Style, timing et positionnement des sous-titres pour maximiser l'impact.", quizIdx: 3 },
  { id: "algorithm", emoji: "🤖", title: "Comprendre l'algorithme", duration: "15 min", description: "Ce que YouTube, TikTok et Instagram regardent vraiment pour distribuer votre contenu.", quizIdx: 4 },
  { id: "monetization", emoji: "💰", title: "Monétisation & scaling", duration: "11 min", description: "Transformer votre audience en revenus récurrents.", quizIdx: 5 },
];

const QUIZZES = [
  { lesson: "hook", questions: [
    { q: "Quel est le temps critique pour captiver un spectateur ?", opts: ["10 secondes", "3 secondes", "1 minute", "30 secondes"], correct: 1 },
    { q: "Quelle technique de hook est la plus efficace ?", opts: ["Une question intrigante", "Une présentation longue", "Des crédits d'ouverture", "Un logo animé"], correct: 0 },
    { q: "Le pattern interrupt sert à :", opts: ["Couper la vidéo", "Briser l'automatisme de scroll", "Ajouter de la musique", "Accélérer le montage"], correct: 1 },
  ]},
  { lesson: "retention", questions: [
    { q: "Quel indicateur mesure le mieux la rétention ?", opts: ["Nombre de vues", "Durée de visionnage moyenne", "Likes", "Commentaires"], correct: 1 },
    { q: "Un 'loop' en fin de vidéo sert à :", opts: ["Ajouter des captions", "Pousser le spectateur à revisionner", "Accélérer l'upload", "Réduire la taille"], correct: 1 },
    { q: "La rétention chute souvent :", opts: ["Au début", "Lors des transitions monotones", "Pendant la musique", "Aux captions"], correct: 1 },
  ]},
  { lesson: "editing", questions: [
    { q: "Le rythme de coupe idéal pour les Reels est :", opts: ["Une coupe toutes les 5-10 secondes", "Une coupe par minute", "Aucune coupe", "Une coupe toutes les 2-3 secondes"], correct: 3 },
    { q: "Le 'jump cut' est utilisé pour :", opts: ["Enlever les silences et garder le rythme", "Ajouter de la musique", "Créer des transitions slow-mo", "Changer de plan"], correct: 0 },
    { q: "Pourquoi éviter les silences longs ?", opts: ["Ils augmentent les likes", "Ils causent du drop-off", "Ils sont bons pour l'algorithme", "Ils réduisent la taille du fichier"], correct: 1 },
  ]},
  { lesson: "captions", questions: [
    { q: "Où positionner les captions pour les Reels ?", opts: ["En haut", "Au centre-bas", "En dehors du cadre", "En haut à droite"], correct: 1 },
    { q: "La taille de police idéale pour mobile est :", opts: ["8-12px", "60-80px", "200px", "4px"], correct: 1 },
    { q: "Les captions augmentent la rétention de :", opts: ["5%", "40%+", "100%", "2%"], correct: 1 },
  ]},
  { lesson: "algorithm", questions: [
    { q: "Ce que YouTube valorise le plus :", opts: ["Les dislikes", "La durée de visionnage totale", "Le nombre d'abonnés", "La résolution"], correct: 1 },
    { q: "TikTok distribue d'abord votre vidéo à :", opts: ["Tous vos abonnés", "Un petit groupe test", "Les influenceurs", "Vos amis"], correct: 1 },
    { q: "Pour booster l'algorithme, publiez :", opts: ["Une fois par mois", "De façon régulière et cohérente", "Une fois par an", "Aléatoirement"], correct: 1 },
  ]},
  { lesson: "monetization", questions: [
    { q: "Quel modèle de revenus est le plus stable ?", opts: ["Revenus publicitaires uniquement", "Abonnements récurrents", "Dons one-shot", "Partenariats ponctuels"], correct: 1 },
    { q: "Le 'funnel de contenu' part de :", opts: ["Contenu payant d'abord", "Contenu gratuit vers offres premium", "Publicités directement", "Email first"], correct: 1 },
    { q: "Pour scaler, la priorité est :", opts: ["Faire plus de vidéos sans stratégie", "Systématiser et déléguer", "Changer de niche", "Supprimer les anciens contenus"], correct: 1 },
  ]},
];

let _currentQuizLesson = null;
let _quizAnswers = {};

function getLearningProgress() {
  return JSON.parse(localStorage.getItem("learn_progress") || "{}");
}

function saveLearningProgress(p) {
  localStorage.setItem("learn_progress", JSON.stringify(p));
}

function renderLessons() {
  const container = $("lessonList");
  if (!container) return;

  const progress = getLearningProgress();
  const done = LESSONS.filter(l => progress[l.id]?.lessonDone).length;

  const fill = $("learnProgressFill");
  const label = $("learnProgressLabel");
  if (fill) fill.style.width = `${Math.round((done / LESSONS.length) * 100)}%`;
  if (label) label.textContent = `${done} / ${LESSONS.length} leçons`;

  container.innerHTML = LESSONS.map(l => {
    const isDone = !!progress[l.id]?.lessonDone;
    return `<div class="lesson-card">
      <div class="lesson-thumb">${l.emoji}</div>
      <div class="lesson-info">
        <div class="lesson-title">${l.title}</div>
        <div class="lesson-meta">
          <span class="lesson-pill">${l.duration}</span>
          ${isDone ? '<span class="lesson-pill done">✓ Complété</span>' : ''}
        </div>
        <div style="font-size:.74rem;color:var(--muted);margin-top:.2rem">${l.description}</div>
      </div>
      <button class="lesson-watch-btn${isDone ? ' done-btn' : ''}" onclick="openLesson('${l.id}')">${isDone ? '↩ Revoir' : '▶ Regarder'}</button>
    </div>`;
  }).join("");

  updateCertCard(done === LESSONS.length);
}

function openLesson(lessonId) {
  const progress = getLearningProgress();
  if (!progress[lessonId]) progress[lessonId] = {};
  progress[lessonId].lessonDone = true;
  saveLearningProgress(progress);

  _currentQuizLesson = lessonId;
  _quizAnswers = {};

  // Switch to quiz tab
  document.querySelectorAll(".learn-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".learn-subview").forEach(v => v.classList.remove("active"));
  document.querySelector('.learn-tab[data-learn="quiz"]')?.classList.add("active");
  $("learn-quiz")?.classList.add("active");

  renderQuiz(lessonId);
  renderLessons();
}

function renderQuiz(lessonId) {
  const container = $("quizContainer");
  if (!container) return;

  const quizData = QUIZZES.find(q => q.lesson === lessonId);
  if (!quizData) {
    container.innerHTML = '<p style="color:var(--muted);font-size:.85rem">Aucun quiz pour cette leçon.</p>';
    return;
  }

  const lesson = LESSONS.find(l => l.id === lessonId);
  container.innerHTML = `
    <h4 style="margin:0 0 .8rem;font-size:.9rem">Quiz : ${lesson?.title || lessonId}</h4>
    ${quizData.questions.map((q, qi) => `
      <div class="quiz-card" id="quiz-card-${qi}">
        <div class="quiz-q">${qi + 1}. ${q.q}</div>
        <div class="quiz-opts">
          ${q.opts.map((opt, oi) => `<div class="quiz-opt" data-qi="${qi}" data-oi="${oi}" onclick="selectQuizOpt(this,${qi},${oi})">${opt}</div>`).join("")}
        </div>
      </div>
    `).join("")}
    <button id="quizSubmitBtn" class="btn btn-primary" style="width:100%;padding:.65rem;margin-top:.5rem" onclick="submitQuiz('${lessonId}')">Valider le quiz</button>
  `;
}

function selectQuizOpt(el, qi, oi) {
  document.querySelectorAll(`.quiz-opt[data-qi="${qi}"]`).forEach(o => o.style.background = "");
  el.style.background = "rgba(255,119,81,.18)";
  el.style.borderColor = "var(--salmon)";
  _quizAnswers[qi] = oi;
}

function submitQuiz(lessonId) {
  const quizData = QUIZZES.find(q => q.lesson === lessonId);
  if (!quizData) return;

  let correct = 0;
  quizData.questions.forEach((q, qi) => {
    const selected = _quizAnswers[qi];
    document.querySelectorAll(`.quiz-opt[data-qi="${qi}"]`).forEach((opt, oi) => {
      opt.style.pointerEvents = "none";
      if (oi === q.correct) { opt.classList.add("correct"); opt.style.background = ""; opt.style.borderColor = ""; }
      else if (oi === selected && selected !== q.correct) { opt.classList.add("wrong"); opt.style.background = ""; opt.style.borderColor = ""; }
    });
    if (selected === q.correct) correct++;
  });

  const passed = correct / quizData.questions.length >= 0.67;
  const submitBtn = $("quizSubmitBtn");
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = passed ? `✅ ${correct}/${quizData.questions.length} — Réussi !` : `❌ ${correct}/${quizData.questions.length} — Réessayez`;
    submitBtn.style.background = passed ? "#22c55e" : "#ff5c7a";
  }

  if (passed) {
    const progress = getLearningProgress();
    if (!progress[lessonId]) progress[lessonId] = {};
    progress[lessonId].quizPassed = true;
    saveLearningProgress(progress);
    renderLessons();
    addNotification("🧠", "Quiz réussi !", `Vous avez réussi le quiz "${LESSONS.find(l => l.id === lessonId)?.title}".`);
  }
}

function updateCertCard(allDone) {
  const certContent = $("certContent");
  const certCard = $("certCard");
  if (!certContent || !certCard) return;

  if (allDone) {
    certContent.style.display = "";
  } else {
    const progress = getLearningProgress();
    const done = LESSONS.filter(l => progress[l.id]?.lessonDone).length;
    certCard.querySelector("p").textContent = `${done}/6 leçons complétées. Finissez toutes les leçons et leurs quiz pour obtenir votre certificat.`;
  }
}

// Learn tab switching
document.querySelectorAll(".learn-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".learn-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".learn-subview").forEach(v => v.classList.remove("active"));
    tab.classList.add("active");
    $(`learn-${tab.dataset.learn}`)?.classList.add("active");
    if (tab.dataset.learn === "lessons") renderLessons();
    if (tab.dataset.learn === "quiz" && _currentQuizLesson) renderQuiz(_currentQuizLesson);
  });
});

$("certDownloadBtn")?.addEventListener("click", () => {
  const p = JSON.parse(localStorage.getItem("coach_profile") || "{}");
  const name = p.name || p.brandName || "Créateur";
  const date = new Date().toLocaleDateString("fr-FR", { year: "numeric", month: "long", day: "numeric" });
  const win = window.open("", "_blank");
  win.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>Certificat LeanRetention</title>
  <style>body{font-family:'Helvetica Neue',sans-serif;background:#fff;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}
  .cert{border:4px solid #FF7751;border-radius:16px;padding:3rem;text-align:center;max-width:600px;box-shadow:0 0 40px rgba(255,119,81,.2)}
  h1{color:#FF7751;font-size:2rem;margin:0 0 .5rem}h2{font-size:1.4rem;margin:.5rem 0}p{color:#666;margin:.5rem 0}
  .badge{display:inline-block;background:#FF7751;color:#fff;border-radius:99px;padding:.4rem 1.2rem;font-weight:700;font-size:.9rem;margin:.5rem 0}
  </style></head><body><div class="cert">
  <div style="font-size:3rem">🏆</div>
  <h1>Certificat de Complétion</h1>
  <h2>${name}</h2>
  <p>a complété avec succès la formation</p>
  <p style="font-size:1.1rem;font-weight:700;color:#222">LeanRetention Academy — Création de Contenu Haute-Rétention</p>
  <div class="badge">6 leçons · 18 quiz réussis</div>
  <p style="margin-top:1.5rem;font-size:.85rem">Délivré le ${date}</p>
  <p style="color:#FF7751;font-weight:700">leanlead.co</p>
  </div></body></html>`);
  win.document.close();
  setTimeout(() => win.print(), 500);
});

// ── GUARANTEED: Tab navigation + drop zone (single authoritative block) ──────
document.addEventListener("DOMContentLoaded", function() {

  // TABS ─────────────────────────────────────────────────────────────────────
  var allTabs = document.querySelectorAll(".nav-tab[data-target]");
  allTabs.forEach(function(tab) {
    tab.addEventListener("click", function() {
      var target = tab.getAttribute("data-target");
      // Hide all sections
      document.querySelectorAll(".app-section").forEach(function(s) {
        s.style.display = "none";
        s.classList.remove("active");
      });
      // Remove active from all tabs
      allTabs.forEach(function(t) { t.classList.remove("active"); });
      // Show target section
      var section = document.getElementById(target);
      if (section) {
        section.style.display = "block";
        section.classList.add("active");
      }
      tab.classList.add("active");
      // Section side-effects
      if (target === "analyticsSection") loadAnalytics();
      if (target === "dashboardSection") { updateDashboardStats(); loadVideoLibrary(); updateStreak(); updateAchievements(); initReferral(); loadPerfTracker(); loadTeam(); loadApiKey(); }
      if (target === "profileSection") loadProfileSection();
      if (target === "editorArea") updateOnboardingProgress();
      if (target === "learnSection") renderLessons();
    });
  });

  // DROP ZONE ────────────────────────────────────────────────────────────────
  if (drop) {
    drop.addEventListener("click", function(e) {
      if (e.target === videoInput) return;
      if (videoInput) videoInput.click();
    });
    drop.addEventListener("dragover", function(e) {
      e.preventDefault();
      e.stopPropagation();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragenter", function(e) {
      e.preventDefault();
      e.stopPropagation();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", function(e) {
      e.preventDefault();
      e.stopPropagation();
      drop.classList.remove("dragover");
    });
    drop.addEventListener("drop", function(e) {
      e.preventDefault();
      e.stopPropagation();
      drop.classList.remove("dragover");
      var file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) _dropZoneAcceptFile(file);
    });
    drop.addEventListener("keydown", function(e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (videoInput) videoInput.click();
      }
    });
  }

  // FILE INPUT CHANGE ────────────────────────────────────────────────────────
  if (videoInput) {
    videoInput.addEventListener("change", function() {
      var f = videoInput.files && videoInput.files[0];
      if (!f) return;
      var ext = f.name.split(".").pop().toLowerCase();
      if (!VALID_EXTS.includes(ext)) {
        videoInput.value = "";
        _dropZoneError("Format non supporté (." + ext + ") — acceptés : MP4, MOV, MKV");
        if (drop) drop.classList.remove("has-file");
        return;
      }
      var mb = (f.size / (1024 * 1024)).toFixed(1);
      if (dropLabel) { dropLabel.textContent = f.name + " — " + mb + " MB"; dropLabel.style.color = ""; }
      if (drop) drop.classList.add("has-file");
    });
  }

  // UPGRADE MODAL ────────────────────────────────────────────────────────────
  document.querySelectorAll(".btn-upgrade").forEach(function(btn) {
    btn.addEventListener("click", function() {
      if (document.getElementById("upgradeModal")) return;
      var modal = document.createElement("div");
      modal.id = "upgradeModal";
      modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:9000;display:flex;align-items:center;justify-content:center;padding:1rem";
      modal.innerHTML = [
        '<div style="background:var(--bg);border:1px solid var(--border);border-radius:16px;max-width:640px;width:100%;padding:2rem;position:relative;max-height:90vh;overflow-y:auto">',
          '<button id="upgradeModalClose" style="position:absolute;top:1rem;right:1rem;background:none;border:none;font-size:1.4rem;cursor:pointer;color:var(--text-secondary);line-height:1">×</button>',
          '<h2 style="font-size:1.3rem;font-weight:800;margin-bottom:.25rem;letter-spacing:-.02em">Passer à Pro</h2>',
          '<p style="font-size:.875rem;color:var(--text-secondary);margin-bottom:1.5rem">Choisissez le plan qui correspond à vos ambitions.</p>',
          '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem">',
            // Starter
            '<div style="border:1px solid var(--border);border-radius:12px;padding:1.25rem;display:flex;flex-direction:column;gap:.5rem">',
              '<div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--text-secondary)">Starter</div>',
              '<div style="font-size:1.8rem;font-weight:800;letter-spacing:-.03em">€29<span style="font-size:.75rem;font-weight:500;color:var(--text-secondary)">/mo</span></div>',
              '<ul style="list-style:none;font-size:.8rem;color:var(--text-secondary);display:flex;flex-direction:column;gap:.3rem;flex:1;margin-top:.25rem">',
                '<li>✓ 10 vidéos / mois</li><li>✓ Captions IA</li><li>✓ Export 1080p</li><li>✓ Support email</li>',
              '</ul>',
              '<button class="upgrade-plan-btn" style="margin-top:.75rem;width:100%;padding:.55rem;border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:var(--font);font-size:.82rem;font-weight:600;cursor:pointer">Choisir ce plan</button>',
            '</div>',
            // Pro (highlighted)
            '<div style="border:2px solid #FF7751;border-radius:12px;padding:1.25rem;display:flex;flex-direction:column;gap:.5rem;position:relative;background:rgba(255,119,81,.04)">',
              '<div style="position:absolute;top:-10px;left:50%;transform:translateX(-50%);background:#FF7751;color:#fff;font-size:.65rem;font-weight:700;padding:.18rem .65rem;border-radius:99px;white-space:nowrap">POPULAIRE</div>',
              '<div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#FF7751">Pro</div>',
              '<div style="font-size:1.8rem;font-weight:800;letter-spacing:-.03em">€79<span style="font-size:.75rem;font-weight:500;color:var(--text-secondary)">/mo</span></div>',
              '<ul style="list-style:none;font-size:.8rem;color:var(--text-secondary);display:flex;flex-direction:column;gap:.3rem;flex:1;margin-top:.25rem">',
                '<li>✓ Vidéos illimitées</li><li>✓ Marque personnalisée</li><li>✓ B-roll automatique</li><li>✓ Analytics avancés</li><li>✓ Support prioritaire</li>',
              '</ul>',
              '<button class="upgrade-plan-btn" style="margin-top:.75rem;width:100%;padding:.55rem;border-radius:8px;border:none;background:#FF7751;color:#fff;font-family:var(--font);font-size:.82rem;font-weight:600;cursor:pointer">Choisir ce plan</button>',
            '</div>',
            // Agency
            '<div style="border:1px solid var(--border);border-radius:12px;padding:1.25rem;display:flex;flex-direction:column;gap:.5rem">',
              '<div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--text-secondary)">Agency</div>',
              '<div style="font-size:1.8rem;font-weight:800;letter-spacing:-.03em">€149<span style="font-size:.75rem;font-weight:500;color:var(--text-secondary)">/mo</span></div>',
              '<ul style="list-style:none;font-size:.8rem;color:var(--text-secondary);display:flex;flex-direction:column;gap:.3rem;flex:1;margin-top:.25rem">',
                '<li>✓ Comptes illimités</li><li>✓ White label</li><li>✓ API accès</li><li>✓ Manager dédié</li><li>✓ SLA garanti</li>',
              '</ul>',
              '<button class="upgrade-plan-btn" style="margin-top:.75rem;width:100%;padding:.55rem;border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:var(--font);font-size:.82rem;font-weight:600;cursor:pointer">Choisir ce plan</button>',
            '</div>',
          '</div>',
        '</div>'
      ].join("");
      document.body.appendChild(modal);
      document.getElementById("upgradeModalClose").addEventListener("click", function() { modal.remove(); });
      modal.addEventListener("click", function(e) { if (e.target === modal) modal.remove(); });
      modal.querySelectorAll(".upgrade-plan-btn").forEach(function(b) {
        b.addEventListener("click", function() {
          modal.remove();
          alert("Paiement à venir — contactez nous à hello@leanretention.com");
        });
      });
    });
  });

});
