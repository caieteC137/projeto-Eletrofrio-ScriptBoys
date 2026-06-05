/* Central de Automatizacao - Eletrofrio
 *
 * Logica client-side da aba "Central de Automatizacao".
 * Hoje a aba expoe apenas os kill switches que controlam
 *   - main.py        (envio automatico de notificacoes)
 *   - bot_polling.py (respostas automaticas do bot)
 *
 * O estado dos flags fica em data/automation_flags.json e e
 * lido a cada iteracao do loop dos servicos. A propagacao
 * leva no maximo 1 ciclo (60s no main, 5s no bot).
 *
 * Auto-refresh: a cada AUTO_REFRESH_MS a aba recarrega o estado
 * dos kill switches.
 */

const AUTO_REFRESH_MS = 20000;
let autoTimer = null;

const aels = {
  // Kill switches (pausar/retomar main.py e bot_polling.py)
  btnToggleMain: document.getElementById("btn-toggle-main"),
  btnToggleBot:  document.getElementById("btn-toggle-bot"),
  ksMainTile:    document.getElementById("ks-main-tile"),
  ksBotTile:     document.getElementById("ks-bot-tile"),
  ksMainDot:     document.getElementById("ks-main-dot"),
  ksBotDot:      document.getElementById("ks-bot-dot"),
  ksMainText:    document.getElementById("ks-main-text"),
  ksBotText:     document.getElementById("ks-bot-text"),
  ksMainHelp:    document.getElementById("ks-main-help"),
  ksBotHelp:     document.getElementById("ks-bot-help"),
  ksResult:      document.getElementById("ks-result"),
};

/* === Helpers === */
function showResult(el, ok, payload) {
  el.hidden = false;
  el.classList.remove("ok", "error");
  el.classList.add(ok ? "ok" : "error");
  const text = typeof payload === "string"
    ? payload
    : JSON.stringify(payload, null, 2);
  el.textContent = text;
}

function hideResult(el) {
  el.hidden = true;
  el.textContent = "";
  el.classList.remove("ok", "error");
}

async function apiGet(url) {
  const r = await fetch(url);
  return await r.json();
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : null,
  });
  return await r.json();
}

/* Modal de confirmacao custom (substitui o confirm() nativo do browser).
   Uso: const ok = await askConfirm({ title, body, confirmLabel });
   Retorna Promise<boolean>: true = confirmou, false = cancelou. */
function askConfirm({
  title = "Confirmação",
  body = "",
  icon = "fa-solid fa-circle-question",
  confirmLabel = "Confirmar",
  cancelLabel = "Cancelar",
} = {}) {
  return new Promise((resolve) => {
    const overlay    = document.getElementById("modal-confirm");
    const titleEl    = document.getElementById("modal-confirm-title");
    const bodyEl     = document.getElementById("modal-confirm-body");
    const iconEl     = document.getElementById("modal-confirm-icon").querySelector("i");
    const btnOk      = document.getElementById("modal-confirm-ok");
    const btnCancel  = document.getElementById("modal-confirm-cancel");
    const btnClose   = document.getElementById("modal-confirm-close");

    titleEl.textContent = title;
    bodyEl.textContent = body;
    iconEl.className = icon;
    btnOk.textContent = confirmLabel;
    btnCancel.textContent = cancelLabel;

    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      overlay.hidden = true;
      btnOk.removeEventListener("click", onOk);
      btnCancel.removeEventListener("click", onCancel);
      btnClose.removeEventListener("click", onCancel);
      overlay.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKey);
      resolve(result);
    };
    const onOk      = () => finish(true);
    const onCancel  = () => finish(false);
    const onBackdrop = (e) => { if (e.target === overlay) onCancel(); };
    const onKey      = (e) => {
      if (e.key === "Escape") { onCancel(); return; }
      if (e.key === "Enter" && document.activeElement !== btnCancel) { onOk(); }
    };

    btnOk.addEventListener("click", onOk);
    btnCancel.addEventListener("click", onCancel);
    btnClose.addEventListener("click", onCancel);
    overlay.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKey);

    overlay.hidden = false;
    setTimeout(() => btnOk.focus(), 0);
  });
}

/* === Kill switches (main / bot) === */
function applyKsUI(flags) {
  // main
  const mainOn = !!flags.main_enabled;
  aels.ksMainTile.dataset.state = mainOn ? "active" : "paused";
  aels.ksMainText.textContent = mainOn ? "ATIVO" : "PAUSADO";
  aels.ksMainHelp.textContent = mainOn
    ? "Os alarmes detectados pela Eletrofrio estão sendo notificados automaticamente via WhatsApp para as unidades responsáveis."
    : "Envio automático pausado. Os alarmes continuam sendo detectados e registrados, mas nenhuma notificação WhatsApp será disparada até a retomada. A alteração é aplicada em até 60 segundos.";
  setKsButtonState(aels.btnToggleMain, mainOn, "main");

  // bot
  const botOn = !!flags.bot_enabled;
  aels.ksBotTile.dataset.state = botOn ? "active" : "paused";
  aels.ksBotText.textContent = botOn ? "ATIVO" : "PAUSADO";
  aels.ksBotHelp.textContent = botOn
    ? "O bot de atendimento está ativo e responde automaticamente às mensagens recebidas no WhatsApp."
    : "Respostas automáticas pausadas. As mensagens recebidas no WhatsApp ficam sem resposta até a retomada. A alteração é aplicada em até 5 segundos.";
  setKsButtonState(aels.btnToggleBot, botOn, "bot");
}

