const $ = (id) => document.getElementById(id);
let state;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "FootNotes could not complete that action.");
  return body;
}

function providerChanged() {
  const provider = document.querySelector("input[name=provider]:checked").value;
  $("ollamaHelp").classList.toggle("hidden", provider !== "ollama");
}

async function checkOllama() {
  const state = $("ollamaState"), actions = $("ollamaActions"), commands = $("ollamaCommands");
  state.textContent = "Checking Local AI on this Mac…"; actions.classList.add("hidden"); commands.replaceChildren();
  try {
    const health = await api("/footnotes-api/ollama-status");
    state.textContent = health.message;
    if (!health.runtime_available) {
      actions.classList.remove("hidden");
      $("ollamaGuidance").innerHTML = 'Install the free <a href="https://ollama.com/download/mac" target="_blank" rel="noreferrer">Ollama app for Mac</a>. FootNotes will not install it for you. The two model downloads require about <b>2 GB</b> total.';
    } else if (health.missing_models?.length) {
      actions.classList.remove("hidden");
      $("ollamaGuidance").textContent = "Download the missing local models. FootNotes will not start these downloads for you:";
      const sizes = { embeddinggemma:"about 622 MB", "qwen3:1.7b":"about 1.4 GB" };
      health.missing_models.forEach((model) => {
        const line = document.createElement("code"); line.textContent = `ollama pull ${model}  ·  ${sizes[model] || "size shown by Ollama"}`; commands.append(line);
      });
    }
  } catch (error) { state.textContent = error.message; }
}

function showSetup(data = {}) {
  $("loading").classList.add("hidden"); $("status").classList.add("hidden"); $("setup").classList.remove("hidden");
  $("vaultPath").value = data.vault_path || $("vaultPath").value;
  const selected = document.querySelector(`input[name=provider][value="${data.provider || "ollama"}"]`);
  if (selected) selected.checked = true;
  providerChanged();
}

function compactPath(path) {
  return String(path || "").replace(/^\/Users\/[^/]+/, "~");
}

function setServiceState(running) {
  $("headerState").textContent = running ? "running" : "stopped";
  document.querySelector(".service-summary").classList.toggle("stopped", !running);
  $("statusTitle").textContent = `FootNotes is ${running ? "running" : "stopped"}.`;
  if (!running) {
    $("statusLead").textContent = "Your memories remain safely stored on this machine. Open FootNotes again whenever you want to capture or recall something.";
  }
}

function showStatus(data) {
  $("loading").classList.add("hidden"); $("setup").classList.add("hidden"); $("status").classList.remove("hidden");
  $("version").textContent = `v${data.version || "0.1.0"}`;
  setServiceState(true);
  $("statusVault").textContent = compactPath(data.vault_path);
  const names = { ollama:"Local AI · Ollama", none:"No AI" };
  const health = data.provider_health || {};
  $("statusProvider").textContent = names[data.provider] || data.provider;
  const providerReady = Boolean(health.embedding_ready && health.enrichment_ready);
  $("statusProviderHealth").textContent = providerReady ? "ready" : data.provider === "none" ? "keyword mode" : "local features ready";
  $("statusProviderHealth").classList.toggle("unavailable", !providerReady);
  if (data.provider === "ollama" && health.missing_models?.length) {
    $("statusProviderHealth").textContent = "setup incomplete";
  }
  const semantic = data.semantic_memory || {};
  $("statusSemantic").replaceChildren();
  const count = document.createElement("span");
  count.textContent = data.provider === "none" ? "Keyword Recall ready" : `${semantic.ready || 0} ready`;
  $("statusSemantic").append(count);
  if (data.provider !== "none") {
    const detail = document.createElement("span");
    detail.className = "semantic-detail";
    detail.textContent = semantic.pending ? ` · ${semantic.pending} indexing` : " · semantic index built";
    $("statusSemantic").append(detail);
  }
  $("logPath").textContent = data.log_path || "~/Library/Logs/FootNotes/footnotes.log";
}

async function init() {
  state = await api("/footnotes-api/setup");
  if (state.setup_complete) return showStatus(state);
  showSetup(state);
  checkOllama();
}

document.querySelectorAll("input[name=provider]").forEach((input) => input.addEventListener("change", providerChanged));
$("checkOllama").addEventListener("click", checkOllama);
$("chooseFolder").addEventListener("click", async () => {
  try { const result = await api("/footnotes-api/choose-folder", { method:"POST", body:"{}" }); if (!result.cancelled) $("vaultPath").value = result.vault_path; } catch (error) { $("setupMessage").textContent = error.message; }
});
$("finish").addEventListener("click", async () => {
  const button = $("finish"), message = $("setupMessage"); button.disabled = true; message.className="message"; message.textContent="Saving locally…";
  try {
    const provider = document.querySelector("input[name=provider]:checked").value;
    await api("/footnotes-api/setup", { method:"POST", body:JSON.stringify({ vault_path:$("vaultPath").value, provider }) });
    state = await api("/footnotes-api/setup"); showStatus(state);
  } catch (error) { message.className="message error"; message.textContent=error.message; } finally { button.disabled=false; }
});
$("stop").addEventListener("click", async (event) => {
  // Browser/session restoration and automation must never terminate the local
  // service. Only a genuine user activation may reach the stop endpoint.
  if (!event.isTrusted) return;
  $("stop").disabled = true; $("stop").textContent = "Stopping…";
  try {
    await api("/footnotes-api/stop", {method:"POST",body:"{}"});
    setServiceState(false); $("stop").textContent = "Stopped";
  } catch { $("stop").disabled = false; $("stop").textContent = "Stop FootNotes"; }
});
$("changeProvider").addEventListener("click", () => showSetup(state));
$("logAction").addEventListener("click", async () => {
  const button = $("logAction");
  try {
    await api("/footnotes-api/open-log", { method:"POST", body:"{}" });
    button.textContent = "log opened";
    setTimeout(() => { button.textContent = "open log ↗"; }, 1800);
  } catch { button.title = $("logPath").textContent; }
});
init().catch((error) => { $("loading").innerHTML=`<p>${error.message}</p>`; });
