// popup.js — Shows whether the local server is reachable and what's in the vault.
//
// This exists because every other signal FootNotes produced was a console.log. If
// the Python server wasn't running, bookmarking a tweet did nothing visible
// anywhere, and there was no way to tell a broken install from an idle one.

const FOOTNOTES_SERVER = "http://localhost:8000";

const el = (id) => document.getElementById(id);

el("openRecall").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "OPEN_RECALL" });
  window.close();
});

function setStatus(kind, text, hintHTML = "") {
  el("shell").className = `shell status-${kind}`;
  el("connectionState").className = `connection-state status-${kind}`;
  el("dot").className = `dot ${kind}`;
  el("statusText").textContent = kind === "bad" ? "offline" : kind === "warn" ? "limited" : "connected";
  el("statusText").title = text;
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

function sourceHost(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "").replace(/^twitter\.com$/, "x.com");
  } catch {
    return "web";
  }
}

function relativeSavedTime(savedAt) {
  const elapsed = Date.now() - new Date(savedAt).getTime();
  if (!Number.isFinite(elapsed) || elapsed < 0) return "just now";
  const minutes = Math.floor(elapsed / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function renderResurfaced(entries) {
  const section = el("resurfaced");
  section.replaceChildren();
  section.classList.toggle("visible", entries.length > 0);
  if (!entries.length) return;
  const heading = document.createElement("div");
  heading.className = "resurfaced-title";
  heading.textContent = `FootNotes · ${entries.length}`;
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
  el("savedMeta").textContent = `${sourceHost(capture.source_url)}  ·  ${relativeSavedTime(capture.saved_at)}`;
  const icon = el("savedSourceIcon");
  const source = sourceLabel(capture);
  icon.className = `source-icon ${source === "YouTube" ? "youtube" : source === "X" ? "x" : "web"}`;
  icon.textContent = source === "X" ? "𝕏" : source === "YouTube" ? "▶" : "↗";
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
      `${FOOTNOTES_SERVER}/entries/${encodeURIComponent(recentCapture.entry_id)}/note`,
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
    el("noteMessage").textContent = "Couldn’t save. Is FootNotes running?";
  } finally {
    button.disabled = false;
  }
});

async function refresh() {
  const queued = await queuedCount();

  let health;
  try {
    const res = await fetch(`${FOOTNOTES_SERVER}/health`, {
      signal: AbortSignal.timeout(2500),
    });
    if (!res.ok) throw new Error(String(res.status));
    health = await res.json();
  } catch {
    setStatus(
      "bad",
      "FootNotes isn’t running",
      `Open the FootNotes app, then try again.` +
        (queued
          ? `<br><br>${queued} capture${queued === 1 ? "" : "s"} waiting — they'll send automatically once it's up.`
          : "")
    );
    return;
  }

  const counts = health.entry_counts || {};
  const sourceCounts = {
    tweets: counts.tweets || 0,
    articles: counts.articles || 0,
    youtube: counts.youtube || 0,
  };
  el("cTweets").textContent = sourceCounts.tweets;
  el("cArticles").textContent = sourceCounts.articles;
  el("cYoutube").textContent = sourceCounts.youtube;
  el("cTotal").textContent = health.total_entries || 0;
  el("cSources").textContent = Object.values(sourceCounts).filter(Boolean).length;
  ["Articles", "Tweets", "Youtube"].forEach((name) => {
    const count = sourceCounts[name.toLowerCase()];
    el(`bar${name}`).style.flexGrow = String(count);
    el(`bar${name}`).style.display = count ? "block" : "none";
    el(`chip${name}`).classList.toggle("disabled", count === 0);
  });

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
    ? `${embeddings.ready || 0} embedded${embeddings.pending ? ` · ${embeddings.pending} pending` : ""}`
    : "semantic off";
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
      `Captures and exact Recall work. Open the FootNotes app to check ${providerName === "ollama" ? "Local AI" : "your intelligence mode"}.`
    );
  } else {
    setStatus("ok", "Connected", "Bookmark on X, or right-click → Save to FootNotes.");
  }
}

refresh();
loadRecentCapture();
loadResurfacingSetting();
