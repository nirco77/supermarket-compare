const API = '';

let lastResult = null;
let selectedStore = null;  // for confirm purchase
let credStore = 'ah';

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  checkCredentialStatus();
  switchTab('search');
});

// ── Tabs ──────────────────────────────────────────────────────────────────────

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.style.display = 'none');
  document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
  document.getElementById(`tab-${tab}`).style.display = 'block';
  if (tab === 'history') loadHistory();
}

// ── Credential status pills ───────────────────────────────────────────────────

async function checkCredentialStatus() {
  try {
    const data = await apiFetch('/api/credentials/status');
    updatePill('pill-ah', data.ah, 'AH');
    updatePill('pill-jumbo', data.jumbo, 'Jumbo');
  } catch (_) {}
}

function updatePill(id, ok, label) {
  const el = document.getElementById(id);
  el.textContent = ok ? `${label} ✓` : `${label} —`;
  el.className = `pill ${ok ? 'pill-ok' : 'pill-off'}`;
}

// ── Search ────────────────────────────────────────────────────────────────────

async function doSearch() {
  const raw = document.getElementById('shopping-list').value.trim();
  if (!raw) {
    const warning = document.getElementById('empty-warning');
    const ta = document.getElementById('shopping-list');
    if (warning) warning.style.display = 'block';
    ta.focus();
    ta.style.borderColor = '#E30613';
    setTimeout(() => {
      ta.style.borderColor = '';
      if (warning) warning.style.display = 'none';
    }, 3000);
    return;
  }
  document.getElementById('empty-warning').style.display = 'none';

  const items = parseShoppingList(raw);
  if (!items.length) return;

  setSearching(true);

  try {
    const result = await apiFetch('/api/search', 'POST', { items });
    lastResult = result;
    renderResults(result);
  } catch (e) {
    alert('Search failed: ' + e.message);
  } finally {
    setSearching(false);
  }
}

function parseShoppingList(text) {
  return text.split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => {
      const m = line.match(/^(.+?)\s+(\d+)$/);
      return m
        ? { raw_text: m[1].trim(), quantity: parseInt(m[2]) }
        : { raw_text: line, quantity: 1 };
    });
}

function setSearching(on) {
  document.getElementById('search-btn').disabled = on;
  document.getElementById('spinner').classList.toggle('active', on);
  if (on) {
    document.getElementById('results').classList.remove('visible');
  }
}

// ── Render results ────────────────────────────────────────────────────────────

function renderResults(result) {
  renderRecommendations(result);
  renderItemsTable(result);
  document.getElementById('results').classList.add('visible');
  selectedStore = result.cheapest_single_store?.store || null;
  updateConfirmButton();
}

function renderRecommendations(result) {
  const single = result.cheapest_single_store;
  const split  = result.optimal_split;
  const wrap   = document.getElementById('recommendations');
  wrap.innerHTML = '';

  if (single) {
    const isBest = !split || single.total <= split.total;
    wrap.innerHTML += recCard({
      label: 'Alternative 1 — Single store',
      store: storeName(single.store),
      storeKey: single.store,
      total: single.total,
      savings: single.savings > 0 ? `€${single.savings.toFixed(2)} saved with discounts` : null,
      best: isBest,
      onclick: `selectStore('${single.store}')`,
    });
  }

  if (split) {
    const isBest = !single || split.total < single.total;
    const storeLabels = `${storeName(split.primary_store)} + ${storeName(split.secondary_store)}`;
    wrap.innerHTML += recCard({
      label: 'Alternative 2 — Split across 2 stores',
      store: storeLabels,
      storeKey: `${split.primary_store}+${split.secondary_store}`,
      total: split.total,
      savings: split.savings_vs_single_cheapest > 0
        ? `€${split.savings_vs_single_cheapest.toFixed(2)} cheaper than single-store`
        : null,
      best: isBest,
      onclick: `selectStore('${split.primary_store}+${split.secondary_store}')`,
    });
  }

  if (!single && !split) {
    wrap.innerHTML = '<p class="no-results">Could not find matching products in enough stores.</p>';
  }
}

function recCard({ label, store, storeKey, total, savings, best, onclick }) {
  const dots = storeKey.split('+').map(s =>
    `<span class="store-dot dot-${s}"></span>`
  ).join('');
  return `
    <div class="rec-card ${best ? 'best' : ''}" onclick="${onclick}" style="cursor:pointer">
      ${best ? '<span class="rec-badge">Best deal</span>' : ''}
      <div class="rec-label">${label}</div>
      <div class="rec-store">${dots}${store}</div>
      <div class="rec-total">€${total.toFixed(2)}</div>
      ${savings ? `<div class="rec-savings">✓ ${savings}</div>` : ''}
    </div>`;
}

