// Opt-in contextual resurfacing for ordinary public webpages. The current
// page remains ephemeral: only a bounded context is sent to Orbit's local
// server and nothing here writes browsing history.
(function () {
  if (globalThis.__orbitPageContextLoaded) return;
  globalThis.__orbitPageContextLoaded = true;
  if (!OrbitPageContext.isSupportedUrl(location.href)) return;

  let enabled = false;
  let lastUrl = location.href;
  let lastFingerprint = "";
  let timer = null;

  function readableText() {
    const root = document.querySelector("article, main, [role='main']") || document.body;
    if (!root) return "";
    const forbidden = "form, input, textarea, select, option, button, [contenteditable='true'], script, style, noscript, [hidden], [aria-hidden='true']";
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const parts = [];
    let length = 0;
    while (walker.nextNode() && length < OrbitPageContext.MAX_CONTEXT_TEXT + 1000) {
      const node = walker.currentNode;
      if (node.parentElement?.closest(forbidden)) continue;
      const value = node.textContent?.replace(/\s+/g, " ").trim();
      if (!value) continue;
      parts.push(value);
      length += value.length + 1;
    }
    return parts.join(" ");
  }

  function description() {
    return document.querySelector(
      "meta[name='description'], meta[property='og:description']"
    )?.content || "";
  }

  async function analyze() {
    timer = null;
    if (!enabled || !OrbitPageContext.isSupportedUrl(location.href)) return;
    if (document.querySelector("input[type='password']")) return;
    const context = OrbitPageContext.buildContext({
      url: location.href,
      title: document.title,
      description: description(),
      text: readableText(),
    });
    if (!OrbitPageContext.isMeaningfulContext(context)) return;
    const nextFingerprint = OrbitPageContext.fingerprint(context);
    if (nextFingerprint === lastFingerprint) return;
    lastFingerprint = nextFingerprint;
    try {
      const response = await chrome.runtime.sendMessage({
        type: "PAGE_CONTEXT",
        fingerprint: nextFingerprint,
        context,
      });
      if (response?.retry_after_ms) {
        lastFingerprint = "";
        schedule(response.retry_after_ms);
      }
    } catch {
      // Browsing must remain unaffected when Orbit or its service worker is unavailable.
      lastFingerprint = "";
      schedule(60_000);
    }
  }

  function schedule(delay = 1400) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(analyze, delay);
  }

  chrome.storage.local.get({ resurfacingEnabled: false }).then((stored) => {
    enabled = stored.resurfacingEnabled === true;
    if (enabled) schedule();
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.resurfacingEnabled) return;
    enabled = changes.resurfacingEnabled.newValue === true;
    lastFingerprint = "";
    if (enabled) schedule(100);
    else if (timer) clearTimeout(timer);
  });

  // SPA-aware without observing or reacting to every DOM mutation.
  setInterval(() => {
    if (location.href === lastUrl) return;
    lastUrl = location.href;
    lastFingerprint = "";
    schedule();
  }, 1000);
})();
