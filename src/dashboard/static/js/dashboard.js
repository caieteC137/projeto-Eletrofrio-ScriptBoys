/* Dashboard de Notificacoes - Eletrofrio
 * Read-only: este front-end apenas consulta o backend Flask.
 * Nenhuma acao de envio, edicao ou disparo eh disparada por aqui.
 *
 * A central de automacao (aba dedicada) tem sua logica em automation.js.
 */

const REFRESH_INTERVAL_MS = 15000;
const SIDEBAR_STORAGE_KEY = "ef-sidebar-collapsed";

const PAGE_META = {
  notificacoes: {
    title: "Notificações",
    subtitle: "Monitoramento de alarmes e envios via WhatsApp",
  },
  automacao: {
    title: "Automatização",
    subtitle: "Controle centralizado de envios e respostas automáticas",
  },
  bot: {
    title: "Monitor do Bot",
    subtitle: "Auditoria das conversas e desempenho do assistente",
  },
  sistema: {
    title: "Status do Sistema",
    subtitle: "Saúde dos serviços integrados e logs de erro",
  },
};

const els = {
  tbody: document.getElementById("tbody-notificacoes"),
  resultCount: document.getElementById("result-count"),
  lastUpdate: document.getElementById("last-update"),
  footerFetched: document.getElementById("footer-fetched"),
  modal: document.getElementById("modal-msg"),
  modalTitle: document.getElementById("modal-title"),
  modalBody: document.getElementById("modal-body"),
  modalClose: document.getElementById("modal-close"),
  autoRefresh: document.getElementById("auto-refresh"),
  btnAplicar: document.getElementById("btn-aplicar"),
  btnExportCsv: document.getElementById("btn-export-csv"),
  btnLimparFiltros: document.getElementById("btn-limpar-filtros"),
  filterSearch: document.getElementById("filter-search"),
  filterStatus: document.getElementById("filter-status"),
  filterCriticidade: document.getElementById("filter-criticidade"),
  filterLoja: document.getElementById("filter-loja"),
  filterLimit: document.getElementById("filter-limit"),
  pageTitle: document.getElementById("page-title"),
  pageSubtitle: document.getElementById("page-subtitle"),
  sidebar: document.getElementById("sidebar"),
  sidebarCollapse: document.getElementById("sidebar-collapse"),
  sidebarOverlay: document.getElementById("sidebar-overlay"),
  sidebarMobileTrigger: document.getElementById("sidebar-mobile-trigger"),
  kpis: {
    total: document.getElementById("kpi-total"),
    enviado: document.getElementById("kpi-enviado"),
    falhas: document.getElementById("kpi-falhas"),
    pendentes: document.getElementById("kpi-pendentes"),
    h24: document.getElementById("kpi-24h"),
    taxa: document.getElementById("kpi-taxa"),
    hints: {
      total: document.getElementById("kpi-total-hint"),
      enviado: document.getElementById("kpi-enviado-hint"),
      falhas: document.getElementById("kpi-falhas-hint"),
      pendentes: document.getElementById("kpi-pendentes-hint"),
      h24: document.getElementById("kpi-24h-hint"),
      taxa: document.getElementById("kpi-taxa-hint"),
    },
  },
  donutCenter: document.getElementById("donut-center-label"),
  donutLegend: document.getElementById("donut-legend"),
  loadable: {
    kpis: document.getElementById("loadable-kpis"),
    charts: document.getElementById("loadable-charts"),
    table: document.getElementById("loadable-table"),
  },
  splashScreen: document.getElementById("splash-screen"),
  splashVideo: document.querySelector(".splash-helice"),
  sidebarLinks: document.querySelectorAll(".sidebar-nav .sidebar-link[data-tab]"),
  views: {
    notificacoes: document.getElementById("view-notificacoes"),
    automacao: document.getElementById("view-automacao"),
    bot: document.getElementById("view-bot"),
    sistema: document.getElementById("view-sistema"),
  },
};

let unidadesMap = new Map();
let refreshTimer = null;
let activeTab = "notificacoes";
let lastFetchedRows = [];
let lastStats = null;
let chartInstances = { hourly: null, unidades: null, eficiencia: null };
let chartSourceRows = [];
let splashStartTime = 0;

