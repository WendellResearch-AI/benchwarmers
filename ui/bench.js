// Benchwarmers. Plain DOM, no framework: serve.py renders the page, this only
// wires the controls. Every write goes through a POST that re-checks its own
// rules server-side, so nothing here is trusted to enforce anything.
(function () {
  'use strict';
  var D = window.BENCH || {models: {}, roles: {}, retired: {}, stamp: 0};
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function post(url, body) {
    return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify(body)}).then(function (r) { return r.json(); });
  }
  function listErrors(node, errors) { node.textContent = errors.map(function (x) { return '• ' + x; }).join('\n'); node.style.whiteSpace = 'pre-line'; }

  // ---- tabs, remembered per browser ----------------------------------
  var tabs = document.querySelectorAll('.tabs button[data-tab]');      // not the sidebar's own toggle
  var onShow = {};                     // a page's loader, run each time it is shown
  function show(id) {
    tabs.forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.tab === id)); });
    document.querySelectorAll('.tab').forEach(function (s) { s.classList.toggle('active', s.id === 'tab-' + id); });
    store('bench.tab', id);
    if (onShow[id]) onShow[id]();
  }
  tabs.forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.tab); }); });
  // A button that opens another page (Endpoints' Manage), from the top.
  document.querySelectorAll('[data-goto]').forEach(function (b) {
    b.addEventListener('click', function () { show(b.dataset.goto); window.scrollTo(0, 0); });
  });
  var wanted = store('bench.tab');
  show(wanted && $('tab-' + wanted) ? wanted : 'glance');

  // ---- sortable tables -------------------------------------------------
  function key(cell) {
    var raw = cell.dataset.sort != null ? cell.dataset.sort : cell.textContent.trim();
    var n = parseFloat(raw);
    return isNaN(n) || !/^-?[\d.]+/.test(raw) ? raw.toLowerCase() : n;
  }
  document.querySelectorAll('th[data-sortable]').forEach(function (th) {
    th.tabIndex = 0;
    function sort() {
      var table = th.closest('table'), body = table.tBodies[0];
      var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
      var dir = th.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending';
      table.querySelectorAll('th[aria-sort]').forEach(function (o) { o.removeAttribute('aria-sort'); });
      th.setAttribute('aria-sort', dir);
      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {
        var x = key(a.cells[idx]), y = key(b.cells[idx]);
        var c = (typeof x === typeof y) ? (x < y ? -1 : x > y ? 1 : 0) : (typeof x === 'number' ? -1 : 1);
        return dir === 'ascending' ? c : -c;
      });
      rows.forEach(function (r) { body.appendChild(r); });
    }
    th.addEventListener('click', sort);
    th.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sort(); } });
  });

  // ---- filter boxes: one per panel (or the whole page: data-filter="page"),
  // matching any text in a table row or a log line, and the panel's facet
  // selects (serve.facet), each matching a row's data-<facet> exactly ------
  function applyFilter(scope) {
    var box = scope.querySelector('input[data-filter]'), q = box ? box.value.trim().toLowerCase() : '';
    var facets = Array.prototype.filter.call(scope.querySelectorAll('select[data-facet]'), function (s) { return s.value; });
    scope.querySelectorAll('tbody tr, .log-line').forEach(function (r) {
      r.hidden = (q && r.textContent.toLowerCase().indexOf(q) === -1)
        || facets.some(function (s) { return r.dataset[s.dataset.facet] !== s.value; });
    });
    // A match inside a folded list (an endpoint's models) opens it.
    if (q) scope.querySelectorAll('details.models').forEach(function (d) {
      if (d.querySelector('tbody tr:not([hidden])')) d.open = true;
    });
  }
  document.querySelectorAll('input[data-filter], select[data-facet]').forEach(function (c) {
    var box = c.closest('.toolbar') ? c.closest('.toolbar').querySelector('input[data-filter]') : c;
    var scope = box && box.dataset.filter === 'page' ? c.closest('.tab') : c.closest('.panel');
    c.addEventListener(c.tagName === 'SELECT' ? 'change' : 'input', function () { applyFilter(scope); });
  });

  // ---- Action needed: Hide folds it to its heading, per browser, until an
  // action it has not seen appears or 12 hours pass, whichever is first ----
  var acts = $('actions');
  if (acts) {
    var keys = Array.prototype.map.call(acts.querySelectorAll('.problem[data-key]'), function (p) { return p.dataset.key; });
    var hid = null;
    try { hid = JSON.parse(store('bench.actions.hidden') || 'null'); } catch (err) { hid = null; }
    var fold = function (on) {
      acts.classList.toggle('collapsed', on);
      $('actions-hide').textContent = on ? 'show' : 'Hide';
      $('actions-hide').setAttribute('aria-expanded', String(!on));
    };
    fold(!!(hid && hid.keys && Date.now() - hid.at < 12 * 3600 * 1000
            && keys.every(function (k) { return hid.keys.indexOf(k) !== -1; })));
    $('actions-hide').addEventListener('click', function () {
      var on = !acts.classList.contains('collapsed');
      store('bench.actions.hidden', on ? JSON.stringify({at: Date.now(), keys: keys}) : '');
      fold(on);
    });
  }

  // ---- retired rows: hidden unless asked for ---------------------------
  var showRetired = store('bench.retired') === '1';
  function applyRetired() {
    document.body.classList.toggle('show-retired', showRetired);
    document.querySelectorAll('input[data-retired]').forEach(function (c) { c.checked = showRetired; });
  }
  document.querySelectorAll('input[data-retired]').forEach(function (c) {
    c.addEventListener('change', function () { showRetired = c.checked; store('bench.retired', showRetired ? '1' : '0'); applyRetired(); });
  });
  applyRetired();

  // ---- Discover: paid models hidden unless asked for -------------------
  var showPaid = store('bench.paid') === '1';
  function applyPaid() {
    document.body.classList.toggle('show-paid', showPaid);
    document.querySelectorAll('input[data-paid]').forEach(function (c) { c.checked = showPaid; });
  }
  document.querySelectorAll('input[data-paid]').forEach(function (c) {
    c.addEventListener('change', function () { showPaid = c.checked; store('bench.paid', showPaid ? '1' : '0'); applyPaid(); });
  });
  applyPaid();

  // ---- fresh results: offered, never forced ----------------------------
  var fresh = $('fresh');
  setInterval(function () {
    if (document.hidden || !fresh.hidden) return;
    fetch('/api/stamp').then(function (r) { return r.json(); }).then(function (s) {
      if (s.stamp > D.stamp + 0.5) fresh.hidden = false;
    }).catch(function () {});
  }, 15000);
  fresh.addEventListener('click', function () { location.reload(); });

  // ---- the model dialog ------------------------------------------------
  var md = $('model-dialog');
  var m = {title: $('m-title'), served: $('m-served'), editor: $('m-editor'), gone: $('m-gone'),
           alias: $('m-alias'), role: $('m-role'), roleWhy: $('m-role-why'), adhoc: $('m-adhoc'),
           danger: $('m-danger'), errors: $('m-errors'), done: $('m-done'), save: $('m-save'),
           restore: $('m-restore'), close: $('m-close'), removeBox: $('m-remove-box'),
           reason: $('m-reason'), typed: $('m-typed'), remove: $('m-remove'),
           qtypes: $('m-qtypes'), aliasWhy: $('m-alias-why')};
  m.aliasWhy.dataset.chat = m.aliasWhy.textContent;
  var editing = null;

  function openModel(alias) {
    var model = D.models[alias], gone = (D.retired || {})[alias];
    if (!model && !gone) return;
    editing = model ? alias : null;
    m.title.textContent = alias;
    m.errors.textContent = ''; m.done.textContent = '';
    m.editor.hidden = !model; m.save.hidden = !model; m.removeBox.hidden = !model;
    m.gone.hidden = !!model; m.restore.hidden = !!model;
    if (gone) {
      m.served.textContent = 'served as ' + gone.served + (gone.tag ? ' · ' + gone.tag : '');
      m.gone.textContent = '';
      m.gone.appendChild(el('strong', {}, 'Retired ' + gone.at.slice(0, 10) + (gone.by === 'auto' ? ' automatically' : ' by hand')));
      m.gone.appendChild(el('p', {style: 'margin:.3rem 0'}, gone.why || ''));
      m.gone.appendChild(el('p', {class: 'muted', style: 'margin:0'},
        'Its evidence is untouched. Restore puts back the same alias, role and endpoint; failures recorded before the restore are ignored.'));
      m.restore.dataset.alias = alias; m.restore.disabled = false;
    } else {
      m.served.textContent = 'serves ' + model.served + (model.tag ? ' · ' + model.tag : '');
      m.alias.value = alias;
      m.role.textContent = '';
      Object.keys(D.roles).forEach(function (r) {
        var o = el('option', {value: r}, r); if (r === model.role) o.selected = true; m.role.appendChild(o);
      });
      // A decision model answers typed questions, never chat: its role is fixed both ways.
      var decision = model.kind === 'decision';
      m.role.disabled = decision;
      m.qtypes.hidden = !decision;
      m.qtypes.querySelectorAll('input').forEach(function (c) {
        c.checked = decision && model.question_types.indexOf(c.value) !== -1;
      });
      fillFacts(alias, decision);
      m.aliasWhy.textContent = decision
        ? 'The name decide.py and the gateway call it by, and every record is keyed by. Letters, digits, dot, dash, underscore; unused, and never a retired name.'
        : m.aliasWhy.dataset.chat;
      Array.prototype.forEach.call(m.role.options, function (o) {
        o.disabled = model.kind !== 'decision' && o.value === 'decision';
      });
      m.adhoc.checked = !!model.ad_hoc;
      m.save.disabled = false; m.save.textContent = 'Save';
      m.reason.value = ''; m.typed.value = ''; m.removeBox.open = false; armRemove(); refresh();
    }
    md.showModal();
  }
  // ---- model facts: yours in the boxes, the sources' as placeholders -----
  var FACT_NUM = ['context_window', 'max_output'], FACT_CAP = ['tools', 'vision', 'structured_output', 'reasoning'];
  function fillFacts(alias, decision) {
    var box = $('m-facts'), f = (D.facts || {})[alias], profile = $('m-profile');
    box.hidden = profile.hidden = decision || !f;
    profile.textContent = '';
    $('f-errors').textContent = ''; $('f-done').textContent = '';
    if (box.hidden) return;
    var src = f.sources || {}, mine = function (k) { return src[k] === 'you'; };
    // At a glance, as models.dev shows a model: what goes in, what comes out.
    [['Input', 'input_modalities'], ['Output', 'output_modalities']].forEach(function (p) {
      var row = el('div', {class: 'prof'}, null);
      row.appendChild(el('span', {class: 'muted'}, p[0] + ' '));
      (f[p[1]] && f[p[1]].length ? f[p[1]] : ['unknown']).forEach(function (x) { row.appendChild(el('span', {class: 'chip'}, x)); });
      if (src[p[1]]) row.appendChild(el('span', {class: 'src'}, ' ' + src[p[1]]));
      profile.appendChild(row);
    });
    var bits = [], c = f.cost || {};
    if (f.release_date) bits.push('released ' + f.release_date);
    if (f.knowledge_cutoff) bits.push('knowledge to ' + f.knowledge_cutoff);
    if (f.open_weights != null) bits.push(f.open_weights ? 'open weights' : 'closed weights');
    if (c.input != null && c.output != null) {
      bits.push(c.input === 0 && c.output === 0 ? 'list price free'
        : 'list price $' + c.input + ' in / $' + c.output + ' out per 1M tokens (a free tier may still apply)');
    }
    if (bits.length) profile.appendChild(el('div', {class: 'prof muted'}, bits.join(' · ')));
    var show = function (k, v) { $('s-' + k).textContent = v == null ? 'unknown' : v + (src[k] ? ' · ' + src[k] : ''); };
    FACT_NUM.forEach(function (k) {
      $('f-' + k).value = mine(k) ? f[k] : '';
      $('f-' + k).placeholder = f[k] != null && !mine(k) ? f[k] : '';
      show(k, f[k] != null ? Number(f[k]).toLocaleString() : null);
    });
    FACT_CAP.forEach(function (k) {
      $('f-' + k).value = mine(k) ? (f[k] ? 'yes' : 'no') : '';
      // The blank choice is "whatever the sources say": say what that is.
      $('f-' + k).options[0].textContent = mine(k) ? 'auto (hand back to the sources)'
        : 'auto: ' + (f[k] == null ? 'unknown' : (f[k] ? 'yes' : 'no'));
      show(k, f[k] == null ? null : (f[k] ? 'yes' : 'no'));
    });
    $('f-knowledge_cutoff').value = mine('knowledge_cutoff') ? f.knowledge_cutoff : '';
    $('f-knowledge_cutoff').placeholder = !mine('knowledge_cutoff') && f.knowledge_cutoff ? f.knowledge_cutoff : '';
    show('knowledge_cutoff', f.knowledge_cutoff || null);
    var own = src.limits === 'you (this model)';
    document.querySelectorAll('#f-limits input').forEach(function (b) {
      var v = (f.limits || {})[b.dataset.k];
      b.value = own && v ? v : ''; b.placeholder = !own && v ? v : '—';
    });
    var lsrc = Object.keys(src).filter(function (k) { return k.indexOf('limits') === 0; }).map(function (k) { return src[k]; });
    $('s-limits').textContent = lsrc.length ? lsrc.join(' · ') : 'none known';
  }
  $('f-save').addEventListener('click', function () {
    if (!editing) return;
    var facts = {limits: {}};
    FACT_NUM.forEach(function (k) { if ($('f-' + k).value) facts[k] = parseInt($('f-' + k).value, 10); });
    FACT_CAP.forEach(function (k) { var v = $('f-' + k).value; if (v) facts[k] = v === 'yes'; });
    if ($('f-knowledge_cutoff').value.trim()) facts.knowledge_cutoff = $('f-knowledge_cutoff').value.trim();
    document.querySelectorAll('#f-limits input').forEach(function (b) { if (b.value) facts.limits[b.dataset.k] = parseInt(b.value, 10); });
    $('f-errors').textContent = ''; $('f-done').textContent = '';
    post('/api/model-facts', {alias: editing, facts: facts}).then(function (r) {
      if (!r.ok) { listErrors($('f-errors'), r.errors); return; }
      $('f-done').textContent = 'Saved. Reloading…'; setTimeout(function () { location.reload(); }, 700);
    });
  });

  function refresh() {
    m.danger.hidden = !(editing && m.alias.value.trim() !== editing);
    m.roleWhy.textContent = D.roles[m.role.value] || '';
  }
  function armRemove() { m.remove.disabled = !(editing && m.typed.value === editing && m.reason.value.trim().length >= 5); }
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a.model');
    if (a) { e.preventDefault(); openModel(a.dataset.alias); }
  });
  m.close.addEventListener('click', function () { md.close(); });
  m.alias.addEventListener('input', refresh);
  m.role.addEventListener('change', refresh);
  m.reason.addEventListener('input', armRemove);
  m.typed.addEventListener('input', armRemove);

  // A success is said, left up for two seconds, then the popup closes and
  // the page reloads with the change in it.
  function closeAndReload(dialog) {
    setTimeout(function () { dialog.close(); location.reload(); }, 2000);
  }
  function after(node, button, label) {
    return function (res) {
      if (!res.ok) { listErrors(node, res.errors); if (button) { button.disabled = false; button.textContent = label; } return; }
      m.done.textContent = 'Done: ' + (res.changed || []).join(', ') + (res.sync ? ' · ' + res.sync : '') + '. Closing…';
      closeAndReload(md);
    };
  }
  m.save.addEventListener('click', function () {
    if (!editing) return;
    var body = {alias: editing, new_alias: m.alias.value.trim(), new_role: m.role.value, new_ad_hoc: m.adhoc.checked};
    if (!m.qtypes.hidden) {
      body.new_question_types = Array.prototype.filter.call(m.qtypes.querySelectorAll('input'),
        function (c) { return c.checked; }).map(function (c) { return c.value; });
    }
    if (body.new_alias !== editing && !confirm('Rename ' + editing + ' to ' + body.new_alias + '? Every record keyed by the old alias follows it.')) return;
    m.save.disabled = true; m.save.textContent = 'Saving…'; m.errors.textContent = '';
    post('/api/model', body).then(after(m.errors, m.save, 'Save')).catch(function (err) { m.errors.textContent = String(err); m.save.disabled = false; m.save.textContent = 'Save'; });
  });
  m.remove.addEventListener('click', function () {
    if (!editing || !confirm('Retire ' + editing + '? The registry entry goes; the evidence stays, and Restore brings it back.')) return;
    m.remove.disabled = true;
    post('/api/remove', {alias: editing, typed: m.typed.value, reason: m.reason.value}).then(after(m.errors, null)).catch(function (err) { m.errors.textContent = String(err); armRemove(); });
  });
  m.restore.addEventListener('click', function () {
    m.restore.disabled = true; m.restore.textContent = 'Restoring…';
    post('/api/restore', {alias: m.restore.dataset.alias}).then(after(m.errors, m.restore, 'Restore')).catch(function (err) { m.errors.textContent = String(err); });
  });

  // ---- problem actions ---------------------------------------------------
  document.querySelectorAll('[data-act]').forEach(function (b) {
    b.addEventListener('click', function () {
      var act = b.dataset.act, aliases = (b.dataset.aliases || b.dataset.alias || '').split(' ').filter(Boolean);
      var status = b.closest('.problem').querySelector('.why');
      if (act === 'open') { openModel(aliases[0]); return; }
      if (act === 'retry') {                 // no confirm: one call, and only an answer changes anything
        b.disabled = true; b.textContent = 'Asking…';
        post('/api/retry', {alias: aliases[0]}).then(function (res) {
          if (!res.ok) { status.textContent = res.errors.join('; '); status.classList.add('err'); b.disabled = false; b.textContent = 'Retry'; return; }
          status.textContent = res.changed.join(' · ') + '. Reloading…'; setTimeout(function () { location.reload(); }, 900);
        }).catch(function (err) { status.textContent = String(err); b.disabled = false; b.textContent = 'Retry'; });
        return;
      }
      var verb = act === 'restore' ? 'Restore' : 'Retire';
      if (!confirm(verb + ' ' + aliases.join(', ') + '?' + (act === 'restore' ? '' : ' The evidence stays and Restore brings it back.'))) return;
      b.disabled = true; b.textContent = verb === 'Restore' ? 'Restoring…' : 'Retiring…';
      var chain = Promise.resolve([]);
      aliases.forEach(function (alias) {
        chain = chain.then(function (errs) {
          var call = act === 'restore' ? post('/api/restore', {alias: alias})
            : post('/api/remove', {alias: alias, typed: alias, reason: 'from the dashboard: ' + (b.dataset.why || 'retired by hand')});
          return call.then(function (res) { return errs.concat(res.ok ? [] : res.errors); });
        });
      });
      chain.then(function (errs) {
        if (errs.length) { status.textContent = errs.join('; '); status.classList.add('err'); b.disabled = false; b.textContent = verb; return; }
        status.textContent = 'Done. Reloading…'; setTimeout(function () { location.reload(); }, 700);
      });
    });
  });

  // ---- onboarding --------------------------------------------------------
  var od = $('onboard-dialog');
  var o = {find: $('o-find'), picker: $('o-picker'), errors: $('o-errors'), start: $('o-start'),
           close: $('o-close'), choose: $('o-choose'), run: $('o-run'), steps: $('o-steps'),
           log: $('o-log'), title: $('o-title'), result: $('o-result')};
  var STEPS = ['Served by the endpoint','Registered', 'Synced to the llm CLI', 'Availability probe, 4 calls', 'Internal suite'];
  var timer = null, data = null;

  function pickRow(c, dismissed) {
    var row = el('label', {class: 'pick'});
    var radio = el('input', {type: 'radio', name: 'o-pick', value: c.served, 'data-endpoint': c.endpoint});
    if (dismissed) radio.disabled = true;
    row.appendChild(radio);
    var name = el('span', {class: 'alias'}, c.served);
    row.appendChild(name);
    var side = el('span');
    if (c.new) side.appendChild(el('span', {class: 'chip new'}, 'new'));
    var b = el('button', {type: 'button', class: 'linkish', style: 'margin-left:.6rem'}, dismissed ? 'Bring back' : 'Dismiss');
    b.addEventListener('click', function (e) {
      e.preventDefault();
      post('/api/dismiss', {served: c.key, undo: dismissed}).then(loadCandidates);
    });
    side.appendChild(b);
    row.appendChild(side);
    radio.addEventListener('change', function () { o.start.disabled = false; });
    return row;
  }
  function renderPicker() {
    o.picker.textContent = '';
    if (!data) return;
    var q = o.find.value.trim().toLowerCase();
    var groups = {};
    data.candidates.forEach(function (c) {
      // The endpoint too: "Onboard a model from it" filters on it, and the default
      // endpoint's models are grouped by connection, not by its name.
      if (q && [c.served, c.group, c.endpoint].every(function (s) { return s.toLowerCase().indexOf(q) === -1; })) return;
      (groups[c.group] = groups[c.group] || []).push(c);
    });
    var names = Object.keys(groups).sort();
    if (!names.length) o.picker.appendChild(el('p', {class: 'empty'}, data.candidates.length ? 'Nothing matches.' : 'Everything the endpoints serve is already on the bench, retired or dismissed.'));
    names.forEach(function (g) {
      o.picker.appendChild(el('h3', {}, g + ' · ' + groups[g].length));
      groups[g].forEach(function (c) { o.picker.appendChild(pickRow(c, false)); });
    });
    if (data.dismissed.length) {
      var d = el('details'); d.appendChild(el('summary', {}, data.dismissed.length + ' dismissed'));
      data.dismissed.forEach(function (c) { d.appendChild(pickRow(c, true)); });
      o.picker.appendChild(d);
    }
  }
  function loadCandidates() {
    o.picker.textContent = ''; o.picker.appendChild(el('p', {class: 'empty'}, 'Asking the endpoints what they serve…'));
    o.start.disabled = true;
    o.find.readOnly = true;   // typing before the list arrives is lost, or lands on a row
    fetch('/api/candidates').then(function (r) { return r.json(); }).then(function (res) {
      o.find.readOnly = false; o.find.focus();
      if (!res.ok) { o.picker.textContent = ''; listErrors(o.errors, res.errors); return; }
      if (res.errors.length) listErrors(o.errors, res.errors);   // one endpoint down; the rest listed
      data = res; renderPicker();
    }).catch(function (err) { o.find.readOnly = false; o.errors.textContent = String(err); });
  }
  $('onboard-open').addEventListener('click', function () {
    o.errors.textContent = ''; o.find.value = ''; o.choose.hidden = false; o.run.hidden = true;
    o.start.hidden = false; o.start.textContent = 'Onboard'; o.title.textContent = 'Onboard a model';
    od.showModal(); o.find.focus(); loadCandidates();
  });
  o.find.addEventListener('input', renderPicker);
  o.close.addEventListener('click', function () { od.close(); });
  od.addEventListener('close', function () { if (timer) { clearInterval(timer); timer = null; } });

  function renderSteps(text, finished, exit) {
    var reached = 0;
    for (var i = 1; i <= 5; i++) if (text.indexOf('[' + i + '/5]') !== -1) reached = i;
    var stopped = /STOPPING|is not served|cannot reach/.test(text) || (finished && exit !== 0);
    var lines = text.split('\n');
    o.steps.textContent = '';
    STEPS.forEach(function (label, i) {
      var n = i + 1, li = el('li', {}, label);
      var line = lines.filter(function (l) { return l.indexOf('[' + n + '/5]') !== -1; })[0];
      if (line) li.appendChild(el('small', {}, line.replace(/^\s*\[\d\/5\]\s*/, '')));
      if (n < reached || (n === reached && finished && !stopped)) li.className = 'done';
      else if (n === reached && stopped) {
        li.className = 'fail';
        // Why it stopped, under the step that failed, not only in the full log.
        var at = text.search(/STOPPING|is not served|cannot reach/);
        if (at !== -1) li.appendChild(el('small', {}, text.slice(at).split('\n').slice(0, 3).join(' ').trim()));
      }
      else if (n === reached || (n === 1 && !reached && !finished)) li.className = 'now';
      o.steps.appendChild(li);
    });
  }
  o.start.addEventListener('click', function () {
    var pick = od.querySelector('input[name=o-pick]:checked');
    if (!pick) return;
    o.errors.textContent = ''; o.start.disabled = true; o.start.textContent = 'Starting…';
    post('/api/onboard', {served: pick.value, endpoint: pick.dataset.endpoint}).then(function (res) {
      if (!res.ok) { listErrors(o.errors, res.errors); o.start.disabled = false; o.start.textContent = 'Onboard'; return; }
      follow(res.log, 'Onboarding ' + pick.value + ' from ' + pick.dataset.endpoint, '');
    }).catch(function (err) { o.errors.textContent = String(err); o.start.disabled = false; o.start.textContent = 'Onboard'; });
  });
  // ---- Discover: a row opens its model, with every route to add it by -----
  // The rest of what is known comes from /api/discover-model, so the page does
  // not carry 1,200 models' details it will mostly never show.
  var dd = $('discover-dialog');
  function price(x) {
    var c = x.cost || {};
    return x.free ? 'free' : (c.input != null ? '$' + c.input + ' in / $' + c.output + ' out per 1M tokens' : 'paid');
  }
  function limits(l) {
    return Object.keys(l || {}).map(function (k) { return Number(l[k]).toLocaleString() + ' ' + k; }).join(' · ');
  }
  function addVia(x, b) {
    var direct = x.via === 'direct';
    if (!confirm('Add ' + x.model + ' via ' + x.label + (x.free ? '' : ' (PAID)') + '?\n\n'
      + (direct ? 'The bench only: register it on the ' + x.endpoint + ' endpoint, a 4-call probe, then the suite.'
        // A LiteLLM or CLI Proxy API route says its own steps: only what that hub takes.
        : x.steps ? x.steps.map(function (s, i) { return (i + 1) + '. ' + s; }).join('\n')
        : '1. Open WebUI: add it to the ' + x.connection + ' connection\'s Model IDs (if that connection lists them)\n'
        + '2. Open WebUI: a public model record, tagged and with the logo of the connection\'s other models\n'
        + '3. The bench: register, a 4-call probe, then the suite'))) return;
    b.disabled = true; b.textContent = 'Adding…'; $('d-errors').textContent = '';
    var call = direct ? post('/api/onboard', {served: x.model, endpoint: x.endpoint})
      : post('/api/discover/add', {connection: x.connection, model: x.model, endpoint: x.endpoint});
    call.then(function (res) {
      if (!res.ok && !res.log) {
        listErrors($('d-errors'), (res.steps || []).concat(res.errors));
        b.disabled = false; b.textContent = 'Add via this'; return;
      }
      dd.close(); o.errors.textContent = ''; od.showModal();
      follow(res.log, 'Adding ' + (res.served || x.model) + ' via ' + x.label,
             res.steps && res.steps.length ? res.steps.join('. ') + '. ' : '');
    }).catch(function (err) { $('d-errors').textContent = String(err); b.disabled = false; b.textContent = 'Add via this'; });
  }
  function openDiscover(provider, model) {
    $('d-errors').textContent = '';
    fetch('/api/discover-model?provider=' + encodeURIComponent(provider) + '&model=' + encodeURIComponent(model))
      .then(function (r) { return r.json(); }).then(function (res) {
        if (!res.ok) { listErrors($('d-errors'), res.errors); dd.showModal(); return; }
        if (res.errors.length) listErrors($('d-errors'), res.errors);   // a hub that could not be asked
        var r = res.model, p = $('d-profile'), list = $('d-routes');
        $('d-title').textContent = r.name;
        $('d-served').textContent = r.provider_name + ' · ' + r.model + ' · listed by ' + r.sources.join(' and ')
          + (r.on_bench_as ? ' · on the bench as ' + r.on_bench_as : '');
        p.textContent = '';
        [['Input', r.in_mods], ['Output', r.out_mods]].forEach(function (x) {
          var row = el('div', {class: 'prof'});
          row.appendChild(el('span', {class: 'muted'}, x[0] + ' '));
          (x[1] && x[1].length ? x[1] : ['unknown']).forEach(function (v) { row.appendChild(el('span', {class: 'chip'}, v)); });
          p.appendChild(row);
        });
        var can = [['tools', r.tools], ['vision', r.vision], ['reasons', r.reasoning]]
          .filter(function (x) { return x[1]; }).map(function (x) { return x[0]; });
        var facts = [
          r.context ? 'context ' + Number(r.context).toLocaleString() + ' tokens' : 'context unknown',
          r.output ? 'output up to ' + Number(r.output).toLocaleString() : '',
          can.length ? 'can: ' + can.join(', ') : '',
          price(r),
          r.limits ? 'limits ' + limits(r.limits) : '',
          r.released ? 'released ' + r.released : '',
          r.knowledge ? 'knowledge to ' + r.knowledge : '',
          r.open_weights != null ? (r.open_weights ? 'open weights' : 'closed weights') : '',
          'first seen ' + (r.first_seen === 'baseline' ? 'before the list began' : r.first_seen)];
        p.appendChild(el('div', {class: 'prof'}, facts.filter(Boolean).join(' · ')));
        if (r.also_on && r.also_on.length) p.appendChild(el('div', {class: 'prof muted'}, 'Also on the bench as ' + r.also_on.join(', ')));
        list.textContent = '';
        res.routes.forEach(function (x) {
          var row = el('div', {class: 'endpoint'}), left = el('div');
          left.appendChild(el('div', {class: 'what'}, x.label));
          left.appendChild(el('div', {class: 'base'}, x.model + ' · ' + price(x) + (x.limits ? ' · ' + limits(x.limits) : '')));
          left.appendChild(el('div', {class: 'base'}, x.note));
          row.appendChild(left);
          var acts = el('div', {class: 'acts'});
          if (x.on_bench_as) acts.appendChild(el('span', {class: 'chip ok'}, 'on the bench as ' + x.on_bench_as));
          else {
            var b = el('button', {class: 'btn small', type: 'button'}, 'Add via this');
            b.addEventListener('click', function () { addVia(x, b); });
            acts.appendChild(b);
          }
          row.appendChild(acts);
          list.appendChild(row);
        });
        if (!res.routes.length) list.appendChild(el('p', {class: 'rules'}, 'None: ' + r.provider_name
          + ' needs a connection in a hub or an endpoint of its own first'
          + (r.api ? ' (its OpenAI-compatible base URL is ' + r.api + ')' : '') + '.'));
        dd.showModal();
      }).catch(function (err) { $('d-errors').textContent = String(err); dd.showModal(); });
  }
  document.addEventListener('click', function (e) {
    var row = e.target.closest('tr.pick-row');           // the whole row: its name, its Add…, anywhere
    if (!row) return;
    e.preventDefault(); openDiscover(row.dataset.provider, row.dataset.model);
  });
  $('d-close').addEventListener('click', function () { dd.close(); });
  function follow(log, title, before) {
    o.choose.hidden = true; o.run.hidden = false; o.start.hidden = true;
    o.title.textContent = title; o.result.className = 'served';
    o.result.textContent = before + 'Running in the background; closing this does not stop it. Log: runs/' + log;
    renderSteps('', false, null);
    o.log.textContent = ''; delete o.log.dataset.opened; o.log.parentNode.open = false;
    timer = setInterval(function () {
      fetch('/api/onboard?log=' + encodeURIComponent(log)).then(function (r) { return r.json(); }).then(function (s) {
        if (!s.ok) return;
        o.log.textContent = s.tail || '(no output yet)';
        // Open the log the first time it has something in it; after that the user decides.
        if (s.tail && !o.log.dataset.opened) { o.log.parentNode.open = true; o.log.dataset.opened = '1'; }
        renderSteps(s.tail || '', s.done, s.exit);
        if (s.done) {
          clearInterval(timer); timer = null;
          var alias = (/->\s+alias\s+(\S+)/.exec(s.tail) || [])[1];
          if (s.exit === 0) {
            o.result.textContent = 'Finished. ' + (alias ? alias + ' is on the bench. ' : '') + 'The suite scores are in the dated suite note; whether to route work there is your call. Closing…';
            o.result.className = 'done';
            closeAndReload(od);
          } else {
            o.result.textContent = 'Stopped with exit ' + s.exit + '. The last lines of the log say why.';
            fresh.hidden = false;
          }
        }
      });
    }, 3000);
  }

  // ---- theme: applied the moment it is picked, and saved ------------------
  // Every theme's colours are on the page (serve.brand_css), keyed by the
  // data-theme on <html>, so switching is one attribute: no reload. The
  // charts read their colours when drawn, so they are drawn again.
  document.querySelectorAll('#themes input[name=theme]').forEach(function (input) {
    input.addEventListener('change', function () {
      document.documentElement.setAttribute('data-theme', input.value);
      $('t-errors').textContent = ''; $('t-done').textContent = '';
      if (typeof drawCharts === 'function') drawCharts();
      post('/api/theme', {theme: input.value}).then(function (r) {
        if (!r.ok) { listErrors($('t-errors'), r.errors); return; }
        $('t-done').textContent = 'Saved: ' + input.value + ' is the theme from now on.';
        if (location.search.indexOf('theme=') !== -1) history.replaceState(null, '', '/');   // drop a ?theme preview
      });
    });
  });

  // ---- gateway keys: shown once, then only their names ---------------------
  if ($('k-make')) $('k-make').addEventListener('click', function () {
    $('k-errors').textContent = '';
    var scope = Array.prototype.map.call(document.querySelectorAll('input[name=k-scope]:checked'),
                                         function (c) { return c.value; });
    post('/api/gateway-key', {name: $('k-name').value, collections: scope, rpm: $('k-rpm').value}).then(function (r) {
      if (!r.ok) { listErrors($('k-errors'), r.errors); return; }
      $('k-value').value = r.key;
      $('k-done').hidden = false;
      $('k-value').select();
      $('k-make').disabled = true;               // reload to see it listed; the value is gone then
    });
  });
  document.querySelectorAll('[data-revoke-key]').forEach(function (b) {
    b.addEventListener('click', function () {
      var name = b.dataset.revokeKey;
      if (!confirm('Revoke the gateway key ' + name + '? Anything using it is refused from now on.')) return;
      post('/api/gateway-key/remove', {name: name}).then(function (r) {
        if (!r.ok) { listErrors($('k-errors'), r.errors); return; }
        location.reload();
      });
    });
  });

  // ---- endpoints: keys go in, never come back out -------------------------
  // The Manage page lists them; Add an endpoint and Change key are one dialog.
  var ed = $('endpoint-dialog');
  var e = {list: $('e-list'), name: $('e-name'), base: $('e-base'), key: $('e-key'),
           kind: $('e-kind'), hub: $('e-hub'), admin: $('e-admin'),
           errors: $('e-errors'), done: $('e-done'), test: $('e-test'), save: $('e-save'),
           listErrors: $('el-errors'), listDone: $('el-done')};
  e.kind.addEventListener('change', function () {
    $('e-hub-row').hidden = $('e-admin-row').hidden = e.kind.value !== 'hub';
  });
  ed.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', function () { ed.close(); }); });
  function loadEndpoints() {
    fetch('/api/endpoints').then(function (r) { return r.json(); }).then(function (res) {
      e.list.textContent = '';
      res.endpoints.forEach(function (x) {
        var row = el('div', {class: 'endpoint'});
        var left = el('div');
        var title = el('div', {class: 'what'});
        title.appendChild(el('span', {class: 'alias'}, x.name + ' '));
        if (x.default) title.appendChild(el('span', {class: 'chip'}, 'default'));
        title.appendChild(el('span', {class: 'chip ' + (x.key_set ? 'ok' : 'warn'), style: 'margin-left:.3rem'},
                             x.key_set ? 'key set' : 'no key'));
        if (x.connect) {                                    // signed in from Connect
          var until = x.connect.expires && !x.connect.refreshes ? ' until ' + x.connect.expires.slice(0, 10) : '';
          title.appendChild(el('span', {class: 'chip ' + (x.connect.expired ? 'warn' : 'via'), style: 'margin-left:.3rem'},
                               x.connect.expired ? 'sign in again' : 'signed in' + until));
        }
        left.appendChild(title);
        left.appendChild(el('div', {class: 'base mono'}, x.base + ' · ' + x.models + ' model' + (x.models === 1 ? '' : 's') + ' on the bench'));
        row.appendChild(left);
        var acts = el('div', {class: 'acts'});
        var edit = el('button', {class: 'btn small', type: 'button'}, 'Change key');
        edit.addEventListener('click', function () { openEndpoint('change', x); });
        acts.appendChild(edit);
        if (x.connect && x.connect.flow && x.connect.flow !== 'local') {     // this endpoint, not a new one
          var again = el('button', {class: 'btn small', type: 'button'}, 'Sign in again');
          again.addEventListener('click', function () {
            openEndpoint('again', x);
            connectStart({id: x.connect.provider, flow: x.connect.flow, label: x.connect.label}, x.name);
          });
          acts.appendChild(again);
        }
        if (!x.default) {
          var def = el('button', {class: 'btn small', type: 'button'}, 'Make default');
          def.addEventListener('click', function () {
            if (!confirm('Make ' + x.name + ' the default endpoint? The default is where a model is taken'
                         + ' to be when nothing says otherwise, and the one endpoint that cannot be removed.')) return;
            post('/api/endpoint/default', {name: x.name}).then(function (r) {
              e.listErrors.textContent = '';
              if (!r.ok) { listErrors(e.listErrors, r.errors); return; }
              e.listDone.textContent = r.note + '.'; loadEndpoints();
            });
          });
          acts.appendChild(def);
          // Its models go with it, after saying how many: each is retired as by
          // hand (evidence kept, restorable once the endpoint is back).
          var rm = el('button', {class: 'btn small danger', type: 'button'}, 'Remove');
          rm.addEventListener('click', function () {
            var n = x.models, them = n + ' model' + (n === 1 ? '' : 's');
            if (!confirm(n ? 'Remove the ' + x.name + ' endpoint and the ' + them + ' on the bench through it?\n\n'
                             + 'The ' + them + ' will be retired too. Their results and history are kept, and'
                             + ' Restore brings them back if an endpoint named ' + x.name + ' is added again.'
                           : 'Remove the ' + x.name + ' endpoint?')) return;
            post('/api/endpoint/remove', {name: x.name, with_models: n > 0}).then(function (r) {
              e.listErrors.textContent = '';
              if (!r.ok) { listErrors(e.listErrors, r.errors); return; }
              e.listDone.textContent = 'Removed ' + x.name + '; ' + r.note + '.'; loadEndpoints();
              if (r.retired && r.retired.length) setTimeout(function () { location.reload(); }, 2500);   // the model tables changed
            });
          });
          acts.appendChild(rm);
        }
        row.appendChild(acts);
        e.list.appendChild(row);
      });
    });
  }
  function loadLimits() {
    var list = $('l-list');
    fetch('/api/connections').then(function (r) { return r.json(); }).then(function (res) {
      list.textContent = '';
      res.connections.forEach(function (c) {
        var row = el('div', {class: 'endpoint'});
        var left = el('div');
        left.appendChild(el('div', {class: 'what'}, c.name));
        left.appendChild(el('div', {class: 'base'}, c.models.join(', ')));
        var inputs = el('div', {class: 'limits'});
        res.keys.forEach(function (k) {
          var lab = el('label', {}, k + ' ');
          var box = el('input', {type: 'number', min: '1', 'data-k': k, placeholder: '—'});
          if (c.limits[k]) box.value = c.limits[k];
          lab.appendChild(box); inputs.appendChild(lab);
        });
        left.appendChild(inputs);
        row.appendChild(left);
        var save = el('button', {class: 'btn small', type: 'button'}, 'Save');
        save.addEventListener('click', function () {
          var limits = {};
          inputs.querySelectorAll('input').forEach(function (b) { if (b.value) limits[b.dataset.k] = parseInt(b.value, 10); });
          $('l-errors').textContent = ''; $('l-done').textContent = '';
          post('/api/connection-limits', {connection: c.name, limits: limits}).then(function (r) {
            if (!r.ok) { listErrors($('l-errors'), r.errors); return; }
            $('l-done').textContent = 'Saved limits for ' + c.name + '.';
          });
        });
        var acts = el('div', {class: 'acts'}); acts.appendChild(save); row.appendChild(acts);
        list.appendChild(row);
      });
    });
  }
  onShow.manage = function () {
    e.listErrors.textContent = e.listDone.textContent = '';
    $('cn-app-errors').textContent = $('cn-app-done').textContent = '';
    loadEndpoints(); loadProviders(); loadLimits();
  };
  // The dialog, in one of three modes. add: the choices, API key first, then
  // each provider's sign-in; change: one endpoint's key form; again: a
  // sign-in under way for an endpoint that has one.
  function openEndpoint(mode, x) {
    cn.run++;
    e.adding = mode === 'add';     // Add refuses a name in use; Change key edits that one
    $('e-title').textContent = mode === 'change' ? 'Change the key for ' + x.name
      : mode === 'again' ? 'Sign in to ' + x.connect.label + ' again' : 'Add an endpoint';
    $('e-blurb').hidden = cn.list.hidden = $('e-kinds').hidden = !e.adding;
    e.key.placeholder = e.adding ? 'blank if it needs none, as a local runtime' : 'leave blank to keep the current one';
    cn.wait.hidden = true; $('e-after').hidden = true;
    resetKeyForm();
    choose(mode === 'again' ? null : 'key');
    if (mode === 'change') {
      e.name.value = x.name; e.base.value = x.base; e.name.readOnly = true;
      $('e-admin-row').hidden = !x.hub;                     // a hub's admin key changes here too
    }
    if (e.adding) loadProviders();
    if (!ed.open) ed.showModal();
    (mode === 'change' ? e.key : e.name).focus();
  }
  // Which form shows: 'key' (paste an API key), a provider (sign in), or none.
  function choose(p) {
    cn.pick = p && p !== 'key' ? p : null;
    $('e-form').hidden = p !== 'key';
    $('cn-form').hidden = !cn.pick;
    if (cn.pick) {
      $('cn-name').value = cn.pick.suggest;
      $('cn-go').textContent = (cn.pick.flow === 'local' ? 'Add ' : 'Sign in to ') + cn.pick.label;
    }
    e.errors.textContent = ''; e.done.textContent = '';
    press();
  }
  function press() {
    var on = cn.pick ? cn.pick.id : $('e-form').hidden ? '' : 'key';
    cn.list.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.choice === on)); });
  }
  function resetKeyForm() {
    e.name.value = e.base.value = e.key.value = e.admin.value = ''; e.name.readOnly = false;
    e.kind.value = 'provider'; e.hub.selectedIndex = 0; $('e-hub-row').hidden = $('e-admin-row').hidden = true;
    untested();
  }
  // Save opens only after Test passes, and closes again when anything in the
  // form changes. The server tests again on Save: this is the form's manners,
  // not the guarantee.
  function untested() { e.save.disabled = true; e.save.title = 'Test it first'; }
  $('e-form').addEventListener('input', function () { untested(); e.done.textContent = ''; });
  function keyBody() {
    var body = {name: e.name.value, base: e.base.value, key: e.key.value, new: e.adding,
                admin: $('e-admin-row').hidden ? '' : e.admin.value};
    if (e.adding) { body.kind = e.kind.value; body.hub = e.kind.value === 'hub' ? e.hub.value : ''; }   // Change key keeps its kind
    return body;
  }
  $('e-add').addEventListener('click', function () { openEndpoint('add'); });
  e.test.addEventListener('click', function () {
    e.errors.textContent = ''; e.done.textContent = ''; untested();
    e.test.disabled = true; e.test.textContent = 'Testing…';
    var body = keyBody(), sent = JSON.stringify(body), run = cn.run; body.test = true;
    function end() { e.test.disabled = false; e.test.textContent = 'Test'; }
    post('/api/endpoint', body).then(function (r) {
      end();
      if (run !== cn.run || JSON.stringify(keyBody()) !== sent) return;   // opened afresh, or edited, meanwhile
      if (!r.ok) { listErrors(e.errors, r.errors); return; }
      e.done.textContent = 'It answers: ' + r.served + ' models listed. ' + (r.note || '');
      e.save.disabled = false; e.save.title = '';
    }).catch(function (err) { end(); e.errors.textContent = String(err); });
  });
  e.save.addEventListener('click', function () {
    e.errors.textContent = ''; e.save.disabled = e.test.disabled = true; e.save.textContent = 'Saving…';
    function end() { e.save.textContent = 'Save'; e.test.disabled = false; }
    post('/api/endpoint', keyBody()).then(function (r) {
      end();
      if (!r.ok) { listErrors(e.errors, r.errors); return; }          // Save stays shut: test again
      var name = e.name.value.trim().toLowerCase();
      finished('Saved. ' + name + ' lists ' + r.served + ' models; they appear in the onboarding picker. '
               + (r.note || ''), e.adding ? name : '');
    }).catch(function (err) { end(); e.errors.textContent = String(err); });
  });
  // Saved or signed in: the forms fold, the message stays, with the next step.
  function finished(message, endpoint) {
    resetKeyForm(); choose(null);
    cn.list.hidden = $('e-blurb').hidden = cn.wait.hidden = true; $('e-after').hidden = false;
    e.done.textContent = message;
    if (endpoint) e.done.appendChild(onboardFrom(endpoint));
    loadEndpoints(); loadProviders();
  }

  // ---- connect: sign in on the provider's page (metrics/connect.py) --------
  // `run` counts the dialog's openings: a sign-in still finishing after the
  // dialog was closed or opened again reports on the Manage page instead.
  var cn = {list: $('cn-list'), wait: $('cn-wait'), msg: $('cn-msg'), timer: null, run: 0};
  // Reached from another machine (a hosted install): a callback to this
  // machine's 127.0.0.1 could not arrive, so sign-ins go by code instead.
  var remote = !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  function connectDone(r, run) {
    clearInterval(cn.timer); cn.timer = null;
    var said = r.ok && r.label + ' is connected as ' + r.endpoint + ': it lists ' + r.served + ' models. ' + (r.note || '');
    if (!ed.open || run !== cn.run) {
      if (r.ok) { e.listDone.textContent = said; loadEndpoints(); loadProviders(); } else listErrors(e.listErrors, r.errors);
      return;
    }
    cn.wait.hidden = true;
    if (!r.ok) {                   // back to the provider's form to try again, or just Close
      $('cn-form').hidden = !cn.pick; $('e-after').hidden = !!cn.pick;
      listErrors(e.errors, r.errors); return;
    }
    finished(said, r.endpoint);
  }
  // The next step for a new endpoint, signed in or pasted: onboard what it serves.
  function onboardFrom(endpoint) {
    var go = el('button', {class: 'btn small', type: 'button', style: 'margin-left:.6rem'}, 'Onboard a model from it');
    go.addEventListener('click', function () {
      ed.close(); $('onboard-open').click(); o.find.value = endpoint;   // the picker filters on it once loaded
    });
    return go;
  }
  function connectStart(p, endpoint) {
    e.errors.textContent = ''; e.done.textContent = '';
    var run = cn.run;
    // Opened now, while this is still the click: a window opened after the
    // request returns is a popup the browser blocks.
    var byCode = remote && p.flow !== 'local';
    var tab = !byCode && (p.flow === 'key' || p.flow === 'oauth') ? window.open('', '_blank') : null;
    if (tab) tab.opener = null;
    post('/api/connect/start', {provider: p.id, endpoint: endpoint, code: byCode}).then(function (r) {
      if (!r.ok || r.state) { if (tab) tab.close(); connectDone(r, run); return; }   // refused, or local: saved at once
      cn.wait.hidden = false; cn.msg.textContent = ''; $('e-after').hidden = false;
      if (r.url) {
        if (tab) tab.location = r.url; else window.open(r.url, '_blank', 'noopener');
        cn.msg.textContent = 'Sign in to ' + p.label + ' in the tab that opened. This updates when it is done.';
      } else {
        cn.msg.appendChild(document.createTextNode('Open '));
        cn.msg.appendChild(el('a', {href: r.verify, target: '_blank', rel: 'noopener'}, r.verify));
        cn.msg.appendChild(document.createTextNode(' and enter the code '));
        cn.msg.appendChild(el('strong', {class: 'mono'}, r.code));
      }
      var tries = 0;
      clearInterval(cn.timer);
      cn.timer = setInterval(function () {
        if (++tries > 450) { connectDone({ok: false, errors: ['No sign-in after fifteen minutes; start again.']}, run); return; }
        post('/api/connect/poll', {id: r.id}).then(function (s) { if (s.state !== 'pending') connectDone(s, run); });
      }, 2000);
    }).catch(function (err) { if (tab) tab.close(); e.errors.textContent = String(err); });
  }
  // The dialog's choices (API key, then each provider) and the Manage page's
  // own-app rows, from one listing.
  function loadProviders() {
    fetch('/api/connect').then(function (r) { return r.json(); }).then(function (res) {
      cn.list.textContent = ''; $('cn-apps').textContent = '';
      choice('key', 'API key', '').addEventListener('click', function () { choose('key'); e.name.focus(); });
      res.providers.forEach(function (p) {
        // A new endpoint, named first (a provider can be several: a pasted key
        // and a sign-in, or two accounts). Signing an existing one in again is
        // its row's button.
        var why = p.missing || (remote && !p.code ? p.label + ' signs in only through a page on the machine'
                                + ' Benchwarmers runs on; from here, choose API key' : '');
        choice(p.id, p.label, why).addEventListener('click', function () { choose(p); $('cn-name').select(); });
        if (p.register) $('cn-apps').appendChild(appRow(p));
      });
      press();
    });
  }
  function choice(id, label, why) {
    var b = el('button', {class: 'btn', type: 'button', 'data-choice': id, 'aria-pressed': 'false'}, label);
    if (why) { b.disabled = true; b.title = why; }
    cn.list.appendChild(b);
    return b;
  }
  // The Sign in click itself opens the provider's tab (connectStart), so the
  // browser does not block it as a popup.
  $('cn-go').addEventListener('click', function () { $('cn-form').hidden = true; connectStart(cn.pick, $('cn-name').value); });
  // Use your own app: a provider's own client id, instead of the shipped one.
  function appRow(p) {
    var row = el('div', {class: 'endpoint'});
    var left = el('div');
    left.appendChild(el('div', {class: 'what'}, p.label));
    left.appendChild(el('div', {class: 'base'}, 'Register ' + p.register));
    var box = el('input', {spellcheck: 'false', autocomplete: 'off', 'aria-label': p.label + ' client id',
                           placeholder: p.shipped ? "Wendell Research's app (leave blank)" : 'client id, needed to sign in'});
    box.value = p.own_id || '';
    var line = el('div', {class: 'row-input'});            // the box and its Save on one line
    line.appendChild(box);
    left.appendChild(line);
    row.appendChild(left);
    var save = el('button', {class: 'btn small', type: 'button'}, 'Save');
    save.addEventListener('click', function () {
      $('cn-app-errors').textContent = ''; $('cn-app-done').textContent = '';
      post('/api/connect/app', {provider: p.id, client_id: box.value}).then(function (r) {
        if (!r.ok) { listErrors($('cn-app-errors'), r.errors); return; }
        $('cn-app-done').textContent = box.value.trim() ? p.label + ' signs in with your app now.'
          : p.label + (p.shipped ? " is back to Wendell Research's app." : ' has no app until one is added.');
        loadProviders();
      });
    });
    line.appendChild(save);
    return row;
  }

  if ($('tab-manage').classList.contains('active')) onShow.manage();   // the page opened on it

  // ---- first run: no endpoint yet, no model, no collection (serve.first_run) -
  var first = $('first-run');
  function firstStep() {
    if (first.dataset.step === 'onboard') { $('onboard-open').click(); return; }
    if (first.dataset.step === 'collection') { show('collections'); $('c-new').click(); return; }
    show('manage'); openEndpoint('add');
  }
  if (first) {
    $('first-go').addEventListener('click', firstStep);
    if (first.dataset.step === 'endpoint') firstStep();   // straight into Add: an empty bench offers nothing else
  }

  // ---- collections: what the bench gateway runs together (its own tab) -----
  var c = {list: $('c-list'), name: $('c-name'), members: $('c-members'), count: $('c-count'),
           shape: $('c-shape'), desc: $('c-desc'), tags: $('c-tags'), errors: $('c-errors'), done: $('c-done'),
           head: $('c-form-head'), maxCalls: $('g-max-calls'), maxExp: $('g-max-explorers'),
           gErrors: $('g-errors'), gDone: $('g-done'), form: $('c-form'), status: $('c-status'),
           del: $('c-delete'), reply: $('c-reply'), merger: $('c-merger')};
  var C = null, open = null;         // the collection whose details are showing
  function explorersFor(n) {
    return Math.max(0, Math.min(C.settings.max_explorers, C.settings.max_calls - n));
  }
  function picked() {
    return Array.prototype.filter.call(c.members.querySelectorAll('input'), function (x) { return x.checked; })
      .map(function (x) { return x.value; });
  }
  function recount() {
    var n = picked().length, ex = explorersFor(n);
    c.count.textContent = n + ' member' + (n === 1 ? '' : 's') + ' + ' + ex + ' explorer' + (ex === 1 ? '' : 's') +
      ' = ' + (n + ex) + ' calls per request' + (n && (n < 2 || n > 4) ? ' · two to four members is recommended' : '');
    c.members.querySelectorAll('input').forEach(function (x) { x.disabled = !x.checked && n >= C.settings.max_calls; });
  }
  // Suggested tags under the field: a click adds or removes one, and the
  // field stays free for any other.
  function tagList() {
    return c.tags.value.split(',').map(function (t) { return t.trim().toLowerCase(); }).filter(Boolean);
  }
  function drawTagPicks() {
    var box = $('c-tag-picks'), have = tagList();
    box.textContent = '';
    (C.tag_suggestions || []).forEach(function (t) {
      var on = have.indexOf(t) !== -1;
      var b = el('button', {type: 'button', class: 'chip' + (on ? ' ok' : ''), 'aria-pressed': String(on)}, (on ? '✓ ' : '+ ') + t);
      b.addEventListener('click', function () {
        var now = tagList(), i = now.indexOf(t);
        if (i === -1) now.push(t); else now.splice(i, 1);
        c.tags.value = now.join(', '); drawTagPicks();
      });
      box.appendChild(b);
    });
  }
  c.tags.addEventListener('input', drawTagPicks);
  // A collection opens in a popup, as every other edit on the dashboard does:
  // the one clicked, or a new one from New collection.
  function closeForm() {
    if (c.form.open) c.form.close();
    open = null;
  }
  c.form.addEventListener('close', function () { open = null; });
  function fillForm(row) {
    c.errors.textContent = ''; c.done.textContent = ''; c.status.textContent = '';
    open = row ? row.name : null; c.del.hidden = !row;
    c.head.textContent = row ? 'bench/' + row.name : 'New collection';
    if (!c.form.open) c.form.showModal();
    c.name.value = row ? row.name : ''; c.name.readOnly = !!row;
    c.desc.value = row ? row.description : ''; c.tags.value = row ? row.tags.join(', ') : '';
    drawTagPicks();
    c.members.textContent = '';
    C.models.forEach(function (alias) {
      var box = el('input', {type: 'checkbox', value: alias});
      box.checked = !!row && row.members.indexOf(alias) !== -1;
      box.addEventListener('change', recount);
      var lab = el('label', {class: 'check'}); lab.appendChild(box); lab.appendChild(el('span', {class: 'mono'}, alias));
      c.members.appendChild(lab);
    });
    c.shape.textContent = '';
    c.shape.appendChild(el('option', {value: ''}, 'automatic'));
    C.shapes.forEach(function (s) {
      var o = el('option', {value: s}, s); if (row && row.shape === s) o.selected = true; c.shape.appendChild(o);
    });
    c.reply.value = row ? row.reply : 'all';
    c.merger.textContent = '';
    c.merger.appendChild(el('option', {value: ''}, 'automatic'));
    C.models.forEach(function (alias) {
      var o = el('option', {value: alias}, alias); if (row && row.merger === alias) o.selected = true; c.merger.appendChild(o);
    });
    recount();
  }
  function loadCollections(keep) {
    fetch('/api/collections').then(function (r) { return r.json(); }).then(function (res) {
      C = res; c.list.textContent = '';
      res.collections.forEach(function (row) {
        // The whole row opens the collection: no tight button pair on it.
        var box = el('button', {class: 'endpoint c-row', type: 'button', 'data-name': row.name,
                                'aria-label': 'Open bench/' + row.name});
        var left = el('div');
        var title = el('div', {class: 'what'});
        title.appendChild(el('span', {class: 'alias'}, 'bench/' + row.name + ' '));
        row.tags.forEach(function (t) { title.appendChild(el('span', {class: 'chip', style: 'margin-left:.3rem'}, t)); });
        left.appendChild(title);
        if (row.description) left.appendChild(el('div', {}, row.description));
        left.appendChild(el('div', {class: 'base'}, row.members.join(', ') + ' + ' + row.explorers + ' explorer' +
          (row.explorers === 1 ? '' : 's') + (row.shape ? ' (' + row.shape + ')' : '') +
          (row.reply === 'merged' ? ' · merged by ' + (row.merger || 'verdicts') : '')));
        var t = row.together;
        if (t) {
          var lim = Object.keys(t.limits || {}).filter(function (k) { return t.limits[k]; })
            .map(function (k) { return t.limits[k].toLocaleString() + ' ' + k; });
          left.appendChild(el('div', {class: 'base'}, 'Together: ' +
            (t.context_window ? 'prompts up to ' + Math.floor(t.context_window / 1000).toLocaleString() + 'k tokens' : 'context unknown') +
            (t.context_unknown.length ? ' (unknown for ' + t.context_unknown.join(', ') + ')' : '') +
            ' · all can: ' + (t.capabilities.join(', ') || 'nothing known in common') +
            ' · ' + t.connections.length + ' connection' + (t.connections.length === 1 ? '' : 's') +
            (lim.length ? ' · tightest ' + lim.join(', ') : '')));
        }
        box.appendChild(left);
        box.appendChild(el('span', {class: 'open-hint', 'aria-hidden': 'true'}, 'Open ›'));
        box.addEventListener('click', function () { fillForm(row); });
        c.list.appendChild(box);
      });
      if (!res.collections.length) c.list.appendChild(el('p', {class: 'empty'}, 'No collections yet.'));
      c.maxCalls.value = res.settings.max_calls; c.maxExp.value = res.settings.max_explorers;
      if (!keep) closeForm();
    });
  }
  loadCollections();
  $('c-new').addEventListener('click', function () { fillForm(null); c.name.focus(); });
  $('c-close').addEventListener('click', closeForm);
  // Done: say so in the popup, close it a moment later, and keep the message
  // above the list, as onboarding and retiring do.
  function finished(message) {
    c.done.textContent = message; c.status.textContent = message; loadCollections(true);
    setTimeout(closeForm, 1200);
  }
  c.del.addEventListener('click', function () {
    var name = open;
    if (!name || !confirm('Delete bench/' + name + '? Callers asking for it will get an error.')) return;
    post('/api/collection/remove', {name: name}).then(function (r) {
      if (!r.ok) { listErrors(c.errors, r.errors); return; }
      finished('Deleted bench/' + name + '.');
    });
  });
  $('c-save').addEventListener('click', function () {
    c.errors.textContent = ''; c.done.textContent = '';
    var name = c.name.value.trim().replace(/^bench\//, '');
    post('/api/collection', {name: name, members: picked(), shape: c.shape.value, description: c.desc.value,
                             tags: c.tags.value.split(','), reply: c.reply.value,
                             merger: c.merger.value}).then(function (r) {
      if (!r.ok) { listErrors(c.errors, r.errors); return; }
      finished('Saved bench/' + name + '.');
    }).catch(function (err) { c.errors.textContent = String(err); });
  });
  $('g-save').addEventListener('click', function () {
    c.gErrors.textContent = ''; c.gDone.textContent = '';
    post('/api/gateway-settings', {max_calls: parseInt(c.maxCalls.value, 10),
                                   max_explorers: parseInt(c.maxExp.value, 10)}).then(function (r) {
      if (!r.ok) { listErrors(c.gErrors, r.errors); return; }
      c.gDone.textContent = 'Settings saved.'; loadCollections(true);
    }).catch(function (err) { c.gErrors.textContent = String(err); });
  });

  // ---- a click on the backdrop closes a popup, as its Close button does ----
  // Pressed AND released outside: a text selection dragged out of an input
  // must not throw the form away.
  document.querySelectorAll('dialog').forEach(function (d) {
    var downOutside = false;
    function outside(e) {
      var r = d.getBoundingClientRect();
      return e.target === d && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom);
    }
    d.addEventListener('pointerdown', function (e) { downOutside = outside(e); });
    d.addEventListener('click', function (e) { if (downOutside && outside(e)) d.close(); downOutside = false; });
  });

  // ---- verdict on one gateway seat --------------------------------------
  var vd = $('verdict-dialog'), vcall = null;
  function lines(id) { return $(id).value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean); }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('button.seat');
    if (!b) return;
    vcall = b.dataset.call;
    $('v-errors').textContent = ''; $('v-done').textContent = '';
    fetch('/api/gateway-seat?call=' + encodeURIComponent(vcall)).then(function (r) { return r.json(); }).then(function (s) {
      if (!s.ok) { listErrors($('v-errors'), s.errors); vd.showModal(); return; }
      $('v-title').textContent = s.alias + ' · ' + s.seat;
      $('v-meta').textContent = s.model + ' · ' + s.status + ' in ' + s.seconds + 's · ' + s.at.slice(0, 16).replace('T', ' ') + ' · ' + s.call;
      $('v-prompt').textContent = s.prompt;
      $('v-answer').textContent = s.answer || (s.error ? 'Error: ' + s.error : 'Empty reply: usually a token budget, not incapacity.');
      document.querySelectorAll('input[name=v-useful]').forEach(function (r) {
        r.checked = s.useful !== null && r.value === (s.useful ? 'yes' : 'no');
      });
      $('v-note').value = s.note; $('v-real').value = s.real.join('\n'); $('v-false').value = s.false.join('\n');
      vd.showModal();
    });
  });
  $('v-close').addEventListener('click', function () { vd.close(); });
  $('v-save').addEventListener('click', function () {
    var pick = document.querySelector('input[name=v-useful]:checked');
    $('v-errors').textContent = ''; $('v-done').textContent = '';
    post('/api/verdict', {call: vcall, useful: pick ? pick.value === 'yes' : null, note: $('v-note').value,
                          real: lines('v-real'), false: lines('v-false')}).then(function (r) {
      if (!r.ok) { listErrors($('v-errors'), r.errors); return; }
      $('v-done').textContent = 'Recorded: ' + r.changed.join(', ') + '. Reloading…';
      setTimeout(function () { location.reload(); }, 700);
    }).catch(function (err) { $('v-errors').textContent = String(err); });
  });

  // ---- Benchmarks charts: Observable Plot, data from serve.charts_data ------
  // A function, not run once: a theme switched on Settings redraws them in its colours.
  function drawCharts() {
    var holder = $('charts-data');
    if (!holder) return;
    var C = JSON.parse(holder.textContent);
    var css = getComputedStyle(document.documentElement);
    function tok(name, fallback) { return (css.getPropertyValue(name) || '').trim() || fallback; }
    // `good` is the theme's OK colour, not its brand: in a theme whose brand is
    // red (Hardwood), a found bug drawn in the brand colour would read as bad.
    var ink = tok('--ink', '#24331f'), good = tok('--ok', '#327a16'), err = tok('--err', '#a12b2b'),
        warn = tok('--warn', '#7a4d00'),
        signal = tok('--signal', '#fae66c'), sky = tok('--sky', '#69bdeb'), muted = tok('--muted', '#6b685a'),
        line = tok('--line', '#e4dfd2'), halo = tok('--paper-2', '#ffffff');
    var style = {background: 'transparent', color: ink, fontSize: '13px', overflow: 'visible'};
    // Tabs other than the open one have no width yet, so size from the column
    // the pages share. It was the whole page, which made every chart wider than
    // its column beside the sidebar, scaled down, with smaller text.
    function width() {
      var col = document.querySelector('.panes') || document.querySelector('main');
      return Math.max(520, (col.clientWidth || 900) - 50);
    }
    function put(id, make) {
      var box = $(id);
      if (!box) return;
      if (!window.Plot) {
        box.className += ' muted';
        box.textContent = 'Charts need Observable Plot from cdn.jsdelivr.net, which did not load; the table below has the same numbers.';
        return;
      }
      box.textContent = '';                      // a redraw replaces the chart
      try { box.appendChild(make(width())); } catch (e) { box.textContent = 'Chart failed: ' + e; }
    }
    // Pixel offsets that keep scatter labels apart: each label, in order, moves
    // up or down until it clears the ones already placed.
    function spread(points, xpx, ypx) {
      var boxes = [], tries = [-12, 14, -26, 28, -40, 42, -54, 56];
      return points.map(function (d) {
        var cx = xpx(d), half = d.model.length * 2.9;
        for (var k = 0; k < tries.length; k++) {
          var cy = ypx(d) + tries[k];
          if (!boxes.some(function (b) { return Math.abs(b.x - cx) < b.half + half + 4 && Math.abs(b.y - cy) < 12; })) {
            boxes.push({x: cx, y: cy, half: half});
            return tries[k];
          }
        }
        return tries[0];
      });
    }
    function rows(n) { return 20 * n + 80; }
    // Who to trust: finds real bugs (right) vs makes things up (up = less).
    put('chart-overview', function (w) {
      if (!C.overview.length) return el('p', {class: 'hint'}, 'No model has both suite scores and answers yet.');
      var worst = Math.max(0.1, Math.max.apply(null, C.overview.map(function (d) { return d.wrong; })));
      var x0 = Math.max(0, Math.floor((Math.min.apply(null, C.overview.map(function (d) { return d.recall; })) - 0.05) * 20) / 20);
      var pxW = w - 110, pxH = 370;
      var pts = C.overview.slice().sort(function (a, b) { return a.wrong - b.wrong || b.recall - a.recall; });
      var offs = spread(pts, function (d) { return (d.recall - x0) / (1 - x0) * pxW; },
                        function (d) { return d.wrong / worst * pxH; });
      var labels = pts.map(function (d, i) { return {model: d.model, recall: d.recall, ty: d.wrong + offs[i] * worst / pxH}; });
      return Plot.plot({
        width: w, height: 440, style: style, grid: true, inset: 16, marginRight: 50, marginTop: 30,
        x: {label: 'Finds real bugs in our code →', domain: [x0, 1], tickFormat: '.0%'},
        y: {label: '↑ Makes fewer things up', domain: [0, worst], reverse: true, tickFormat: '.0%'},
        marks: [
          // The zone a reviewer should be in: outlined, so it reads on a dark ground too.
          Plot.rect([{x1: x0 + (1 - x0) / 2, x2: 1, y1: 0, y2: worst / 4}], {x1: 'x1', x2: 'x2', y1: 'y1', y2: 'y2',
            fill: good, fillOpacity: 0.12, stroke: good, strokeOpacity: 0.5, strokeDasharray: '4 3'}),
          Plot.dot(C.overview, {x: 'recall', y: 'wrong', r: 6, fill: good, stroke: ink, strokeWidth: 0.5}),
          // A halo in the panel's colour keeps each name legible over dots, grid lines and the zone.
          Plot.text(labels, {x: 'recall', y: 'ty', text: 'model', fontSize: 11, fill: ink,
                             stroke: halo, strokeWidth: 3.5, strokeOpacity: 0.9, paintOrder: 'stroke'}),
          Plot.tip(C.overview, Plot.pointer({x: 'recall', y: 'wrong',
            title: function (d) { return d.model + '\nfinds ' + d.found + ' real findings\nconfidently wrong in ' + d.wrongN + ' answers'; }}))
        ]
      });
    });

    // Bug hunt: right of zero the bug tasks (found, wrong line, missed); left of
    // it the same files clean, where any bar is a false alarm.
    put('chart-hunt', function (w) {
      var segs = [], score = {}, tasks = {};
      C.hunt.forEach(function (h) {
        var bugs = h.found + h['wrong line'] + h.missed, clean = h.clean + h['false alarm'];
        tasks[h.model] = bugs + clean;
        var f = bugs ? h.found / bugs : 0, wl = bugs ? h['wrong line'] / bugs : 0, ms = bugs ? h.missed / bugs : 0;
        var fa = clean ? h['false alarm'] / clean : 0;
        score[h.model] = f - fa;
        segs.push({model: h.model, part: 'false alarm', x1: -fa, x2: 0, n: h['false alarm'] + ' of ' + clean + ' clean files'});
        segs.push({model: h.model, part: 'found', x1: 0, x2: f, n: h.found + ' of ' + bugs + ' bugs'});
        segs.push({model: h.model, part: 'wrong line', x1: f, x2: f + wl, n: h['wrong line'] + ' of ' + bugs + ' bugs'});
        segs.push({model: h.model, part: 'missed', x1: f + wl, x2: f + wl + ms, n: h.missed + ' of ' + bugs + ' bugs'});
      });
      var models = Object.keys(score).sort(function (a, b) { return score[b] - score[a]; });
      return Plot.plot({
        width: w, height: rows(models.length), style: style, marginLeft: 250,
        x: {domain: [-1, 1], label: '← false alarms on clean files · bugs found →', grid: true,
            tickFormat: function (d) { return Math.abs(Math.round(d * 100)) + '%'; }},
        y: {domain: models, label: null, tickFormat: function (m) { return m + '  (' + tasks[m] + ')'; }},
        color: {legend: true, domain: ['found', 'wrong line', 'missed', 'false alarm'], range: [good, signal, line, err]},
        marks: [
          Plot.barX(segs.filter(function (s) { return s.x2 > s.x1; }), {x1: 'x1', x2: 'x2', y: 'model', fill: 'part',
            title: function (d) { return d.model + ': ' + d.part + ', ' + d.n; }}),
          Plot.ruleX([0], {stroke: ink, strokeWidth: 1.5})
        ]
      });
    });

    // Real work: the share of judged offloads that helped, sample size beside the name.
    put('chart-work', function (w) {
      var R = C.work.slice().sort(function (a, b) { return b.useful / b.judged - a.useful / a.judged || b.judged - a.judged; });
      if (!R.length) return el('p', {class: 'hint'}, 'No judged offloads yet.');
      var by = {};
      R.forEach(function (r) { by[r.model] = r; });
      function share(r) { return r.useful / r.judged; }
      return Plot.plot({
        width: w, height: rows(R.length), style: style, marginLeft: 250, marginRight: 50,
        x: {domain: [0, 1], label: 'Helped, share of judged offloads →', tickFormat: '.0%', grid: true},
        y: {domain: R.map(function (r) { return r.model; }), label: null,
            tickFormat: function (m) { return m + '  (' + by[m].judged + ')'; }},
        marks: [
          Plot.barX(R, {x: share, y: 'model', fill: function (r) { return share(r) >= 0.5 ? good : err; },
            title: function (r) { return r.model + ': helped ' + r.useful + ' of ' + r.judged + ' judged (' + r.offloads + ' offloads)'
              + (r.real || r.false ? '\nverified findings ' + r.real + ', false claims ' + r.false : ''); }}),
          Plot.text(R, {x: share, y: 'model', text: function (r) { return r.useful + '/' + r.judged; }, dx: 18, fill: ink}),
          Plot.ruleX([0], {stroke: ink})
        ]
      });
    });

    // Did it answer: each probe of the last 14 days, by what came back.
    put('chart-reliability', function (w) {
      if (!C.reliability.length) return el('p', {class: 'hint'}, 'No probes in the last 14 days.');
      var total = {}, ok = {};
      C.reliability.forEach(function (d) {
        total[d.model] = (total[d.model] || 0) + d.count;
        if (d.outcome === 'answered') ok[d.model] = d.count;
      });
      var models = Object.keys(total).sort(function (a, b) { return (ok[b] || 0) / total[b] - (ok[a] || 0) / total[a]; });
      var colour = {answered: good, capacity: signal, timeout: sky, structural: err, paywalled: warn,
                    entitlement: '#c0563f', other: muted, error: ink};
      var order = Object.keys(colour).filter(function (o) { return C.reliability.some(function (d) { return d.outcome === o; }); });
      return Plot.plot({
        width: w, height: rows(models.length), style: style, marginLeft: 250,
        x: {label: 'Share of probes →', tickFormat: '.0%', grid: true},
        y: {domain: models, label: null},
        color: {legend: true, domain: order, range: order.map(function (o) { return colour[o]; })},
        marks: [Plot.barX(C.reliability, Plot.stackX({x: 'count', y: 'model', fill: 'outcome', offset: 'normalize', order: order,
          title: function (d) { return d.model + ': ' + d.outcome + ' ' + d.count + ' of ' + total[d.model] + ' probes'; }}))]
      });
    });

    // Speed vs quality: median seconds on our own tasks against findings located.
    put('chart-speed', function (w) {
      if (!C.speed.length) return el('p', {class: 'hint'}, 'No suite timings yet.');
      var s = C.speed.map(function (d) { return d.seconds; });
      var lo = Math.max(1, Math.min.apply(null, s) * 0.8), hi = Math.max.apply(null, s) * 1.25;
      var y0 = Math.max(0, Math.floor((Math.min.apply(null, C.speed.map(function (d) { return d.recall; })) - 0.05) * 20) / 20);
      var pxW = w - 110, pxH = 340;
      var pts = C.speed.slice().sort(function (a, b) { return b.recall - a.recall; });
      var offs = spread(pts, function (d) { return (Math.log(d.seconds) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * pxW; },
                        function (d) { return (1 - d.recall) / (1 - y0) * pxH; });
      var labels = pts.map(function (d, i) { return {model: d.model, seconds: d.seconds, ty: d.recall - offs[i] * (1 - y0) / pxH}; });
      return Plot.plot({
        width: w, height: 420, style: style, grid: true, inset: 16, marginRight: 50, marginTop: 30,
        x: {type: 'log', domain: [lo, hi], label: 'Median seconds per answer on our tasks →'},
        y: {domain: [y0, 1], label: '↑ Findings located', tickFormat: '.0%'},
        marks: [
          Plot.dot(C.speed, {x: 'seconds', y: 'recall', r: 6, fill: sky, stroke: ink, strokeWidth: 0.5}),
          Plot.text(labels, {x: 'seconds', y: 'ty', text: 'model', fontSize: 11, fill: ink}),
          Plot.tip(C.speed, Plot.pointer({x: 'seconds', y: 'recall',
            title: function (d) { return d.model + '\nmedian ' + d.seconds.toFixed(0) + 's over ' + d.tasks + ' tasks\nfinds ' + Math.round(d.recall * 100) + '%'; }}))
        ]
      });
    });

    // Our code: one square per task and model, grouped by kind of task.
    put('chart-code', function (w) {
      var fam = {}, first = {};
      C.heat.forEach(function (d) { fam[d.task] = d.family; });
      var tasks = Object.keys(fam).sort(function (a, b) {
        return C.families.indexOf(fam[a]) - C.families.indexOf(fam[b]) || (a < b ? -1 : 1);
      });
      tasks.forEach(function (t) { if (!(fam[t] in first)) first[fam[t]] = t; });
      var label = {};
      Object.keys(first).forEach(function (f) { label[first[f]] = f; });
      // By total found, so a model that played three tasks does not top one that played forty.
      var sum = {};
      C.heat.forEach(function (d) { sum[d.model] = (sum[d.model] || 0) + (d.rate || 0); });
      var models = Object.keys(sum).sort(function (a, b) { return sum[b] - sum[a]; });
      function tip(d) { return d.model + '\n' + d.task + ' (' + d.family + ')\n' + d.label + (d.invented ? '\ninvented something' : ''); }
      return Plot.plot({
        width: w, height: 22 * models.length + 70, style: style, marginLeft: 230, marginTop: 30, padding: 0.08,
        x: {domain: tasks, axis: 'top', label: null, tickSize: 0, tickFormat: function (t) { return label[t] || ''; }},
        y: {domain: models, label: null, tickSize: 0},
        color: {type: 'linear', domain: [0, 0.5, 1], range: [err, signal, good]},
        marks: [
          Plot.cell(C.heat.filter(function (d) { return d.rate == null; }), {x: 'task', y: 'model', fill: line, title: tip}),
          Plot.cell(C.heat.filter(function (d) { return d.rate != null; }), {x: 'task', y: 'model', fill: 'rate', title: tip}),
          Plot.dot(C.heat.filter(function (d) { return d.invented; }), {x: 'task', y: 'model', r: 2.4, fill: ink}),
          Plot.ruleX(Object.keys(label), {x: function (t) { return t; }, stroke: ink, strokeOpacity: 0.25, dx: -2})
        ]
      });
    });

    // Confidently wrong: stacked by kind, worst at the top.
    put('chart-wrong', function (w) {
      var T = C.wrongTotals;
      var models = Object.keys(T).sort(function (a, b) { return T[b].rate - T[a].rate || T[b].answers - T[a].answers; });
      return Plot.plot({
        width: w, height: 20 * models.length + 70, style: style, marginLeft: 250,
        x: {label: 'Confidently wrong, share of answers that could have been →', tickFormat: '.0%', grid: true},
        // The count beside each name: 1 of 2 is not the same evidence as 17 of 26.
        y: {domain: models, label: null, tickFormat: function (m) { return m + '  (' + T[m].answers + ')'; }},
        color: {legend: true, domain: C.kinds, range: [err, signal, sky, muted]},
        marks: [
          Plot.barX(C.wrong, {x: 'rate', y: 'model', fill: 'kind', title: function (d) { return d.model + ': ' + d.kind + ' ' + d.n; }}),
          Plot.ruleX([0], {stroke: ink})
        ]
      });
    });

  }
  drawCharts();
})();
