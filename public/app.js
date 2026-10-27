const $ = (id) => document.getElementById(id);
const CURRENCY = '₹'; // change to '$' or '' if you prefer

const state = { books: [], authors: [], stock: null, threshold: 5 };
let openBookId = null; // book currently shown in the review dialog

// ---------- Helpers ----------

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

const money = (n) => (n == null ? '' : CURRENCY + Number(n).toLocaleString('en-IN', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
}));
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString() : '');
const stars = (n) => '★'.repeat(Math.round(n)) + '☆'.repeat(5 - Math.round(n));

const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* ignore */ } },
};

async function api(url, options) {
  try {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, data };
  } catch {
    return { ok: false, data: { error: 'Could not reach the server' } };
  }
}

const send = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

function setMsg(id, text, isError = false) {
  const el = $(id);
  el.textContent = text;
  el.className = isError ? 'error' : '';
}

// Show a note under a table: empty message, or an error if loading failed
function tableNote(id, rows, res, emptyText) {
  const el = $(id);
  if (!res.ok) {
    el.textContent = `Could not load data: ${res.data.error || 'unknown error'}`;
    el.className = 'note error';
    el.hidden = false;
  } else {
    el.textContent = emptyText;
    el.className = 'note';
    el.hidden = rows.length > 0;
  }
}

function debounce(fn, ms = 250) {
  let t;
  return () => { clearTimeout(t); t = setTimeout(fn, ms); };
}

// ---------- Stock status ----------

function stockStatus(n) {
  if (n <= 0) return 'out';
  return n <= state.threshold ? 'low' : 'ok';
}

function statusBadge(status) {
  if (status === 'out') return '<span class="badge out">Out of stock</span>';
  if (status === 'low') return '<span class="badge low">Low</span>';
  return '<span class="badge">OK</span>';
}

async function refreshStock() {
  const res = await api('/api/stock');
  if (res.ok) {
    state.stock = res.data;
    state.threshold = res.data.threshold;
  } else {
    state.stock = null;
  }
  renderAlert();
  renderStock();
  renderBooks();
}

// Warning banner, shown on every tab
function renderAlert() {
  const el = $('alert');
  const s = state.stock;
  const bad = s ? s.books.filter((b) => b.status !== 'ok') : [];
  if (!bad.length) { el.hidden = true; return; }
  const names = bad.slice(0, 3).map((b) => `${b.title} (${b.stock_quantity})`).join(', ');
  const more = bad.length > 3 ? ` and ${bad.length - 3} more` : '';
  el.innerHTML =
    `⚠️ <strong>${s.summary.low} low</strong> and <strong>${s.summary.out} out of stock</strong> ` +
    `(warning at ${s.threshold} or fewer): ${esc(names)}${esc(more)}. ` +
    '<a href="#" data-goto="stock">View stock</a>';
  el.hidden = false;
}

$('alert').addEventListener('click', (e) => {
  if (e.target.dataset.goto) { e.preventDefault(); showTab(e.target.dataset.goto); }
});

function renderStock() {
  const s = state.stock;
  if (!s) return;
  $('stock-cards').innerHTML = [
    ['Titles', s.summary.titles, ''],
    ['Units in stock', s.summary.units, ''],
    ['Low stock', s.summary.low, 'low'],
    ['Out of stock', s.summary.out, 'out'],
  ].map(([label, n, kind]) => `
    <div class="stat ${n && kind ? kind : ''}">
      <div class="stat-n">${esc(n)}</div><div class="stat-l">${label}</div>
    </div>`).join('');

  if (document.activeElement !== $('threshold')) $('threshold').value = s.threshold;
  $('threshold-hint').textContent = s.can_save
    ? ''
    : 'This threshold can only be saved after you run setup_cart_stock.sql once (see README). Until then 5 is used.';

  const rows = s.books.filter((b) => !$('only-low').checked || b.status !== 'ok');
  $('stock-rows').innerHTML = rows.map((b) => `
    <tr class="row-${b.status}">
      <td>${esc(b.title)}</td>
      <td>${esc(b.author_name)}</td>
      <td><strong>${esc(b.stock_quantity)}</strong></td>
      <td>${statusBadge(b.status)}</td>
      <td>${esc(b.sold_30d)}</td>
      <td>${b.days_left == null ? '<span class="muted">—</span>' : `${esc(b.days_left)} days`}</td>
      <td>${money(b.unit_price)}</td>
    </tr>`).join('');
  $('stock-empty').hidden = rows.length > 0;
}

$('only-low').addEventListener('change', renderStock);

