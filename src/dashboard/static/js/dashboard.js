/* Dashboard de Notificacoes - Eletrofrio
 * Read-only: este front-end apenas consulta o backend Flask.
 * Nenhuma acao de envio, edicao ou disparo eh disparada por aqui.
 */

const REFRESH_INTERVAL_MS = 15000;

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
  filterStatus: document.getElementById("filter-status"),
  filterCriticidade: document.getElementById("filter-criticidade"),
  filterLoja: document.getElementById("filter-loja"),
  filterLimit: document.getElementById("filter-limit"),
  kpis: {
    total: document.getElementById("kpi-total"),
    enviado: document.getElementById("kpi-enviado"),
    falhas: document.getElementById("kpi-falhas"),
    pendentes: document.getElementById("kpi-pendentes"),
    h24: document.getElementById("kpi-24h"),
    taxa: document.getElementById("kpi-taxa"),
  },
};

let unidadesMap = new Map();
let refreshTimer = null;

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
    const porStatus = json.por_status || {};
    const pendentes =
      (porStatus.pendente || 0) +
      (porStatus.pendente_retry || 0);

    els.kpis.total.textContent = json.total ?? 0;
    els.kpis.enviado.textContent = porStatus.enviado ?? 0;
    els.kpis.falhas.textContent = json.falhas ?? 0;
    els.kpis.pendentes.textContent = pendentes;
    els.kpis.h24.textContent = json.ultimas_24h ?? 0;
    els.kpis.taxa.textContent = `${json.taxa_sucesso ?? 0}%`;
  } catch (e) {
    console.error("Erro ao buscar stats:", e);
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

async function fetchNotificacoes() {
  try {
    const qs = buildQueryParams();
    const resp = await fetch(`/api/notificacoes?${qs}`);
    const json = await resp.json();
    if (!json.ok) {
      els.tbody.innerHTML = `<tr><td colspan="9" class="empty">Erro: ${escapeHtml(json.error || "desconhecido")}</td></tr>`;
      return;
    }
    renderRows(json.data || []);
    els.resultCount.textContent = `${json.count ?? 0} resultados`;

    const now = new Date();
    const stamp = now.toLocaleTimeString("pt-BR");
    els.lastUpdate.textContent = `Atualizado às ${stamp}`;
    els.footerFetched.textContent = `Última consulta: ${stamp}`;
  } catch (e) {
    console.error("Erro ao buscar notificações:", e);
    els.tbody.innerHTML = `<tr><td colspan="9" class="empty">Falha na comunicação com o servidor.</td></tr>`;
  }
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
        <td><span class="${getCritClass(r.criticidade)}">${escapeHtml((r.criticidade || "N/A").toUpperCase())}</span></td>
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
    if (els.autoRefresh.checked) refreshAll();
  }, REFRESH_INTERVAL_MS);
}

async function refreshAll() {
  await Promise.all([fetchStats(), fetchNotificacoes()]);
}

document.addEventListener("DOMContentLoaded", () => {
  els.btnAplicar.addEventListener("click", () => fetchNotificacoes());
  els.modalClose.addEventListener("click", closeModal);
  els.modal.addEventListener("click", e => {
    if (e.target === els.modal) closeModal();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
  });

  els.filterStatus.addEventListener("change", fetchNotificacoes);
  els.filterCriticidade.addEventListener("change", fetchNotificacoes);
  els.filterLimit.addEventListener("change", fetchNotificacoes);

  (async () => {
    await fetchUnidades();
    await refreshAll();
    startAutoRefresh();
  })();
});