const SPLASH_MIN_MS = 1400;
const SPLASH_FADE_MS = 500;

function setSectionLoading(section, loading) {
  const el = els.loadable[section];
  if (!el) return;
  el.classList.toggle("is-loading", loading);
  el.setAttribute("aria-busy", loading ? "true" : "false");
}

function setDashboardLoading(loading) {
  setSectionLoading("kpis", loading);
  setSectionLoading("charts", loading);
  setSectionLoading("table", loading);
}

function showTableSkeleton(rowCount = 8) {
  if (!els.tbody) return;
  els.tbody.innerHTML = Array.from({ length: rowCount }, () =>
    `<tr class="skeleton-row">${Array(9).fill('<td><span class="skeleton skeleton-cell"></span></td>').join("")}</tr>`
  ).join("");
}

function initSplashScreen() {
  splashStartTime = Date.now();
  document.body.classList.add("splash-active");
  els.splashVideo?.play().catch(() => { /* autoplay bloqueado */ });
}

function hideSplashScreen() {
  return new Promise(resolve => {
    if (!els.splashScreen) {
      resolve();
      return;
    }

    const elapsed = Date.now() - splashStartTime;
    const wait = Math.max(0, SPLASH_MIN_MS - elapsed);

    setTimeout(() => {
      els.splashScreen.classList.add("is-hidden");
      els.splashScreen.setAttribute("aria-busy", "false");
      document.body.classList.remove("splash-active");

      if (els.splashVideo) {
        els.splashVideo.pause();
      }

      setTimeout(() => {
        els.splashScreen.remove();
        resolve();
      }, SPLASH_FADE_MS);
    }, wait);
  });
}

const CRIT_LABELS = {
  A: "A — Alta",
  B: "B — Média",
  C: "C — Baixa",
  I: "I — Informativo",
  M: "M — Média",
  CRÍTICO: "Crítico",
  CRITICO: "Crítico",
  ALTO: "Alto",
  MEDIO: "Médio",
  MÉDIO: "Médio",
  BAIXO: "Baixo",
};

const CRIT_ORDER = ["A", "B", "C", "M", "I", "CRÍTICO", "CRITICO", "ALTO", "MEDIO", "MÉDIO", "BAIXO"];

const CHART_COLORS = {
  primary: "#00afc9",
  purple: "#8b5cf6",
  green: "#16a34a",
  amber: "#f59e0b",
  red: "#e11d48",
};

const SPARK_COLORS = {
  total: CHART_COLORS.primary,
  enviado: CHART_COLORS.green,
  falhas: CHART_COLORS.red,
  pendentes: CHART_COLORS.amber,
  h24: CHART_COLORS.purple,
  taxa: "#3b82f6",
};

