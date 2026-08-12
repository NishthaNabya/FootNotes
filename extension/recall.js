// Focused Recall UI. All ranking stays in the local FastAPI server.
const ORBIT_SERVER = "http://localhost:8000";
const $ = (id) => document.getElementById(id);
const queryInput = $("query");
const resultsElement = $("results");
const stateElement = $("state");
const searchingElement = $("searching");
const connectionElement = $("connection");
const relatedElement = $("related");

let results = [];
let selectedIndex = -1;
let serverOnline = false;
let relatedTimer = null;
let relatedController = null;
let relatedSequence = 0;
const relatedCache = new Map();

function setConnection(online) {
  serverOnline = online;
  connectionElement.textContent = online ? "Local service connected" : "Local service offline";
  connectionElement.className = `connection ${online ? "online" : "offline"}`;
}

function setState(kind, message) {
  stateElement.className = `state ${kind || ""}`;
  stateElement.replaceChildren();
  if (kind === "offline") {
    const strong = document.createElement("strong");
    strong.textContent = "Orbit’s local service isn’t running.";
    const detail = document.createElement("span");
    detail.append("Start it from the Orbit folder: ");
    const code = document.createElement("code");
    code.textContent = "uvicorn server:app --port 8000";
    detail.append(code);
    stateElement.append(strong, detail);
  } else {
    const paragraph = document.createElement("p");
    paragraph.textContent = message;
    stateElement.append(paragraph);
  }
}

function metaText(entry) {
  return [
    OrbitRecall.sourceLabel(entry),
    entry.author_handle || entry.author || "",
    OrbitRecall.savedDate(entry.captured_at),
  ].filter(Boolean).join(" · ");
}

function openResult(entry) {
  const url = OrbitRecall.resultUrl(entry);
  if (url) chrome.tabs.create({ url });
}

function hideRelated() {
  relatedElement.hidden = true;
  relatedElement.replaceChildren();
}

function renderRelated(entries) {
  if (!entries.length) {
    hideRelated();
    return;
  }
  const heading = document.createElement("h2");
  heading.className = "related-title";
  heading.textContent = "Related memories";
  const list = document.createElement("div");
  list.className = "related-list";
  entries.forEach((entry) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "related-item";
    const title = document.createElement("h3");
    title.className = "related-item-title";
    title.textContent = entry.title || entry.source_url || "Untitled memory";
    const meta = document.createElement("div");
    meta.className = "related-item-meta";
    meta.textContent = metaText(entry);
    item.append(title, meta);
    const previewText = OrbitRecall.relatedPreview(entry);
    if (previewText) {
      const preview = document.createElement("p");
      preview.className = `related-item-preview${entry.user_note ? " note" : ""}`;
      preview.textContent = previewText;
      item.append(preview);
    }
    item.addEventListener("click", () => openResult(entry));
    list.append(item);
  });
  relatedElement.replaceChildren(heading, list);
  relatedElement.hidden = false;
}

function scheduleRelated(entry) {
  relatedSequence += 1;
  const current = relatedSequence;
  if (relatedTimer) clearTimeout(relatedTimer);
  relatedTimer = null;
  if (relatedController) relatedController.abort();
  relatedController = null;
  hideRelated();
  if (!entry?.id) return;

  const cached = relatedCache.get(entry.id);
  if (cached) {
    renderRelated(cached);
    return;
  }
  relatedTimer = setTimeout(async () => {
    relatedController = new AbortController();
    try {
      const response = await fetch(
        `${ORBIT_SERVER}/entries/${encodeURIComponent(entry.id)}/related?limit=3`,
        { signal: relatedController.signal }
      );
      if (!response.ok) throw new Error(String(response.status));
      const payload = await response.json();
      const related = payload.entries || [];
      relatedCache.set(entry.id, related);
      if (current === relatedSequence) renderRelated(related);
    } catch (error) {
      if (error?.name !== "AbortError" && current === relatedSequence) hideRelated();
    }
  }, 160);
}