$('threshold-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const res = await api('/api/settings/low-stock', send('PUT', { threshold: $('threshold').value }));
  if (!res.ok) return setMsg('threshold-message', res.data.error || 'Could not save', true);
  setMsg('threshold-message', 'Saved');
  refreshStock();
});

// ---------- Tabs ----------

const loaders = {
  books: () => Promise.all([loadAuthors(), loadBooks()]),
  stock: () => Promise.resolve(),
  cart: loadCartTab,
  orders: loadOrders,
  customers: loadCustomers,
  authors: loadAuthors,
};

function showTab(name) {
  document.querySelectorAll('.tabs [data-tab]').forEach((b) =>
    b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  loaders[name]();
  refreshStock(); // keeps the warning banner and stock numbers fresh
}

document.querySelector('.tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-tab]');
  if (btn) showTab(btn.dataset.tab);
});

// ---------- Authors ----------

async function loadAuthors() {
  const res = await api('/api/authors');
  state.authors = res.ok ? res.data : [];

  const current = $('author_id').value;
  $('author_id').innerHTML = '<option value="">Choose…</option>' + state.authors
    .map((a) => `<option value="${a.author_id}">${esc(a.author_name)}</option>`).join('');
  $('author_id').value = current;

  $('author-rows').innerHTML = state.authors.map((a) => `
    <tr>
      <td>${esc(a.author_id)}</td>
      <td>${esc(a.author_name)}</td>
      <td>${esc(a.country)}</td>
      <td>${esc(a.book_count)}</td>
    </tr>`).join('');
  tableNote('authors-empty', state.authors, res, 'No authors found.');
}

$('author-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const res = await api('/api/authors', send('POST', {
    author_name: $('a_name').value,
    country: $('a_country').value,
  }));
  if (!res.ok) return setMsg('author-message', res.data.error || 'Could not add author', true);
  $('author-form').reset();
  setMsg('author-message', 'Author added');
  loadAuthors();
});

// ---------- Books ----------

const bookFields = ['title', 'author_id', 'isbn', 'unit_price', 'stock_quantity', 'publication_year'];

async function loadBooks() {
  const q = encodeURIComponent($('search').value.trim());
  const res = await api(`/api/books?q=${q}&sort=${$('sort').value}`);
  state.books = res.ok ? res.data : [];
  renderBooks();
  tableNote('empty', state.books, res, 'No books found.');
}

function ratingCell(b) {
  if (!b.review_count) return '<span class="muted">No reviews</span>';
  return `<span class="stars">★</span> ${esc(b.avg_rating)} <span class="muted">(${esc(b.review_count)})</span>`;
}

function renderBooks() {
  $('book-rows').innerHTML = state.books.map((b) => {
    const status = stockStatus(b.stock_quantity);
    return `
    <tr>
      <td><a href="#" class="title-link" data-reviews="${b.book_id}">${esc(b.title)}</a></td>
      <td>${esc(b.author_name)}</td>
      <td>${esc(b.isbn)}</td>
      <td>${money(b.unit_price)}</td>
      <td>${esc(b.stock_quantity)} ${status === 'ok' ? '' : statusBadge(status)}</td>
      <td>${esc(b.publication_year)}</td>
      <td>${ratingCell(b)}</td>
      <td class="row-actions">
        <button class="small" data-reviews="${b.book_id}">Reviews</button>
        <button class="small secondary" data-edit="${b.book_id}">Edit</button>
        <button class="small danger" data-delete="${b.book_id}">Delete</button>
      </td>
    </tr>`;
  }).join('');
}

function resetBookForm() {
  $('book-form').reset();
  $('book_id').value = '';
  $('stock_quantity').value = 0;
  $('form-title').textContent = 'Add a book';
  $('save-btn').textContent = 'Add book';
  $('cancel-btn').hidden = true;
}

function startEdit(id) {
  const b = state.books.find((x) => x.book_id === id);
  if (!b) return;
  $('book_id').value = b.book_id;
  bookFields.forEach((f) => { $(f).value = b[f] ?? ''; });
  $('form-title').textContent = 'Edit book';
  $('save-btn').textContent = 'Save changes';
  $('cancel-btn').hidden = false;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

$('book-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const id = $('book_id').value;
  const payload = {};
  bookFields.forEach((f) => { payload[f] = $(f).value; });

  const res = await api(id ? `/api/books/${id}` : '/api/books', send(id ? 'PUT' : 'POST', payload));
  if (!res.ok) return setMsg('message', res.data.error || 'Something went wrong', true);

  setMsg('message', id ? 'Book updated' : 'Book added');
  resetBookForm();
  loadBooks();
  loadAuthors();
  refreshStock();
});