function formatDateTime(iso) {
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

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function shorten(text, max = 60) {
  if (!text) return "";
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? clean.slice(0, max) + "…" : clean;
}

function getStatusClass(status) {
  const s = (status || "desconhecido").toLowerCase();
  return `status status-${s}`;
}

function getCritClass(crit) {
  const c = (crit || "N/A").toUpperCase();
  return `crit crit-${c}`;
}

function getCritLabel(crit) {
  const key = (crit || "").toUpperCase();
  return CRIT_LABELS[key] || key || "N/A";
}

function populateCriticidadeFilter(porCriticidade, rows = []) {
  if (!els.filterCriticidade) return;

  const values = new Map();
  Object.entries(porCriticidade || {}).forEach(([key, count]) => {
    const k = (key || "").toUpperCase();
    if (k && k !== "N/A" && count > 0) values.set(k, k);
  });
  rows.forEach(r => {
    const k = (r.criticidade || "").toUpperCase();
    if (k && k !== "N/A") values.set(k, k);
  });

  const current = els.filterCriticidade.value;
  const sorted = [...values.keys()].sort((a, b) => {
    const ia = CRIT_ORDER.indexOf(a);
    const ib = CRIT_ORDER.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });

  els.filterCriticidade.innerHTML = `<option value="">Todas</option>${sorted.map(v =>
    `<option value="${escapeHtml(v)}">${escapeHtml(getCritLabel(v))}</option>`
  ).join("")}`;

  if (current && sorted.includes(current)) {
    els.filterCriticidade.value = current;
  }
}

async function fetchUnidades() {
  try {
    const resp = await fetch("/api/unidades");
    const json = await resp.json();
    if (json.ok && Array.isArray(json.data)) {
      unidadesMap = new Map(json.data.map(u => [u.lojaId, u]));
    }
  } catch (e) {
    console.warn("Falha ao carregar unidades:", e);
  }
}

async function fetchStats() {
  try {
    const resp = await fetch("/api/stats");
    const json = await resp.json();
    if (!json.ok) {
      console.warn("Stats error:", json.error);
      return;
    }
    lastStats = json;
    const porStatus = json.por_status || {};
    const pendentes =
      (porStatus.pendente || 0) +
      (porStatus.pendente_retry || 0);
    const enviado = porStatus.enviado ?? 0;
    const total = json.total ?? 0;
    const taxa = json.taxa_sucesso ?? 0;
    const falhas = json.falhas ?? 0;
    const h24 = json.ultimas_24h ?? 0;

    els.kpis.total.textContent = total;
    els.kpis.enviado.textContent = enviado;
    els.kpis.falhas.textContent = falhas;
    els.kpis.pendentes.textContent = pendentes;
    els.kpis.h24.textContent = h24;
    els.kpis.taxa.textContent = `${taxa}%`;

    if (els.kpis.hints.total) {
      els.kpis.hints.total.textContent = h24 > 0
        ? `${h24} nas últimas 24h`
        : "Volume acumulado";
    }
    if (els.kpis.hints.enviado) {
      const pct = total > 0 ? ((enviado / total) * 100).toFixed(1) : "0.0";
      els.kpis.hints.enviado.textContent = `${pct}% entregas`;
    }
    if (els.kpis.hints.falhas) {
      els.kpis.hints.falhas.textContent = falhas > 0
        ? "Crítico — intervenção"
        : "Nenhuma falha ativa";
    }
    if (els.kpis.hints.pendentes) {
      els.kpis.hints.pendentes.textContent = pendentes > 0
        ? "Carga na API"
        : "Fila esvaziada";
    }
    if (els.kpis.hints.h24) {
      els.kpis.hints.h24.textContent = "Gatilhos ao vivo";
    }
    if (els.kpis.hints.taxa) {
      els.kpis.hints.taxa.textContent = taxa >= 95
        ? "Meta operacional atingida"
        : "Meta operacional > 95%";
    }

    populateCriticidadeFilter(json.por_criticidade, lastFetchedRows);
    updateDonutChart(porStatus, total, taxa);
  } catch (e) {
    console.error("Erro ao buscar stats:", e);
  }
}

async function fetchChartSourceRows() {
  try {
    const resp = await fetch("/api/notificacoes?limit=500");
    const json = await resp.json();
    if (json.ok && Array.isArray(json.data)) {
      chartSourceRows = json.data;
      updateSparklines(chartSourceRows);
      updateHourlyChart(chartSourceRows);
      updateUnidadesChart(chartSourceRows);
    }
  } catch (e) {
    console.warn("Falha ao carregar dados para gráficos:", e);
  }
}

function bucketByDay(rows, days = 7, statusFilter = null) {
  const buckets = Array(days).fill(0);
  const now = new Date();
  now.setHours(0, 0, 0, 0);

  rows.forEach(r => {
    if (statusFilter && !statusFilter(r.status)) return;
    const d = new Date(r.created_at);
    if (isNaN(d.getTime())) return;
    const dayStart = new Date(d);
    dayStart.setHours(0, 0, 0, 0);
    const diff = Math.floor((now - dayStart) / 86400000);
    if (diff >= 0 && diff < days) {
      buckets[days - 1 - diff] += 1;
    }
  });
  return buckets;
}

function drawSparkline(canvasId, values, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.offsetWidth || 180;
  const h = canvas.height || 36;
  canvas.width = w;
  canvas.height = h;
  ctx.clearRect(0, 0, w, h);

  const data = values.length ? values : [0, 0, 0, 0, 0, 0, 0];
  const max = Math.max(...data, 1);
  const padX = 4;
  const padY = 6;
  const step = (w - padX * 2) / Math.max(data.length - 1, 1);

  ctx.beginPath();
  data.forEach((v, i) => {
    const x = padX + i * step;
    const y = h - padY - (v / max) * (h - padY * 2);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.stroke();

  ctx.lineTo(padX + (data.length - 1) * step, h);
  ctx.lineTo(padX, h);
  ctx.closePath();
  ctx.fillStyle = color + "22";
  ctx.fill();
}

function updateSparklines(rows) {
  drawSparkline("spark-total", bucketByDay(rows), SPARK_COLORS.total);
  drawSparkline("spark-enviado", bucketByDay(rows, 7, s => s === "enviado"), SPARK_COLORS.enviado);
  drawSparkline("spark-falhas", bucketByDay(rows, 7, s => s === "falha" || s === "falha_permanente"), SPARK_COLORS.falhas);
  drawSparkline("spark-pendentes", bucketByDay(rows, 7, s => s === "pendente" || s === "pendente_retry"), SPARK_COLORS.pendentes);
  drawSparkline("spark-24h", bucketByDay(rows.slice(0, 200), 7), SPARK_COLORS.h24);
  drawSparkline("spark-taxa", bucketByDay(rows, 7, s => s === "enviado"), SPARK_COLORS.taxa);
}

function updateHourlyChart(rows) {
  const canvas = document.getElementById("chart-hourly");
  if (!canvas || typeof Chart === "undefined") return;

  const hours = Array.from({ length: 13 }, (_, i) => 8 + i);
  const counts = hours.map(h => {
    return rows.filter(r => {
      const d = new Date(r.created_at);
      return !isNaN(d.getTime()) && d.getHours() === h;
    }).length;
  });

  if (chartInstances.hourly) chartInstances.hourly.destroy();
  chartInstances.hourly = new Chart(canvas, {
    type: "line",
    data: {
      labels: hours.map(h => `${String(h).padStart(2, "0")}:00`),
      datasets: [{
        data: counts,
        borderColor: CHART_COLORS.primary,
        backgroundColor: "rgba(0, 175, 201, 0.12)",
        pointBackgroundColor: CHART_COLORS.primary,
        pointRadius: 4,
        pointHoverRadius: 5,
        borderWidth: 2,
        fill: true,
        tension: 0.35,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          grid: { display: false },
          ticks: { font: { size: 10 }, color: "#94a3b8" },
        },
        y: {
          beginAtZero: true,
          grid: { color: "#eef2f7" },
          ticks: { font: { size: 10 }, color: "#94a3b8", precision: 0 },
        },
      },
    },
  });
}

function updateUnidadesChart(rows) {
  const canvas = document.getElementById("chart-unidades");
  if (!canvas || typeof Chart === "undefined") return;

  const counts = new Map();
  rows.forEach(r => {
    const id = r.lojaId;
    if (id == null) return;
    counts.set(id, (counts.get(id) || 0) + 1);
  });

  const top = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  const labels = top.map(([id]) => {
    const u = unidadesMap.get(id);
    const name = u ? u.lojaNm : `Loja ${id}`;
    return name.length > 28 ? name.slice(0, 26) + "…" : name;
  });
  const data = top.map(([, c]) => c);

  if (chartInstances.unidades) chartInstances.unidades.destroy();
  chartInstances.unidades = new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: "rgba(139, 92, 246, 0.75)",
        borderRadius: 6,
        barThickness: 14,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          beginAtZero: true,
          grid: { color: "#eef2f7" },
          ticks: { font: { size: 10 }, color: "#94a3b8", precision: 0 },
        },
        y: {
          grid: { display: false },
          ticks: { font: { size: 10 }, color: "#64748b" },
        },
      },
    },
  });
}

