const toast = document.getElementById('toast');
let toastTimer;
function showToast(message) {
  if (!toast) return;
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 5000);
}

document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', async () => {
    const source = document.getElementById(button.dataset.copy);
    if (!source) return;
    try {
      await navigator.clipboard.writeText(source.textContent.trim());
      showToast('Instrucción copiada. Pégala en Claude Desktop.');
    } catch {
      const range = document.createRange();
      range.selectNodeContents(source);
      window.getSelection().removeAllRanges();
      window.getSelection().addRange(range);
      showToast('Texto seleccionado. Usa ⌘C o Ctrl+C para copiar.');
    }
  });
});

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
