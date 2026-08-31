// Claude Racehorse — dashboard client.
// Zero dependencies: fetch() for the initial snapshot, EventSource for the
// live feed. All rendering is plain DOM (no innerHTML on untrusted data).

(() => {
  "use strict";

  const RESULTS_DISPLAY_LIMIT = 30;
  const RESULTS_CACHE_LIMIT = 60; // a little slack above the display cap
  const FINISHING_HOOK_EVENTS = new Set(["SubagentStop", "SessionEnd"]);

  // horse_id -> { data, el, runnerEl, trackEl, elapsedEl, activityEl,
  //               subEl, kindBadgeEl, autoBadgeEl }
  const running = new Map();
  // horse_id -> finished/stalled horse record (server shape)
  const results = new Map();
  // horse_id -> assigned racing number / hue, stable for the page's lifetime
  const identities = new Map();
  let nextRacingNumber = 1;

  const el = {
    lanes: document.getElementById("lanes"),
    trackEmpty: document.getElementById("track-empty"),
    runningCount: document.getElementById("running-count"),
    results: document.getElementById("results"),
    resultsEmpty: document.getElementById("results-empty"),
    resultsCount: document.getElementById("results-count"),
    connStatus: document.getElementById("conn-status"),
    connLabel: document.getElementById("conn-label"),
  };

  // ---------- small helpers ----------

  function parseTs(ts) {
    if (!ts) return NaN;
    const t = Date.parse(ts);
    return Number.isNaN(t) ? NaN : t;
  }

  function formatElapsed(ms) {
    if (!Number.isFinite(ms) || ms < 0) ms = 0;
    const totalSec = Math.floor(ms / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
    if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
    return `${s}s`;
  }

  function shortPath(cwd) {
    if (!cwd) return "";
    const parts = cwd.split("/").filter(Boolean);
    if (parts.length <= 2) return cwd;
    return "…/" + parts.slice(-2).join("/");
  }

  function shortId(id) {
    if (!id) return "";
    return id.length > 10 ? id.slice(0, 8) + "…" : id;
  }

  function hashString(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) {
      h = (h * 31 + str.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }

  function identityFor(horseId) {
    let idn = identities.get(horseId);
    if (!idn) {
      const hash = hashString(horseId);
      idn = {
        number: nextRacingNumber++,
        hue: hash % 360,
        gallopDur: (0.4 + (hash % 5) * 0.06).toFixed(2) + "s",
        dashDur: (0.7 + (hash % 7) * 0.08).toFixed(2) + "s",
      };
      identities.set(horseId, idn);
    }
    return idn;
  }

  function kindLabel(horse) {
    if (horse.kind === "subagent") {
      return horse.agent_type ? `subagent · ${horse.agent_type}` : "subagent";
    }
    return "session";
  }

  function subLabel(horse) {
    const bits = [];
    bits.push(shortId(horse.horse_id));
    const path = shortPath(horse.cwd);
    if (path) bits.push(path);
    return bits.join("  ·  ");
  }

  // ---------- connection status ----------

  function setConnState(state, label) {
    el.connStatus.dataset.state = state;
    el.connLabel.textContent = label;
  }

  // ---------- empty states + counts ----------

  function updateTrackEmptyState() {
    el.trackEmpty.classList.toggle("visible", running.size === 0);
    el.runningCount.textContent = `${running.size} running`;
  }

  function updateResultsEmptyState() {
    el.resultsEmpty.classList.toggle("visible", results.size === 0);
    el.resultsCount.textContent = `${results.size} finished`;
  }

  // ---------- lane (running horse) rendering ----------

  function buildLane(horse) {
    const idn = identityFor(horse.horse_id);

    const lane = document.createElement("div");
    lane.className = "lane";
    lane.style.setProperty("--gallop-dur", idn.gallopDur);
    lane.style.setProperty("--dash-dur", idn.dashDur);
    lane.dataset.horseId = horse.horse_id;

    const meta = document.createElement("div");
    meta.className = "lane-meta";

    const silk = document.createElement("div");
    silk.className = "silk";
    silk.style.background = `hsl(${idn.hue} 70% 55%)`;
    silk.textContent = `#${idn.number}`;

    const info = document.createElement("div");
    info.className = "lane-info";

    const topRow = document.createElement("div");
    topRow.className = "lane-top-row";

    const kindBadge = document.createElement("span");
    kindBadge.className =
      "badge " + (horse.kind === "subagent" ? "kind-subagent" : "kind-session");
    kindBadge.textContent = kindLabel(horse);

    const autoBadge = document.createElement("span");
    autoBadge.className = "badge auto-badge";
    autoBadge.textContent = "🤖 auto";
    autoBadge.hidden = horse.permission_mode !== "auto";

    const elapsed = document.createElement("span");
    elapsed.className = "elapsed";
    elapsed.textContent = "0s";

    topRow.append(kindBadge, autoBadge, elapsed);

    const activity = document.createElement("div");
    activity.className = "activity-label";
    activity.textContent = horse.activity_label || "(no activity yet)";

    const sub = document.createElement("div");
    sub.className = "lane-sub";
    sub.textContent = subLabel(horse);

    info.append(topRow, activity, sub);
    meta.append(silk, info);

    const track = document.createElement("div");
    track.className = "track";

    const dashes = document.createElement("div");
    dashes.className = "track-dashes";

    const flag = document.createElement("div");
    flag.className = "finish-flag";

    const runner = document.createElement("div");
    runner.className = "runner";
    runner.textContent = horse.kind === "subagent" ? "🐴" : "🐎";

    track.append(dashes, flag, runner);
    lane.append(meta, track);

    return {
      el: lane,
      runnerEl: runner,
      trackEl: track,
      elapsedEl: elapsed,
      activityEl: activity,
      subEl: sub,
      kindBadgeEl: kindBadge,
      autoBadgeEl: autoBadge,
    };
  }

  function addRunningHorse(horse) {
    if (running.has(horse.horse_id)) {
      updateRunningHorse(horse);
      return;
    }
    const refs = buildLane(horse);
    refs.data = { ...horse };
    running.set(horse.horse_id, refs);
    el.lanes.appendChild(refs.el);
    updateTrackEmptyState();
  }

  function updateRunningHorse(patch) {
    const entry = running.get(patch.horse_id);
    if (!entry) return;
    Object.assign(entry.data, patch);

    entry.activityEl.textContent = entry.data.activity_label || "(no activity yet)";
    entry.subEl.textContent = subLabel(entry.data);
    entry.kindBadgeEl.textContent = kindLabel(entry.data);
    entry.kindBadgeEl.className =
      "badge " + (entry.data.kind === "subagent" ? "kind-subagent" : "kind-session");
    entry.autoBadgeEl.hidden = entry.data.permission_mode !== "auto";
  }

  function tickElapsed() {
    const now = Date.now();
    for (const entry of running.values()) {
      const started = parseTs(entry.data.started_ts);
      entry.elapsedEl.textContent = formatElapsed(now - started);
    }
  }

  // ---------- finishing a horse ----------

  function finishHorse(horseId, finalData) {
    const entry = running.get(horseId);
    if (!entry) {
      // Not currently rendered as a lane (e.g. learned about the finish
      // purely from a resync) — just record the result, nothing to animate.
      addResult(finalData);
      return;
    }

    if (finalData.status === "stalled") {
      const tag = document.createElement("div");
      tag.className = "stalled-tag";
      tag.textContent = "stalled";
      entry.trackEl.appendChild(tag);
      entry.runnerEl.style.opacity = "0.35";
    } else {
      entry.runnerEl.classList.add("crossing");
    }
    entry.el.classList.add("flash");

    const crossDelay = finalData.status === "stalled" ? 450 : 650;
    setTimeout(() => {
      entry.el.classList.add("leaving");
      setTimeout(() => {
        entry.el.remove();
        running.delete(horseId);
        updateTrackEmptyState();
        addResult(finalData);
      }, 420);
    }, crossDelay);
  }

  // ---------- results (finished / stalled) rendering ----------

  function addResult(data) {
    const isNew = !results.has(data.horse_id);
    // Merge rather than blindly overwrite so an earlier, richer record isn't
    // clobbered by a sparser one arriving later.
    const existing = results.get(data.horse_id) || {};
    results.set(data.horse_id, { ...existing, ...data });

    if (results.size > RESULTS_CACHE_LIMIT) {
      const sorted = [...results.entries()].sort(
        (a, b) => (parseTs(a[1].finished_ts) || 0) - (parseTs(b[1].finished_ts) || 0)
      );
      const toDrop = sorted.slice(0, results.size - RESULTS_CACHE_LIMIT);
      for (const [id] of toDrop) results.delete(id);
    }

    renderResultsList();
    return isNew;
  }

  function renderResultsList() {
    const sorted = [...results.values()]
      .sort((a, b) => (parseTs(b.finished_ts) || 0) - (parseTs(a.finished_ts) || 0))
      .slice(0, RESULTS_DISPLAY_LIMIT);

    el.results.innerHTML = "";
    for (const horse of sorted) {
      el.results.appendChild(buildResultRow(horse));
    }
    updateResultsEmptyState();
  }

  function buildResultRow(horse) {
    const idn = identityFor(horse.horse_id);

    const row = document.createElement("div");
    row.className = "result-row";

    const silk = document.createElement("div");
    silk.className = "silk";
    silk.style.background = `hsl(${idn.hue} 70% 55%)`;
    silk.textContent = `#${idn.number}`;

    const info = document.createElement("div");
    info.className = "result-info";

    const topRow = document.createElement("div");
    topRow.className = "result-top-row";

    const kindBadge = document.createElement("span");
    kindBadge.className =
      "badge " + (horse.kind === "subagent" ? "kind-subagent" : "kind-session");
    kindBadge.textContent = kindLabel(horse);

    const statusPill = document.createElement("span");
    const status = horse.status === "stalled" ? "stalled" : "finished";
    statusPill.className = `status-pill ${status}`;
    statusPill.textContent = status;

    topRow.append(kindBadge, statusPill);

    const activity = document.createElement("div");
    activity.className = "result-activity";
    activity.textContent = horse.activity_label || "(no activity recorded)";

    info.append(topRow, activity);

    const duration = document.createElement("div");
    duration.className = "result-duration";
    const started = parseTs(horse.started_ts);
    const finished = parseTs(horse.finished_ts);
    duration.textContent =
      Number.isFinite(started) && Number.isFinite(finished)
        ? formatElapsed(finished - started)
        : "—";

    row.append(silk, info, duration);
    return row;
  }

  // ---------- reconciliation ----------

  function normalizeHorse(raw) {
    // Accepts either a full server horse record or a bare hook event and
    // returns the fields the UI cares about with sane fallbacks.
    const isSubagent = !!raw.agent_id;
    return {
      horse_id: raw.horse_id,
      session_id: raw.session_id ?? null,
      agent_id: raw.agent_id ?? null,
      agent_type: raw.agent_type ?? null,
      kind: raw.kind || (isSubagent ? "subagent" : "session"),
      activity_label: raw.activity_label || "",
      permission_mode: raw.permission_mode ?? null,
      cwd: raw.cwd ?? null,
      started_ts: raw.started_ts || raw.ts || null,
      last_update_ts: raw.last_update_ts || raw.ts || null,
      status: raw.status || "running",
      finished_ts: raw.finished_ts || null,
    };
  }

  // Full-snapshot reconcile: used for the initial /api/state load and every
  // periodic `event: state` SSE resync. Adds horses we're missing, updates
  // ones we have, and retires anything that fell out of the running set
  // (covers idle-timeout stalls, which never arrive as discrete events).
  function reconcile(snapshot) {
    const snapHorses = new Map((snapshot.horses || []).map((h) => [h.horse_id, h]));
    const snapHistory = new Map((snapshot.history || []).map((h) => [h.horse_id, h]));

    for (const [id, h] of snapHorses) {
      const horse = normalizeHorse(h);
      if (running.has(id)) {
        updateRunningHorse(horse);
      } else {
        addRunningHorse(horse);
      }
    }

    for (const id of [...running.keys()]) {
      if (snapHorses.has(id)) continue;
      const hist = snapHistory.get(id);
      if (hist) {
        finishHorse(id, normalizeHorse(hist));
      } else {
        // Vanished with no history record (log rotated/cleared) — just drop it.
        const entry = running.get(id);
        if (entry) entry.el.remove();
        running.delete(id);
        updateTrackEmptyState();
      }
    }

    for (const [id, h] of snapHistory) {
      if (!results.has(id)) {
        addResult(normalizeHorse(h));
      }
    }
    updateResultsEmptyState();
  }

  // ---------- live event handling ----------

  function handleRawEvent(ev) {
    const id = ev.horse_id;
    if (!id) return;
    const isFinishing = FINISHING_HOOK_EVENTS.has(ev.hook_event_name);

    if (!running.has(id)) {
      if (isFinishing) {
        // We never saw this horse start (missed by a reconnect gap) — let
        // the next periodic state resync pick it up with the real
        // started_ts rather than fabricating one here.
        return;
      }
      addRunningHorse(
        normalizeHorse({
          horse_id: id,
          session_id: ev.session_id,
          agent_id: ev.agent_id,
          agent_type: ev.agent_type,
          activity_label: ev.activity_label,
          permission_mode: ev.permission_mode,
          cwd: ev.cwd,
          ts: ev.ts,
        })
      );
      return;
    }

    updateRunningHorse({
      horse_id: id,
      activity_label: ev.activity_label || running.get(id).data.activity_label,
      last_update_ts: ev.ts,
      permission_mode: ev.permission_mode || running.get(id).data.permission_mode,
      agent_type: ev.agent_type || running.get(id).data.agent_type,
      cwd: ev.cwd || running.get(id).data.cwd,
      kind: running.get(id).data.kind,
      started_ts: running.get(id).data.started_ts,
    });

    if (isFinishing) {
      const entry = running.get(id);
      finishHorse(id, {
        ...entry.data,
        status: "finished",
        finished_ts: ev.ts,
      });
    }
  }

  // ---------- bootstrap ----------

  function connectSSE() {
    const es = new EventSource("/api/events");

    es.onopen = () => setConnState("live", "live");
    es.onerror = () => {
      // EventSource retries automatically; just reflect the gap in the UI.
      setConnState("connecting", "reconnecting…");
    };
    es.onmessage = (msg) => {
      try {
        handleRawEvent(JSON.parse(msg.data));
      } catch (_) {
        /* ignore malformed line */
      }
    };
    es.addEventListener("state", (msg) => {
      try {
        reconcile(JSON.parse(msg.data));
      } catch (_) {
        /* ignore malformed snapshot */
      }
    });
  }

  function init() {
    updateTrackEmptyState();
    updateResultsEmptyState();
    setConnState("connecting", "connecting…");

    fetch("/api/state")
      .then((r) => r.json())
      .then((snapshot) => {
        reconcile(snapshot);
        connectSSE();
      })
      .catch(() => {
        setConnState("down", "can't reach server");
        // Still try to open the stream — the HTTP server may come up shortly.
        connectSSE();
      });

    setInterval(tickElapsed, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
