const $ = (id) => document.getElementById(id);
let state;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Orbit could not complete that action.");
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
    const health = await api("/orbit-api/ollama-status");
    state.textContent = health.message;
    if (!health.runtime_available) {
      actions.classList.remove("hidden");
      $("ollamaGuidance").innerHTML = 'Install the free <a href="https://ollama.com/download/mac" target="_blank" rel="noreferrer">Ollama app for Mac</a>. Orbit will not install it for you. The two model downloads require about <b>2 GB</b> total.';
    } else if (health.missing_models?.length) {
      actions.classList.remove("hidden");
      $("ollamaGuidance").textContent = "Download the missing local models. Orbit will not start these downloads for you:";
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

function showStatus(data) {
  $("loading").classList.add("hidden"); $("setup").classList.add("hidden"); $("status").classList.remove("hidden");
  $("statusVault").textContent = data.vault_path;
  const names = { ollama:"Local AI · Ollama", gemini:"Gemini", none:"No AI" };
  const health = data.provider_health || {};
  $("statusProvider").textContent = names[data.provider] || data.provider;
  $("statusProviderHealth").textContent = health.message || (data.provider_available ? "Connected" : "Unavailable — local features still work");
  if (data.provider === "ollama" && health.missing_models?.length) {
    $("statusProviderHealth").textContent += ` Download: ${health.missing_models.join(" and ")}.`;
  }
  const semantic = data.semantic_memory || {};
  $("statusSemantic").textContent = data.provider === "none" ? "Unavailable in No AI mode" : semantic.pending ? `${semantic.ready || 0} ready · ${semantic.pending} scheduled gradually` : `${semantic.ready || 0} memories ready`;
  $("logPath").textContent = data.log_path || "~/Library/Logs/Orbit/orbit.log";
}

async function init() {
  state = await api("/orbit-api/setup");
  if (state.setup_complete) return showStatus(state);
  showSetup(state);
  checkOllama();
}

document.querySelectorAll("input[name=provider]").forEach((input) => input.addEventListener("change", providerChanged));
$("checkOllama").addEventListener("click", checkOllama);
$("chooseFolder").addEventListener("click", async () => {
  try { const result = await api("/orbit-api/choose-folder", { method:"POST", body:"{}" }); if (!result.cancelled) $("vaultPath").value = result.vault_path; } catch (error) { $("setupMessage").textContent = error.message; }
});
$("finish").addEventListener("click", async () => {
  const button = $("finish"), message = $("setupMessage"); button.disabled = true; message.className="message"; message.textContent="Saving locally…";
  try {
    const provider = document.querySelector("input[name=provider]:checked").value;
    await api("/orbit-api/setup", { method:"POST", body:JSON.stringify({ vault_path:$("vaultPath").value, provider, api_key:"" }) });
    state = await api("/orbit-api/setup"); showStatus(state);
  } catch (error) { message.className="message error"; message.textContent=error.message; } finally { button.disabled=false; }
});
$("stop").addEventListener("click", async () => { $("stop").disabled=true; $("stop").textContent="Stopping…"; try { await api("/orbit-api/stop", {method:"POST",body:"{}"}); } catch {} });
$("changeProvider").addEventListener("click", () => showSetup(state));
init().catch((error) => { $("loading").innerHTML=`<p>${error.message}</p>`; });