function updateDonutChart(porStatus, total, taxa) {
  const canvas = document.getElementById("chart-eficiencia");
  if (!canvas || typeof Chart === "undefined") return;

  const enviado = porStatus.enviado || 0;
  const pendentes = (porStatus.pendente || 0) + (porStatus.pendente_retry || 0);
  const falhas = (porStatus.falha || 0) + (porStatus.falha_permanente || 0);

  const slices = [
    { label: "Enviados", value: enviado, color: CHART_COLORS.green },
    { label: "Pendentes", value: pendentes, color: CHART_COLORS.amber },
    { label: "Falhas", value: falhas, color: CHART_COLORS.red },
  ].filter(s => s.value > 0);

  if (!slices.length) {
    slices.push({ label: "Sem dados", value: 1, color: "#e2e8f0" });
  }

  if (chartInstances.eficiencia) chartInstances.eficiencia.destroy();
  chartInstances.eficiencia = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: slices.map(s => s.label),
      datasets: [{
        data: slices.map(s => s.value),
        backgroundColor: slices.map(s => s.color),
        borderWidth: 0,
        cutout: "72%",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
    },
  });

  if (els.donutCenter) {
    els.donutCenter.querySelector(".donut-center-value").textContent = `${taxa}%`;
  }

  if (els.donutLegend) {
    els.donutLegend.innerHTML = slices.map(s => {
      const pct = total > 0 ? ((s.value / total) * 100).toFixed(1) : "0.0";
      return `
        <li>
          <span class="donut-legend-dot" style="background:${s.color}"></span>
          <span>${s.label}</span>
          <span class="donut-legend-meta">${s.value} · ${pct}%</span>
        </li>`;
    }).join("");
  }
}

