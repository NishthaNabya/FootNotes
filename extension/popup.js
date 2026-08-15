// popup.js — Shows whether the local server is reachable and what's in the vault.
//
// This exists because every other signal Orbit produced was a console.log. If
// the Python server wasn't running, bookmarking a tweet did nothing visible
// anywhere, and there was no way to tell a broken install from an idle one.

const ORBIT_SERVER = "http://localhost:8000";

const el = (id) => document.getElementById(id);

el("openRecall").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "OPEN_RECALL" });
  window.close();
});

function setStatus(kind, text, hintHTML = "") {
  el("dot").className = `dot ${kind}`;
  el("statusText").textContent = text;
  el("hint").innerHTML = hintHTML;
}

async function queuedCount() {
  const { queue = [] } = await chrome.storage.local.get("queue");
  return queue.length;
}

let recentCapture = null;

function sourceLabel(entry) {
  if (entry.source_platform === "x") return "X";
  if (entry.source_platform === "youtube") return "YouTube";
  try {
    return new URL(entry.source_url).hostname.replace(/^www\./, "");
  } catch {
    return "Web";
  }
}

function renderResurfaced(entries) {
  const section = el("resurfaced");
  section.replaceChildren();
  section.classList.toggle("visible", entries.length > 0);
  if (!entries.length) return;
  const heading = document.createElement("div");
  heading.className = "resurfaced-title";
  heading.textContent = `Orbit · ${entries.length}`;
  section.append(heading);
  entries.forEach((entry) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "resurfaced-item";
    const title = document.createElement("div");
    title.className = "resurfaced-name";
    title.textContent = entry.title || entry.source_url || "Untitled memory";
    const meta = document.createElement("div");
    meta.className = "resurfaced-meta";
    meta.textContent = [sourceLabel(entry), entry.author_handle || entry.author || ""]
      .filter(Boolean).join(" · ");
    item.append(title, meta);
    const previewText = entry.user_note || entry.excerpt || "";
    if (previewText) {
      const preview = document.createElement("div");
      preview.className = "resurfaced-preview";
      preview.textContent = entry.user_note ? `Your thought: ${previewText}` : previewText;
      item.append(preview);
    }
    item.addEventListener("click", () => {
      try {
        const url = new URL(entry.source_url);
        if (url.protocol === "http:" || url.protocol === "https:") chrome.tabs.create({ url: url.href });
      } catch {}
    });
    section.append(item);
  });
}

async function refreshResurfaced() {
  try {
    const response = await chrome.runtime.sendMessage({ type: "GET_RESURFACED" });
    renderResurfaced(response?.entries || []);
  } catch {
    renderResurfaced([]);
  }
}

async function loadResurfacingSetting() {
  const stored = await chrome.storage.local.get({ resurfacingEnabled: false });
  const permitted = await chrome.permissions.contains({
    origins: ["http://*/*", "https://*/*"],
  });
  const enabled = stored.resurfacingEnabled === true && permitted;
  el("resurfacingEnabled").checked = enabled;
  if (stored.resurfacingEnabled && !permitted) {
    await chrome.storage.local.set({ resurfacingEnabled: false });
  }
  if (enabled) await refreshResurfaced();
}

el("resurfacingEnabled").addEventListener("change", async (event) => {
  let enabled = event.target.checked === true;
  if (enabled) {
    enabled = await chrome.permissions.request({
      origins: ["http://*/*", "https://*/*"],
    });
    event.target.checked = enabled;
  }
  await chrome.storage.local.set({ resurfacingEnabled: enabled });
  if (!enabled) {
    await chrome.permissions.remove({ origins: ["http://*/*", "https://*/*"] });
    renderResurfaced([]);
    return;
  }
  await chrome.runtime.sendMessage({ type: "ENABLE_RESURFACING_CURRENT_TAB" });
  await refreshResurfaced();
  setTimeout(refreshResurfaced, 1800);
});

async function loadRecentCapture() {
  const stored = await chrome.storage.local.get("recentCapture");
  const capture = stored.recentCapture;
  if (!capture?.entry_id || !capture.saved_at) return;
  const age = Date.now() - new Date(capture.saved_at).getTime();
  if (!Number.isFinite(age) || age > 30 * 60 * 1000) return;

  recentCapture = capture;
  el("savedTitle").textContent = capture.title || capture.source_url || "Saved memory";
  el("noteInput").value = capture.user_note || "";
  el("captureNote").classList.add("saved");
}

el("noteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!recentCapture) return;
  const note = el("noteInput").value.trim();
  const button = el("noteSave");
  button.disabled = true;
  el("noteMessage").textContent = "Saving…";
  try {
    const response = await fetch(
      `${ORBIT_SERVER}/entries/${encodeURIComponent(recentCapture.entry_id)}/note`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_note: note }),
        signal: AbortSignal.timeout(5000),
      }
    );
    if (!response.ok) throw new Error(String(response.status));
    recentCapture = { ...recentCapture, user_note: note };
    await chrome.storage.local.set({ recentCapture });
    el("noteMessage").textContent = note ? "Thought saved." : "Thought cleared.";
  } catch {
    el("noteMessage").textContent = "Couldn’t save. Is Orbit running?";
  } finally {
    button.disabled = false;
  }
});

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
      "Orbit isn’t running",
      `Open the Orbit app, then try again.` +
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
  const providerHealth = health.provider_health || {};
  const providerName = health.intelligence_provider || "none";
  if (providerHealth.enrichment_ready) {
    enrich.textContent = "active";
    enrich.className = "";
  } else if (health.provider_configured) {
    enrich.textContent = "unavailable";
    enrich.className = "muted";
  } else {
    enrich.textContent = "disabled";
    enrich.className = "muted";
  }
  const embeddings = health.embeddings || {};
  el("semantic").textContent = health.embedding_available
    ? (embeddings.pending ? `${embeddings.ready || 0} ready · ${embeddings.pending} pending` : `${embeddings.ready || 0} ready`)
    : "unavailable";
  const storage = health.vault_dir || "—";
  el("storage").textContent = storage.split("/").filter(Boolean).slice(-2).join("/");
  el("storage").title = storage;

  if (queued) {
    el("queueRow").style.display = "flex";
    el("cQueue").textContent = queued;
  }

  el("stats").style.display = "block";

  if (!providerHealth.enrichment_ready) {
    setStatus(
      "warn",
      "Connected — local features ready",
      `Captures and exact Recall work. Open the Orbit app to check ${providerName === "ollama" ? "Local AI" : "your intelligence mode"}.`
    );
  } else {
    setStatus("ok", "Connected", "Bookmark on X, or right-click → Save to Orbit.");
  }
}

refresh();
loadRecentCapture();
loadResurfacingSetting();
