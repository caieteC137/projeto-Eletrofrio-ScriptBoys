/* Central de Automatizacao - Eletrofrio
 *
 * Logica client-side da aba "Central de Automatizacao".
 * Lida com:
 *   - Estado persistido de main.py  (alarm_state.json + alarm_service.log)
 *   - Estado persistido de bot_polling.py (bot_polling_state.json + pipeline.log)
 *   - Envio manual de notificacao (reusa NotificationManager via /api/automation/send-notification)
 *   - Envio manual de mensagem WhatsApp (reusa EvolutionAPIClient via /api/automation/send-message)
 *   - Limpar sessoes do bot / alarm_state.json
 *
 * Auto-refresh: a cada AUTO_REFRESH_MS a aba recarrega os dados read-only.
 * Os botoes de envio nao dao auto-refresh: a UI atualiza explicitamente
 * via callback do submit.
 */

const AUTO_REFRESH_MS = 20000;
let autoTimer = null;

const aels = {
  // KPIs
  kpiAlarmTotal: document.getElementById("kpi-alarm-total"),
  kpiAlarmNovos: document.getElementById("kpi-alarm-novos"),
  kpiAlarmAlt:   document.getElementById("kpi-alarm-alt"),
  kpiAlarmCrit:  document.getElementById("kpi-alarm-crit"),
  kpiBotSessions:   document.getElementById("kpi-bot-sessions"),
  kpiBotProcessed:  document.getElementById("kpi-bot-processed"),
  kpiBotLastTs:     document.getElementById("kpi-bot-last-ts"),

  // Tabelas
  tbodyAlarmState:  document.getElementById("tbody-alarm-state"),
  alarmStateCount:  document.getElementById("alarm-state-count"),
  tbodyBotSessions: document.getElementById("tbody-bot-sessions"),
  botSessionsCount: document.getElementById("bot-sessions-count"),

  // Kill switches (pausar/retomar main.py e bot_polling.py)
  btnToggleMain:  document.getElementById("btn-toggle-main"),
  btnToggleBot:   document.getElementById("btn-toggle-bot"),
  ksMainTile:     document.getElementById("ks-main-tile"),
  ksBotTile:      document.getElementById("ks-bot-tile"),
  ksMainDot:      document.getElementById("ks-main-dot"),
  ksBotDot:       document.getElementById("ks-bot-dot"),
  ksMainText:     document.getElementById("ks-main-text"),
  ksBotText:      document.getElementById("ks-bot-text"),
  ksMainHelp:     document.getElementById("ks-main-help"),
  ksBotHelp:      document.getElementById("ks-bot-help"),
  ksResult:       document.getElementById("ks-result"),

  // Logs
  logAlarmBody:    document.getElementById("log-alarm-body"),
  logPipelineBody: document.getElementById("log-pipeline-body"),
  btnReloadLogAlarm:    document.getElementById("btn-reload-log-alarm"),
  btnReloadLogPipeline: document.getElementById("btn-reload-log-pipeline"),
};

