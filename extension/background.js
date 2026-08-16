// background.js — FootNotes V2 Service Worker
// Receives normalized payloads from the content script bridge,
// POSTs them to the local FastAPI server, and manages the offline queue.

const FOOTNOTES_SERVER = "http://localhost:8000";
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;
const RESURFACE_SCRIPT_ID = "footnotes-contextual-resurfacing";
const RESURFACE_ORIGINS = ["http://*/*", "https://*/*"];
const resurfacedByTab = new Map();
const checkedPageByTab = new Map();

// ──────────────────────────────────────────────
// 1. EXTENSION LIFECYCLE
// ──────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  console.log("[FootNotes] V2 installed — fetch interception active.");
  setupContextMenus();
  restoreOfflineQueue();
  syncResurfacingRegistration().catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  restoreOfflineQueue();
  syncResurfacingRegistration().catch(() => {});
});

// Recall gets a full extension-owned tab rather than a narrow popup. This
// keeps keyboard navigation stable and gives excerpts enough room to scan.
chrome.commands.onCommand.addListener((command) => {
  if (command === "open-recall") {
    chrome.tabs.create({ url: chrome.runtime.getURL("recall.html") });
  }
});

chrome.runtime.onMessage.addListener((request) => {
  if (request.type === "OPEN_RECALL") {
    chrome.tabs.create({ url: chrome.runtime.getURL("recall.html") });
  }
});

// ──────────────────────────────────────────────
// 2. CONTEXT MENUS (YouTube, Articles, Any Page)
// ──────────────────────────────────────────────

function setupContextMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "footnotes-save-page",
      title: "Save to FootNotes",
      contexts: ["page", "selection", "link"],
    });
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "footnotes-save-page") return;

  const url = info.linkUrl || tab.url;
  const platform = extractPlatform(url);

  const payload = {
    // Was hardcoded to "article", which filed every saved YouTube video as an
    // article. The server re-derives this too, but sending the right value
    // keeps the payload honest on the wire.
    type: detectType(url, platform),
    source_url: url,
    source_platform: platform,
    author: "",
    author_handle: "",
    // Only send the tab title when we're saving the tab itself. For a
    // right-clicked link the tab title describes the wrong page entirely.
    title: info.linkUrl ? "" : tab.title || "",
    captured_at: new Date().toISOString(),
    published_at: null,
    content: "",
    selection: info.selectionText || null,
  };

  await sendToServer(payload, tab.id);
});

function detectType(url, platform) {
  if (platform === "youtube") return "youtube";
  if (platform === "x" && url.includes("/status/")) return "tweet";
  return "article";
}

// ──────────────────────────────────────────────
// 3. MESSAGE LISTENER — From Content Script
// ──────────────────────────────────────────────
// The content script bridge forwards intercepted
// bookmark payloads from the page's main world.

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "BOOKMARK_CAPTURED" && request.payload) {
    console.log("[FootNotes] Bookmark captured:", request.payload.source_url);
    sendToServer(request.payload, sender.tab?.id).then(() => {
      sendResponse({ status: "queued" });
    });
    return true; // Keep message channel open for async response
  }
  return false;
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "PAGE_CONTEXT" && request.context && sender.tab?.id != null) {
    handlePageContext(sender.tab.id, request.context, request.fingerprint)
      .then((state) => sendResponse({
        count: state?.entries?.length || 0,
        retry_after_ms: state?.retry_after_ms || 0,
      }))
      .catch(() => sendResponse({ count: 0, retry_after_ms: 60_000 }));
    return true;
  }
  if (request.type === "GET_RESURFACED") {
    chrome.tabs.query({ active: true, lastFocusedWindow: true }).then(([tab]) => {
      const state = tab?.id != null ? resurfacedByTab.get(tab.id) : null;
      sendResponse({ entries: state?.entries || [] });
    }).catch(() => sendResponse({ entries: [] }));
    return true;
  }
  if (request.type === "ENABLE_RESURFACING_CURRENT_TAB") {
    syncResurfacingRegistration(true).then(async () => {
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      if (tab?.id != null) {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ["page-context-state.js", "page-context.js"],
        });
      }
      sendResponse({ ok: true });
    }).catch(() => sendResponse({ ok: false }));
    return true;
  }
  return false;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes.resurfacingEnabled) return;
  syncResurfacingRegistration(changes.resurfacingEnabled.newValue === true).catch(() => {});
  if (changes.resurfacingEnabled.newValue === true) return;
  for (const tabId of resurfacedByTab.keys()) {
    chrome.action.setBadgeText({ tabId, text: "" });
    chrome.action.setTitle({ tabId, title: "FootNotes" });
  }
  resurfacedByTab.clear();
  checkedPageByTab.clear();
});

