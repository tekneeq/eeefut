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

  state.teams = {
    board: null,
    filter: "",
    detail: null,
    detailAbbr: null,
    openGame: null,
    openDrives: new Set(),
    gameCache: new Map(),
    syncTimer: null,
    loading: false,
  };

  const LIVE_REFRESH_MS = 20000;
  const VALID_TABS = new Set(["matches", "live", "teams", "similar"]);

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
    if (name !== "teams" && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
    if (name === "live") {
      loadLive();
      scheduleLive();
    } else {
      stopLive();
    }
    if (name === "teams") {
      loadTeams();
      if (!location.hash.startsWith("#teams")) history.replaceState(null, "", "#teams");
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

  // ------------------------------------------------------------------ Teams

  function rankClass(rank, total) {
    if (!rank) return "";
    if (rank <= Math.max(3, Math.round(total / 4))) return "good";
    if (rank > total - Math.max(3, Math.round(total / 4))) return "bad";
    return "";
  }

  function fmtMetric(value, spec) {
    if (value === null || value === undefined) return "–";
    return spec.pct ? `${Number(value).toFixed(value % 1 ? 1 : 0)}%` : String(value);
  }

  async function loadTeams(force = false) {
    if (state.teams.loading) return;
    state.teams.loading = true;
    try {
      const res = await fetch(`/api/teams${force ? `?t=${Date.now()}` : ""}`);
      const board = await res.json();
      if (!res.ok) throw new Error(board.error || `HTTP ${res.status}`);
      state.teams.board = board;
    } catch (err) {
      state.teams.board = { error: err.message || String(err), teams: [] };
    } finally {
      state.teams.loading = false;
    }
    renderTeams();
    if (state.teams.detailAbbr) loadTeamDetail(state.teams.detailAbbr, false);
  }

  async function syncTeams() {
    const btn = $("#teamsSync");
    btn.disabled = true;
    try {
      const res = await fetch("/api/ingest", { method: "POST" });
      const status = await res.json();
      $("#teamsMeta").textContent = status.started ? "Syncing from ESPN…" : `Sync ${status.message}`;
      if (state.teams.syncTimer) clearInterval(state.teams.syncTimer);
      state.teams.syncTimer = setInterval(async () => {
        const st = await (await fetch("/api/ingest")).json();
        if (!st.running) {
          clearInterval(state.teams.syncTimer);
          state.teams.syncTimer = null;
          btn.disabled = false;
          await loadTeams(true);
        } else {
          $("#teamsMeta").textContent = `Syncing… ${st.message} · ${st.fetched} fetched`;
        }
      }, 1500);
    } catch (err) {
      $("#teamsMeta").textContent = `Sync failed — ${err.message || err}`;
      btn.disabled = false;
    }
  }

  function teamChiclet(t, total) {
    const offRank = t.ranks?.offense?.yards_pg;
    const defRank = t.ranks?.defense?.yards_pg;
    const live = t.live_game_id ? `<span class="status live"><i class="dot"></i>live</span>` : "";
    const logo = t.logo
      ? `<img class="logo" src="${esc(t.logo)}" alt="" loading="lazy" />`
      : `<span class="logo logo-fallback">${esc(t.abbr.slice(0, 2))}</span>`;
    const pf = t.games ? (t.points_for / t.games).toFixed(1) : "–";
    const pa = t.games ? (t.points_against / t.games).toFixed(1) : "–";
    return `
      <button type="button" class="chiclet team-chiclet" data-abbr="${esc(t.abbr)}" style="--team:#${esc(t.color)}">
        <header class="chiclet-head">
          ${logo}
          <span class="tname"><b>${esc(t.abbr)}</b><span class="full">${esc(t.name)}</span></span>
          ${live}
          <span class="rec-big">${esc(t.record)}</span>
        </header>
        ${
          t.games
            ? `<div class="team-kpis">
                 <span class="kpi"><small>PF / PA</small><b>${pf} · ${pa}</b></span>
                 <span class="kpi"><small>Off yds/g</small><b>${fmtMetric(t.offense.yards_pg, {})}</b>
                   <i class="rank ${rankClass(offRank, total)}">#${offRank ?? "–"}</i></span>
                 <span class="kpi"><small>Def yds/g</small><b>${fmtMetric(t.defense.yards_pg, {})}</b>
                   <i class="rank ${rankClass(defRank, total)}">#${defRank ?? "–"}</i></span>
                 <span class="kpi"><small>Rush / Deep</small><b>${fmtMetric(t.offense.rush_rate, { pct: true })} · ${fmtMetric(t.offense.deep_rate, { pct: true })}</b></span>
               </div>`
            : `<div class="pre-note">No completed games stored yet</div>`
        }
      </button>`;
  }

  function renderTeams() {
    const grid = $("#teamsGrid");
    const meta = $("#teamsMeta");
    const board = state.teams.board;
    if (!board) {
      grid.innerHTML = `<p class="lede">Loading…</p>`;
      return;
    }
    if (board.error) {
      grid.innerHTML = `<p class="lede">Teams unavailable — ${esc(board.error)}</p>`;
      meta.textContent = "Offline";
      return;
    }
    const bits = [`${board.season}`, `${board.completed} games stored`, `${board.players} players`];
    if (board.live) bits.push(`${board.live} live`);
    if (board.ingest?.running) bits.push(`syncing ${board.ingest.message}`);
    meta.textContent = bits.join(" · ");
    $("#teamsSync").disabled = Boolean(board.ingest?.running);

    const q = state.teams.filter.trim().toLowerCase();
    const teams = (board.teams || []).filter((t) => !q || `${t.abbr} ${t.name} ${t.full_name}`.toLowerCase().includes(q));
    const total = (board.teams || []).filter((t) => t.games > 0).length || 32;
    if (!teams.length) {
      grid.innerHTML = `<p class="lede">${board.teams?.length ? "No team matches that filter." : "No games stored yet — hit Sync games."}</p>`;
    } else {
      grid.innerHTML = teams.map((t) => teamChiclet(t, total)).join("");
    }
    const showDetail = Boolean(state.teams.detailAbbr);
    grid.hidden = showDetail;
    $("#teamsToolbar").hidden = showDetail;
    $("#teamDetail").hidden = !showDetail;
  }

  async function loadTeamDetail(abbr, scroll = true) {
    state.teams.detailAbbr = abbr;
    history.replaceState(null, "", `#teams/${abbr}`);
    renderTeams();
    const box = $("#teamDetail");
    if (!state.teams.detail || state.teams.detail.abbr !== abbr) {
      box.innerHTML = `<p class="lede">Loading ${esc(abbr)}…</p>`;
    }
    try {
      const res = await fetch(`/api/teams/${encodeURIComponent(abbr)}`);
      const detail = await res.json();
      if (!res.ok) throw new Error(detail.error || `HTTP ${res.status}`);
      state.teams.detail = detail;
    } catch (err) {
      box.innerHTML = `<button type="button" class="mini" data-act="back">← Teams</button><p class="lede">${esc(err.message || err)}</p>`;
      return;
    }
    renderTeamDetail();
    if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function closeTeamDetail() {
    state.teams.detailAbbr = null;
    state.teams.detail = null;
    state.teams.openGame = null;
    state.teams.openDrives.clear();
    history.replaceState(null, "", "#teams");
    renderTeams();
  }

  function metricTable(detail, view, total) {
    const specs = detail.metrics || [];
    const values = detail[view] || {};
    const ranks = detail.ranks?.[view] || {};
    return specs
      .map((spec) => {
        const rank = ranks[spec.key];
        const label = view === "defense" ? spec.defense_label : spec.label;
        return `
          <div class="metric-row ${spec.ranked ? "" : "tendency"}">
            <span class="ml">${esc(label)}</span>
            <span class="mv">${fmtMetric(values[spec.key], spec)}</span>
            <span class="mr">${spec.ranked && rank ? `<i class="rank ${rankClass(rank, total)}">#${rank}</i>` : ""}</span>
          </div>`;
      })
      .join("");
  }

  function mixBar(label, a, b, la, lb, colorA, colorB) {
    const total = (a || 0) + (b || 0);
    if (!total) return "";
    const pa = Math.round((a / total) * 100);
    const pb = 100 - pa;
    return `
      <div class="mix">
        <span class="mix-label">${esc(label)}</span>
        <div class="wp-bar">
          <span class="seg a" style="width:${pa}%;background:${colorA}">${pa >= 12 ? `${esc(la)} ${pa}%` : ""}</span>
          <span class="seg h" style="width:${pb}%;background:${colorB}">${pb >= 12 ? `${esc(lb)} ${pb}%` : ""}</span>
        </div>
        <span class="mix-n">${a} / ${b}</span>
      </div>`;
  }

  function dirBar(label, dir) {
    const total = dir.left + dir.middle + dir.right;
    if (!total) return "";
    const pct = (n) => Math.round((n / total) * 100);
    return `
      <div class="mix">
        <span class="mix-label">${esc(label)}</span>
        <div class="wp-bar dir">
          <span class="seg" style="width:${pct(dir.left)}%">L ${pct(dir.left)}%</span>
          <span class="seg mid" style="width:${pct(dir.middle)}%">M ${pct(dir.middle)}%</span>
          <span class="seg" style="width:${pct(dir.right)}%">R ${pct(dir.right)}%</span>
        </div>
        <span class="mix-n">${dir.left} / ${dir.middle} / ${dir.right}</span>
      </div>`;
  }

  function playersBlock(title, rows, fmt) {
    if (!rows || !rows.length) return "";
    return `
      <div class="pl-block">
        <h4>${esc(title)}</h4>
        ${rows
          .map(
            (p) => `
          <div class="pl-row">
            <span class="pl-pos">${esc(p.position || "–")}</span>
            <span class="pl-name">${esc(p.name)}</span>
            <span class="pl-line">${esc(fmt(p.totals))}</span>
          </div>`
          )
          .join("")}
      </div>`;
  }

  function gameLogRow(g) {
    const open = state.teams.openGame === g.id;
    const cls = g.result === "W" ? "win" : g.result === "L" ? "loss" : "";
    const when = g.week ? `W${g.week}` : g.date.slice(0, 10);
    return `
      <div class="glog ${cls} ${open ? "open" : ""}" data-game="${esc(g.id)}">
        <button type="button" class="glog-head" data-act="game" data-game="${esc(g.id)}">
          <span class="glog-week">${esc(when)}</span>
          <span class="glog-opp">${g.home_away === "home" ? "vs" : "@"} ${esc(g.opponent)}</span>
          <span class="glog-res ${cls}">${esc(g.result || g.status_detail)} ${esc(g.score)}</span>
          <span class="glog-stat">${g.yards_for} / ${g.yards_against} yds</span>
          <span class="glog-stat">${g.rush_for}r ${g.pass_for}p · allowed ${g.rush_against}r ${g.pass_against}p</span>
          <span class="glog-stat">TO ${g.turnovers} / ${g.takeaways}</span>
          <span class="glog-caret">${open ? "▾" : "▸"}</span>
        </button>
        ${open ? `<div class="drives" id="drives-${esc(g.id)}"><p class="lede">Loading drives…</p></div>` : ""}
      </div>`;
  }

  function renderTeamDetail() {
    const d = state.teams.detail;
    const box = $("#teamDetail");
    if (!d) return;
    const total = (state.teams.board?.teams || []).filter((t) => t.games > 0).length || 32;
    const color = `#${d.color}`;
    const pf = d.games ? (d.points_for / d.games).toFixed(1) : "–";
    const pa = d.games ? (d.points_against / d.games).toFixed(1) : "–";
    const om = d.offense_mix || {};
    const dm = d.defense_mix || {};
    const tp = d.top_players || {};
    box.innerHTML = `
      <div class="detail-head" style="--team:${color}">
        <button type="button" class="mini" data-act="back">← Teams</button>
        ${d.logo ? `<img class="logo big" src="${esc(d.logo)}" alt="" />` : ""}
        <div class="detail-title">
          <h2>${esc(d.full_name || d.name)}</h2>
          <div class="detail-sub">
            <span>${esc(d.record)}</span>
            <span>PF ${d.points_for} · PA ${d.points_against} · ${d.point_diff >= 0 ? "+" : ""}${d.point_diff}</span>
            <span>${pf} / ${pa} per game</span>
            <span>${d.games} game${d.games === 1 ? "" : "s"} · ${d.roster_size} players used</span>
            ${d.live_game_id ? `<span class="status live"><i class="dot"></i>live now</span>` : ""}
          </div>
        </div>
      </div>

      <div class="detail-grid">
        <section class="card">
          <h3>Offense</h3>
          ${metricTable(d, "offense", total)}
        </section>
        <section class="card">
          <h3>Defense</h3>
          ${metricTable(d, "defense", total)}
        </section>
      </div>

      <div class="detail-grid">
        <section class="card">
          <h3>Offensive tendencies</h3>
          ${mixBar("Run / pass", om.rush, om.pass, "Run", "Pass", "#3f7f5f", "#3a6ea5")}
          ${mixBar("Pass depth", om.short, om.deep, "Short", "Deep", "#3a6ea5", "#b0413e")}
          ${dirBar("Rush direction", om.rush_dir || { left: 0, middle: 0, right: 0 })}
          ${dirBar("Pass direction", om.pass_dir || { left: 0, middle: 0, right: 0 })}
          <div class="mini-kpis">
            <span><b>${om.explosive ?? 0}</b> explosive plays</span>
            <span><b>${om.td_drives ?? 0}</b>/${om.drives ?? 0} TD drives</span>
            <span><b>${om.three_and_outs ?? 0}</b> three-and-outs</span>
            <span><b>${om.sacks ?? 0}</b> sacks taken</span>
          </div>
        </section>
        <section class="card">
          <h3>What the defense faces</h3>
          ${mixBar("Run / pass faced", dm.rush, dm.pass, "Run", "Pass", "#3f7f5f", "#3a6ea5")}
          ${mixBar("Depth faced", dm.short, dm.deep, "Short", "Deep", "#3a6ea5", "#b0413e")}
          ${dirBar("Rush direction faced", dm.rush_dir || { left: 0, middle: 0, right: 0 })}
          ${dirBar("Pass direction faced", dm.pass_dir || { left: 0, middle: 0, right: 0 })}
          <div class="mini-kpis">
            <span><b>${dm.explosive ?? 0}</b> explosive allowed</span>
            <span><b>${dm.td_drives ?? 0}</b>/${dm.drives ?? 0} TD drives allowed</span>
            <span><b>${dm.three_and_outs ?? 0}</b> three-and-outs forced</span>
            <span><b>${dm.sacks ?? 0}</b> sacks</span>
          </div>
        </section>
      </div>

      <section class="card">
        <h3>Top players</h3>
        <div class="players-grid">
          ${playersBlock("Passing", tp.passing, (t) => `${t.completions ?? 0}/${t.pass_att ?? 0} · ${t.pass_yds} yds · ${t.pass_td ?? 0} TD · ${t.pass_int ?? 0} INT`)}
          ${playersBlock("Rushing", tp.rushing, (t) => `${t.rush_att ?? 0} car · ${t.rush_yds} yds · ${t.rush_td ?? 0} TD`)}
          ${playersBlock("Receiving", tp.receiving, (t) => `${t.rec ?? 0} rec / ${t.targets ?? 0} tgt · ${t.rec_yds} yds · ${t.rec_td ?? 0} TD`)}
          ${playersBlock("Tackles", tp.tackles, (t) => `${t.tackles} tot · ${t.solo ?? 0} solo · ${t.tfl ?? 0} TFL`)}
          ${playersBlock("Sacks", tp.sacks, (t) => `${t.sacks} sacks · ${t.qb_hits ?? 0} QB hits`)}
          ${playersBlock("Pass defense", tp.pass_defense, (t) => `${t.pass_def} PD · ${t.int ?? 0} INT`)}
          ${playersBlock("Interceptions", tp.interceptions, (t) => `${t.int} INT · ${t.int_yds ?? 0} yds`)}
        </div>
      </section>

      <section class="card">
        <h3>Game log <small>click a game for drives and plays</small></h3>
        <div class="glog-list">
          ${(d.game_log || []).map(gameLogRow).join("") || `<p class="lede">No games yet.</p>`}
        </div>
      </section>`;
    if (state.teams.openGame) renderDrives(state.teams.openGame);
  }

  function playTag(p) {
    if (p.kind === "pass") return `PASS${p.depth ? ` ${p.depth.toUpperCase()}` : ""}${p.direction ? ` ${p.direction[0].toUpperCase()}` : ""}${p.sack ? " · SACK" : ""}`;
    if (p.kind === "rush") return `RUSH${p.direction ? ` ${p.direction[0].toUpperCase()}` : ""}`;
    return (p.kind || p.type || "").toUpperCase();
  }

  function driveRow(drive, game, teamAbbr) {
    const key = drive.id;
    const open = state.teams.openDrives.has(key);
    const mine = drive.team === teamAbbr;
    const scoreCls = drive.result === "TD" ? "td" : drive.result === "FG" ? "fg" : /INT|FUMBLE|DOWNS|SAFETY/.test(drive.result) ? "to" : "";
    const realPlays = drive.plays.filter((p) => p.kind === "pass" || p.kind === "rush");
    const passes = realPlays.filter((p) => p.kind === "pass").length;
    const deep = realPlays.filter((p) => p.depth === "deep").length;
    return `
      <div class="drive ${mine ? "mine" : "theirs"} ${open ? "open" : ""}">
        <button type="button" class="drive-head" data-act="drive" data-drive="${esc(key)}">
          <span class="drive-team">${esc(drive.team)}</span>
          <span class="drive-q">Q${drive.start_period} ${esc(drive.start_clock)}</span>
          <span class="drive-desc">${esc(drive.description)} · ${esc(drive.start_text)} → ${esc(drive.end_text)}</span>
          <span class="drive-mix">${realPlays.length - passes}r / ${passes}p${deep ? ` · ${deep} deep` : ""}</span>
          <span class="drive-res ${scoreCls}">${esc(drive.result || "—")}${drive.points ? ` +${drive.points}` : ""}</span>
        </button>
        ${
          open
            ? `<div class="plays">
                ${drive.plays
                  .filter((p) => p.kind !== "admin")
                  .map(
                    (p) => `
                  <div class="play ${esc(p.kind)} ${p.explosive ? "explosive" : ""} ${p.turnover ? "turnover" : ""} ${p.scoring ? "scoring" : ""}">
                    <span class="play-dd">${esc(p.down_distance || p.type)}</span>
                    <span class="play-tag">${esc(playTag(p))}</span>
                    <span class="play-text">${esc(p.text)}</span>
                    <span class="play-yds">${p.kind === "pass" || p.kind === "rush" ? `${p.yards > 0 ? "+" : ""}${p.yards}` : ""}</span>
                    ${
                      p.participants?.length
                        ? `<span class="play-part">${p.participants.map((x) => `${esc(x.name)}${x.position ? ` (${esc(x.position)})` : ""}`).join(", ")}</span>`
                        : ""
                    }
                  </div>`
                  )
                  .join("")}
              </div>`
            : ""
        }
      </div>`;
  }

  async function renderDrives(gameId) {
    const host = document.getElementById(`drives-${gameId}`);
    if (!host) return;
    let game = state.teams.gameCache.get(gameId);
    if (!game) {
      try {
        const res = await fetch(`/api/games/${encodeURIComponent(gameId)}`);
        game = await res.json();
        if (!res.ok) throw new Error(game.error || `HTTP ${res.status}`);
        state.teams.gameCache.set(gameId, game);
      } catch (err) {
        host.innerHTML = `<p class="lede">${esc(err.message || err)}</p>`;
        return;
      }
    }
    if (state.teams.openGame !== gameId) return;
    const team = state.teams.detail?.abbr;
    const drives = game.drives || [];
    host.innerHTML = drives.length
      ? `<div class="drive-list">${drives.map((dr) => driveRow(dr, game, team)).join("")}</div>`
      : `<p class="lede">No drive data stored for this game.</p>`;
  }

  function onTeamsClick(ev) {
    const chip = ev.target.closest(".team-chiclet");
    if (chip) {
      loadTeamDetail(chip.dataset.abbr);
      return;
    }
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    if (btn.dataset.act === "back") {
      closeTeamDetail();
    } else if (btn.dataset.act === "game") {
      const id = btn.dataset.game;
      state.teams.openGame = state.teams.openGame === id ? null : id;
      state.teams.openDrives.clear();
      renderTeamDetail();
    } else if (btn.dataset.act === "drive") {
      const key = btn.dataset.drive;
      if (state.teams.openDrives.has(key)) state.teams.openDrives.delete(key);
      else state.teams.openDrives.add(key);
      renderDrives(state.teams.openGame);
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
    $("#panel-teams").addEventListener("click", onTeamsClick);
    $("#teamsSync").addEventListener("click", syncTeams);
    $("#teamFilter").addEventListener("input", (ev) => {
      state.teams.filter = ev.target.value || "";
      renderTeams();
    });
    window.addEventListener("hashchange", () => {
      const [tab, arg] = location.hash.replace("#", "").split("/");
      if (tab === "teams" && arg) {
        switchTab("teams");
        loadTeamDetail(arg.toUpperCase(), false);
      } else if (currentTab() !== tab) {
        switchTab(tab);
      }
    });

    const [initial, initialArg] = location.hash.replace("#", "").split("/");
    if (initial === "teams" && initialArg) {
      state.teams.detailAbbr = initialArg.toUpperCase();
      switchTab("teams");
    } else if (VALID_TABS.has(initial) && initial !== "matches") switchTab(initial);
    else fetch("/api/live").then((r) => r.json()).then((b) => {
      if (b && b.counts?.live) {
        $("#liveBadge").textContent = b.counts.live;
        $("#liveBadge").hidden = false;
      }
    }).catch(() => {});
  }

  boot();
})();