function renderResults(nextResults, { initial = false } = {}) {
  results = nextResults;
  selectedIndex = results.length ? 0 : -1;
  resultsElement.replaceChildren();
  searchingElement.textContent = "";

  if (initial) {
    scheduleRelated(null);
    setState("", "Type anything you remember.");
    return;
  }
  if (!results.length) {
    scheduleRelated(null);
    setState("", "No memories found. Try another detail you remember.");
    return;
  }

  setState("", `${results.length} ${results.length === 1 ? "memory" : "memories"}`);
  stateElement.firstElementChild.className = "count";
  results.forEach((entry, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `result${index === selectedIndex ? " selected" : ""}`;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(index === selectedIndex));

    const title = document.createElement("h2");
    title.className = "result-title";
    title.textContent = entry.title || entry.source_url || "Untitled memory";
    const meta = document.createElement("div");
    meta.className = "result-meta";
    meta.textContent = metaText(entry);
    button.append(title, meta);

    if (entry.user_note) {
      const note = document.createElement("p");
      note.className = "result-note";
      const noteLabel = document.createElement("span");
      noteLabel.className = "result-note-label";
      noteLabel.textContent = "Your thought";
      note.append(noteLabel, entry.user_note);
      button.append(note);
    }

    if (entry.excerpt) {
      const excerpt = document.createElement("p");
      excerpt.className = "result-excerpt";
      excerpt.textContent = entry.excerpt;
      button.append(excerpt);
    }
    const match = OrbitRecall.matchLabel(entry);
    if (match) {
      const reason = document.createElement("p");
      reason.className = "result-match";
      reason.textContent = match;
      button.append(reason);
    }
    button.addEventListener("mouseenter", () => selectResult(index, false));
    button.addEventListener("click", () => openResult(entry));
    resultsElement.append(button);
  });
  scheduleRelated(results[selectedIndex]);
}

function selectResult(index, scroll = true) {
  selectedIndex = index;
  [...resultsElement.children].forEach((element, itemIndex) => {
    const selected = itemIndex === selectedIndex;
    element.classList.toggle("selected", selected);
    element.setAttribute("aria-selected", String(selected));
  });
  if (scroll && resultsElement.children[index]) {
    resultsElement.children[index].scrollIntoView({ block: "nearest" });
  }
  scheduleRelated(results[index]);
}

async function search(query, signal) {
  const url = new URL(`${ORBIT_SERVER}/search`);
  url.searchParams.set("q", query);
  url.searchParams.set("limit", "8");
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Orbit returned ${response.status}`);
  const payload = await response.json();
  setConnection(true);
  return payload.entries || [];
}

const runner = OrbitRecall.createSearchRunner({
  search,
  onSearching() {
    searchingElement.textContent = "Searching…";
    scheduleRelated(null);
  },
  onResults: renderResults,
  onError() {
    results = [];
    selectedIndex = -1;
    resultsElement.replaceChildren();
    searchingElement.textContent = "";
    scheduleRelated(null);
    setConnection(false);
    setState("offline");
  },
});

queryInput.addEventListener("input", () => runner.schedule(queryInput.value));
queryInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const direction = event.key === "ArrowDown" ? 1 : -1;
    selectResult(OrbitRecall.moveSelection(selectedIndex, direction, results.length));
  } else if (event.key === "Enter" && selectedIndex >= 0) {
    event.preventDefault();
    openResult(results[selectedIndex]);
  } else if (event.key === "Escape") {
    event.preventDefault();
    if (queryInput.value) {
      queryInput.value = "";
      runner.schedule("");
    }
  }
});

async function checkHealth() {
  try {
    const response = await fetch(`${ORBIT_SERVER}/health`, {
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) throw new Error(String(response.status));
    await response.json();
    setConnection(true);
  } catch {
    setConnection(false);
    setState("offline");
  }
}

window.addEventListener("pagehide", () => {
  runner.cancel();
  scheduleRelated(null);
});
queryInput.focus();
checkHealth();