async function syncResurfacingRegistration(requestedState = null) {
  const enabled = requestedState == null
    ? (await chrome.storage.local.get({ resurfacingEnabled: false })).resurfacingEnabled === true
    : requestedState;
  const hasPermission = await chrome.permissions.contains({ origins: RESURFACE_ORIGINS });
  if (enabled && !hasPermission) {
    await chrome.storage.local.set({ resurfacingEnabled: false });
  }
  const registered = await chrome.scripting.getRegisteredContentScripts({
    ids: [RESURFACE_SCRIPT_ID],
  });
  if (enabled && hasPermission && registered.length === 0) {
    await chrome.scripting.registerContentScripts([{
      id: RESURFACE_SCRIPT_ID,
      matches: RESURFACE_ORIGINS,
      js: ["page-context-state.js", "page-context.js"],
      runAt: "document_idle",
      persistAcrossSessions: true,
    }]);
  } else if ((!enabled || !hasPermission) && registered.length > 0) {
    await chrome.scripting.unregisterContentScripts({ ids: [RESURFACE_SCRIPT_ID] });
  }
}

chrome.tabs.onRemoved.addListener((tabId) => {
  resurfacedByTab.delete(tabId);
  checkedPageByTab.delete(tabId);
});

// ──────────────────────────────────────────────
// 4. PLATFORM DETECTOR
// ──────────────────────────────────────────────

function extractPlatform(url) {
  if (!url) return "other";
  try {
    const hostname = new URL(url).hostname.toLowerCase();
    if (hostname.includes("youtube")) return "youtube";
    if (hostname.includes("x.com") || hostname.includes("twitter")) return "x";
    if (hostname.includes("medium")) return "medium";
    if (hostname.includes("substack")) return "substack";
    return "other";
  } catch {
    return "other";
  }
}

// ──────────────────────────────────────────────
// 5. SERVER COMMUNICATION
// ──────────────────────────────────────────────
// Sends normalized payload to the local FastAPI
// server. If the server is down, queues the
// payload in chrome.storage.local for retry.

