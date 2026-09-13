(() => {
  const state = {
    matches: [],
    selectedId: null,
    minute: 28,
    meta: null,
    live: {
      board: null,
      filter: "all",
      expanded: new Set(),
      timer: null,
      loading: false,
      error: null,
    },
  };

  const LIVE_REFRESH_MS = 20000;
  const VALID_TABS = new Set(["matches", "live", "similar"]);

  const $ = (sel) => document.querySelector(sel);

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

  function currentTab() {
    return document.querySelector(".tab.active")?.dataset.tab || "matches";
  }

  function switchTab(name) {
    if (!VALID_TABS.has(name)) name = "matches";
    document.querySelectorAll(".tab").forEach((tab) => {
      const on = tab.dataset.tab === name;
      tab.classList.toggle("active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      const on = panel.id === `panel-${name}`;
      panel.classList.toggle("active", on);
      panel.hidden = !on;
    });
    if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
    if (name === "live") {
      loadLive();
      scheduleLive();
    } else {
      stopLive();
    }
  }

  // ---------------------------------------------------------------- Matches

  function renderMatches() {
    const q = ($("#matchFilter").value || "").trim().toLowerCase();
    const list = $("#matchList");
    list.innerHTML = "";
    const rows = state.matches.filter((m) => {
      if (!q) return true;
      return `${m.home} ${m.away} ${m.date} ${m.game_type}`.toLowerCase().includes(q);
    });
    for (const m of rows) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "match-row" + (m.is_preset ? " preset-row" : "") + (m.match_id === state.selectedId ? " selected" : "");
      const week = m.is_preset ? "demo" : m.game_type === "REG" ? `W${m.week}` : m.game_type;
      btn.innerHTML = `
        <span class="date">${esc(m.date)}</span>
        <span class="teams">${esc(m.home)} vs ${esc(m.away)}</span>
        <span class="meta">${esc(week)} · FT ${esc(m.ft)} · ${esc(m.box)}</span>
      `;
      btn.addEventListener("click", () => selectMatch(m.match_id, true));
      list.appendChild(btn);
    }
  }

  function renderFreeze({ title, label, meta }) {
    $("#freezeTitle").textContent = title;
    $("#freezeLabel").textContent = label;
    $("#freezeMeta").textContent = meta;
  }

  async function selectMatch(matchId, goSimilar) {
    state.selectedId = matchId;
    state.minute = Number($("#cutMinute").value) || 28;
    renderMatches();
    const snapRes = await fetch(
      `/api/snapshot?match_id=${encodeURIComponent(matchId)}&minute=${state.minute}`
    );
    const snap = await snapRes.json();
    const simRes = await fetch(
      `/api/similar?match_id=${encodeURIComponent(matchId)}&minute=${state.minute}`
    );
    const sim = await simRes.json();
    renderFreeze({
      title: `${snap.home} vs ${snap.away}`,
      label: `${state.minute}' (${snap.clock}) · ${snap.label}`,
      meta: `Frozen snapshot · score ${snap.snapshot.home_score}-${snap.snapshot.away_score}`,
    });
    renderSimilar(sim.hits || []);
    if (goSimilar) switchTab("similar");
  }

  function renderSimilar(hits) {
    const list = $("#similarList");
    list.innerHTML = "";
    if (!hits.length) {
      list.innerHTML = `<p class="lede">No lookalikes yet — warm a season or pick another cut.</p>`;
      return;
    }
    for (const h of hits) {
      const row = document.createElement("div");
      row.className = "similar-row";
      row.innerHTML = `
        <span class="score">${h.score.toFixed(3)}</span>
        <span class="date">${esc(h.date)}</span>
        <span class="teams">${esc(h.home)} vs ${esc(h.away)}</span>
        <span class="meta">${esc(h.label)} · FT ${esc(h.ft)}</span>
      `;
      list.appendChild(row);
    }
  }

  async function loadChiefsPreset() {
    const id = state.meta?.chiefs_preset_id;
    if (!id) {
      alert("Chiefs preset not in cache — run with --warm NFL:2025");
      return;
    }
    $("#cutMinute").value = "28";
    await selectMatch(id, true);
  }

  // ------------------------------------------------------------------- Live

  function stopLive() {
    if (state.live.timer) {
      clearInterval(state.live.timer);
      state.live.timer = null;
    }
  }

  function scheduleLive() {
    stopLive();
    state.live.timer = setInterval(() => {
      if (document.visibilityState === "visible" && currentTab() === "live") loadLive();
    }, LIVE_REFRESH_MS);
  }

  async function loadLive(force = false) {
    if (state.live.loading) return;
    state.live.loading = true;
    $("#liveRefresh").disabled = true;
    try {
      const res = await fetch(`/api/live${force ? "?force=1" : ""}`);
      const board = await res.json();
      if (!res.ok) throw new Error(board.error || `HTTP ${res.status}`);
      state.live.board = board;
      state.live.error = board.error || null;
    } catch (err) {
      state.live.error = err.message || String(err);
    } finally {
      state.live.loading = false;
      $("#liveRefresh").disabled = false;
    }
    renderLive();
  }

  function fmtKickoff(iso) {
    if (!iso) return "TBD";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "TBD";
    return d.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  }

  function fmtUpdated(epoch) {
    if (!epoch) return "";
    return new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function teamColor(team) {
    const hex = (team.color || "444444").replace("#", "");
    return `#${hex}`;
  }

  function fieldSvg(game) {
    const s = game.situation;
    if (!s) return "";
    const home = game.home;
    const away = game.away;
    const y = s.yard_line;
    const hasBall = typeof y === "number";
    const poss = s.possession;
    const dir = poss === "home" ? 1 : poss === "away" ? -1 : 0;
    const x = hasBall ? 10 + y : null;
    const fdLine =
      hasBall && s.down && s.distance > 0 && dir !== 0 ? Math.max(0, Math.min(100, y + dir * s.distance)) : null;
    const lp = s.last_play;
    const trail =
      lp && typeof lp.start === "number" && typeof lp.end === "number" && lp.start !== lp.end
        ? `<line class="trail" x1="${10 + lp.start}" y1="20" x2="${10 + lp.end}" y2="20" />`
        : "";
    const yardLines = [];
    for (let i = 10; i <= 90; i += 10) {
      const label = i <= 50 ? i : 100 - i;
      yardLines.push(`<line x1="${10 + i}" y1="0" x2="${10 + i}" y2="40" class="yl" />`);
      yardLines.push(`<text x="${10 + i}" y="37" class="yn">${label}</text>`);
    }
    const hashes = [];
    for (let i = 5; i < 100; i += 5) {
      hashes.push(`<line x1="${10 + i}" y1="12" x2="${10 + i}" y2="14" class="hash" />`);
      hashes.push(`<line x1="${10 + i}" y1="26" x2="${10 + i}" y2="28" class="hash" />`);
    }
    const redLeft = s.is_red_zone && poss === "away";
    const redRight = s.is_red_zone && poss === "home";
    const ball = hasBall
      ? `<g class="ball" transform="translate(${x} 20)">
           <ellipse rx="2.4" ry="1.5" />
           ${dir !== 0 ? `<path class="arrow" d="${dir > 0 ? "M3.2 -1.6 L5.6 0 L3.2 1.6 Z" : "M-3.2 -1.6 L-5.6 0 L-3.2 1.6 Z"}" />` : ""}
         </g>`
      : "";
    return `
      <svg class="field" viewBox="0 0 120 40" preserveAspectRatio="none" role="img"
           aria-label="${esc(s.down_distance_text || "Field position")}">
        <rect x="10" y="0" width="100" height="40" class="turf" />
        <rect x="0" y="0" width="10" height="40" class="ez ${redLeft ? "hot" : ""}" style="fill:${teamColor(home)}" />
        <rect x="110" y="0" width="10" height="40" class="ez ${redRight ? "hot" : ""}" style="fill:${teamColor(away)}" />
        <text x="5" y="22" class="ezt" transform="rotate(-90 5 20)">${esc(home.abbr)}</text>
        <text x="115" y="22" class="ezt" transform="rotate(90 115 20)">${esc(away.abbr)}</text>
        ${yardLines.join("")}
        ${hashes.join("")}
        <line x1="60" y1="0" x2="60" y2="40" class="mid" />
        ${trail}
        ${fdLine !== null ? `<line x1="${10 + fdLine}" y1="0" x2="${10 + fdLine}" y2="40" class="fd" />` : ""}
        ${ball}
      </svg>`;
  }

  function statNumber(value) {
    if (value === undefined || value === null || value === "") return null;
    const n = Number(String(value).split(/[-/:]/)[0]);
    return Number.isFinite(n) ? n : null;
  }

  function statRow(label, key, game) {
    const a = game.away.stats?.[key];
    const h = game.home.stats?.[key];
    if ((a === undefined || a === "") && (h === undefined || h === "")) return "";
    const an = statNumber(a);
    const hn = statNumber(h);
    const total = (an ?? 0) + (hn ?? 0);
    const aw = total > 0 && an !== null ? Math.round((an / total) * 100) : 50;
    const hw = total > 0 && hn !== null ? 100 - aw : 50;
    const pct = (n) => (n >= 14 ? `${n}%` : "");
    return `
      <div class="stat-row">
        <span class="sv away">${esc(a ?? "–")}</span>
        <span class="sbar" role="img" aria-label="${esc(label)} ${aw}% away, ${hw}% home">
          <span class="seg a" style="width:${aw}%;background:${teamColor(game.away)}">${pct(aw)}</span>
          <span class="seg h" style="width:${hw}%;background:${teamColor(game.home)}">${pct(hw)}</span>
        </span>
        <span class="sv home">${esc(h ?? "–")}</span>
        <span class="sl">${esc(label)}</span>
      </div>`;
  }

  function leaderRow(label, key, game) {
    const a = game.away.leaders?.[key];
    const h = game.home.leaders?.[key];
    if (!a && !h) return "";
    const cell = (l) =>
      l ? `<b>${esc(l.name)}</b><small>${esc(l.line)}</small>` : `<b>–</b>`;
    return `
      <div class="leader-row">
        <span class="lv away">${cell(a)}</span>
        <span class="ll">${esc(label)}</span>
        <span class="lv home">${cell(h)}</span>
      </div>`;
  }

  function linescore(game) {
    const a = game.away.linescores || [];
    const h = game.home.linescores || [];
    const n = Math.max(a.length, h.length);
    if (!n) return "";
    const heads = Array.from({ length: n }, (_, i) => (i < 4 ? `Q${i + 1}` : i === 4 ? "OT" : `OT${i - 3}`));
    const cells = (arr) => heads.map((_, i) => `<span>${arr[i] ?? "–"}</span>`).join("");
    return `
      <div class="linescore">
        <span class="lsn"></span>${heads.map((x) => `<span class="lsh">${x}</span>`).join("")}<span class="lsh">T</span>
        <span class="lsn">${esc(game.away.abbr)}</span>${cells(a)}<span class="lst">${game.away.score}</span>
        <span class="lsn">${esc(game.home.abbr)}</span>${cells(h)}<span class="lst">${game.home.score}</span>
      </div>`;
  }

  function teamRow(game, side) {
    const t = game[side];
    const s = game.situation;
    const hasBall = s && s.possession === side;
    const timeouts = s ? (side === "home" ? s.home_timeouts : s.away_timeouts) : null;
    const loser = game.state === "post" && !t.winner && game.home.score !== game.away.score;
    const logo = t.logo
      ? `<img class="logo" src="${esc(t.logo)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'" />`
      : `<span class="logo logo-fallback">${esc(t.abbr.slice(0, 2))}</span>`;
    return `
      <div class="team-row ${side} ${hasBall ? "has-ball" : ""} ${t.winner ? "winner" : ""} ${loser ? "loser" : ""}"
           style="--team:${teamColor(t)}">
        ${logo}
        <span class="tname">
          <b>${esc(t.abbr)}</b>
          <span class="full">${esc(t.name)}</span>
          ${t.record ? `<small class="rec">${esc(t.record)}</small>` : ""}
        </span>
        ${hasBall ? `<span class="poss" title="Possession" aria-label="has the ball"></span>` : ""}
        ${
          timeouts !== null && game.state === "in"
            ? `<span class="to" title="Timeouts left">${"●".repeat(timeouts)}${"○".repeat(Math.max(0, 3 - timeouts))}</span>`
            : ""
        }
        <span class="tscore">${game.state === "pre" ? "" : t.score}</span>
      </div>`;
  }

  function statusBadge(game) {
    if (game.state === "in") {
      return `<span class="status live"><i class="dot"></i>${esc(game.period_label)}</span>`;
    }
    if (game.state === "post") {
      return `<span class="status final">${esc(game.period_label || "Final")}</span>`;
    }
    return `<span class="status pre">${esc(fmtKickoff(game.date))}</span>`;
  }

  function winProb(game) {
    const wp = game.situation?.win_prob;
    if (!wp) return "";
    const away = Math.max(0, Math.min(100, Math.round(wp.away * 100)));
    const home = 100 - away;
    const label = (side, pct) =>
      pct >= 18 ? `<b>${esc(game[side].abbr)}</b> ${pct}%` : pct >= 10 ? `${pct}%` : "";
    return `
      <div class="wp-bar" title="Win probability ${esc(game.away.abbr)} ${away}% · ${esc(game.home.abbr)} ${home}%">
        <span class="seg a" style="width:${away}%;background:${teamColor(game.away)}">${label("away", away)}</span>
        <span class="seg h" style="width:${home}%;background:${teamColor(game.home)}">${label("home", home)}</span>
      </div>`;
  }

  function chicletHtml(game) {
    const expanded = state.live.expanded.has(game.id);
    const s = game.situation;
    const lp = s?.last_play;
    const redZone = s?.is_red_zone ? " red-zone" : "";
    const showStats = game.state !== "pre" && (Object.keys(game.home.stats || {}).length || Object.keys(game.away.stats || {}).length);
    const canSimilar = game.snapshot && game.snapshot.has_box;

    const core = [
      statRow("Total yds", "total_yards", game),
      statRow("1st downs", "first_downs", game),
      statRow("Pass", "passing_yards", game),
      statRow("Rush", "rushing_yards", game),
      statRow("Turnovers", "turnovers", game),
    ].join("");
    const more = expanded
      ? [
          statRow("3rd down", "third_down", game),
          statRow("Yds / play", "yards_per_play", game),
          statRow("Penalties", "penalties", game),
          statRow("Possession", "possession", game),
        ].join("")
      : "";
    const leaders = expanded
      ? [leaderRow("PASS", "passing", game), leaderRow("RUSH", "rushing", game), leaderRow("REC", "receiving", game)].join("")
      : "";

    return `
      <article class="chiclet state-${esc(game.state)}${redZone}${expanded ? " expanded" : ""}" data-id="${esc(game.id)}">
        <header class="chiclet-head">
          ${statusBadge(game)}
          <span class="net">${esc([game.broadcast, game.state === "pre" ? game.venue : ""].filter(Boolean).join(" · "))}</span>
        </header>
        <div class="team-rows">
          ${teamRow(game, "away")}
          ${teamRow(game, "home")}
        </div>
        ${winProb(game)}
        ${
          s
            ? `<div class="drive">
                 ${fieldSvg(game)}
                 <div class="down-line">
                   <span class="dd">${esc(s.down_distance_text || s.possession_text || "—")}</span>
                   ${s.is_red_zone ? `<span class="rz">RED ZONE</span>` : ""}
                 </div>
               </div>`
            : ""
        }
        ${
          lp && lp.text
            ? `<div class="last-play">
                 <span class="lp-tag">${esc(lp.type || "Last play")}${lp.score_value ? ` · +${lp.score_value}` : ""}</span>
                 <span class="lp-text">${esc(lp.text)}</span>
                 ${lp.drive ? `<span class="lp-drive">Drive: ${esc(lp.drive)}</span>` : ""}
               </div>`
            : ""
        }
        ${showStats ? `<div class="stat-compare">${core}${more}</div>` : ""}
        ${expanded && leaders ? `<div class="leaders">${leaders}</div>` : ""}
        ${expanded ? linescore(game) : ""}
        ${
          game.state === "pre"
            ? `<div class="pre-note">${esc(game.status_detail)}${game.venue ? ` · ${esc(game.venue)}` : ""}</div>`
            : ""
        }
        <footer class="chiclet-foot">
          <button type="button" class="mini toggle" data-act="toggle">${expanded ? "Less" : "More"}</button>
          ${
            canSimilar
              ? `<button type="button" class="mini sim" data-act="similar" title="Freeze this snapshot into Similar">
                   Similar ${esc(game.snapshot.minute)}′ ↗
                 </button>`
              : ""
          }
          ${game.snapshot?.label && game.state !== "pre" ? `<span class="snap-label">${esc(game.snapshot.label)}</span>` : ""}
        </footer>
      </article>`;
  }

  function renderLive() {
    const grid = $("#liveGrid");
    const meta = $("#liveMeta");
    const badge = $("#liveBadge");
    const board = state.live.board;

    if (!board) {
      grid.innerHTML = `<p class="lede">${state.live.error ? `Live feed unavailable — ${esc(state.live.error)}` : "Loading scoreboard…"}</p>`;
      meta.textContent = state.live.error ? "Offline" : "Loading…";
      badge.hidden = true;
      return;
    }

    const counts = board.counts || {};
    if (counts.live) {
      badge.textContent = counts.live;
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
    const seasonBits = [];
    if (board.season) seasonBits.push(`${board.season}`);
    if (board.week) seasonBits.push(`Week ${board.week}`);
    seasonBits.push(`${counts.live || 0} live · ${counts.final || 0} final · ${counts.upcoming || 0} upcoming`);
    seasonBits.push(`updated ${fmtUpdated(board.fetched_at)}`);
    if (state.live.error || board.stale) seasonBits.push("stale");
    meta.textContent = seasonBits.join(" · ");
    meta.classList.toggle("stale", Boolean(state.live.error || board.stale));

    document.querySelectorAll("#liveFilter .chip").forEach((chip) => {
      chip.classList.toggle("active", chip.dataset.state === state.live.filter);
    });

    const games = (board.games || []).filter((g) => state.live.filter === "all" || g.state === state.live.filter);
    if (!games.length) {
      grid.innerHTML = `<p class="lede">No games in this view.</p>`;
      return;
    }
    grid.innerHTML = games.map(chicletHtml).join("");
  }

  async function similarFromLive(game) {
    const snap = game.snapshot;
    if (!snap) return;
    const params = new URLSearchParams({
      minute: String(snap.minute),
      hs: String(snap.home_score),
      as: String(snap.away_score),
      hy: String(snap.home_yards),
      ay: String(snap.away_yards),
      hfd: String(snap.home_fd),
      afd: String(snap.away_fd),
      scores: (snap.score_minutes || []).join(","),
    });
    const res = await fetch(`/api/similar?${params.toString()}`);
    const sim = await res.json();
    state.selectedId = null;
    renderMatches();
    renderFreeze({
      title: `${game.home.name} vs ${game.away.name} · live`,
      label: `${snap.minute}' (${snap.clock}) · ${snap.label}`,
      meta: `Frozen from live feed · ${game.period_label} · score ${snap.home_score}-${snap.away_score}`,
    });
    renderSimilar(sim.hits || []);
    switchTab("similar");
  }

  function onLiveGridClick(ev) {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const card = btn.closest(".chiclet");
    const id = card?.dataset.id;
    const game = state.live.board?.games.find((g) => g.id === id);
    if (!game) return;
    if (btn.dataset.act === "toggle") {
      if (state.live.expanded.has(id)) state.live.expanded.delete(id);
      else state.live.expanded.add(id);
      card.outerHTML = chicletHtml(game);
    } else if (btn.dataset.act === "similar") {
      similarFromLive(game);
    }
  }

  // ------------------------------------------------------------------- Boot

  async function boot() {
    const meta = await (await fetch("/api/meta")).json();
    state.meta = meta;
    $("#seasonLabel").textContent = `${meta.season} · ${meta.match_count} games · ${meta.history_count} history`;
    const data = await (await fetch("/api/matches")).json();
    state.matches = data.matches || [];
    renderMatches();

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => switchTab(tab.dataset.tab));
    });
    $("#matchFilter").addEventListener("input", renderMatches);
    $("#cutMinute").addEventListener("change", () => {
      if (state.selectedId) selectMatch(state.selectedId, false);
    });
    $("#chiefsPreset").addEventListener("click", loadChiefsPreset);

    $("#liveGrid").addEventListener("click", onLiveGridClick);
    $("#liveRefresh").addEventListener("click", () => loadLive(true));
    document.querySelectorAll("#liveFilter .chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        state.live.filter = chip.dataset.state;
        renderLive();
      });
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && currentTab() === "live") loadLive();
    });
    window.addEventListener("hashchange", () => switchTab(location.hash.replace("#", "")));

    const initial = location.hash.replace("#", "");
    if (VALID_TABS.has(initial) && initial !== "matches") switchTab(initial);
    else fetch("/api/live").then((r) => r.json()).then((b) => {
      if (b && b.counts?.live) {
        $("#liveBadge").textContent = b.counts.live;
        $("#liveBadge").hidden = false;
      }
    }).catch(() => {});
  }

  boot();
})();