function buildQueryParams() {
  const params = new URLSearchParams();
  if (els.filterStatus.value) params.set("status", els.filterStatus.value);
  if (els.filterCriticidade.value) params.set("criticidade", els.filterCriticidade.value);
  if (els.filterLoja.value) params.set("loja_id", els.filterLoja.value);
  params.set("limit", els.filterLimit.value);
  return params.toString();
}

async function fetchNotificacoes({ withLoader = false } = {}) {
  if (withLoader) {
    setSectionLoading("table", true);
    showTableSkeleton();
  }
  try {
    const qs = buildQueryParams();
    const resp = await fetch(`/api/notificacoes?${qs}`);
    const json = await resp.json();
    if (!json.ok) {
      els.tbody.innerHTML = `<tr><td colspan="9" class="empty">Erro: ${escapeHtml(json.error || "desconhecido")}</td></tr>`;
      return;
    }
    lastFetchedRows = json.data || [];
    populateCriticidadeFilter(lastStats?.por_criticidade, lastFetchedRows);
    applySearchFilter();

    const now = new Date();
    const stamp = now.toLocaleTimeString("pt-BR");
    els.lastUpdate.textContent = `Atualizado às ${stamp}`;
    els.footerFetched.textContent = `Última consulta: ${stamp}`;
  } catch (e) {
    console.error("Erro ao buscar notificações:", e);
    els.tbody.innerHTML = `<tr><td colspan="9" class="empty">Falha na comunicação com o servidor.</td></tr>`;
  } finally {
    if (withLoader) setSectionLoading("table", false);
  }
}

function rowMatchesSearch(r, term) {
  if (!term) return true;
  const unidade = unidadesMap.get(r.lojaId);
  const nomeUnidade = unidade ? unidade.lojaNm : "";
  const haystack = [
    r.id, r.alarmeId, r.lojaId, r.telefone, r.status, r.criticidade,
    nomeUnidade, r.mensagem,
  ].join(" ").toLowerCase();
  return haystack.includes(term);
}

function applySearchFilter() {
  const term = (els.filterSearch?.value || "").trim().toLowerCase();
  const filtered = lastFetchedRows.filter(r => rowMatchesSearch(r, term));
  renderRows(filtered);
  const suffix = term ? " filtrados" : " registros";
  els.resultCount.textContent = `${filtered.length}${suffix}`;
}

function clearFilters() {
  if (els.filterSearch) els.filterSearch.value = "";
  els.filterStatus.value = "";
  els.filterCriticidade.value = "";
  els.filterLoja.value = "";
  els.filterLimit.value = "200";
  fetchNotificacoes({ withLoader: true });
}

