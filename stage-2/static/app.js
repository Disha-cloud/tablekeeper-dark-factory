/* Tablekeeper browser app: vanilla JS single-page UI, no external requests. */
(() => {
  'use strict';

  /* ------------------------------------------------------------ helpers -- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const enc = encodeURIComponent;

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'html') el.innerHTML = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  function uuid() {
    const c = window.crypto;
    if (c && c.randomUUID) return c.randomUUID();
    const b = new Uint8Array(16);
    if (c && c.getRandomValues) c.getRandomValues(b);
    else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const x = Array.from(b, (n) => n.toString(16).padStart(2, '0')).join('');
    return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
  }

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  function fmtDate(iso) {
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    return `${DOW[dow]} ${d} ${MON[m - 1]} ${y}`;
  }
  const fmtLocal = (s) => `${fmtDate(s)} at ${s.slice(11, 16)}`;
  const tableName = (label) => (String(label).length <= 3 ? `Table ${label}` : String(label));
  const todayLocal = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };

  /* ------------------------------------------------------------ session -- */
  const SKEY = 'tk.session';
  function loadSession() {
    try {
      const s = JSON.parse(localStorage.getItem(SKEY));
      if (s && typeof s.token === 'string' && s.token) return s;
    } catch (e) { /* ignore */ }
    return null;
  }
  let session = loadSession();
  function setSession(s) {
    session = s;
    try {
      if (s) localStorage.setItem(SKEY, JSON.stringify(s));
      else localStorage.removeItem(SKEY);
    } catch (e) { /* ignore */ }
    renderHeader();
  }

  /* ---------------------------------------------------------------- api -- */
  async function api(method, path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    const init = { method, headers, signal: opts.signal };
    if (opts.body !== undefined) {
      init.body = JSON.stringify(opts.body);
      headers['Content-Type'] = 'application/json';
    }
    if (opts.auth !== false && session) headers.Authorization = `Bearer ${session.token}`;
    const res = await fetch(path, init); // rejects on network failure
    const text = await res.text(); // rejects if the connection drops mid-body
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    return { status: res.status, data };
  }
  const errCode = (r) => (r.data && r.data.error && r.data.error.code) || '';
  const errMsg = (r) => (r.data && r.data.error && r.data.error.message) || '';

  const MESSAGES = {
    table_unavailable: 'Sorry, that table was just taken. We have refreshed the availability — please choose another table or time.',
    party_exceeds_capacity: 'That party is too large for the selected table(s). Try a bigger table or a combined option.',
    outside_opening_hours: 'The restaurant is not open at that time.',
    not_on_slot_grid: 'That is not a bookable start time.',
    invalid_local_time: 'That time does not exist on that date (clocks change).',
    combination_not_allowed: 'Those tables cannot be combined.',
    validation_failed: 'Please check the details and try again.',
    not_found: 'We could not find that.',
    cutoff_passed: 'This reservation is too close to its start time to be changed online. Please contact the restaurant.',
    reservation_cancelled: 'This reservation has already been cancelled.',
  };
  function friendly(r) {
    const code = errCode(r);
    if (MESSAGES[code]) return MESSAGES[code];
    return errMsg(r) || `Something went wrong (HTTP ${r.status}). Please try again.`;
  }

  /* ------------------------------------------------------------- header -- */
  function navItem(href, label) {
    const cur = normPath() === href;
    return h('li', {}, h('a', { href, 'data-nav': '', 'aria-current': cur ? 'page' : null }, label));
  }
  function renderHeader() {
    const hd = $('#site-header');
    hd.replaceChildren(
      h('a', { class: 'brand', href: '/', 'data-nav': '' },
        h('span', { html: '<svg class="brand-mark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" fill="none" stroke="#f6e7c8" stroke-width="5"/><circle cx="32" cy="32" r="10" fill="#f6e7c8"/></svg>' }),
        'Tablekeeper'),
      h('nav', { 'aria-label': 'Main', style: 'flex:1 1 auto' },
        h('ul', { class: 'nav' },
          navItem('/', 'Search'),
          navItem('/lookup', 'Look up'),
          session ? null : [navItem('/login', 'Sign in'), navItem('/signup', 'Sign up')])),
      session ? h('div', { class: 'header-user' },
        h('span', { class: 'user-chip', 'data-testid': 'current-user', title: 'Signed in' }, session.name),
        h('button', { type: 'button', class: 'btn header-btn', 'data-testid': 'logout-button', onclick: logout }, 'Sign out')) : null);
  }
  function logout() {
    setSession(null);
    render();
  }

  /* ------------------------------------------------------------- router -- */
  const main = () => $('#main');
  const normPath = () => location.pathname.replace(/\/+$/, '') || '/';
  const routes = { '/': renderSearch, '/signup': renderSignup, '/login': renderLogin, '/lookup': renderLookup };
  const titles = { '/': 'Find a table', '/signup': 'Create account', '/login': 'Sign in', '/lookup': 'Look up a reservation' };
  let ctx = null;

  function navigate(path) {
    if (path !== location.pathname + location.search) history.pushState({}, '', path);
    render();
    window.scrollTo(0, 0);
  }
  function render() {
    if (ctx && ctx.cleanup) ctx.cleanup();
    ctx = {};
    const p = normPath();
    const fn = routes[p] || renderSearch;
    const root = main();
    root.replaceChildren();
    document.title = `${titles[p] || 'Tablekeeper'} — Tablekeeper`;
    fn(root, ctx);
    renderHeader();
  }
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest('a[data-nav]');
    if (!a) return;
    e.preventDefault();
    navigate(a.getAttribute('href'));
  });
  window.addEventListener('popstate', render);

  const loadingBox = (text, testid) => h('div', { class: 'loading', role: 'status', 'data-testid': testid }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), text);
  const field = (id, label, input, hint) => h('div', { class: 'field' }, h('label', { for: id }, label), input, hint ? h('span', { class: 'hint' }, hint) : null);

  /* ------------------------------------------------------- signup/login -- */
  function authScreen(root, cfg) {
    const errBox = h('div', { 'aria-live': 'assertive' });
    const showErr = (msg) => errBox.replaceChildren(h('div', { class: 'notice error', role: 'alert', 'data-testid': 'auth-error' }, msg));
    const submit = h('button', { type: 'submit', class: 'btn', 'data-testid': cfg.submitId }, cfg.submitLabel);
    let busy = false;
    const form = h('form', {
      class: 'form-stack', novalidate: true,
      onsubmit: async (e) => {
        e.preventDefault();
        if (busy) return;
        busy = true; submit.setAttribute('aria-busy', 'true');
        try {
          const r = await api('POST', cfg.path, { body: cfg.body(), auth: false });
          if ((r.status === 200 || r.status === 201) && r.data && r.data.token) {
            errBox.replaceChildren();
            setSession({ token: r.data.token, userId: r.data.user_id, name: r.data.display_name });
            navigate('/');
            return;
          }
          showErr(cfg.explain(r));
        } catch (err) {
          showErr('We could not reach the server. Please check your connection and try again.');
        } finally { busy = false; submit.removeAttribute('aria-busy'); }
      },
    }, cfg.fields, errBox, submit);
    root.append(h('section', { class: 'card narrow' }, h('h1', {}, cfg.title), h('p', { class: 'hero' }, cfg.lead), form,
      h('p', { class: 'auth-foot' }, cfg.foot)));
  }

  function renderSignup(root) {
    const name = h('input', { id: 'su-name', type: 'text', autocomplete: 'name', 'data-testid': 'signup-display-name' });
    const email = h('input', { id: 'su-email', type: 'email', autocomplete: 'email', 'data-testid': 'signup-email' });
    const pw = h('input', { id: 'su-pw', type: 'password', autocomplete: 'new-password', 'data-testid': 'signup-password' });
    authScreen(root, {
      title: 'Create your account', lead: 'Sign up to book tables and manage your reservations.',
      path: '/auth/signup', submitId: 'signup-submit', submitLabel: 'Create account',
      fields: [field('su-name', 'Your name', name), field('su-email', 'Email', email), field('su-pw', 'Password', pw, 'At least 8 characters.')],
      body: () => ({ email: email.value.trim(), password: pw.value, display_name: name.value.trim() }),
      explain: (r) => {
        const c = errCode(r);
        if (c === 'email_taken') return 'That email is already registered. Try signing in instead.';
        if (r.status === 422) return `Please check your details: ${errMsg(r) || 'something looks wrong'}.`;
        return friendly(r);
      },
      foot: [ 'Already have an account? ', h('a', { href: '/login', 'data-nav': '' }, 'Sign in') ],
    });
  }
  function renderLogin(root) {
    const email = h('input', { id: 'li-email', type: 'email', autocomplete: 'email', 'data-testid': 'login-email' });
    const pw = h('input', { id: 'li-pw', type: 'password', autocomplete: 'current-password', 'data-testid': 'login-password' });
    authScreen(root, {
      title: 'Welcome back', lead: 'Sign in to book and manage your reservations.',
      path: '/auth/login', submitId: 'login-submit', submitLabel: 'Sign in',
      fields: [field('li-email', 'Email', email), field('li-pw', 'Password', pw)],
      body: () => ({ email: email.value.trim(), password: pw.value }),
      explain: (r) => (r.status === 401 ? 'That email and password do not match. Please try again.' : friendly(r)),
      foot: [ 'New here? ', h('a', { href: '/signup', 'data-nav': '' }, 'Create an account') ],
    });
  }

  /* ------------------------------------------------------ search screen -- */
  const UNCERTAIN = 'We did not receive a response, so we cannot tell whether your booking went through. Press “Confirm reservation” again to check — retrying is safe and will never book twice.';

  function renderSearch(root, c) {
    const st = { seq: 0, ctrl: null, params: null, avail: null, rest: null, form: null, alive: true };
    c.cleanup = () => { st.alive = false; st.seq += 1; if (st.ctrl) st.ctrl.abort(); };

    const authBox = h('div', { 'aria-live': 'assertive' });
    const resultsBox = h('div', { 'aria-live': 'polite' });
    const formBox = h('div');
    const restSel = h('select', { id: 'f-rest', 'data-testid': 'restaurant-select' }, h('option', { value: '' }, 'Loading restaurants…'));
    const dateIn = h('input', { id: 'f-date', type: 'date', value: todayLocal(), 'data-testid': 'date-input' });
    const partyIn = h('input', { id: 'f-party', type: 'number', min: '1', step: '1', value: '2', inputmode: 'numeric', 'data-testid': 'party-size-input' });
    const searchBtn = h('button', { type: 'submit', class: 'btn', 'data-testid': 'search-button' }, 'Find a table');
    const restNote = h('div');

    const showAuthError = (msg) => authBox.replaceChildren(
      h('div', { class: 'notice error', role: 'alert', 'data-testid': 'auth-error' }, msg + ' ', h('a', { href: '/login', 'data-nav': '' }, 'Sign in')));
    const clearAuthError = () => authBox.replaceChildren();

    root.append(
      h('section', { class: 'hero' }, h('h1', {}, 'Find your table'),
        h('p', {}, 'Choose a restaurant, a day and your party size to see every table that is open. Combined tables are offered for larger parties.')),
      authBox,
      h('form', { class: 'card', novalidate: true, 'aria-label': 'Search availability', onsubmit: (e) => { e.preventDefault(); runSearch(); } },
        h('div', { class: 'search-grid' },
          field('f-rest', 'Restaurant', restSel), field('f-date', 'Date', dateIn),
          field('f-party', 'Party size', partyIn), searchBtn), restNote),
      resultsBox, formBox);

    resultsBox.append(h('div', { class: 'empty' }, h('span', { class: 'big' }, 'Ready when you are'), 'Pick a restaurant and press “Find a table”.'));

    async function loadRestaurants() {
      restNote.replaceChildren();
      try {
        const r = await api('GET', '/restaurants', { auth: false });
        if (!st.alive) return;
        if (r.status !== 200 || !r.data || !Array.isArray(r.data.restaurants)) throw new Error('bad');
        const list = r.data.restaurants;
        if (!list.length) {
          restSel.replaceChildren(h('option', { value: '' }, 'No restaurants available'));
          restNote.append(h('div', { class: 'notice info' }, 'No restaurants are available right now.'));
          return;
        }
        restSel.replaceChildren(...list.map((x) => h('option', { value: x.id }, x.name)));
      } catch (e) {
        if (!st.alive) return;
        restSel.replaceChildren(h('option', { value: '' }, 'Could not load restaurants'));
        restNote.append(h('div', { class: 'notice error', role: 'alert', 'data-testid': 'restaurants-error' },
          'We could not load the restaurant list. ',
          h('button', { type: 'button', class: 'btn secondary', onclick: () => { restSel.replaceChildren(h('option', { value: '' }, 'Loading restaurants…')); loadRestaurants(); } }, 'Try again')));
      }
    }
    loadRestaurants();

    function searchError(msg) {
      resultsBox.replaceChildren(h('div', { class: 'notice error', role: 'alert', 'data-testid': 'search-error' }, msg));
    }

    function runSearch() {
      const p = { rid: restSel.value, date: dateIn.value, party: partyIn.value.trim() };
      if (!p.rid) { searchError('Please choose a restaurant first.'); return; }
      if (!/^\d{4}-\d{2}-\d{2}$/.test(p.date)) { searchError('Please pick a date.'); return; }
      if (!/^[0-9]+$/.test(p.party) || Number(p.party) < 1) { searchError('Please enter a party size of 1 or more.'); return; }
      closeForm();
      clearAuthError();
      startSearch(p, false);
    }

    function startSearch(p, keepResults) {
      const mine = ++st.seq;
      if (st.ctrl) st.ctrl.abort();
      const ctrl = new AbortController();
      st.ctrl = ctrl;
      st.params = p;
      if (!keepResults) resultsBox.replaceChildren(loadingBox('Looking for tables…', 'search-loading'));
      const q = `/availability?restaurant_id=${enc(p.rid)}&date=${enc(p.date)}&party_size=${enc(p.party)}`;
      Promise.all([
        api('GET', q, { auth: false, signal: ctrl.signal }),
        api('GET', `/restaurants/${enc(p.rid)}`, { auth: false, signal: ctrl.signal }),
      ]).then(([a, r]) => {
        if (mine !== st.seq || !st.alive) return; // a newer search owns the screen
        if (a.status !== 200 || r.status !== 200 || !a.data || !r.data) {
          searchError(a.status === 404 || r.status === 404 ? 'We could not find that restaurant.' : friendly(a.status !== 200 ? a : r));
          return;
        }
        st.avail = a.data; st.rest = r.data;
        renderResults();
      }).catch((e) => {
        if (mine !== st.seq || !st.alive || (e && e.name === 'AbortError')) return;
        searchError('We could not load availability. Please check your connection and try again.');
      });
    }

    const labelOf = (rest, id) => {
      const t = (rest.tables || []).find((x) => x.id === id);
      return tableName(t ? t.label : id);
    };
    const seatsOf = (rest, id) => {
      const t = (rest.tables || []).find((x) => x.id === id);
      return t ? t.capacity : 0;
    };

    function renderResults() {
      const { avail, rest, params } = st;
      if (!avail.slots.length) {
        resultsBox.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'empty', 'data-testid': 'no-slots' },
          h('span', { class: 'big' }, 'No tables to book on this day'),
          `${rest.name} has no bookable times on ${fmtDate(params.date)}. Try another date.`)));
        return;
      }
      const party = Number(params.party);
      const times = avail.slots.map((s) => s.starts_at_local.slice(11, 16));
      const freeSingles = avail.slots.map((s) => new Set(s.available_table_ids));
      const freeOptions = avail.slots.map((s) => new Set((s.available_options || []).map((o) => o.table_ids.join('+'))));

      const mkCell = (ids, i, isFree) => {
        const time = times[i];
        const names = ids.map((id) => labelOf(rest, id)).join(' + ');
        return h('td', {}, h('button', {
          type: 'button', class: 'cell', 'data-testid': `slot-${ids.join('+')}-${time}`,
          'data-available': isFree ? 'true' : 'false',
          'aria-label': `${names} at ${time}, ${isFree ? 'available' : 'not available'}`,
          onclick: () => { if (isFree) pick({ ids, local: avail.slots[i].starts_at_local }); },
        }, isFree ? 'Open' : 'Taken'));
      };
      const rows = [];
      for (const t of rest.tables || []) {
        rows.push(h('tr', {}, h('th', { scope: 'row' }, h('span', { class: 'row-label' }, tableName(t.label), h('small', {}, `seats ${t.capacity}`))),
          times.map((_, i) => mkCell([t.id], i, freeSingles[i].has(t.id)))));
      }
      for (const pair of rest.combinable || []) {
        const cap = seatsOf(rest, pair[0]) + seatsOf(rest, pair[1]);
        if (cap < party) continue;
        rows.push(h('tr', { class: 'combo-row' }, h('th', { scope: 'row' },
          h('span', { class: 'row-label' }, h('span', { class: 'combo-tag' }, 'Combined'), h('br'),
            pair.map((id) => labelOf(rest, id)).join(' + '), h('small', {}, `seats ${cap} together`))),
        times.map((_, i) => mkCell(pair, i, freeOptions[i].has(pair.join('+'))))));
      }
      const open = avail.slots.reduce((n, s) => n + (s.available_options || []).length, 0);
      resultsBox.replaceChildren(h('div', { class: 'card', 'data-testid': 'availability-grid' },
        h('div', { class: 'grid-head' }, h('h2', {}, rest.name),
          h('span', { class: 'meta' }, `${fmtDate(params.date)} · party of ${params.party} · times are local to the restaurant`)),
        h('div', { class: 'legend', 'aria-hidden': 'true' }, h('span', { class: 'l-open' }, 'Open'), h('span', { class: 'l-taken' }, 'Taken'), h('span', { class: 'l-sel' }, 'Selected')),
        open === 0 ? h('div', { class: 'notice info' }, 'Everything is taken for this party size. Try another day or a smaller party.') : null,
        h('div', { class: 'grid-scroll', role: 'region', 'aria-label': 'Availability, scrolls sideways', tabindex: '0' },
          h('table', { class: 'slots' },
            h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Seating'), times.map((t) => h('th', { scope: 'col' }, t)))),
            h('tbody', {}, rows)))));
      markSelected();
    }

    function markSelected() {
      for (const b of resultsBox.querySelectorAll('.cell.selected')) { b.classList.remove('selected'); b.removeAttribute('aria-pressed'); }
      const f = st.form;
      if (!f) return;
      const b = resultsBox.querySelector(`[data-testid="${CSS.escape(`slot-${f.ids.join('+')}-${f.local.slice(11, 16)}`)}"]`);
      if (b) { b.classList.add('selected'); b.setAttribute('aria-pressed', 'true'); if (b.textContent === 'Open') b.textContent = 'Selected'; }
    }

    function closeForm() { st.form = null; formBox.replaceChildren(); }

    function pick(opt) {
      if (!session) { showAuthError('Please sign in to book a table.'); return; }
      clearAuthError();
      const f = st.form;
      if (f && f.inflight) return;
      if (f && f.ids.join('+') === opt.ids.join('+') && f.local === opt.local) { f.el.party.focus(); return; }
      openForm(opt);
      for (const b of resultsBox.querySelectorAll('.cell.selected')) { b.classList.remove('selected'); b.removeAttribute('aria-pressed'); if (b.textContent === 'Selected') b.textContent = 'Open'; }
      markSelected();
    }

    /* ------------------------------------------------------ booking form -- */
    function openForm(opt) {
      const f = { ids: opt.ids.slice(), local: opt.local, key: null, lastBody: null, confirmedBody: null, inflight: false, rest: st.rest, params: st.params, el: {} };
      st.form = f;
      const names = f.ids.map((id) => labelOf(f.rest, id)).join(' + ');
      const summary = h('div', { class: 'booking-summary', 'data-testid': 'booking-summary' }, `${f.rest.name} · ${names} · ${fmtLocal(f.local)}`);
      const party = h('input', { id: 'bk-party', type: 'number', min: '1', step: '1', inputmode: 'numeric', value: f.params.party, 'data-testid': 'booking-party-size',
        oninput: () => { const b = currentBody(f); if (b === null || JSON.stringify(b) !== f.confirmedBody) hideConfirmation(f); } });
      const submit = h('button', { type: 'submit', class: 'btn', 'data-testid': 'booking-submit' }, 'Confirm reservation');
      const msgs = h('div', { 'aria-live': 'assertive' });
      const conf = h('div', { 'aria-live': 'polite' });
      f.el = { party, submit, msgs, conf };
      const form = h('form', { class: 'card booking', novalidate: true, 'data-testid': 'booking-form', onsubmit: (e) => { e.preventDefault(); submitBooking(f); } },
        h('h2', {}, 'Reserve your table'), summary,
        h('div', { class: 'row' }, field('bk-party', 'Party size', party, `Seats up to ${f.ids.reduce((n, id) => n + seatsOf(f.rest, id), 0)}.`), submit),
        msgs, conf);
      formBox.replaceChildren(form);
      form.scrollIntoView({ block: 'nearest' });
    }

    function currentBody(f) {
      const n = f.el.party.value.trim();
      if (!/^[0-9]+$/.test(n) || Number(n) < 1) return null;
      const body = { restaurant_id: f.params.rid };
      if (f.ids.length === 1) body.table_id = f.ids[0]; else body.table_ids = f.ids.slice();
      body.starts_at_local = f.local;
      body.party_size = Number(n);
      return body;
    }

    function setNotice(f, kind, text) {
      if (kind === 'error') f.el.msgs.replaceChildren(h('div', { class: 'notice error', role: 'alert', 'data-testid': 'booking-error' }, h('strong', {}, 'We could not book that'), text));
      else if (kind === 'uncertain') f.el.msgs.replaceChildren(h('div', { class: 'notice uncertain', role: 'status', 'data-testid': 'booking-uncertain' }, h('strong', {}, 'Not sure it went through'), text));
      else f.el.msgs.replaceChildren();
    }
    function hideConfirmation(f) { f.el.conf.replaceChildren(); }

    function showConfirmation(f, data) {
      const ids = Array.isArray(data.table_ids) && data.table_ids.length ? data.table_ids : [data.table_id];
      const names = ids.map((id) => labelOf(f.rest, id)).join(' + ');
      f.el.conf.replaceChildren(h('div', { class: 'confirmation', 'data-testid': 'confirmation' },
        h('h3', {}, 'You are booked. See you soon!'),
        h('span', { class: 'ref-label' }, 'Confirmation reference'),
        h('span', { class: 'ref', 'data-testid': 'confirmation-reference' }, data.reference),
        h('p', { 'data-testid': 'confirmation-details' }, `${f.rest.name} · ${names} · ${fmtLocal(data.starts_at_local)} · party of ${data.party_size}`),
        h('p', {}, 'Seating: ', h('strong', { 'data-testid': 'confirmation-tables' }, names)),
        h('p', {}, h('a', { href: `/lookup?ref=${enc(data.reference)}`, 'data-nav': '' }, 'Look up or cancel this booking later'))));
    }

    async function submitBooking(f) {
      if (f.inflight || st.form !== f) return;
      const body = currentBody(f);
      if (!body) { setNotice(f, 'error', 'Please enter a party size of 1 or more.'); return; }
      const bs = JSON.stringify(body);
      if (bs !== f.lastBody) { f.key = uuid(); f.lastBody = bs; } // new body -> new request identity
      if (bs !== f.confirmedBody) hideConfirmation(f);
      f.inflight = true;
      f.el.submit.setAttribute('aria-busy', 'true');
      f.el.party.readOnly = true;
      const done = () => { f.inflight = false; f.el.submit.removeAttribute('aria-busy'); f.el.party.readOnly = false; };
      let r;
      try {
        r = await api('POST', '/reservations', { body, headers: { 'Idempotency-Key': f.key } });
      } catch (e) {
        done();
        if (st.form === f) setNotice(f, 'uncertain', UNCERTAIN);
        return;
      }
      done();
      if (st.form !== f) return;
      if ((r.status === 201 || r.status === 200) && r.data && typeof r.data.reference === 'string') {
        f.confirmedBody = bs;
        setNotice(f, null);
        showConfirmation(f, r.data);
      } else if (r.status >= 500 || (r.status < 400)) {
        setNotice(f, 'uncertain', UNCERTAIN);
      } else if (r.status === 401) {
        setSession(null);
        setNotice(f, null);
        showAuthError('Your session has ended. Please sign in again to book.');
      } else if (r.status === 409 && errCode(r) === 'table_unavailable') {
        hideConfirmation(f);
        setNotice(f, 'error', MESSAGES.table_unavailable);
        startSearch(st.params, true);
      } else {
        hideConfirmation(f);
        setNotice(f, 'error', friendly(r));
      }
    }
  }

  /* ------------------------------------------------------ lookup screen -- */
  function renderLookup(root, c) {
    let seq = 0;
    let alive = true;
    c.cleanup = () => { alive = false; seq += 1; };
    const prefill = new URLSearchParams(location.search).get('ref') || '';
    const input = h('input', { id: 'lk-ref', type: 'text', autocomplete: 'off', autocapitalize: 'characters', spellcheck: 'false', value: prefill, 'data-testid': 'lookup-reference-input' });
    const out = h('div', { 'aria-live': 'polite' });
    const btn = h('button', { type: 'submit', class: 'btn', 'data-testid': 'lookup-submit' }, 'Look up');
    root.append(
      h('section', { class: 'hero' }, h('h1', {}, 'Look up a reservation'),
        h('p', {}, 'Enter the confirmation reference you were given to check, or cancel, your booking.')),
      h('form', { class: 'card', novalidate: true, onsubmit: (e) => { e.preventDefault(); lookup(); } },
        h('div', { class: 'inline-form' }, field('lk-ref', 'Confirmation reference', input), btn),
        session ? null : h('div', { class: 'notice info' }, 'You need to be signed in to look up a reservation. ', h('a', { href: '/login', 'data-nav': '' }, 'Sign in'))),
      out);

    const errBox = (msg) => h('div', { class: 'notice error', role: 'alert', 'data-testid': 'reservation-error' }, msg);

    async function lookup() {
      const ref = input.value.trim();
      const mine = ++seq;
      if (!ref) { out.replaceChildren(errBox('Please enter a confirmation reference.')); return; }
      if (!session) { out.replaceChildren(errBox('Please sign in to look up your reservations.')); return; }
      out.replaceChildren(loadingBox('Looking up your reservation…', 'lookup-loading'));
      try {
        const r = await api('GET', `/reservations/${enc(ref)}`);
        if (mine !== seq || !alive) return;
        if (r.status === 401) { setSession(null); out.replaceChildren(errBox('Your session has ended. Please sign in again to look up reservations.')); return; }
        if (r.status === 404) { out.replaceChildren(errBox('We could not find a reservation with that reference. Check the code and try again.')); return; }
        if (r.status !== 200 || !r.data) { out.replaceChildren(errBox(friendly(r))); return; }
        let rest = null;
        try {
          const rr = await api('GET', `/restaurants/${enc(r.data.restaurant_id)}`, { auth: false });
          if (rr.status === 200) rest = rr.data;
        } catch (e) { /* labels fall back to ids */ }
        if (mine !== seq || !alive) return;
        showDetail(r.data, rest, mine);
      } catch (e) {
        if (mine !== seq || !alive) return;
        out.replaceChildren(errBox('We could not reach the server. Please try again.'));
      }
    }

    function showDetail(res, rest, mine, errText) {
      const ids = Array.isArray(res.table_ids) && res.table_ids.length ? res.table_ids : [res.table_id];
      const label = (id) => { const t = rest && (rest.tables || []).find((x) => x.id === id); return tableName(t ? t.label : id); };
      const names = ids.map(label).join(' + ');
      const cancelBtn = res.status === 'confirmed'
        ? h('button', { type: 'button', class: 'btn danger', 'data-testid': 'reservation-cancel-button', onclick: () => cancel(res, rest, mine, cancelBtn) }, 'Cancel reservation') : null;
      out.replaceChildren(
        h('div', { class: 'card', 'data-testid': 'reservation-detail' },
          h('h2', {}, rest ? rest.name : 'Your reservation'),
          h('p', {}, h('span', { class: `status-badge ${res.status}`, 'data-testid': 'reservation-status' }, res.status)),
          h('dl', { class: 'facts' },
            h('dt', {}, 'Reference'), h('dd', {}, res.reference),
            h('dt', {}, 'When'), h('dd', {}, fmtLocal(res.starts_at_local)),
            h('dt', {}, 'Seating'), h('dd', { 'data-testid': 'reservation-tables' }, names),
            h('dt', {}, 'Party'), h('dd', {}, `${res.party_size} ${res.party_size === 1 ? 'guest' : 'guests'}`)),
          cancelBtn ? h('div', { class: 'actions' }, cancelBtn) : null),
        errText ? errBox(errText) : null);
    }

    async function cancel(res, rest, mine, btn2) {
      if (btn2.getAttribute('aria-busy') === 'true') return;
      btn2.setAttribute('aria-busy', 'true');
      try {
        const r = await api('POST', `/reservations/${enc(res.reference)}/cancel`);
        if (mine !== seq || !alive) return;
        if (r.status === 200 && r.data) { showDetail(r.data, rest, mine); return; }
        if (r.status === 401) { setSession(null); showDetail(res, rest, mine, 'Your session has ended. Please sign in again.'); return; }
        showDetail(res, rest, mine, friendly(r));
      } catch (e) {
        if (mine !== seq || !alive) return;
        showDetail(res, rest, mine, 'We could not reach the server, so the cancellation may not have gone through. Look the booking up again to check its status.');
      }
    }
  }

  /* ---------------------------------------------------------------- go -- */
  render();
})();