$('cancel-btn').addEventListener('click', () => { resetBookForm(); setMsg('message', ''); });

$('book-rows').addEventListener('click', async (e) => {
  const target = e.target.closest('[data-reviews], [data-edit], [data-delete]');
  if (!target) return;
  e.preventDefault();

  if (target.dataset.reviews) return openReviews(Number(target.dataset.reviews));
  if (target.dataset.edit) return startEdit(Number(target.dataset.edit));

  if (confirm('Delete this book and its reviews?')) {
    const res = await api(`/api/books/${target.dataset.delete}`, { method: 'DELETE' });
    setMsg('message', res.ok ? 'Book deleted' : (res.data.error || 'Could not delete book'), !res.ok);
    if (!res.ok) window.scrollTo({ top: 0, behavior: 'smooth' });
    loadBooks();
    refreshStock();
  }
});

$('search').addEventListener('input', debounce(loadBooks));
$('sort').addEventListener('change', loadBooks);

// ---------- Reviews dialog ----------

const reviewDialog = $('review-dialog');

async function openReviews(id) {
  const b = state.books.find((x) => x.book_id === id);
  if (!b) return;
  openBookId = id;
  $('rv-title').textContent = b.title;
  $('rv-author').textContent = b.author_name ? `by ${b.author_name}` : '';
  $('review-form').reset();
  setMsg('rv-message', '');
  $('rv-list').innerHTML = '';
  $('rv-summary').textContent = 'Loading reviews…';
  reviewDialog.showModal();

  // Opening a book counts as viewing it (drives "Recently viewed")
  api(`/api/books/${id}/view`, { method: 'POST' })
    .then(() => { if ($('sort').value === 'recent_viewed') loadBooks(); });

  await loadReviews();
}

async function loadReviews() {
  const res = await api(`/api/books/${openBookId}/reviews`);
  if (!res.ok) {
    $('rv-summary').textContent = res.data.error || 'Could not load reviews';
    return;
  }
  const reviews = res.data;

  if (!reviews.length) {
    $('rv-summary').textContent = 'No reviews yet. Be the first!';
  } else {
    const avg = reviews.reduce((s, r) => s + r.rating, 0) / reviews.length;
    $('rv-summary').innerHTML =
      `<span class="stars">${stars(avg)}</span> ${avg.toFixed(1)} ` +
      `<span class="muted">· ${reviews.length} review${reviews.length === 1 ? '' : 's'}</span>`;
  }

  $('rv-list').innerHTML = reviews.map((r) => `
    <li>
      <div class="rv-top">
        <strong>${esc(r.reviewer)}</strong>
        <span class="stars">${stars(r.rating)}</span>
        <span class="muted">${fmtDate(r.created_at)}</span>
        <button class="small secondary" data-del-review="${r.review_id}">Delete</button>
      </div>
      ${r.review_text ? `<p>${esc(r.review_text)}</p>` : ''}
    </li>`).join('');
}

$('review-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const res = await api(`/api/books/${openBookId}/reviews`, send('POST', {
    reviewer: $('rv-name').value,
    rating: $('rv-rating').value,
    review_text: $('rv-text').value,
  }));
  if (!res.ok) return setMsg('rv-message', res.data.error || 'Could not post review', true);
  $('review-form').reset();
  setMsg('rv-message', 'Review posted');
  await loadReviews();
  loadBooks(); // refresh the average rating in the table
});

$('rv-list').addEventListener('click', async (e) => {
  const id = e.target.dataset.delReview;
  if (!id || !confirm('Delete this review?')) return;
  await api(`/api/reviews/${id}`, { method: 'DELETE' });
  await loadReviews();
  loadBooks();
});

// ---------- Cart (prices locked when a book is first added) ----------