function renderItemsTable(result) {
  const stores = ['ah', 'jumbo', 'dirk', 'lidl'];
  const thead = `
    <thead>
      <tr>
        <th>Product</th>
        ${stores.map(s => `<th>${storeLogo(s)}</th>`).join('')}
      </tr>
    </thead>`;

  const rows = result.items.map(ir => {
    const cells = stores.map(s => {
      const p = ir.best_per_store[s];
      if (!p) return `<td class="price-na">—</td>`;
      const hasDiscount = p.discount_price !== null && p.discount_price < p.price;
      const priceDisplay = hasDiscount
        ? `€${p.discount_price.toFixed(2)}<span class="price-orig">€${p.price.toFixed(2)}</span>`
        : `€${p.price.toFixed(2)}`;

      // Find cheapest store for this item
      const allPrices = stores
        .map(st => ir.best_per_store[st]?.effective_price)
        .filter(v => v !== undefined);
      const minPrice = Math.min(...allPrices);
      const isCheapest = p.effective_price === minPrice;

      const promoBadge = p.promotion_label
        ? `<div class="promo-badge">🏷 ${p.promotion_label}</div>`
        : '';

      const urlAttr = p.url ? `data-url="${p.url}" data-store="${s}"` : '';

      return `<td class="price-cell ${isCheapest ? 'cheapest' : ''} ${hasDiscount ? 'discounted' : ''}" ${urlAttr}>
        ${priceDisplay}
        <div style="font-size:0.73rem;color:#999">${p.name.length > 30 ? p.name.slice(0, 28) + '…' : p.name}</div>
        ${promoBadge}
      </td>`;
    }).join('');

    const bulkBadge = ir.bulk_suggestion
      ? `<span class="bulk-badge" title="${ir.bulk_suggestion.message}">💡 Bulk ${ir.bulk_suggestion.saving_percent}% off</span>`
      : '';

    const qty = ir.user_quantity > 1 ? ` ×${ir.user_quantity}` : '';
    return `<tr>
      <td><strong>${ir.query}</strong>${qty}${bulkBadge}</td>
      ${cells}
    </tr>`;
  }).join('');

  const tbody = document.getElementById('items-tbody');
  tbody.innerHTML = `${thead}<tbody>${rows}</tbody>`;

  tbody.addEventListener('click', (e) => {
    const td = e.target.closest('.price-cell[data-url]');
    if (!td) return;
    showStorePopup(td.dataset.url, td.dataset.store, td);
  });
}

function showStorePopup(url, store, anchor) {
  document.getElementById('store-popup')?.remove();

  const popup = document.createElement('div');
  popup.id = 'store-popup';
  popup.className = 'store-popup';

  const msg = document.createElement('div');
  msg.className = 'store-popup-msg';
  msg.innerHTML = `Open op <strong>${storeName(store)}</strong>?`;

  const actions = document.createElement('div');
  actions.className = 'store-popup-actions';

  const openBtn = document.createElement('button');
  openBtn.className = 'btn btn-primary';
  openBtn.style.cssText = 'padding:6px 14px;font-size:0.85rem';
  openBtn.textContent = 'Open';
  openBtn.addEventListener('click', () => { window.open(url, '_blank'); popup.remove(); });

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn btn-outline';
  cancelBtn.style.cssText = 'padding:6px 14px;font-size:0.85rem';
  cancelBtn.textContent = 'Annuleer';
  cancelBtn.addEventListener('click', () => popup.remove());

  actions.append(openBtn, cancelBtn);
  popup.append(msg, actions);

  const rect = anchor.getBoundingClientRect();
  popup.style.top  = (rect.bottom + window.scrollY + 6) + 'px';
  popup.style.left = (rect.left  + window.scrollX) + 'px';
  document.body.appendChild(popup);

  const dismiss = (e) => { if (!popup.contains(e.target) && e.target !== anchor) popup.remove(); };
  setTimeout(() => document.addEventListener('click', dismiss, { once: true }), 0);
}

function selectStore(storeKey) {
  selectedStore = storeKey;
  document.querySelectorAll('.rec-card').forEach(c => c.style.outline = 'none');
  event.currentTarget.style.outline = '2px solid #00AE43';
  updateConfirmButton();
}

function updateConfirmButton() {
  const btn = document.getElementById('confirm-btn');
  if (btn) btn.disabled = !selectedStore;
}

// ── Confirm purchase ──────────────────────────────────────────────────────────

async function confirmPurchase() {
  if (!lastResult || !selectedStore) return;

  const split = lastResult.optimal_split;
  const single = lastResult.cheapest_single_store;
  const isSplit = selectedStore.includes('+');

  let payload;
  if (isSplit && split) {
    const [primary, secondary] = selectedStore.split('+');
    // Save both stores' items
    await Promise.all([
      apiFetch('/api/confirm-purchase', 'POST', {
        store: primary,
        items: split.primary_items,
        quantities: {},
      }),
      apiFetch('/api/confirm-purchase', 'POST', {
        store: secondary,
        items: split.secondary_items,
        quantities: {},
      }),
    ]);
  } else if (single) {
    await apiFetch('/api/confirm-purchase', 'POST', {
      store: selectedStore,
      items: single.items,
      quantities: {},
    });
  }

  showToast('Purchase saved to history!');
  loadHistory();
}