function setKsButtonState(btn, enabled, target) {
  // enabled=true  -> mostra botao de pausar (estado atual: ATIVO)
  // enabled=false -> mostra botao de retomar (estado atual: PAUSADO)
  const icon = btn.querySelector(".btn-ks-icon");
  const label = btn.querySelector(".btn-ks-label");
  if (enabled) {
    btn.classList.remove("btn-ks-resume");
    btn.classList.add("btn-ks-pause");
    icon.className = "btn-ks-icon fa-solid fa-pause";
    label.textContent = target === "main"
      ? "Pausar envio automático"
      : "Pausar respostas automáticas";
  } else {
    btn.classList.remove("btn-ks-pause");
    btn.classList.add("btn-ks-resume");
    icon.className = "btn-ks-icon fa-solid fa-play";
    label.textContent = target === "main"
      ? "Retomar envio automático"
      : "Retomar respostas automáticas";
  }
}

async function refreshFlags() {
  try {
    const json = await apiGet("/api/automation/flags");
    if (!json.ok || !json.flags) {
      aels.ksResult.hidden = false;
      aels.ksResult.classList.remove("ok");
      aels.ksResult.classList.add("error");
      aels.ksResult.textContent = `Falha ao ler flags: ${json.error || "desconhecido"}`;
      return;
    }
    applyKsUI(json.flags);
  } catch (e) {
    console.error("Erro ao ler flags:", e);
  }
}

async function onToggleKillSwitch(target, btn) {
  const isMain = target === "main";
  const btnOther = isMain ? aels.btnToggleBot : aels.btnToggleMain;
  const field = isMain ? "main_enabled" : "bot_enabled";

  // Determina o estado atual lendo o dataset do tile
  const tile = isMain ? aels.ksMainTile : aels.ksBotTile;
  const currentlyEnabled = tile.dataset.state === "active";
  const newValue = !currentlyEnabled;
  const ttlTexto = isMain ? "60 segundos" : "5 segundos";

  const ok = await askConfirm({
    title: newValue
      ? (isMain ? "Retomar envio automático?" : "Retomar respostas do bot?")
      : (isMain ? "Pausar envio automático?"   : "Pausar respostas do bot?"),
    body: newValue
      ? (isMain
          ? `O envio de notificações voltará a ser executado em até ${ttlTexto}.`
          : `O bot voltará a responder mensagens automaticamente em até ${ttlTexto}.`)
      : (isMain
          ? `Nenhuma notificação WhatsApp será enviada para novos alarmes. A alteração será aplicada em até ${ttlTexto}.`
          : `Mensagens recebidas no WhatsApp ficarão sem resposta. A alteração será aplicada em até ${ttlTexto}.`),
    icon: newValue
      ? "fa-solid fa-circle-play"
      : "fa-solid fa-circle-pause",
    confirmLabel: newValue ? "Retomar" : "Pausar",
  });
  if (!ok) {
    return;
  }

  btn.disabled = true;
  btnOther.disabled = true;
  hideResult(aels.ksResult);
  try {
    const body = { [field]: newValue };
    const json = await apiPost("/api/automation/flags", body);
    if (!json.ok) {
      showResult(aels.ksResult, false, `Falha ao atualizar: ${json.error || "erro desconhecido"}`);
      return;
    }
    applyKsUI(json.flags);
    const acaoLabel = newValue ? "retomado" : "pausado";
    const alvoLabel = isMain ? "Envio de notificações" : "Respostas do bot";
    const mensagem = `${alvoLabel} ${acaoLabel} com sucesso. A alteração será aplicada em até ${ttlTexto}.`;
    showResult(aels.ksResult, true, mensagem);
  } catch (e) {
    showResult(aels.ksResult, false, `Falha: ${e}`);
  } finally {
    btn.disabled = false;
    btnOther.disabled = false;
  }
}

/* === Refresh geral da central === */
async function refreshAutomation() {
  await refreshFlags();
}

function startAutomationAutoRefresh() {
  if (autoTimer) clearInterval(autoTimer);
  autoTimer = setInterval(() => {
    // So atualiza se a aba de automacao estiver ativa
    const view = document.getElementById("view-automacao");
    if (view && view.classList.contains("view-active")) {
      refreshAutomation();
    }
  }, AUTO_REFRESH_MS);
}

document.addEventListener("DOMContentLoaded", () => {
  // Liga os botoes dos kill switches
  aels.btnToggleMain.addEventListener("click", () => onToggleKillSwitch("main", aels.btnToggleMain));
  aels.btnToggleBot.addEventListener("click",  () => onToggleKillSwitch("bot",  aels.btnToggleBot));

  // Primeiro carregamento + auto-refresh
  refreshAutomation();
  startAutomationAutoRefresh();
});

// Expor para o dashboard.js (que chama apos trocar de aba)
window.refreshAutomation = refreshAutomation;
