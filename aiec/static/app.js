const toast = document.getElementById('toast');
let toastTimer;
function showToast(message) {
  if (!toast) return;
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 5000);
}

const conversationList = document.querySelector('[data-conversation-list]');
const search = document.getElementById('conversation-search');
const rows = [...document.querySelectorAll('[data-conversation-row]')];
const normalize = (text) => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es');
const searchableRows = rows.map((row) => ({ row, text: normalize(row.querySelector('.client-link').textContent) }));
if (search) {
  search.addEventListener('input', () => {
    const query = normalize(search.value.trim());
    let visible = 0;
    searchableRows.forEach(({ row, text }) => {
      row.hidden = !text.includes(query);
      if (!row.hidden) visible += 1;
    });
    const empty = document.querySelector('[data-search-empty]');
    if (empty) empty.hidden = visible !== 0;
    if (conversationList) conversationList.hidden = visible === 0;
    const counter = document.querySelector('[data-result-count]');
    if (counter) counter.textContent = `${visible} ${visible === 1 ? 'conversación' : 'conversaciones'} en esta vista`;
  });
}

document.querySelectorAll('[data-view]').forEach((button) => {
  button.addEventListener('click', () => {
    if (!conversationList) return;
    conversationList.dataset.currentView = button.dataset.view;
    document.querySelectorAll('[data-view]').forEach((option) => {
      const selected = option === button;
      option.classList.toggle('selected', selected);
      option.setAttribute('aria-pressed', String(selected));
    });
  });
});

document.querySelectorAll('form').forEach((form) => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = true;
      button.textContent = 'Procesando…';
    }
  });
});

const catalogSearch = document.getElementById('catalog-search');
const providerSelect = document.getElementById('catalog-provider');
const priceCards = [...document.querySelectorAll('[data-price-card]')];
function filterCatalog() {
  const query = normalize(catalogSearch?.value.trim() || '');
  const provider = providerSelect?.value || '';
  let visible = 0;
  priceCards.forEach((card) => {
    card.hidden = !normalize(card.textContent).includes(query) || (provider && card.dataset.provider !== provider);
    if (!card.hidden) visible += 1;
  });
  const empty = document.getElementById('catalog-empty');
  if (empty) empty.hidden = visible > 0;
}
catalogSearch?.addEventListener('input', filterCatalog);
providerSelect?.addEventListener('change', filterCatalog);

if (document.body.dataset.live === 'true') {
  const initialVersion = document.body.dataset.stateVersion;
  const indicator = document.querySelector('[data-live-status]');
  async function refreshPanel() {
    try {
      if (!document.hidden) {
        const response = await fetch('/api/panel', { cache: 'no-store', credentials: 'same-origin' });
        if (!response.ok) throw new Error('Panel unavailable');
        const state = await response.json();
        const busy = document.activeElement?.matches('input,textarea,select') ||
          document.querySelector('details[open]') || Boolean(search?.value.trim());
        if (state.version !== initialVersion && !busy) {
          location.reload();
          return;
        }
        if (indicator) indicator.textContent = 'Conversaciones actualizadas en vivo';
      }
    } catch {
      if (indicator) indicator.textContent = 'Reconectando con el panel…';
    }
    setTimeout(refreshPanel, 7000);
  }
  setTimeout(refreshPanel, 7000);
}
