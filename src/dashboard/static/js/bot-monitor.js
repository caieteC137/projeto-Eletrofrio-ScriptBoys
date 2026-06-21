/* Monitor do Bot — Fase 4 */

const BOT_REFRESH_MS = 30000;

const botEls = {
  kpiHoje: document.getElementById("bot-kpi-hoje"),
  kpiTempo: document.getElementById("bot-kpi-tempo"),
  kpiTaxa: document.getElementById("bot-kpi-taxa"),
  kpiHojeHint: document.getElementById("bot-kpi-hoje-hint"),
  kpiTempoHint: document.getElementById("bot-kpi-tempo-hint"),
  kpiTaxaHint: document.getElementById("bot-kpi-taxa-hint"),
  tbody: document.getElementById("tbody-bot-logs"),
  logCount: document.getElementById("bot-log-count"),
  loadable: document.getElementById("loadable-bot-table"),
};

let botRefreshTimer = null;

function botEscape(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function botFormatDateTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString("pt-BR", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch { return iso; }
}

function botShorten(text, max = 120) {
  if (!text) return "";
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? clean.slice(0, max) + "…" : clean;
}

function setBotLoading(loading) {
  if (!botEls.loadable) return;
  botEls.loadable.classList.toggle("is-loading", loading);
  botEls.loadable.setAttribute("aria-busy", loading ? "true" : "false");
}

async function fetchBotStats() {
  const resp = await fetch("/api/bot/stats");
  const json = await resp.json();
  if (!json.ok) throw new Error(json.error || "Erro ao buscar stats do bot");

  if (botEls.kpiHoje) botEls.kpiHoje.textContent = json.conversas_hoje ?? 0;
  if (botEls.kpiTaxa) botEls.kpiTaxa.textContent = `${json.taxa_resolucao ?? 0}%`;

  if (botEls.kpiTempo) {
    const seg = json.tempo_medio_resposta_seg;
    botEls.kpiTempo.textContent = seg != null ? `${seg}s` : "—";
  }

  if (botEls.kpiHojeHint) {
    const total = json.total_conversas ?? 0;
    const sess = json.sessoes_ativas ?? 0;
    botEls.kpiHojeHint.textContent = `${total} conversas totais · ${sess} sessões ativas`;
  }
  if (botEls.kpiTaxaHint && json.warning) {
    botEls.kpiTaxaHint.textContent = json.warning;
  }
}

async function fetchBotLogs() {
  const resp = await fetch("/api/bot/logs?limit=50");
  const json = await resp.json();
  if (!json.ok) throw new Error(json.error || "Erro ao buscar logs do bot");
  renderBotLogs(json.data || [], json.warning);
}

function renderBotLogs(rows, warning) {
  if (!botEls.tbody) return;

  if (!rows.length) {
    const msg = warning || "Nenhuma conversa registrada no período.";
    botEls.tbody.innerHTML = `<tr><td colspan="5" class="empty">${botEscape(msg)}</td></tr>`;
    if (botEls.logCount) botEls.logCount.textContent = "0 registros";
    return;
  }

  botEls.tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${botFormatDateTime(r.timestamp)}</td>
      <td>${botEscape(r.telefone || "—")}<br><span class="muted">${botEscape(r.push_name || "")}</span></td>
      <td>${botEscape(botShorten(r.pergunta, 100))}</td>
      <td>${botEscape(botShorten(r.resposta, 100))}</td>
      <td>${r.tokens != null ? botEscape(r.tokens) : "—"}</td>
    </tr>
  `).join("");

  if (botEls.logCount) botEls.logCount.textContent = `${rows.length} registros`;
}

function startBotAutoRefresh() {
  if (botRefreshTimer) clearInterval(botRefreshTimer);
  botRefreshTimer = setInterval(() => {
    if (typeof activeTab !== "undefined" && activeTab === "bot") {
      window.refreshBotMonitor?.();
    }
  }, BOT_REFRESH_MS);
}

window.refreshBotMonitor = async function refreshBotMonitor() {
  setBotLoading(true);
  try {
    await Promise.all([fetchBotStats(), fetchBotLogs()]);
  } catch (e) {
    console.error("Erro no monitor do bot:", e);
    if (botEls.tbody) {
      botEls.tbody.innerHTML = `<tr><td colspan="5" class="empty">Erro: ${botEscape(e.message)}</td></tr>`;
    }
  } finally {
    setBotLoading(false);
  }
};

startBotAutoRefresh();