function exportCsv() {
  const term = (els.filterSearch?.value || "").trim().toLowerCase();
  const rows = lastFetchedRows.filter(r => rowMatchesSearch(r, term));
  if (!rows.length) return;

  const headers = ["ID", "Data/Hora", "Unidade", "Loja ID", "Alarme", "Criticidade", "Telefone", "Status", "Tentativas", "Mensagem"];
  const lines = [headers.join(";")];

  rows.forEach(r => {
    const unidade = unidadesMap.get(r.lojaId);
    const nomeUnidade = unidade ? unidade.lojaNm : `#${r.lojaId ?? "?"}`;
    const cols = [
      r.id,
      formatDateTime(r.created_at),
      nomeUnidade,
      r.lojaId ?? "",
      r.alarmeId ?? "",
      r.criticidade ?? "",
      r.telefone ?? "",
      r.status ?? "",
      `${r.tentativas ?? 0}/${r.max_tentativas ?? 0}`,
      (r.mensagem || "").replace(/[\n\r;]/g, " "),
    ].map(v => `"${String(v).replace(/"/g, '""')}"`);
    lines.push(cols.join(";"));
  });

  const blob = new Blob(["\uFEFF" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `eletrofrio-notificacoes-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function renderRows(rows) {
  if (!rows.length) {
    els.tbody.innerHTML = `<tr><td colspan="9" class="empty">Nenhuma notificação encontrada com os filtros atuais.</td></tr>`;
    return;
  }
  const html = rows.map(r => {
    const unidade = unidadesMap.get(r.lojaId);
    const nomeUnidade = unidade ? unidade.lojaNm : `#${r.lojaId ?? "?"}`;
    const msg = r.mensagem || "";
    return `
      <tr>
        <td>#${r.id}</td>
        <td>${formatDateTime(r.created_at)}</td>
        <td>${escapeHtml(nomeUnidade)}<br><span class="muted">Loja ${r.lojaId ?? "—"}</span></td>
        <td>${escapeHtml(r.alarmeId ?? "—")}</td>
        <td><span class="${getCritClass(r.criticidade)}" title="${escapeHtml(getCritLabel(r.criticidade))}">${escapeHtml((r.criticidade || "N/A").toUpperCase())}</span></td>
        <td>${escapeHtml(r.telefone || "—")}</td>
        <td><span class="${getStatusClass(r.status)}">${escapeHtml(r.status || "desconhecido")}</span></td>
        <td>${r.tentativas ?? 0}/${r.max_tentativas ?? 0}</td>
        <td>
          <span class="msg-preview" data-msg="${encodeURIComponent(msg)}" data-title="Mensagem #${r.id}">
            ${escapeHtml(shorten(msg, 80)) || '<span class="muted">—</span>'}
          </span>
        </td>
      </tr>
    `;
  }).join("");
  els.tbody.innerHTML = html;

  document.querySelectorAll(".msg-preview").forEach(el => {
    el.addEventListener("click", () => {
      const msg = decodeURIComponent(el.dataset.msg || "");
      els.modalTitle.textContent = el.dataset.title || "Mensagem";
      els.modalBody.textContent = msg || "(sem conteúdo)";
      els.modal.hidden = false;
    });
  });
}

function openModal() { els.modal.hidden = false; }
function closeModal() { els.modal.hidden = true; }

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    // So atualiza se a aba de notificacoes estiver ativa e o auto-refresh
    // estiver ligado. A aba de automacao tem seu proprio refresh.
    if (activeTab === "notificacoes" && els.autoRefresh.checked) {
      refreshAll();
    }
  }, REFRESH_INTERVAL_MS);
}

async function refreshAll({ showLoader = false } = {}) {
  if (showLoader) {
    setDashboardLoading(true);
    showTableSkeleton();
  }
  try {
    await Promise.all([fetchStats(), fetchNotificacoes(), fetchChartSourceRows()]);
  } finally {
    if (showLoader) setDashboardLoading(false);
  }
}

/* === Sidebar & navegação === */
function updatePageHeader(tabName) {
  const meta = PAGE_META[tabName];
  if (!meta || !els.pageTitle) return;
  els.pageTitle.textContent = meta.title;
  if (els.pageSubtitle) els.pageSubtitle.textContent = meta.subtitle;
}

function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  if (els.sidebarCollapse) {
    els.sidebarCollapse.setAttribute("aria-label", collapsed ? "Expandir menu" : "Recolher menu");
    els.sidebarCollapse.title = collapsed ? "Expandir menu" : "Recolher menu";
  }
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
  } catch (_) { /* ignore */ }
}

function toggleSidebarCollapsed() {
  setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
}