async function sendToServer(payload, tabId = null) {
  console.log("[FootNotes] Sending to server:", payload.type, payload.source_url);

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const response = await fetch(`${FOOTNOTES_SERVER}/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(5000),
      });

      if (response.ok) {
        const result = await response.json();
        console.log("[FootNotes] Ingested successfully:", payload.source_url);
        await rememberSuccessfulCapture(payload, result);
        await flashBadge("ok", tabId);
        return result;
      }
    } catch (err) {
      console.warn(
        `[FootNotes] Attempt ${attempt}/${MAX_RETRIES} failed:`,
        err.message
      );
    }

    if (attempt < MAX_RETRIES) {
      await sleep(RETRY_DELAY_MS * attempt);
    }
  }

  // All retries exhausted — queue for offline retry
  console.warn("[FootNotes] Server unreachable. Queuing payload locally.");
  await queuePayload(payload);
  await showQueuedBadge();
}

// ──────────────────────────────────────────────
// 5b. BADGE — the only capture feedback the user gets
// ──────────────────────────────────────────────
// Without this, a capture that fails because the server isn't running is
// indistinguishable from one that worked: both are silent. A brief check on
// success, and a persistent count of unsent captures on failure.

async function flashBadge(kind, tabId = null) {
  const { queue = [] } = await chrome.storage.local.get("queue");
  if (queue.length) return; // don't stomp a pending-queue badge with a checkmark

  const target = Number.isInteger(tabId) ? { tabId } : {};
  await chrome.action.setBadgeBackgroundColor({ ...target, color: "#d85a30" });
  await chrome.action.setBadgeText({ ...target, text: "✓" });
  setTimeout(() => {
    if (Number.isInteger(tabId)) applyResurfaceBadge(tabId);
    else chrome.action.setBadgeText({ text: "" });
  }, 2000);
}

async function rememberSuccessfulCapture(payload, result) {
  const entryId = result?.entry_id || result?.existing_id;
  if (!entryId) return;
  await chrome.storage.local.set({
    recentCapture: {
      entry_id: entryId,
      title: payload.title || "",
      source_url: payload.source_url,
      user_note: result?.user_note || "",
      saved_at: new Date().toISOString(),
    },
  });
}

async function showQueuedBadge() {
  const { queue = [] } = await chrome.storage.local.get("queue");
  await chrome.action.setBadgeBackgroundColor({ color: "#ff6b6b" });
  await chrome.action.setBadgeText({ text: queue.length ? String(queue.length) : "" });
  for (const tabId of resurfacedByTab.keys()) {
    if (queue.length) {
      await chrome.action.setBadgeBackgroundColor({ tabId, color: "#ff6b6b" });
      await chrome.action.setBadgeText({ tabId, text: String(queue.length) });
    } else {
      await applyResurfaceBadge(tabId);
    }
  }
}

async function applyResurfaceBadge(tabId) {
  const { queue = [] } = await chrome.storage.local.get("queue");
  if (queue.length) {
    await chrome.action.setBadgeBackgroundColor({ tabId, color: "#ff6b6b" });
    await chrome.action.setBadgeText({ tabId, text: String(queue.length) });
    return;
  }
  const state = resurfacedByTab.get(tabId);
  const count = state?.entries?.length || 0;
  await chrome.action.setBadgeBackgroundColor({ tabId, color: "#d85a30" });
  await chrome.action.setBadgeText({ tabId, text: count ? String(count) : "" });
  await chrome.action.setTitle({
    tabId,
    title: count ? `FootNotes · ${count} related ${count === 1 ? "memory" : "memories"}` : "FootNotes",
  });
}

async function handlePageContext(tabId, context, fingerprint) {
  const { resurfacingEnabled = false } = await chrome.storage.local.get("resurfacingEnabled");
  if (!resurfacingEnabled) return null;
  if (fingerprint && checkedPageByTab.get(tabId) === fingerprint) {
    return resurfacedByTab.get(tabId) || null;
  }
  checkedPageByTab.set(tabId, fingerprint || context.url);
  try {
    const response = await fetch(`${FOOTNOTES_SERVER}/resurface?limit=3`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(context),
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error(String(response.status));
    const payload = await response.json();
    const state = {
      page_url: context.url,
      entries: payload.entries || [],
      retry_after_ms: payload.embedding_available ? 0 : 300_000,
    };
    resurfacedByTab.set(tabId, state);
    if (state.retry_after_ms) checkedPageByTab.delete(tabId);
    await applyResurfaceBadge(tabId);
    return state;
  } catch {
    resurfacedByTab.delete(tabId);
    checkedPageByTab.delete(tabId);
    await applyResurfaceBadge(tabId);
    return { entries: [], retry_after_ms: 60_000 };
  }
}

// ──────────────────────────────────────────────
// 6. OFFLINE QUEUE
// ──────────────────────────────────────────────
// Stores failed payloads in chrome.storage.local.
// Restored on extension startup or when the
// server becomes reachable again.

async function queuePayload(payload) {
  const { queue = [] } = await chrome.storage.local.get("queue");
  queue.push({ payload, queued_at: new Date().toISOString() });
  await chrome.storage.local.set({ queue });
  console.log(`[FootNotes] Queued. Queue size: ${queue.length}`);
}

async function restoreOfflineQueue() {
  const { queue = [] } = await chrome.storage.local.get("queue");
  if (queue.length === 0) return;

  console.log(`[FootNotes] Restoring ${queue.length} queued payloads.`);
  const stillQueued = [];

  for (const item of queue) {
    try {
      const response = await fetch(`${FOOTNOTES_SERVER}/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(item.payload),
        signal: AbortSignal.timeout(5000),
      });

      if (response.ok) {
        const result = await response.json();
        console.log("[FootNotes] Flushed queued item:", item.payload.source_url);
        await rememberSuccessfulCapture(item.payload, result);
      } else {
        stillQueued.push(item);
      }
    } catch {
      stillQueued.push(item);
    }
  }

  await chrome.storage.local.set({ queue: stillQueued });
  console.log(`[FootNotes] Queue remaining: ${stillQueued.length}`);
  await showQueuedBadge();
}

// ──────────────────────────────────────────────
// 7. UTILITIES
// ──────────────────────────────────────────────

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
