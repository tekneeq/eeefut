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
    sort: "power",
    detail: null,
    detailAbbr: null,
    openGame: null,
    openDrives: new Set(),
    gameCache: new Map(),
    syncTimer: null,
    loading: false,
  };

  state.wp = { board: null, week: null, loading: false, error: null };

  const LIVE_REFRESH_MS = 20000;
  const VALID_TABS = new Set(["matches", "live", "teams", "winprob", "similar"]);

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

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
    if (name === "winprob") loadWinProb();
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

  function signed(x, digits = 1) {
    if (x === null || x === undefined) return "–";
    const n = Number(x);
    return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}`;
  }

  function score100(pts) {
    if (pts === null || pts === undefined) return null;
    return Math.round(1000 / (1 + 10 ** (-Number(pts) / 16))) / 10;
  }

  function histScore(h) {
    if (!h) return null;
    return h.score != null ? h.score : score100(h.power);
  }

  function fmtScore(n) {
    if (n === null || n === undefined) return "–";
    return `${Math.round(Number(n))}<span class="per">/100</span>`;
  }

  function deltaHtml(delta, digits = 1, suffix = "") {
    if (delta === null || delta === undefined) return `<span class="delta flat">new</span>`;
    const n = Number(delta);
    const cls = n > 0 ? "up" : n < 0 ? "down" : "flat";
    const arrow = n > 0 ? "▲" : n < 0 ? "▼" : "•";
    return `<span class="delta ${cls}">${arrow} ${Math.abs(n).toFixed(digits)}${suffix}</span>`;
  }

  function rankChangeHtml(change) {
    if (change === null || change === undefined) return "";
    if (change === 0) return `<span class="delta flat">=</span>`;
    return `<span class="delta ${change > 0 ? "up" : "down"}">${change > 0 ? "▲" : "▼"}${Math.abs(change)}</span>`;
  }

  function powerBadge(p) {
    if (!p) return "";
    return `
      <span class="power-badge" title="Overall power: points vs an average team · change since last week">
        <i class="rank ${rankClass(p.rank, 32)}">#${p.rank}</i>
        <b>${fmtScore(p.score ?? score100(p.power))}</b>
        ${deltaHtml(p.score_delta ?? p.power_delta)}
        ${rankChangeHtml(p.rank_change)}
      </span>`;
  }

  function sidePill(label, value, rank, title) {
    const empty = value === null || value === undefined;
    return `
      <span class="side-pill" title="${esc(title)}">
        <small>${esc(label)}</small>
        <b>${empty ? "–" : fmtScore(value)}</b>
        ${empty || !rank ? "" : `<i class="rank ${rankClass(rank, 32)}">#${rank}</i>`}
      </span>`;
  }

  function powerTrio(t) {
    const p = t.power;
    const s = t.side_power || {};
    if (!p && s.offense == null && s.defense == null) return "";
    return `
      <div class="power-trio">
        ${sidePill("OVR", p?.score ?? score100(p?.power), p?.rank, "Overall power out of 100 (50 = average team)")}
        ${sidePill("OFF", s.offense, s.offense_rank, "Offense power out of 100 (50 = average offense)")}
        ${sidePill("DEF", s.defense, s.defense_rank, "Defense power out of 100 (50 = average defense)")}
        ${p ? sparkline(p.history) : ""}
      </div>`;
  }

  function sparkline(history, width = 90, height = 26) {
    if (!history || history.length < 2) return "";
    const vals = history.map(histScore);
    const min = Math.min(...vals, 50);
    const max = Math.max(...vals, 50);
    const span = max - min || 1;
    const x = (i) => (i / (history.length - 1)) * (width - 4) + 2;
    const y = (v) => height - 3 - ((v - min) / span) * (height - 6);
    const pts = history.map((h, i) => `${x(i).toFixed(1)},${y(histScore(h)).toFixed(1)}`).join(" ");
    const last = history[history.length - 1];
    const trend = histScore(last) >= histScore(history[0]) ? "up" : "down";
    return `
      <svg class="spark ${trend}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
        <line x1="0" x2="${width}" y1="${y(50).toFixed(1)}" y2="${y(50).toFixed(1)}" class="zero" />
        <polyline points="${pts}" />
        <circle cx="${x(history.length - 1).toFixed(1)}" cy="${y(histScore(last)).toFixed(1)}" r="2" />
      </svg>`;
  }

  function powerChart(history, color) {
    if (!history || !history.length) return `<p class="lede">No rating history yet.</p>`;
    const W = 640;
    const H = 220;
    const padL = 36;
    const padR = 44;
    const padT = 18;
    const padB = 30;
    const vals = history.map(histScore);
    const min = 0;
    const max = 100;
    const span = 100;
    const n = history.length;
    const x = (i) => padL + (n === 1 ? (W - padL - padR) / 2 : (i / (n - 1)) * (W - padL - padR));
    const y = (v) => padT + (1 - (v - min) / span) * (H - padT - padB);
    const pts = history.map((h, i) => `${x(i).toFixed(1)},${y(histScore(h)).toFixed(1)}`).join(" ");
    const ticks = [0, 25, 50, 75, 100];
    return `
      <svg class="power-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Power rating out of 100 by week">
        ${ticks
          .map(
            (v) => `<line x1="${padL}" x2="${W - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="grid ${v === 50 ? "zero" : ""}" />
                    <text x="${padL - 6}" y="${(y(v) + 3).toFixed(1)}" class="ylab">${v}</text>`
          )
          .join("")}
        <polyline points="${pts}" class="line" style="stroke:${color}" />
        ${history
          .map(
            (h, i) => `
          <g class="pt">
            <circle cx="${x(i).toFixed(1)}" cy="${y(histScore(h)).toFixed(1)}" r="4" style="fill:${color}" />
            <text x="${x(i).toFixed(1)}" y="${(y(histScore(h)) - 9).toFixed(1)}" class="vlab">${Math.round(histScore(h))}</text>
            <text x="${x(i).toFixed(1)}" y="${(y(histScore(h)) + 15).toFixed(1)}" class="rlab">#${h.rank}</text>
            <text x="${x(i).toFixed(1)}" y="${H - 8}" class="xlab">${h.week === 0 ? "Pre" : `W${h.week}`}</text>
          </g>`
          )
          .join("")}
        <text x="${W - padR + 8}" y="${(y(50) + 3).toFixed(1)}" class="ylab avg">50</text>
      </svg>`;
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
        ${powerTrio(t)}
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
      renderRankChart(null, true);
      return;
    }
    if (board.error) {
      grid.innerHTML = `<p class="lede">Teams unavailable — ${esc(board.error)}</p>`;
      meta.textContent = "Offline";
      renderRankChart(board, true);
      return;
    }
    const bits = [`${board.season}`, `${board.completed} games stored`, `${board.players} players`];
    if (board.live) bits.push(`${board.live} live`);
    if (board.ingest?.running) bits.push(`syncing ${board.ingest.message}`);
    $("#teamsSync").disabled = Boolean(board.ingest?.running);

    const q = state.teams.filter.trim().toLowerCase();
    const teams = (board.teams || []).filter((t) => !q || `${t.abbr} ${t.name} ${t.full_name}`.toLowerCase().includes(q));
    const winPct = (t) => (t.games ? (t.wins + 0.5 * (t.ties || 0)) / t.games : 0);
    const sorters = {
      power: (a, b) => (a.power?.rank ?? 99) - (b.power?.rank ?? 99) || winPct(b) - winPct(a),
      offense: (a, b) => (a.side_power?.offense_rank ?? 99) - (b.side_power?.offense_rank ?? 99) || (b.side_power?.offense ?? -99) - (a.side_power?.offense ?? -99),
      defense: (a, b) => (a.side_power?.defense_rank ?? 99) - (b.side_power?.defense_rank ?? 99) || (b.side_power?.defense ?? -99) - (a.side_power?.defense ?? -99),
      record: (a, b) => winPct(b) - winPct(a) || b.point_diff - a.point_diff,
      movers: (a, b) => Math.abs(b.power?.power_delta ?? 0) - Math.abs(a.power?.power_delta ?? 0),
    };
    teams.sort(sorters[state.teams.sort] || sorters.power);
    $$("#teamSort .chip").forEach((c) => c.classList.toggle("active", c.dataset.sort === state.teams.sort));
    if (board.power_through_week) bits.push(`power through W${board.power_through_week}`);
    meta.textContent = bits.join(" · ");
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
    renderRankChart(board, showDetail);
  }

  function weekLabel(week) {
    return week === 0 ? "Pre" : `Week ${week}`;
  }

  function rankChartHtml(board) {
    const cols = (board.power_ladder || []).filter((c) => c.week > 0 && (c.ranks || []).length);
    if (!cols.length) {
      return `<h3>Rank by week</h3><p class="lede small">Rankings 1–32 appear here after week 1 is in the books.</p>`;
    }
    const nWeeks = cols.length;
    const nRanks = Math.max(1, ...cols.map((c) => c.ranks.reduce((m, s) => Math.max(m, s.rank), 0)));
    const colors = {};
    for (const t of board.teams || []) {
      if (t.color) colors[t.abbr] = `#${t.color}`;
    }
    const hex = (abbr) => colors[abbr] || teamHex(abbr);
    const byTeam = {};
    cols.forEach((col, wi) => {
      for (const slot of col.ranks) {
        (byTeam[slot.team] ||= []).push({ wi, rank: slot.rank });
      }
    });
    const x = (wi) => ((wi + 0.5) / nWeeks) * 100;
    const y = (rank) => ((rank - 0.5) / nRanks) * 100;
    const bumps = Object.entries(byTeam)
      .map(([team, pts]) => {
        if (pts.length < 2) return "";
        const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.wi).toFixed(2)},${y(p.rank).toFixed(2)}`).join(" ");
        return `<path class="bump" data-team="${esc(team)}" d="${d}" style="stroke:${hex(team)}" />`;
      })
      .join("");
    const yTicks =
      nRanks <= 8 ? Array.from({ length: nRanks }, (_, i) => i + 1) : [1, 4, 8, 12, 16, 20, 24, 28, 32].filter((r) => r <= nRanks);
    const rows = Array.from({ length: nRanks }, (_, i) => {
      const rank = i + 1;
      const cells = cols
        .map((col) => {
          const slot = col.ranks.find((s) => s.rank === rank);
          if (!slot) return `<span class="rank-cell empty"></span>`;
          return `
            <button type="button" class="rank-cell" data-team="${esc(slot.team)}" title="${esc(slot.name)} · ${slot.score != null ? `${Math.round(slot.score)}/100` : signed(slot.power)} · week ${col.week}" style="--team:${hex(slot.team)}">
              <b>${esc(slot.team)}</b> <span class="rn">${esc(slot.name)}</span>
            </button>`;
        })
        .join("");
      return `<li class="rank-row"><span class="rank-y">${rank}</span>${cells}</li>`;
    }).join("");
    return `
      <div class="rank-chart-head">
        <h3>Rank by week</h3>
        <p class="lede small">X is the week. Y is league rank (1 at the top through ${nRanks}). The name at each rank is who sat there after that week's games.</p>
      </div>
      <div class="rank-chart" style="--weeks:${nWeeks};--ranks:${nRanks}" data-weeks="${nWeeks}">
        <div class="rank-x">
          <span class="rank-y-lab">Rank</span>
          ${cols.map((c) => `<span>${esc(weekLabel(c.week))}</span>`).join("")}
        </div>
        <div class="rank-body">
          <svg class="rank-bumps" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            ${yTicks.map((r) => `<line class="grid ${r === 1 ? "top" : ""}" x1="0" x2="100" y1="${y(r).toFixed(2)}" y2="${y(r).toFixed(2)}" />`).join("")}
            ${bumps}
          </svg>
          <ol class="rank-rows">${rows}</ol>
        </div>
      </div>`;
  }

  function renderRankChart(board, hide) {
    const box = $("#teamsRankChart");
    if (!box) return;
    if (hide || !board || board.error) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.innerHTML = rankChartHtml(board);
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

  function powerCard(d, color) {
    const p = d.power;
    const s = d.side_power || {};
    if (!p && s.offense == null && s.defense == null) return "";
    const hist = p?.history || [];
    const first = hist[0];
    const last = hist[hist.length - 1];
    const season = first && last && hist.length > 1 ? histScore(last) - histScore(first) : null;
    const weekLabel = hist.length ? (last.week === 0 ? "preseason" : `through week ${last.week}`) : "";
    const weekRows = hist
      .slice()
      .reverse()
      .map((h, i, arr) => {
        const prev = arr[i + 1];
        return `
          <tr>
            <td>${h.week === 0 ? "Preseason" : `Week ${h.week}`}</td>
            <td class="num">${fmtScore(histScore(h))}</td>
            <td class="num">${prev ? deltaHtml(histScore(h) - histScore(prev)) : `<span class="delta flat">–</span>`}</td>
            <td class="num">#${h.rank}</td>
            <td class="num">${prev ? rankChangeHtml(prev.rank - h.rank) : ""}</td>
          </tr>`;
      })
      .join("");
    return `
      <section class="card power-card">
        <div class="power-head">
          <div>
            <h3>Power rating</h3>
            <p class="lede small">All three numbers are out of 100 (50 = average)${weekLabel ? `, ${esc(weekLabel)}` : ""}. Overall is Elo; offense and defense come from scoring and the box score.</p>
          </div>
          <div class="power-kpis">
            <span class="kpi"><b class="power-num">${p ? fmtScore(p.score ?? score100(p.power)) : "–"}</b><small>overall</small></span>
            <span class="kpi"><b>${s.offense == null ? "–" : fmtScore(s.offense)}</b><small>offense ${s.offense_rank ? `#${s.offense_rank}` : ""}</small></span>
            <span class="kpi"><b>${s.defense == null ? "–" : fmtScore(s.defense)}</b><small>defense ${s.defense_rank ? `#${s.defense_rank}` : ""}</small></span>
            <span class="kpi"><b>${p ? `#${p.rank}` : "–"}</b><small>of 32 ${p ? rankChangeHtml(p.rank_change) : ""}</small></span>
            <span class="kpi"><b>${p ? deltaHtml(p.score_delta ?? p.power_delta) : "–"}</b><small>vs last week</small></span>
            <span class="kpi"><b>${season === null ? "–" : deltaHtml(season)}</b><small>since preseason</small></span>
          </div>
        </div>
        ${powerChart(hist, color)}
        <details class="power-table">
          <summary>Week-by-week</summary>
          <table>
            <thead><tr><th>Week</th><th class="num">Power</th><th class="num">Δ</th><th class="num">Rank</th><th class="num">Δ</th></tr></thead>
            <tbody>${weekRows}</tbody>
          </table>
        </details>
      </section>`;
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

      ${powerCard(d, color)}

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
    const ladder = ev.target.closest(".rank-cell[data-team]");
    if (ladder) {
      loadTeamDetail(ladder.dataset.team);
      return;
    }
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

  // ---------------------------------------------------------------- WinProb

  const TEAM_COLORS = {
    ARI: "97233f", ATL: "a71930", BAL: "241773", BUF: "00338d", CAR: "0085ca", CHI: "0b162a", CIN: "fb4f14",
    CLE: "311d00", DAL: "003594", DEN: "fb4f14", DET: "0076b6", GB: "203731", HOU: "03202f", IND: "002c5f",
    JAX: "006778", KC: "e31837",     LA: "003594", LAR: "003594", LAC: "0080c6", LV: "a5acaf", MIA: "008e97", MIN: "4f2683",
    NE: "002244", NO: "d3bc8d", NYG: "0b2265", NYJ: "125740", PHI: "004c54", PIT: "ffb612", SEA: "69be28",
    SF: "aa0000", TB: "d50a0a", TEN: "4b92db", WAS: "5a1414", WSH: "5a1414",
  };
  const teamHex = (abbr) => `#${TEAM_COLORS[abbr] || "555555"}`;

  async function loadWinProb(force = false) {
    if (state.wp.loading) return;
    state.wp.loading = true;
    $("#wpRefresh").disabled = true;
    try {
      const res = await fetch(`/api/winprob${force ? "?force=1" : ""}`);
      const board = await res.json();
      if (!res.ok) throw new Error(board.error || `HTTP ${res.status}`);
      state.wp.board = board;
      state.wp.error = null;
      if (state.wp.week === null || !board.weeks.includes(state.wp.week)) state.wp.week = board.current_week;
    } catch (err) {
      state.wp.error = err.message || String(err);
    } finally {
      state.wp.loading = false;
      $("#wpRefresh").disabled = false;
    }
    renderWinProb();
  }

  function pct(x, digits = 0) {
    return x === null || x === undefined ? "–" : `${Number(x).toFixed(digits)}%`;
  }

  function marginText(team, margin) {
    if (!margin) return "Pick 'em";
    return `${team} by ${margin}`;
  }

  function spreadText(market, home, away) {
    if (!market || market.spread === null || market.spread === undefined) return "";
    if (market.spread === 0) return "PK";
    const fav = market.spread > 0 ? home : away;
    return `${fav} -${Math.abs(market.spread)}`;
  }

  function wpGameCard(g) {
    const r = g.result;
    const homeFav = g.favorite === g.home;
    const cls = r ? (r.correct === null ? "tie" : r.correct ? "hit" : "miss") : "pending";
    const kickoff = g.played
      ? "Final"
      : `${g.weekday ? g.weekday.slice(0, 3) : ""} ${g.date.slice(5)} · ${g.time} ET`.trim();
    const line = spreadText(g.market, g.home, g.away);
    const mkt = g.market?.home_prob !== null && g.market?.home_prob !== undefined
      ? `Vegas ${line} · ${pct(Math.max(g.market.home_prob, g.market.away_prob) * 100)}`
      : line
        ? `Vegas ${line}`
        : "";
    const row = (side) => {
      const abbr = g[side];
      const isFav = g.favorite === abbr;
      const won = r && r.winner === abbr;
      const lost = r && r.winner && r.winner !== abbr;
      return `
        <div class="team-row ${side} ${isFav ? "fav" : ""} ${won ? "winner" : ""} ${lost ? "loser" : ""}" style="--team:${teamHex(abbr)}">
          <img class="logo" src="${esc(g[`${side}_logo`])}" alt="" loading="lazy" onerror="this.style.visibility='hidden'" />
          <span class="tname"><b>${esc(abbr)}</b><span class="full">${esc(g[`${side}_name`])}</span>
            <small class="rec">Elo ${g[`${side}_elo`]}</small></span>
          <span class="wp-pct ${isFav ? "fav" : ""}">${pct(g[`${side}_prob`] * 100, 1)}</span>
          <span class="tscore">${g.played ? g[`${side}_score`] : ""}</span>
        </div>`;
    };
    const ap = Math.round(g.away_prob * 100);
    const hp = 100 - ap;
    return `
      <article class="chiclet wp-card ${cls}" data-id="${esc(g.id)}">
        <header class="chiclet-head">
          <span class="status ${g.played ? "final" : "pre"}">${esc(kickoff)}</span>
          <span class="net">${g.neutral ? "Neutral site" : ""}</span>
          ${
            r
              ? `<span class="verdict ${cls}">${r.correct === null ? "TIE" : r.correct ? "✓ HIT" : "✗ MISS"}</span>`
              : `<span class="bucket-tag" title="Favourite's probability bucket">${esc(g.bucket)}%</span>`
          }
        </header>
        <div class="team-rows">${row("away")}${row("home")}</div>
        <div class="wp-bar" title="${esc(g.away)} ${ap}% · ${esc(g.home)} ${hp}%">
          <span class="seg a" style="width:${ap}%;background:${teamHex(g.away)}">${ap >= 18 ? `<b>${esc(g.away)}</b> ${ap}%` : ap >= 10 ? `${ap}%` : ""}</span>
          <span class="seg h" style="width:${hp}%;background:${teamHex(g.home)}">${hp >= 18 ? `<b>${esc(g.home)}</b> ${hp}%` : hp >= 10 ? `${hp}%` : ""}</span>
        </div>
        <div class="wp-lines">
          <span class="wp-margin"><small>Model</small> ${esc(marginText(g.favorite, g.favorite_margin))}</span>
          ${mkt ? `<span class="wp-market"><small>Market</small> ${esc(mkt)}</span>` : ""}
          ${
            r
              ? `<span class="wp-actual"><small>Actual</small> ${r.winner ? `${esc(r.winner)} by ${Math.abs(r.margin)}` : "Tie"} · off by ${r.margin_error}</span>`
              : ""
          }
        </div>
      </article>`;
  }

  function wpAccuracyHtml(board) {
    const weeks = board.record?.weekly || [];
    if (!weeks.length) {
      return `<h3>Right by week</h3><p class="lede small">Weekly hit counts show up once games finish.</p>`;
    }
    const maxY = Math.max(16, ...weeks.map((w) => Math.max(w.decided || 0, w.games || 0)));
    const step = maxY <= 8 ? 2 : 4;
    const ticks = [];
    for (let v = maxY; v >= 0; v -= step) ticks.push(v);
    const cols = weeks
      .map((w) => {
        const decided = w.decided || 0;
        const correct = w.correct || 0;
        const cls = !decided ? "pending" : w.pct >= 60 ? "good" : w.pct < 50 ? "bad" : "mid";
        const active = w.week === state.wp.week ? "active" : "";
        const label = decided ? `${correct} / ${decided}` : "";
        const tip = decided ? `${correct} / ${decided}${w.pct != null ? ` · ${w.pct}%` : ""}` : w.pending ? `${w.pending} still to play` : "no games";
        const stackH = decided ? (decided / maxY) * 100 : 0;
        const hitH = decided ? (correct / decided) * 100 : 0;
        const shortX = weeks.length > 6;
        return `
          <button type="button" class="wp-acc-col ${cls} ${active}" data-week="${w.week}" title="Week ${w.week}: ${tip}">
            <span class="wp-acc-label">${esc(label)}</span>
            <span class="wp-acc-stack" style="height:${stackH}%">
              <i class="miss"></i>
              <i class="hit" style="height:${hitH}%"></i>
            </span>
            <span class="wp-acc-x">${esc(shortX ? `W${w.week}` : weekLabel(w.week))}</span>
          </button>`;
      })
      .join("");
    return `
      <div class="wp-acc-head">
        <h3>Right by week</h3>
        <p class="lede small">X is the week. Each bar is how many picks the model got right out of the games that finished — 10 / 15 means 10 correct of 15 decided. Click a bar to open that week.</p>
      </div>
      <div class="wp-acc" style="--max:${maxY}">
        <div class="wp-acc-y" aria-hidden="true">${ticks.map((v) => `<span>${v}</span>`).join("")}</div>
        <div class="wp-acc-plot">
          <div class="wp-acc-grid">${ticks.map((v) => `<i style="--v:${v}"></i>`).join("")}</div>
          <div class="wp-acc-bars">${cols}</div>
        </div>
      </div>`;
  }

  function wpRecordHtml(board) {
    const t = board.record.total;
    const weekly = board.record.weekly.filter((w) => w.decided || w.week === board.current_week);
    return `
      <div class="card wp-season">
        <h3>Model picks</h3>
        <div class="wp-big">
          <span class="wp-big-rec">${esc(t.record)}</span>
          <span class="wp-big-pct">${pct(t.pct, 1)}</span>
        </div>
        <p class="lede small">The model picked the winner in ${t.correct} of ${t.decided} finished games${t.decided ? ` (${esc(t.record)})` : ""}.</p>
        <div class="mini-kpis">
          <span><b>${t.decided}</b> decided</span>
          <span><b>${t.pending}</b> still to play</span>
          <span>Brier <b>${t.brier ?? "–"}</b></span>
          <span>Margin off by <b>${t.avg_margin_error ?? "–"}</b> pts</span>
          <span title="How often the betting favorite won, on the same games">Vegas favorites <b>${t.market_decided ? `${t.market_correct}–${t.market_decided - t.market_correct}` : "–"}</b> (${pct(t.market_pct, 1)})</span>
        </div>
      </div>
      <div class="card wp-weekly">
        <h3>Weekly record</h3>
        <div class="wp-week-strip">
          ${weekly
            .map(
              (w) => `
            <button type="button" class="wk ${w.week === state.wp.week ? "active" : ""} ${w.pct === null ? "pending" : w.pct >= 60 ? "good" : w.pct < 50 ? "bad" : ""}" data-week="${w.week}">
              <small>W${w.week}</small>
              <b>${w.decided ? `Model ${esc(w.record)}` : `${w.pending} tbd`}</b>
              <span>${w.decided ? pct(w.pct) : "–"}</span>
              ${w.market_decided ? `<i title="Vegas favorites on the same games">Vegas ${w.market_correct}–${w.market_decided - w.market_correct}</i>` : ""}
            </button>`
            )
            .join("")}
        </div>
      </div>`;
  }

  function wpBucketsHtml(board) {
    const rows = board.buckets
      .map((b) => {
        const actual = b.pct;
        const expected = b.expected_pct;
        const decided = b.wins + b.losses;
        const diff = actual !== null && expected !== null ? actual - expected : null;
        return `
          <div class="bucket-row">
            <span class="bk-label">${esc(b.label)}</span>
            <div class="bk-bar" title="Actual ${pct(actual, 1)} vs expected ${pct(expected, 1)}">
              <i class="exp" style="left:${expected ?? 0}%"></i>
              <span class="act ${diff === null ? "" : diff >= 0 ? "good" : "bad"}" style="width:${actual ?? 0}%"></span>
              <em class="tick" style="left:50%"></em>
            </div>
            <span class="bk-rec"><b>${esc(b.record)}</b>${decided ? ` · ${pct(actual, 1)}` : ""}</span>
            <span class="bk-exp">${expected !== null ? `expected ${pct(expected, 1)}` : ""}${b.pending ? ` · ${b.pending} pending` : ""}</span>
            <span class="bk-split">home ${esc(b.home.record)} · away ${esc(b.away.record)}</span>
          </div>`;
      })
      .join("");
    return `
      <h3>Favourites by probability bucket <small>combined record · home / away split underneath</small></h3>
      <div class="bucket-legend"><i class="exp"></i> expected win % &nbsp; <span class="sw good"></span> actual ≥ expected &nbsp; <span class="sw bad"></span> actual below</div>
      ${rows}`;
  }

  function wpTeamBucketsHtml(board) {
    const keys = board.buckets.map((b) => b.key);
    return `
      <div class="tb-grid" style="--cols:${keys.length}">
        <span class="tb-h">Team</span><span class="tb-h">Favoured</span><span class="tb-h">Underdog</span>
        ${keys.map((k) => `<span class="tb-h">${esc(k)}%</span>`).join("")}
        ${board.team_buckets
          .map(
            (t) => `
          <span class="tb-team"><img class="logo" src="${esc(t.logo)}" alt="" loading="lazy" /> ${esc(t.team)}</span>
          <span class="tb-c">${esc(t.favored.record)}</span>
          <span class="tb-c muted">${esc(t.underdog.record)}</span>
          ${keys
            .map((k) => {
              const b = t.buckets[k];
              const n = b.wins + b.losses + b.ties;
              return `<span class="tb-c ${n ? (b.wins > b.losses ? "good" : b.wins < b.losses ? "bad" : "") : "empty"}">${n ? esc(b.record) : "·"}</span>`;
            })
            .join("")}`
          )
          .join("")}
      </div>`;
  }

  function wpRatingsHtml(board) {
    const through = board.through_week ? `through week ${board.through_week}` : "preseason";
    return `
      <p class="lede small">Power is out of 100 (50 = average), ${esc(through)}. Arrows show the change since last week; the sparkline is the season so far.</p>
      <div class="rating-list">
        <div class="rating-row head">
          <span class="rk"></span><span></span><span class="rn">Team</span><span class="rr">Rec</span>
          <span class="rp">/100</span><span class="rd">Δ wk</span><span class="rd">Rank</span><span class="rs"></span><span class="re">Elo</span>
        </div>
        ${board.ratings
          .map(
            (r) => `
          <button type="button" class="rating-row" data-team="${esc(r.team)}" title="Open ${esc(r.name)} in Teams">
            <span class="rk">#${r.rank}</span>
            <img class="logo" src="${esc(r.logo)}" alt="" loading="lazy" />
            <span class="rn"><b>${esc(r.team)}</b> ${esc(r.name)}</span>
            <span class="rr">${esc(r.record)}</span>
            <span class="rp">${fmtScore(r.score ?? score100(r.power))}</span>
            <span class="rd">${deltaHtml(r.score_delta ?? r.power_delta)}</span>
            <span class="rd">${rankChangeHtml(r.rank_change)}</span>
            <span class="rs">${sparkline(r.history, 70, 22)}</span>
            <span class="re">${r.elo}</span>
          </button>`
          )
          .join("")}
      </div>`;
  }

  function renderWinProb() {
    const board = state.wp.board;
    const meta = $("#wpMeta");
    if (!board) {
      $("#wpGames").innerHTML = `<p class="lede">${state.wp.error ? `Model unavailable — ${esc(state.wp.error)}` : "Loading…"}</p>`;
      meta.textContent = state.wp.error ? "Offline" : "Loading…";
      $("#wpWeekChart").hidden = true;
      return;
    }
    const t = board.record.total;
    meta.textContent = `${board.season} · ${board.model.name} · HFA ${board.model.hfa_points} pts · ${t.record} (${pct(t.pct, 1)}) · ${board.games.length} games`;
    $("#wpRecord").innerHTML = wpRecordHtml(board);
    const acc = $("#wpWeekChart");
    acc.hidden = false;
    acc.innerHTML = wpAccuracyHtml(board);
    $("#wpBuckets").innerHTML = wpBucketsHtml(board);
    $("#wpWeeks").innerHTML = board.weeks
      .map((w) => `<button type="button" class="chip ${w === state.wp.week ? "active" : ""}" data-week="${w}">W${w}</button>`)
      .join("");
    const games = board.games.filter((g) => g.week === state.wp.week);
    const wk = board.record.weekly.find((w) => w.week === state.wp.week);
    $("#wpWeekTitle").textContent = `Week ${state.wp.week}${wk && wk.decided ? ` · ${wk.record} (${pct(wk.pct)})` : ""}${wk && wk.pending ? ` · ${wk.pending} to play` : ""}`;
    $("#wpGames").innerHTML = games.map(wpGameCard).join("") || `<p class="lede">No games this week.</p>`;
    $("#wpTeamBuckets").innerHTML = wpTeamBucketsHtml(board);
    $("#wpRatingList").innerHTML = wpRatingsHtml(board);
  }

  function onWinProbClick(ev) {
    const team = ev.target.closest("button[data-team]");
    if (team) {
      location.hash = `#teams/${team.dataset.team}`;
      switchTab("teams");
      loadTeamDetail(team.dataset.team);
      return;
    }
    const wk = ev.target.closest("[data-week]");
    if (!wk) return;
    state.wp.week = Number(wk.dataset.week);
    renderWinProb();
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
    $("#teamsRankChart").addEventListener("mouseover", (ev) => {
      const cell = ev.target.closest("[data-team]");
      const chart = $("#teamsRankChart .rank-chart");
      if (!chart) return;
      const team = cell?.dataset.team || "";
      chart.classList.toggle("hot", Boolean(team));
      $$("#teamsRankChart [data-team]").forEach((el) => el.classList.toggle("on", Boolean(team) && el.dataset.team === team));
    });
    $("#teamsRankChart").addEventListener("mouseleave", () => {
      const chart = $("#teamsRankChart .rank-chart");
      if (!chart) return;
      chart.classList.remove("hot");
      $$("#teamsRankChart [data-team]").forEach((el) => el.classList.remove("on"));
    });
    $("#panel-winprob").addEventListener("click", onWinProbClick);
    $("#wpRefresh").addEventListener("click", () => loadWinProb(true));
    $("#teamsSync").addEventListener("click", syncTeams);
    $("#teamFilter").addEventListener("input", (ev) => {
      state.teams.filter = ev.target.value || "";
      renderTeams();
    });
    $("#teamSort").addEventListener("click", (ev) => {
      const chip = ev.target.closest(".chip[data-sort]");
      if (!chip) return;
      state.teams.sort = chip.dataset.sort;
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