async function loadCartTab() {
  const [cres, bres, sres] = await Promise.all([
    api('/api/customers'),
    api('/api/books?sort=title_asc'),
    api('/api/order-statuses'),
  ]);

  const customers = cres.ok ? cres.data : [];
  const chosen = $('cart-customer').value || store.get('cartCustomer') || '';
  customers.sort((a, b) => `${a.first_name} ${a.last_name}`.localeCompare(`${b.first_name} ${b.last_name}`));
  $('cart-customer').innerHTML = '<option value="">Choose a customer…</option>' + customers.map((c) =>
    `<option value="${c.customer_id}">${esc(c.first_name)} ${esc(c.last_name)} (${esc(c.email)})</option>`).join('');
  $('cart-customer').value = customers.some((c) => String(c.customer_id) === chosen) ? chosen : '';

  const books = bres.ok ? bres.data : [];
  const chosenBook = $('cart-book').value;
  $('cart-book').innerHTML = '<option value="">Choose a book…</option>' + books.map((b) => `
    <option value="${b.book_id}" ${b.stock_quantity <= 0 ? 'disabled' : ''}>
      ${esc(b.title)} — ${esc(b.author_name || '')} · ${money(b.unit_price)} · ${b.stock_quantity} in stock
    </option>`).join('');
  $('cart-book').value = chosenBook;

  // Use statuses already present in your orders so the database accepts the value
  const statuses = sres.ok && sres.data.length ? sres.data : ['Pending'];
  const keep = $('cart-status').value;
  const preferred = statuses.includes(keep) ? keep
    : (statuses.find((s) => /pend|new|process|placed/i.test(s)) || statuses[0]);
  $('cart-status').innerHTML = statuses.map((s) =>
    `<option value="${esc(s)}" ${s === preferred ? 'selected' : ''}>${esc(s)}</option>`).join('');

  await loadCart();
}

let cartItems = [];

async function loadCart() {
  const cid = $('cart-customer').value;
  store.set('cartCustomer', cid);
  $('checkout-btn').disabled = true;

  if (!cid) {
    cartItems = [];
    $('cart-rows').innerHTML = '';
    $('cart-total').textContent = '';
    $('cart-empty').textContent = 'Choose a customer to see their cart.';
    $('cart-empty').className = 'note';
    $('cart-empty').hidden = false;
    return;
  }

  const res = await api(`/api/customers/${cid}/cart`);
  cartItems = res.ok ? res.data : [];
  $('cart-rows').innerHTML = cartItems.map((i) => {
    const changed = Number(i.current_price) !== Number(i.locked_price);
    return `
    <tr>
      <td>${esc(i.title)}</td>
      <td>${esc(i.author_name)}</td>
      <td><input class="qty" type="number" min="1" max="${esc(i.stock_quantity)}"
                 value="${esc(i.quantity)}" data-qty="${i.book_id}" /></td>
      <td>
        ${money(i.locked_price)} <span title="Price locked when added to the cart">🔒</span>
        ${changed ? `<div class="muted">Price is now ${money(i.current_price)}; yours stays ${money(i.locked_price)}</div>` : ''}
      </td>
      <td>${money(i.line_total)}</td>
      <td class="row-actions"><button class="small secondary" data-remove="${i.book_id}">Remove</button></td>
    </tr>`;
  }).join('');
  $('cart-total').textContent = money(cartItems.reduce((s, i) => s + Number(i.line_total || 0), 0));

  if (!res.ok) {
    $('cart-empty').textContent = `Could not load the cart: ${res.data.error || 'unknown error'}`;
    $('cart-empty').className = 'note error';
  } else {
    $('cart-empty').textContent = 'This cart is empty.';
    $('cart-empty').className = 'note';
  }
  $('cart-empty').hidden = cartItems.length > 0;
  $('checkout-btn').disabled = cartItems.length === 0;
}

$('cart-customer').addEventListener('change', () => {
  setMsg('cart-message', '');
  $('cart-warn').textContent = '';
  loadCart();
});

$('cart-add-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const cid = $('cart-customer').value;
  if (!cid) return setMsg('cart-message', 'Choose a customer first', true);
  if (!$('cart-book').value) return setMsg('cart-message', 'Choose a book', true);

  const res = await api(`/api/customers/${cid}/cart`, send('POST', {
    book_id: $('cart-book').value,
    quantity: $('cart-qty').value,
  }));
  if (!res.ok) return setMsg('cart-message', res.data.error || 'Could not add to cart', true);

  setMsg('cart-message', `Added. Price locked at ${money(res.data.locked_price)}`);
  $('cart-warn').textContent = '';
  $('cart-qty').value = 1;
  loadCart();
});

$('cart-rows').addEventListener('change', async (e) => {
  const book = e.target.dataset.qty;
  if (!book) return;
  const res = await api(`/api/customers/${$('cart-customer').value}/cart/${book}`,
    send('PUT', { quantity: e.target.value }));
  setMsg('cart-message', res.ok ? 'Quantity updated' : (res.data.error || 'Could not update'), !res.ok);
  loadCart();
});

$('cart-rows').addEventListener('click', async (e) => {
  const book = e.target.dataset.remove;
  if (!book) return;
  await api(`/api/customers/${$('cart-customer').value}/cart/${book}`, { method: 'DELETE' });
  setMsg('cart-message', 'Removed from cart');
  loadCart();
});

