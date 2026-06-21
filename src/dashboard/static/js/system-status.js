/* Status do Sistema — Fase 4 */

const SYSTEM_REFRESH_MS = 15000;

const sysEls = {
  grid: document.getElementById("service-grid"),
  version: document.getElementById("sys-version"),
  uptime: document.getElementById("sys-uptime"),
  started: document.getElementById("sys-started"),
  mainFlag: document.getElementById("sys-main-flag"),
  botFlag: document.getElementById("sys-bot-flag"),
  lastCheck: document.getElementById("sys-last-check"),
  syncBadge: document.getElementById("system-sync-badge"),
  errorLogs: document.getElementById("system-error-logs"),
  errorCount: document.getElementById("system-error-count"),
};

let systemRefreshTimer = null;

const SERVICE_META = {
  supabase: { label: "Supabase", icon: "fa-solid fa-database" },
  eletrofrio_api: { label: "API Eletrofrio", icon: "fa-solid fa-cloud" },
  whatsapp: { label: "WhatsApp (Evolution)", icon: "fa-brands fa-whatsapp" },
};

function sysEscape(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function statusLabel(status) {
  if (status === "online") return "Online";
  if (status === "degraded") return "Lento";
  return "Offline";
}

function renderServiceCards(services) {
  if (!sysEls.grid) return;
  const entries = Object.entries(services || {});

  sysEls.grid.innerHTML = entries.map(([key, info]) => {
    const meta = SERVICE_META[key] || { label: key, icon: "fa-circle" };
    const status = info.status || "offline";
    return `
      <article class="service-card service-${status} card">
        <div class="service-card-head">
          <span class="service-icon"><i class="${meta.icon}" aria-hidden="true"></i></span>
          <div>
            <h3>${sysEscape(meta.label)}</h3>
            <span class="service-status">
              <span class="service-dot"></span>
              ${statusLabel(status)}
            </span>
          </div>
        </div>
        <p class="service-detail">${sysEscape(info.detail || "—")}</p>
        <span class="service-latency">${info.latency_ms != null ? `${info.latency_ms} ms` : "—"}</span>
      </article>
    `;
  }).join("");
}

function renderSystemInfo(json) {
  if (sysEls.version) sysEls.version.textContent = json.version || "—";
  if (sysEls.uptime) sysEls.uptime.textContent = json.uptime || "—";
  if (sysEls.started) {
    sysEls.started.textContent = json.started_at
      ? new Date(json.started_at).toLocaleString("pt-BR")
      : "—";
  }
  if (sysEls.lastCheck) {
    sysEls.lastCheck.textContent = json.fetched_at
      ? new Date(json.fetched_at).toLocaleString("pt-BR")
      : "—";
  }
  if (sysEls.syncBadge) sysEls.syncBadge.textContent = "Atualizado agora";

  const auto = json.automation || {};
  if (sysEls.mainFlag) {
    sysEls.mainFlag.textContent = auto.main_enabled ? "Ativo" : "Pausado";
    sysEls.mainFlag.className = auto.main_enabled ? "flag-on" : "flag-off";
  }
  if (sysEls.botFlag) {
    sysEls.botFlag.textContent = auto.bot_enabled ? "Ativo" : "Pausado";
    sysEls.botFlag.className = auto.bot_enabled ? "flag-on" : "flag-off";
  }

  const logs = json.error_logs || [];
  if (sysEls.errorCount) {
    sysEls.errorCount.textContent = `${logs.length} linha${logs.length === 1 ? "" : "s"}`;
  }
  if (sysEls.errorLogs) {
    sysEls.errorLogs.textContent = logs.length
      ? logs.map(l => `[${l.source}] ${l.line}`).join("\n")
      : "Nenhum erro recente nos logs monitorados.";
  }
}

async function fetchSystemHealth() {
  const resp = await fetch("/api/system/health");
  const json = await resp.json();
  if (!json.ok) throw new Error(json.error || "Erro ao verificar sistema");
  renderServiceCards(json.services);
  renderSystemInfo(json);
}

function startSystemAutoRefresh() {
  if (systemRefreshTimer) clearInterval(systemRefreshTimer);
  systemRefreshTimer = setInterval(() => {
    if (typeof activeTab !== "undefined" && activeTab === "sistema") {
      window.refreshSystemStatus?.();
    }
  }, SYSTEM_REFRESH_MS);
}

window.refreshSystemStatus = async function refreshSystemStatus() {
  try {
    await fetchSystemHealth();
  } catch (e) {
    console.error("Erro no status do sistema:", e);
    if (sysEls.grid) {
      sysEls.grid.innerHTML = `<p class="empty-state">Erro: ${sysEscape(e.message)}</p>`;
    }
  }
};

startSystemAutoRefresh();