function openMobileSidebar() {
  document.body.classList.add("sidebar-mobile-open");
  if (els.sidebarOverlay) {
    els.sidebarOverlay.hidden = false;
    els.sidebarOverlay.classList.add("is-visible");
    els.sidebarOverlay.setAttribute("aria-hidden", "false");
  }
  if (els.sidebarMobileTrigger) {
    els.sidebarMobileTrigger.setAttribute("aria-expanded", "true");
  }
}

function closeMobileSidebar() {
  document.body.classList.remove("sidebar-mobile-open");
  if (els.sidebarOverlay) {
    els.sidebarOverlay.hidden = true;
    els.sidebarOverlay.classList.remove("is-visible");
    els.sidebarOverlay.setAttribute("aria-hidden", "true");
  }
  if (els.sidebarMobileTrigger) {
    els.sidebarMobileTrigger.setAttribute("aria-expanded", "false");
  }
}

function isMobileViewport() {
  return window.matchMedia("(max-width: 768px)").matches;
}

function handleViewportChange() {
  if (!isMobileViewport()) {
    closeMobileSidebar();
  }
  if (chartSourceRows.length) {
    updateSparklines(chartSourceRows);
  }
  Object.values(chartInstances).forEach(chart => chart?.resize());
}

function initSidebar() {
  try {
    if (localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1") {
      setSidebarCollapsed(true);
    }
  } catch (_) { /* ignore */ }

  if (els.sidebarCollapse) {
    els.sidebarCollapse.addEventListener("click", toggleSidebarCollapsed);
  }

  if (els.sidebarMobileTrigger) {
    els.sidebarMobileTrigger.addEventListener("click", () => {
      if (document.body.classList.contains("sidebar-mobile-open")) {
        closeMobileSidebar();
      } else {
        openMobileSidebar();
      }
    });
  }

  if (els.sidebarOverlay) {
    els.sidebarOverlay.addEventListener("click", closeMobileSidebar);
  }

  window.addEventListener("resize", handleViewportChange);

  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (document.body.classList.contains("sidebar-mobile-open") && els.modal?.hidden) {
      closeMobileSidebar();
    }
  });
}

function switchTab(tabName) {
  if (!els.views[tabName]) return;
  activeTab = tabName;

  els.sidebarLinks.forEach(link => {
    const isActive = link.dataset.tab === tabName;
    link.classList.toggle("sidebar-link-active", isActive);
    link.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  Object.entries(els.views).forEach(([name, view]) => {
    if (!view) return;
    const isActive = name === tabName;
    view.classList.toggle("view-active", isActive);
    if (isActive) {
      view.removeAttribute("hidden");
    } else {
      view.setAttribute("hidden", "");
    }
  });

  updatePageHeader(tabName);
  closeMobileSidebar();

  if (tabName === "automacao" && typeof window.refreshAutomation === "function") {
    window.refreshAutomation();
  }
  if (tabName === "bot" && typeof window.refreshBotMonitor === "function") {
    window.refreshBotMonitor();
  }
  if (tabName === "sistema" && typeof window.refreshSystemStatus === "function") {
    window.refreshSystemStatus();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initSplashScreen();
  showTableSkeleton();

  const fetchTable = () => fetchNotificacoes({ withLoader: true });

  els.btnAplicar.addEventListener("click", fetchTable);
  els.modalClose.addEventListener("click", closeModal);
  els.modal.addEventListener("click", e => {
    if (e.target === els.modal) closeModal();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
  });

  els.filterStatus.addEventListener("change", fetchTable);
  els.filterCriticidade.addEventListener("change", fetchTable);
  els.filterLimit.addEventListener("change", fetchTable);
  els.filterSearch?.addEventListener("input", applySearchFilter);
  els.btnLimparFiltros?.addEventListener("click", clearFilters);
  els.btnExportCsv?.addEventListener("click", exportCsv);

  initSidebar();

  els.sidebarLinks.forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      switchTab(link.dataset.tab);
    });
  });

  document.querySelector(".sidebar-brand")?.addEventListener("click", e => e.preventDefault());

  (async () => {
    try {
      await fetchUnidades();
      await refreshAll();
    } finally {
      await hideSplashScreen();
    }
    startAutoRefresh();
  })();
});