$('checkout-btn').addEventListener('click', async () => {
  const cid = $('cart-customer').value;
  const total = cartItems.reduce((s, i) => s + Number(i.line_total || 0), 0);
  if (!cid || !confirm(`Place this order for ${money(total)}?`)) return;

  const res = await api(`/api/customers/${cid}/checkout`, send('POST', { status: $('cart-status').value }));
  if (!res.ok) {
    setMsg('cart-message', res.data.error || 'Could not place the order', true);
    $('cart-warn').textContent = '';
    return loadCartTab();
  }
  setMsg('cart-message', `Order #${res.data.order_id} placed. Total ${money(res.data.total)}`);
  $('cart-warn').textContent = res.data.low_stock.length
    ? 'Heads up, now low on stock: ' + res.data.low_stock.map((b) => `${b.title} (${b.stock_quantity} left)`).join(', ')
    : '';
  await loadCartTab();
  refreshStock();
});

// ---------- Orders ----------

async function loadOrders() {
  const q = encodeURIComponent($('order-search').value.trim());
  const res = await api(`/api/orders?q=${q}`);
  const orders = res.ok ? res.data : [];
  $('order-rows').innerHTML = orders.map((o) => `
    <tr>
      <td>#${esc(o.order_id)}</td>
      <td>${esc(o.customer_name)}</td>
      <td>${fmtDate(o.order_date)}</td>
      <td><span class="badge">${esc(o.order_status)}</span></td>
      <td>${esc(o.item_count)}</td>
      <td>${money(o.total)}</td>
      <td class="row-actions"><button class="small" data-order="${o.order_id}">Details</button></td>
    </tr>`).join('');
  tableNote('orders-empty', orders, res, 'No orders found.');
}

$('order-search').addEventListener('input', debounce(loadOrders));

$('order-rows').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-order]');
  if (btn) openOrder(Number(btn.dataset.order));
});

const orderDialog = $('order-dialog');

async function openOrder(id) {
  $('od-title').textContent = `Order #${id}`;
  $('od-meta').textContent = '';
  $('od-rows').innerHTML = '';
  $('od-total').textContent = '';
  $('od-message').textContent = 'Loading…';
  orderDialog.showModal();

  const res = await api(`/api/orders/${id}`);
  if (!res.ok) {
    $('od-message').textContent = res.data.error || 'Could not load order';
    return;
  }
  const o = res.data;
  $('od-meta').textContent =
    `${o.customer_name || 'Unknown customer'}${o.email ? ' · ' + o.email : ''} · ` +
    `${fmtDate(o.order_date)} · ${o.order_status || ''}`;
  $('od-rows').innerHTML = o.items.map((i) => `
    <tr>
      <td>${esc(i.title)}</td>
      <td>${esc(i.author_name)}</td>
      <td>${esc(i.quantity)}</td>
      <td>${money(i.unit_price)}</td>
      <td>${money(i.line_total)}</td>
    </tr>`).join('');
  $('od-total').textContent = money(o.items.reduce((s, i) => s + Number(i.line_total || 0), 0));
  $('od-message').textContent = o.items.length ? '' : 'This order has no items.';
}

// ---------- Customers ----------

async function loadCustomers() {
  const q = encodeURIComponent($('customer-search').value.trim());
  const res = await api(`/api/customers?q=${q}`);
  const customers = res.ok ? res.data : [];
  $('customer-rows').innerHTML = customers.map((c) => `
    <tr>
      <td>${esc(c.customer_id)}</td>
      <td>${esc(c.first_name)} ${esc(c.last_name)}</td>
      <td>${esc(c.email)}</td>
      <td>${esc(c.phone)}</td>
      <td>${fmtDate(c.registration_date)}</td>
      <td>${esc(c.order_count)}</td>
    </tr>`).join('');
  tableNote('customers-empty', customers, res, 'No customers found.');
}

$('customer-search').addEventListener('input', debounce(loadCustomers));

$('customer-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const res = await api('/api/customers', send('POST', {
    first_name: $('c_first').value,
    last_name: $('c_last').value,
    email: $('c_email').value,
    phone: $('c_phone').value,
  }));
  if (!res.ok) return setMsg('customer-message', res.data.error || 'Could not add customer', true);
  $('customer-form').reset();
  setMsg('customer-message', 'Customer added');
  loadCustomers();
});

// ---------- Dialogs: close buttons and backdrop click ----------

$('rv-close').addEventListener('click', () => reviewDialog.close());
$('od-close').addEventListener('click', () => orderDialog.close());
[reviewDialog, orderDialog].forEach((d) =>
  d.addEventListener('click', (e) => { if (e.target === d) d.close(); }));

showTab('books');