/* === Helpers === */
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function fmtNumber(n) {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("pt-BR").format(n);
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString("pt-BR", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function fmtIdade(seg) {
  if (seg === null || seg === undefined) return "—";
  if (seg < 60) return `${seg}s`;
  if (seg < 3600) return `${Math.floor(seg / 60)}m`;
  if (seg < 86400) return `${Math.floor(seg / 3600)}h ${Math.floor((seg % 3600) / 60)}m`;
  return `${Math.floor(seg / 86400)}d`;
}

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

/* === KPI / Estado de alarmes (main.py) === */
async function refreshAlarmState() {
  try {
    const json = await apiGet("/api/automation/alarm-state");
    if (!json.ok) {
      aels.kpiAlarmTotal.textContent = "—";
      aels.kpiAlarmNovos.textContent = "—";
      aels.kpiAlarmAlt.textContent = "—";
      aels.kpiAlarmCrit.textContent = "—";
      aels.tbodyAlarmState.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(json.error || "sem dados")}</td></tr>`;
      aels.alarmStateCount.textContent = "0";
      return;
    }

    const porStatus = json.por_status || {};
    const porCrit = json.por_criticidade || {};
    aels.kpiAlarmTotal.textContent = fmtNumber(json.total);
    aels.kpiAlarmNovos.textContent = fmtNumber(porStatus.novo || 0);
    aels.kpiAlarmAlt.textContent = fmtNumber(porStatus.alterado || 0);
    aels.kpiAlarmCrit.textContent = fmtNumber(porCrit.A || porCrit["CRÍTICO"] || 0);

    const recent = json.recent || [];
    aels.alarmStateCount.textContent = String(recent.length);
    if (!recent.length) {
      aels.tbodyAlarmState.innerHTML = `<tr><td colspan="6" class="empty">Nenhum alarme no estado local.</td></tr>`;
    } else {
      aels.tbodyAlarmState.innerHTML = recent.map(a => `
        <tr>
          <td>#${escapeHtml(a.alarmeId ?? "?")}</td>
          <td>${escapeHtml(fmtDateTime(a.alarmeDhCad))}</td>
          <td>${escapeHtml(a.lojaNm || ("#" + (a.lojaId ?? "?")))}</td>
          <td>${escapeHtml(a.dispositivoNm || "—")}</td>
          <td>${escapeHtml((a.criticidade || "N/A").toUpperCase())}</td>
          <td>${escapeHtml(a.status || "—")}</td>
        </tr>
      `).join("");
    }
  } catch (e) {
    console.error("Erro ao buscar alarm state:", e);
    aels.tbodyAlarmState.innerHTML = `<tr><td colspan="6" class="empty">Falha de comunicação.</td></tr>`;
  }
}

/* === KPI / Sessoes do bot (bot_polling.py) === */
async function refreshBotState() {
  try {
    const json = await apiGet("/api/automation/bot-state");
    if (!json.ok) {
      aels.kpiBotSessions.textContent = "—";
      aels.kpiBotProcessed.textContent = "—";
      aels.kpiBotLastTs.textContent = "—";
      aels.tbodyBotSessions.innerHTML = `<tr><td colspan="4" class="empty">${escapeHtml(json.error || "sem dados")}</td></tr>`;
      aels.botSessionsCount.textContent = "0";
      return;
    }

    aels.kpiBotSessions.textContent = fmtNumber(json.sessoes_ativas);
    aels.kpiBotProcessed.textContent = fmtNumber(json.processed_count);
    aels.kpiBotLastTs.textContent = json.last_timestamp_iso
      ? fmtDateTime(json.last_timestamp_iso)
      : "—";

    const sessoes = json.sessoes || [];
    aels.botSessionsCount.textContent = String(sessoes.length);
    if (!sessoes.length) {
      aels.tbodyBotSessions.innerHTML = `<tr><td colspan="4" class="empty">Nenhuma sessão ativa no momento.</td></tr>`;
    } else {
      aels.tbodyBotSessions.innerHTML = sessoes.map(s => {
        const stepClass = `step-pill step-${escapeHtml(s.step || "idle")}`;
        const lojaTxt = s.loja
          ? `${escapeHtml(s.loja.lojaNm || ("#" + s.loja.lojaId))}`
          : "—";
        return `
          <tr>
            <td>${escapeHtml((s.phone || "").replace("@s.whatsapp.net", ""))}</td>
            <td><span class="${stepClass}">${escapeHtml(s.step || "—")}</span></td>
            <td>${lojaTxt}</td>
            <td>${escapeHtml(fmtIdade(s.idade_segundos))}</td>
          </tr>
        `;
      }).join("");
    }
  } catch (e) {
    console.error("Erro ao buscar bot state:", e);
    aels.tbodyBotSessions.innerHTML = `<tr><td colspan="4" class="empty">Falha de comunicação.</td></tr>`;
  }
}

/* === Logs === */
async function refreshLog(source) {
  const el = source === "alarm" ? aels.logAlarmBody : aels.logPipelineBody;
  el.textContent = "Carregando...";
  try {
    const json = await apiGet(`/api/automation/logs/${source}?lines=120`);
    if (!json.ok) {
      el.textContent = `Erro: ${json.error || "desconhecido"}`;
      return;
    }
    if (!json.exists) {
      el.textContent = "(arquivo de log ainda não existe)";
      return;
    }
    const lines = json.lines || [];
    el.textContent = lines.length ? lines.join("\n") : "(vazio)";
    el.scrollTop = el.scrollHeight;
  } catch (e) {
    el.textContent = "Falha ao carregar log.";
  }
}

/* === Acoes: limpar sessoes / alarm_state === */
/* === Kill switches (main / bot) === */
function applyKsUI(flags) {
  // main
  const mainOn = !!flags.main_enabled;
  aels.ksMainTile.dataset.state = mainOn ? "active" : "paused";
  aels.ksMainText.textContent = mainOn ? "ATIVO" : "⏸ PAUSADO";
  aels.ksMainHelp.textContent = mainOn
    ? "Notificações WhatsApp estão sendo enviadas normalmente para alarmes críticos detectados pela API da Eletrofrio."
    : "Envio automático PAUSADO. Alarmes ainda são detectados e gravados no estado local, mas nenhuma notificação WhatsApp é disparada. A mudança propaga em até 60s.";
  setKsButtonState(aels.btnToggleMain, mainOn, "main");

  // bot
  const botOn = !!flags.bot_enabled;
  aels.ksBotTile.dataset.state = botOn ? "active" : "paused";
  aels.ksBotText.textContent = botOn ? "ATIVO" : "⏸ PAUSADO";
  aels.ksBotHelp.textContent = botOn
    ? "O bot está consultando novas mensagens e respondendo com ajuda do Gemini + Supabase."
    : "Respostas automáticas PAUSADAS. Mensagens recebidas não são respondidas. A mudança propaga em até 5s.";
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
    icon.textContent = "⏸";
    label.textContent = target === "main"
      ? "Pausar envio automático"
      : "Pausar respostas automáticas";
  } else {
    btn.classList.remove("btn-ks-pause");
    btn.classList.add("btn-ks-resume");
    icon.textContent = "▶";
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

  // Determina o estado atual lendo o texto do tile
  const tile = isMain ? aels.ksMainTile : aels.ksBotTile;
  const currentlyEnabled = tile.dataset.state === "active";
  const newValue = !currentlyEnabled;
  const actionLabel = newValue
    ? (isMain ? "retomar o envio de notificações" : "retomar as respostas do bot")
    : (isMain ? "pausar o envio de notificações"  : "pausar as respostas do bot");

  if (!confirm(
    newValue
      ? `Deseja realmente ${actionLabel}? O serviço voltará a executar automaticamente no próximo ciclo.`
      : `Deseja realmente ${actionLabel}? O serviço permanecerá no ar, mas não executará ações automáticas até você reativar.`
  )) {
    return;
  }

  btn.disabled = true;
  btnOther.disabled = true;
  hideResult(aels.ksResult);
  try {
    const body = { [field]: newValue };
    const json = await apiPost("/api/automation/flags", body);
    if (!json.ok) {
      showResult(aels.ksResult, false, json);
      return;
    }
    applyKsUI(json.flags);
    showResult(aels.ksResult, true, {
      mensagem: newValue
        ? `▶ ${isMain ? "Envio de notificações" : "Respostas do bot"} REATIVADO. Propagação em até ${isMain ? "60s" : "5s"}.`
        : `⏸ ${isMain ? "Envio de notificações" : "Respostas do bot"} PAUSADO. Propagação em até ${isMain ? "60s" : "5s"}.`,
      flags: json.flags,
    });
  } catch (e) {
    showResult(aels.ksResult, false, `Falha: ${e}`);
  } finally {
    btn.disabled = false;
    btnOther.disabled = false;
  }
}

/* === Refresh geral da central === */
async function refreshAutomation() {
  await Promise.all([
    refreshAlarmState(),
    refreshBotState(),
    refreshLog("alarm"),
    refreshLog("pipeline"),
    refreshFlags(),
  ]);
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
  // Liga os botoes
  aels.btnToggleMain.addEventListener("click", () => onToggleKillSwitch("main", aels.btnToggleMain));
  aels.btnToggleBot.addEventListener("click",  () => onToggleKillSwitch("bot",  aels.btnToggleBot));
  aels.btnReloadLogAlarm.addEventListener("click", () => refreshLog("alarm"));
  aels.btnReloadLogPipeline.addEventListener("click", () => refreshLog("pipeline"));

  // Primeiro carregamento + auto-refresh
  refreshAutomation();
  startAutomationAutoRefresh();
});

// Expor para o dashboard.js (que chama apos trocar de aba)
window.refreshAutomation = refreshAutomation;
