// popup.js — Shows whether the local server is reachable and what's in the vault.
//
// This exists because every other signal Orbit produced was a console.log. If
// the Python server wasn't running, bookmarking a tweet did nothing visible
// anywhere, and there was no way to tell a broken install from an idle one.

const ORBIT_SERVER = "http://localhost:8000";

const el = (id) => document.getElementById(id);

function setStatus(kind, text, hintHTML = "") {
  el("dot").className = `dot ${kind}`;
  el("statusText").textContent = text;
  el("hint").innerHTML = hintHTML;
}

async function queuedCount() {
  const { queue = [] } = await chrome.storage.local.get("queue");
  return queue.length;
}

async function refresh() {
  const queued = await queuedCount();

  let health;
  try {
    const res = await fetch(`${ORBIT_SERVER}/health`, {
      signal: AbortSignal.timeout(2500),
    });
    if (!res.ok) throw new Error(String(res.status));
    health = await res.json();
  } catch {
    setStatus(
      "bad",
      "Server not running",
      `Start it from the Orbit folder:<br><code>uvicorn server:app --port 8000</code>` +
        (queued
          ? `<br><br>${queued} capture${queued === 1 ? "" : "s"} waiting — they'll send automatically once it's up.`
          : "")
    );
    return;
  }

  const counts = health.entry_counts || {};
  el("cTweets").textContent = counts.tweets || 0;
  el("cArticles").textContent = counts.articles || 0;
  el("cYoutube").textContent = counts.youtube || 0;
  el("cTotal").textContent = health.total_entries || 0;

  const enrich = el("enrich");
  if (health.gemini_available) {
    enrich.textContent = "active";
    enrich.className = "";
  } else {
    enrich.textContent = "off — no API key";
    enrich.className = "muted";
  }

  if (queued) {
    el("queueRow").style.display = "flex";
    el("cQueue").textContent = queued;
  }

  el("stats").style.display = "block";

  if (!health.gemini_available) {
    setStatus(
      "warn",
      "Connected — saving without tags",
      `Captures are saved, but not tagged or summarized. Add a key to <code>.env</code> and restart the server.`
    );
  } else {
    setStatus("ok", "Connected", "Bookmark on X, or right-click → Save to Orbit.");
  }
}

refresh();