// ── History tab ───────────────────────────────────────────────────────────────

async function loadHistory() {
  try {
    const stats = await apiFetch('/api/history/stats');
    renderStats(stats);
  } catch (_) {}

  const store = document.getElementById('filter-store')?.value || '';
  const name  = document.getElementById('filter-name')?.value  || '';
  const params = new URLSearchParams({ limit: 100 });
  if (store) params.set('store', store);
  if (name)  params.set('product_name', name);

  try {
    const data = await apiFetch(`/api/history?${params}`);
    renderHistoryTable(data.records || []);
  } catch (e) {
    document.getElementById('history-table-body').innerHTML =
      `<tr><td colspan="6" class="no-results">Could not load history</td></tr>`;
  }
}

function renderStats(stats) {
  document.getElementById('stat-purchases').textContent = stats.total_purchases;
  document.getElementById('stat-spent').textContent = `€${stats.total_spent.toFixed(2)}`;
  document.getElementById('stat-saved').textContent = `€${stats.total_saved.toFixed(2)}`;
}

function renderHistoryTable(records) {
  const tbody = document.getElementById('history-table-body');
  if (!records.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="no-results">No purchases recorded yet.</td></tr>';
    return;
  }
  tbody.innerHTML = records.map(r => `
    <tr>
      <td>${r.timestamp.slice(0, 10)}</td>
      <td><span class="store-dot dot-${r.store}"></span>${storeName(r.store)}</td>
      <td>${r.product_name}</td>
      <td>${r.quantity_bought}</td>
      <td>€${r.price_paid.toFixed(2)}</td>
      <td>${r.regular_price && r.regular_price > r.price_paid
        ? `<span style="color:#00AE43">−€${(r.regular_price - r.price_paid).toFixed(2)}</span>`
        : '—'
      }</td>
    </tr>`).join('');
}

// ── Credentials modal ─────────────────────────────────────────────────────────

function openCredsModal() {
  document.getElementById('creds-modal').classList.add('open');
  setCredStore('ah');
}

function closeCredsModal() {
  document.getElementById('creds-modal').classList.remove('open');
  document.getElementById('modal-status').textContent = '';
}

function setCredStore(store) {
  credStore = store;
  document.querySelectorAll('.store-select-btn').forEach(b => {
    b.className = 'store-select-btn';
    if (b.dataset.store === store) b.classList.add(`active-${store}`);
  });
}

async function saveModalCreds() {
  const username = document.getElementById('modal-username').value.trim();
  const password = document.getElementById('modal-password').value;
  if (!username || !password) return;

  const status = document.getElementById('modal-status');
  status.textContent = 'Saving…';
  status.className = 'modal-status';

  try {
    await apiFetch('/api/credentials', 'POST', { store: credStore, username, password });
    status.textContent = `✓ Saved for ${storeName(credStore)}`;
    status.className = 'modal-status ok';
    checkCredentialStatus();
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
    status.className = 'modal-status err';
  }
}

async function testLogin() {
  const status = document.getElementById('modal-status');
  status.textContent = 'Logging in…';
  status.className = 'modal-status';

  // Save first if fields are filled
  const username = document.getElementById('modal-username').value.trim();
  const password = document.getElementById('modal-password').value;
  if (username && password) {
    await apiFetch('/api/credentials', 'POST', { store: credStore, username, password });
  }

  try {
    const result = await apiFetch('/api/auth/login', 'POST', { store: credStore });
    if (result.success) {
      status.textContent = `✓ ${result.message} (${result.method})`;
      status.className = 'modal-status ok';
    } else {
      status.textContent = `✗ ${result.message}`;
      status.className = 'modal-status err';
    }
    checkCredentialStatus();
  } catch (e) {
    status.textContent = 'Login failed: ' + e.message;
    status.className = 'modal-status err';
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function storeName(key) {
  return { ah: 'Albert Heijn', jumbo: 'Jumbo', dirk: 'Dirk', lidl: 'Lidl' }[key] || key;
}

function storeLogo(key) {
  const srcs = { ah: '/logos/ah.svg', jumbo: '/logos/jumbo.svg', dirk: '/logos/dirk.svg', lidl: '/logos/lidl.svg' };
  const src = srcs[key];
  if (!src) return storeName(key);
  return `<img src="${src}" alt="${storeName(key)}" class="store-logo" onerror="this.replaceWith(document.createTextNode('${storeName(key)}'))">`;
}

async function apiFetch(path, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(API + path, opts);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

function showToast(msg) {
  const t = document.createElement('div');
  t.textContent = msg;
  Object.assign(t.style, {
    position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
    background: '#222', color: '#fff', padding: '10px 20px', borderRadius: '8px',
    fontSize: '0.9rem', zIndex: 999, boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
  });
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}
