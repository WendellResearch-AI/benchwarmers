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
  // Every reload says it is under way (bench.css .leaving) while the server
  // renders, so no action needs its own wait before reloading.
  addEventListener('beforeunload', function () { document.body.classList.add('leaving'); });
  addEventListener('pageshow', function () { document.body.classList.remove('leaving'); });   // back, from the cache
  function post(url, body) {
    return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify(body)}).then(function (r) { return r.json(); });
  }
  function get(url) { return fetch(url, {cache: 'no-store'}).then(function (r) { return r.json(); }); }
  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
  function listErrors(node, errors) { node.textContent = errors.map(function (x) { return '• ' + x; }).join('\n'); node.style.whiteSpace = 'pre-line'; }
  // A control inside a page, by its id, on the document: pages arrive after
  // load (load), and again after a change, as new elements each time.
  var handlers = {};
  function on(type, id, f) {
    if (!handlers[type]) {
      handlers[type] = {};
      document.addEventListener(type, function (ev) {
        var t = ev.target.closest && ev.target.closest('[id]'), g = t && handlers[type][t.id];
        if (g) g.call(t, ev);
      }, type === 'close');              // a popup's close does not bubble: caught on its way down
    }
    handlers[type][id] = f;
  }
  // ---- motion (ui/UI.md; bench.css "motion"): each follows the state it shows and
  // never holds it up; under prefers-reduced-motion none runs and the state changes at
  // once. The duration and the easing are bench.css's tokens, read at the first move (a
  // read as the page loads worked out its every style once more).
  var calm = matchMedia('(prefers-reduced-motion: reduce)'), timing = null;
  function moved(node, from) {
    if (!timing) {
      var t = getComputedStyle(document.documentElement);
      timing = {duration: parseFloat(t.getPropertyValue('--base')) || 180, easing: t.getPropertyValue('--ease').trim() || 'ease'};
    }
    return node.animate([{transform: from}, {transform: 'none'}], timing);
  }
  var TICK = '<svg class="tick" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8"/><path d="M5.8 10.4l2.9 2.8 5.6-6"/></svg>';
  // Motions 2 and 10: a button whose action finished says so on itself, a drawn tick
  // and `text` (Saved, Copied), for a second and a half.
  function doneOn(b, text) {
    if (!b.dataset.was) b.dataset.was = b.textContent;
    clearTimeout(b.back); b.innerHTML = TICK; b.append(text); b.classList.add('said');
    b.back = setTimeout(function () { b.textContent = b.dataset.was; delete b.dataset.was; b.classList.remove('said'); }, 1500);
  }
  // Motion 2: a step done (ol.steps li.done) carries a tick; those done since `was` were draw theirs.
  function ticks(list, was) {
    list.querySelectorAll('li.done').forEach(function (li, i) {
      if (!li.querySelector('.tick')) li.insertAdjacentHTML('afterbegin', TICK);
      li.classList.toggle('just', i >= was);
    });
  }
  // Motion 1: where you are is one mark in the rail (bench.css .ink), moved to the page
  // chosen. The links' places are read once, in a frame (a read in the click forced a
  // layout that cost two frames a page), and the mark is made where it already stands,
  // so nothing slides in on load. Under 800px the rail is a menu, the link itself marked.
  var mark = null, tops = null;
  function marker() {
    var a = document.querySelector('#rail-body a[aria-current]');
    if (tops) { if (a && mark) mark.style.transform = 'translateY(' + tops[a.dataset.tab] + 'px)'; return; }
    requestAnimationFrame(function () {
      a = document.querySelector('#rail-body a[aria-current]');
      if (tops || !a || !a.offsetParent) return;
      tops = {};
      document.querySelectorAll('#rail-body a[data-tab]').forEach(function (x) { tops[x.dataset.tab] = x.offsetTop; });
      var at = 'translateY(' + tops[a.dataset.tab] + 'px)';
      if (mark) mark.style.transform = at;
      else $('rail-body').prepend(mark = el('i', {class: 'ink', 'aria-hidden': 'true', style: 'transform:' + at + ';height:' + a.offsetHeight + 'px'}));
    });
  }
  matchMedia('(min-width: 800px)').addEventListener('change', function () { tops = null; marker(); });
  // The page's own confirm, a Promise of true/false: window.confirm is
  // silently false where a browser blocks dialogs. Opens over any open popup.
  var askD = $('ask-dialog'), askDone = null;
  function ask(text, yes, danger, onboard) {
    if (askDone) askDone(false);
    $('ask-text').textContent = text;
    $('ask-onboard').hidden = !onboard;     // Discover's Add: the Onboard dialog's mode and Vision box
    $('ask-vision').checked = false;
    $('ask-yes').textContent = yes || 'OK';
    $('ask-yes').className = 'btn ' + (danger ? 'danger' : 'primary');
    askD.showModal(); $('ask-no').focus();
    return new Promise(function (resolve) { askDone = resolve; });
  }
  function answer(yes) { var r = askDone; askDone = null; if (askD.open) askD.close(); if (r) r(yes); }
  $('ask-yes').addEventListener('click', function () { answer(true); });
  $('ask-no').addEventListener('click', function () { answer(false); });
  askD.addEventListener('close', function () { answer(false); });   // Escape

  // ---- restart (serve.restart; Settings' button `restart`, Info's `restart-info`, both
  // in the page at once): the page reloads once the new one answers ----
  function restart(force, id) {
    var rs = $(id), out = $(id + '-out');
    rs.disabled = true; out.textContent = 'Restarting…';
    post('/api/restart', {force: force}).then(function (res) {
      if (res.ok) return back(out, Date.now());
      rs.disabled = false; out.textContent = '';
      if (!res.busy) { listErrors(out, res.errors); return; }
      ask('Still in progress: ' + res.errors.join('; ') + '. Restart anyway and cut them off?', 'Restart anyway', true)
        .then(function (yes) { if (yes) restart(true, id); });
    }).catch(function (err) { rs.disabled = false; out.textContent = String(err); });
  }
  // The old one stops half a second after answering; /health answers again once the new one is up.
  function back(out, since) {
    setTimeout(function () {
      fetch('/health', {cache: 'no-store'}).then(function (r) { if (!r.ok) throw r.status; location.reload(); })
        .catch(function () {
          if (Date.now() - since < 120000) back(out, since);
          else out.textContent = 'Not back after two minutes: look at whatever runs it.';
        });
    }, 1500);
  }
  ['restart', 'restart-info'].forEach(function (id) {
    on('click', id, function () {
      ask('Restart Benchwarmers? The dashboard and the gateway on this port stop for a few seconds.', 'Restart')
        .then(function (yes) { if (yes) restart(false, id); });
    });
  });

  // ---- Export and import (serve.export_zip, import_zip) ----------------
  // A link with download, not location: a page that "leaves" for a download
  // never comes back (beforeunload's loading state).
  on('click', 'x-export', function () {
    var scope = document.querySelector('input[name=x-scope]:checked').value;
    el('a', {href: '/api/export?scope=' + scope + ($('x-keys').checked ? '&keys=1' : ''), download: ''}).click();
  });
  function mb(n) { return n < 1e6 ? Math.max(1, Math.round(n / 1e3)) + ' KB' : (n / 1e6).toFixed(1) + ' MB'; }
  // Picked: what is in it, asked of the server. Import: the same file again, written.
  function importZip(apply) {
    var f = $('x-file').files[0], go = $('x-import'), what = $('x-what'), errs = $('x-errors');
    errs.textContent = ''; what.textContent = f ? (apply ? 'Importing…' : 'Reading it…') : ''; go.disabled = true;
    if (!f) return;
    fetch('/api/import' + (apply ? '?apply=1' : ''), {method: 'POST', headers: {'Content-Type': 'application/zip'}, body: f})
      .then(function (r) { return r.json(); }).then(function (r) {
        if (!r.ok) { what.textContent = ''; listErrors(errs, r.errors); return; }
        var keys = r.keys ? ', and ' + plural(r.keys, 'API key') : '';
        if (apply) {
          what.textContent = 'Imported ' + r.files + ' files' + keys + '. This install\'s own are in ' + r.backup
            + '. Restart now so every part reads them.';
          return;
        }
        what.textContent = (r.scope === 'all' ? 'Everything' : 'Settings only') + ' from Benchwarmers ' + r.release
          + ', made ' + r.made.slice(0, 16).replace('T', ' ') + ': ' + r.files + ' files (' + mb(r.bytes) + ')' + keys + '. '
          + (r.replaces ? r.replaces + ' of them replace files here.' : 'None of them is here yet.');
        go.disabled = false;
      }).catch(function (e) { what.textContent = ''; listErrors(errs, [String(e)]); });
  }
  on('change', 'x-file', function () { importZip(false); });
  on('click', 'x-import', function () { importZip(true); });

  // ---- pages: a link each, at its own address (serve.href) -------------
  // The server renders only the page the address names; the others are
  // fetched when first opened (GET /api/pane), and again after a change to
  // them (refreshPanes). A link swaps the page in place, at once.
  var app = document.querySelector('.app');
  var tabs = document.querySelectorAll('.tabs a[data-tab]');
  // A box's scroll bar shows while it scrolls and a moment after (bench.css .scrolling).
  var stillAt = new Map();
  document.addEventListener('scroll', function (e) {
    var box = e.target === document ? document.documentElement : e.target;
    if (!box.classList) return;
    if (!stillAt.has(box)) box.classList.add('scrolling');
    clearTimeout(stillAt.get(box));
    stillAt.set(box, setTimeout(function () { box.classList.remove('scrolling'); stillAt.delete(box); }, 900));
  }, {capture: true, passive: true});
  // And while the mouse is near the edge a bar sits on (bench.css .near), so it can be grabbed:
  // the nearest box under the pointer that scrolls that way, its right edge or its bottom.
  var near = null, nearAsked = false;
  function scrolls(box, axis) {
    return axis === 'y' ? box.scrollHeight > box.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(box).overflowY)
                        : box.scrollWidth > box.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(box).overflowX);
  }
  function nearTo(on) {
    if (on === near) return;
    if (near) near.classList.remove('near');
    if (on) on.classList.add('near');
    near = on;
  }
  document.addEventListener('pointermove', function (e) {
    if (e.pointerType !== 'mouse' || nearAsked) return;
    nearAsked = true;
    var t = e.target, x = e.clientX, y = e.clientY;
    requestAnimationFrame(function () {                  // once a frame, however fast the mouse moves
      nearAsked = false;
      for (var box = t; box && box.nodeType === 1 && box !== document.documentElement; box = box.parentElement) {
        var r = box.getBoundingClientRect();
        if ((r.right - x < 18 && scrolls(box, 'y')) || (r.bottom - y < 18 && scrolls(box, 'x'))) return nearTo(box);
      }
      nearTo(null);
    });
  }, {passive: true});
  document.documentElement.addEventListener('mouseleave', function () { nearTo(null); });
  // The docked panel's width: its edge (bench.css .grip) dragged, or moved with the arrow
  // keys, between 320 and 640px; a double-click gives the usual width back. Kept per browser.
  var grip = el('div', {class: 'grip', role: 'separator', tabindex: '0', 'aria-orientation': 'vertical',
    'aria-label': 'Panel width', 'aria-valuemin': '320', 'aria-valuemax': '640',
    title: 'Drag to make the panel wider or narrower; double-click for its usual width'});
  app.appendChild(grip);
  function asideW(w, keep) {
    if (w === null) app.style.removeProperty('--aside-w');
    else {
      w = Math.round(Math.max(320, Math.min(640, w)));
      app.style.setProperty('--aside-w', w + 'px'); grip.setAttribute('aria-valuenow', w);
    }
    if (keep) store('bench.aside', w === null ? '' : String(w));
  }
  if (+store('bench.aside')) asideW(+store('bench.aside'));
  grip.addEventListener('pointerdown', function (e) {
    if (e.button) return;
    e.preventDefault(); grip.setPointerCapture(e.pointerId); app.classList.add('resizing');
  });
  grip.addEventListener('pointermove', function (e) { if (grip.hasPointerCapture(e.pointerId)) asideW(innerWidth - e.clientX); });
  grip.addEventListener('lostpointercapture', function () {
    app.classList.remove('resizing');
    if (app.style.getPropertyValue('--aside-w')) asideW(parseInt(app.style.getPropertyValue('--aside-w'), 10), true);
  });
  grip.addEventListener('dblclick', function () { asideW(null, true); });
  grip.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    asideW(innerWidth - grip.getBoundingClientRect().right + 4 + (e.key === 'ArrowLeft' ? 16 : -16), true);
  });
  var onShow = {};                     // a page's loader, run each time it is shown
  var onLoad = {};                     // a page's set-up, run each time its HTML arrives
  var shown = null, fetching = {}, ready = {}, marks = 0, last = {}, edited = {};
  var KEPT = 'input[data-filter], select[data-facet], input[data-retired], input[data-df], #d-q, [data-kept]';    // controls a swap keeps (or may reset)
  function show(id) {
    if (id !== shown && md && md.open && !md.matches(':modal')) md.close();   // a model docked on a page left: the new page's own panel shows
    shown = id;
    tabs.forEach(function (a) {
      if (a.dataset.tab === id) { a.setAttribute('aria-current', 'page'); document.title = (id === 'suite' ? '' : a.firstChild.textContent + ' · ') + 'Benchwarmers'; }
      else a.removeAttribute('aria-current');
    });
    marker();
    document.querySelectorAll('.tab').forEach(function (s) { s.classList.toggle('active', s.id === 'tab-' + id); });
    document.querySelectorAll('.tab:not(.active) dialog[open]').forEach(function (d) { d.close(); });   // a record docked on a page left
    sheet('menu', false); sheet('facts', false);
    return load(id).then(function () { markHand(); if (onShow[id] && shown === id) onShow[id](); });
  }
  // A page chosen (a link, a button that leads there): a new entry in the history, and
  // the focus on its name, where a keyboard or a screen reader carries on from.
  function go(id) {
    var a = document.querySelector('.tabs a[data-tab="' + id + '"]');
    if (id !== shown) history.pushState(null, '', a.getAttribute('href') + location.search);   // a ?theme preview stays
    var done = show(id);
    $('tab-' + id).querySelector('.bar h1').focus({preventScroll: true});
    return done;
  }
  function pageAt(path) { var k = path.split('/')[1]; return k && $('tab-' + k) ? k : 'suite'; }
  addEventListener('popstate', function () { if (pageAt(location.pathname) !== shown) show(pageAt(location.pathname)); });
  // A plain click; with Ctrl, Shift or the middle button a link opens a new tab as links do.
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a[data-tab], [data-go]');
    if (!a || (a.href && (e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey))) return;
    e.preventDefault();
    go(a.dataset.tab || a.dataset.go);
  });
  // Under 800px the rail is a bar whose Menu opens the pages; under 1280px a page's
  // own facts are a sheet, opened from its bar. Escape, or a page chosen, puts both away.
  function sheet(which, open) {
    app.classList.toggle(which + '-open', open);
    document.querySelectorAll(which === 'menu' ? '#menu' : '.facts-open').forEach(function (b) { b.setAttribute('aria-expanded', String(open)); });
  }
  document.addEventListener('click', function (e) {
    if (e.target.closest('#menu')) sheet('menu', !app.classList.contains('menu-open'));
    else if (e.target.closest('.facts-open')) sheet('facts', !app.classList.contains('facts-open'));
    else if (e.target.closest('.facts-close, aside.facts a')) sheet('facts', false);
  });
  // A page in place and set up: fetched if it has no HTML yet, or only old HTML
  // (data-lazy), which stays on screen until the new arrives. `quiet`: asked
  // ahead of a click (early), so no loading bar unless the click comes first.
  var awaited = {};
  function load(id, quiet) {
    var s = $('tab-' + id);
    if (!s.hasAttribute('data-lazy')) return ready[id] || Promise.resolve();
    var asked = s.getAttribute('data-lazy');
    if (!quiet && !/^live/.test(asked)) { awaited[id] = 1; document.body.classList.add('loading'); }   // a live refresh shows only what changed
    if (!fetching[id]) {
      fetching[id] = fetch('/api/pane?tab=' + id, {cache: 'no-store'}).then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      }).catch(function (err) { location.reload(); throw err; })         // could not ask: the old way
        .then(function (p) {
          delete fetching[id]; delete awaited[id];
          if (!Object.keys(awaited).length) document.body.classList.remove('loading');
          // Marked again (refreshPanes) or swapped in (refreshNeeds) while this
          // was on its way: its HTML is older than the page wanted now.
          if (s.getAttribute('data-lazy') !== asked) return load(id);
          // A popup open in the old page (Collections' editor, Quotas' Models
          // list) is not pulled away: the page is fetched again once it closes.
          var held = s.querySelector('dialog[open]');
          if (held) return new Promise(function (done) { held.addEventListener('close', function () { done(load(id)); }, {once: true}); });
          return swap(id, p.html, /^live/.test(asked));
        });
    }
    return fetching[id];
  }
  // A page is fetched before the click: once the pointer rests on its link in the
  // rail (80 ms, so a pointer passing over the rail asks for nothing), or the link
  // takes the focus. The same load, not shown.
  var resting = 0;
  function early(a) { if (a && a.dataset.tab !== shown) load(a.dataset.tab, true); }
  document.addEventListener('pointerover', function (e) {
    var a = e.target.closest && e.target.closest('.tabs a[data-tab]');
    clearTimeout(resting);
    if (a) resting = setTimeout(function () { early(a); }, 80);
  });
  document.addEventListener('focusin', function (e) { early(e.target.closest && e.target.closest('.tabs a[data-tab]')); });
  // New HTML for a page, keeping what its filter boxes and facets say, which
  // sections are open and which column each table is sorted by. `live` (the
  // data moved, nobody acted): the same HTML as last time is left alone, and
  // so is a page someone is at work on (busy), with New results offered.
  function swap(id, html, live) {
    var s = $('tab-' + id), boxes = 'input[data-filter], select[data-facet]', old = s.querySelectorAll(boxes);
    if (live && (html === last[id] || (s.childElementCount > 1 && busy(s)))) {
      if (html !== last[id]) fresh.hidden = false;
      s.removeAttribute('data-lazy');
      return ready[id] || setUp(id);
    }
    var kept = Array.prototype.map.call(old, function (c) { return c.value; });
    var typing = Array.prototype.indexOf.call(old, document.activeElement);     // a box being typed in keeps the keys
    var folds = {}, sorts = {};
    named(s.querySelectorAll('details'), foldKey).forEach(function (p) { folds[p[0]] = p[1].open; });
    named(s.querySelectorAll('thead tr'), headKey).forEach(function (p) {
      var th = p[1].querySelector('th[aria-sort]');
      if (th) sorts[p[0]] = [Array.prototype.indexOf.call(p[1].children, th), th.getAttribute('aria-sort')];
    });
    var bar = s.querySelector(':scope > .bar');          // the page's name stays, and its focus
    while (bar.nextSibling) bar.nextSibling.remove();
    bar.insertAdjacentHTML('afterend', last[id] = html); s.removeAttribute('data-lazy');
    named(s.querySelectorAll('details'), foldKey).forEach(function (p) { if (p[0] in folds) p[1].open = folds[p[0]]; });
    named(s.querySelectorAll('thead tr'), headKey).forEach(function (p) {
      var k = sorts[p[0]], th = k && p[1].children[k[0]];
      if (th && th.hasAttribute('data-sortable')) sort(th, k[1]);
    });
    s.querySelectorAll(boxes).forEach(function (c, i) {
      // A facet value no longer offered (its last model gone) is "any", not a blank select.
      if (kept[i]) { c.value = kept[i]; if (c.selectedIndex === -1) c.value = ''; applyFilter(scopeOf(c)); }
      if (i === typing) c.focus();
    });
    return setUp(id);
  }
  // Each node with a key to find it again in new HTML: what it says, numbers
  // aside (counts and times move), and its place among those saying the same.
  function named(nodes, text) {
    var seen = {};
    return Array.prototype.map.call(nodes, function (n) {
      var k = text(n).replace(/\d[\d.,:%]*/g, '#');
      return [k + '|' + (seen[k] = (seen[k] || 0) + 1), n];
    });
  }
  function foldKey(d) { var sm = d.querySelector('summary'); return d.className + ' ' + (sm ? sm.textContent : ''); }
  function headKey(tr) { return tr.textContent; }
  function setUp(id) {
    delete edited['tab-' + id];
    applyRetired(); markHand();
    return (ready[id] = Promise.resolve(onLoad[id] && onLoad[id]()));
  }
  // The page the server rendered is set up as a fetched one is, once every
  // onLoad below is defined.
  var rendered = document.querySelector('.tab:not([data-lazy])');
  Promise.resolve().then(function () { if (rendered) setUp(rendered.id.slice(4)); });
  show(rendered ? rendered.id.slice(4) : 'suite');

  // ---- sortable tables -------------------------------------------------
  // A cell's data-sort, or its text, as a number where it is one; numbers before words.
  function key(raw) {
    var n = parseFloat(raw);
    return isNaN(n) || !/^-?[\d.]+/.test(raw) ? raw.toLowerCase() : n;
  }
  function ordered(pairs, dir) {             // [key, thing] pairs, sorted on the key: the things
    var up = dir === 'ascending' ? 1 : -1;
    return pairs.sort(function (a, b) {
      var x = a[0], y = b[0];
      return up * ((typeof x === typeof y) ? (x < y ? -1 : x > y ? 1 : 0) : (typeof x === 'number' ? -1 : 1));
    }).map(function (p) { return p[1]; });
  }
  function sort(th, dir) {
    var table = th.closest('table');
    var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
    dir = dir || (th.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending');
    table.querySelectorAll('th[aria-sort]').forEach(function (o) { o.removeAttribute('aria-sort'); });
    th.setAttribute('aria-sort', dir);
    if (table.id === 'd-table') return dApply();          // Discover's rows are data, drawn in this order
    // Motion 8: the rows in view glide from where they were (measure, reorder, play back).
    // Not on more than 80 of them: a long table sorted again reads as a new list.
    var was = new Map(), view = calm.matches ? null : (table.closest('.list') || table.closest('.tab') || document.documentElement).getBoundingClientRect();
    if (view) Array.prototype.forEach.call(table.querySelectorAll('tbody tr'), function (r) {
      var y = r.offsetParent && r.getBoundingClientRect().top;
      if (y && y < view.bottom && y > view.top - 40) was.set(r, y);
    });
    Array.prototype.forEach.call(table.tBodies, function (body) {     // each group (Models' connections) in itself, under its heading
      // Not a group's heading, nor a row that spans the table ("None yet"): it has no such cell.
      ordered(Array.prototype.filter.call(body.rows, function (r) { return !r.classList.contains('grp') && r.cells[idx]; }).map(function (r) {
        var c = r.cells[idx];
        return [key(c.dataset.sort != null ? c.dataset.sort : c.textContent.trim()), r];
      }), dir).forEach(function (r) { body.appendChild(r); });
    });
    var moving = [];
    was.forEach(function (y, r) { var d = y - r.getBoundingClientRect().top; if (d) moving.push([r, d]); });
    if (moving.length <= 80) moving.forEach(function (p) { moved(p[0], 'translateY(' + p[1] + 'px)'); });
  }
  // Motion 5: the row in hand (tr.in-hand) is marked at once; a mark glides there from the
  // row that had it, when one row in the same table had it (bench.css .ghost).
  new MutationObserver(function (changes) {
    if (calm.matches) return;
    var lost = [], got = [];
    changes.forEach(function (c) {
      var r = c.target, had = /\bin-hand\b/.test(c.oldValue || ''), has = r.classList.contains('in-hand');
      if (r.tagName === 'TR' && had !== has) (has ? got : lost).push(r);
    });
    got.forEach(function (to) {
      var t = to.closest('table'), from = lost.filter(function (r) { return r.closest('table') === t; });
      var host = to.closest('.list, .scroll');
      if (from.length !== 1 || !host || got.filter(function (r) { return r.closest('table') === t; }).length !== 1) return;
      var a = from[0].getBoundingClientRect(), b = to.getBoundingClientRect(), h = host.getBoundingClientRect();
      if (!a.height || !b.height) return;                  // filtered out or folded away: no path to show
      var g = el('i', {class: 'ghost', style: 'top:' + (b.top - h.top + host.scrollTop) + 'px;left:' + (b.left - h.left + host.scrollLeft)
        + 'px;width:' + b.width + 'px;height:' + b.height + 'px'});
      host.appendChild(g); to.classList.add('gliding');
      var p = moved(g, 'translateY(' + (a.top - b.top) + 'px) scaleY(' + a.height / b.height + ')');
      p.onfinish = p.oncancel = function () { g.remove(); to.classList.remove('gliding'); };
    });
  }).observe(document.querySelector('.panes'), {subtree: true, attributeFilter: ['class'], attributeOldValue: true});
  // On the document, not each header: a part of the page swapped in after an
  // action (refreshNeeds) sorts too.
  document.addEventListener('click', function (e) {
    var th = e.target.closest('th[data-sortable]');
    if (th) sort(th);
  });
  document.addEventListener('keydown', function (e) {
    var th = e.target.closest && e.target.closest('th[data-sortable]');
    if (th && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); sort(th); }
  });

  // ---- filter boxes: one per panel (or the whole page: data-filter="page"),
  // matching any text in a table row or a log line, and the panel's facet
  // selects (serve.facet), each matching a row's data-<facet> exactly ------
  function applyFilter(scope) {
    var box = scope.querySelector('input[data-filter]'), q = box ? box.value.trim().toLowerCase() : '';
    var facets = Array.prototype.filter.call(scope.querySelectorAll('select[data-facet]'), function (s) { return s.value; });
    scope.querySelectorAll('tbody tr:not(.grp), .log-line').forEach(function (r) {
      // A group's name (data-conn: Models' connection) counts as text of each of its rows,
      // and so does what a row is found by beside its text (data-find: an endpoint's models).
      var g = r.closest('[data-conn]'), text = r.textContent + (g ? ' ' + g.dataset.conn : '') + ' ' + (r.dataset.find || '');
      r.hidden = (q && text.toLowerCase().indexOf(q) === -1)
        || facets.some(function (s) { return r.dataset[s.dataset.facet] !== s.value; });
    });
    // A group with no row left is hidden; one with a match opens.
    scope.querySelectorAll('[data-conn]').forEach(function (g) {
      var any = !!g.querySelector('tr:not([hidden], .grp)');
      g.hidden = !!q && !any;
      if (q && any && g.hasAttribute('data-shut')) { fold(g, true); foldButtons(scope); }
    });
    // Nothing left: one quiet line under the table's head says so (the box's data-none).
    var none = box && box.dataset.none, line = scope.querySelector('.no-match'), table = scope.querySelector('table');
    if (!none || !table) return;
    if (!line) table.after(line = el('p', {class: 'no-match muted', role: 'status'}));
    line.hidden = !q || !!scope.querySelector('tbody tr:not(.grp, [hidden])');
    line.textContent = line.hidden ? '' : none + ' “' + box.value.trim() + '”.';
  }
  function scopeOf(c) {
    var box = c.closest('.toolbar') ? c.closest('.toolbar').querySelector('input[data-filter]') : c;
    return box && box.dataset.filter === 'page' ? c.closest('.tab') : c.closest('.panel');
  }
  document.addEventListener('input', function (ev) { if (ev.target.matches('input[data-filter]')) applyFilter(scopeOf(ev.target)); });
  document.addEventListener('change', function (ev) { if (ev.target.matches('select[data-facet]')) applyFilter(scopeOf(ev.target)); });

  // ---- Models' connections: each a group of rows whose heading folds it (serve.models_page),
  // and one Collapse all / Expand all in the bar, as accordions have it: Collapse all while
  // every one is open, else Expand all. Which are shut is kept when the page comes again.
  var shut = {};
  function fold(g, open) {
    g.toggleAttribute('data-shut', !open);
    g.querySelector('tr.grp button').setAttribute('aria-expanded', String(open));
    shut[g.dataset.conn] = !open;
  }
  function foldButtons(tab) {
    var all = !tab.querySelector('tbody[data-shut]');
    tab.querySelectorAll('[data-fold-all]').forEach(function (b) {
      b.dataset.foldAll = all ? 'close' : 'open';
      b.setAttribute('aria-expanded', String(all));
      b.lastChild.textContent = all ? 'Collapse all' : 'Expand all';
    });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-fold-all], tr.grp button');
    if (!b) return;
    var tab = b.closest('.tab'), g = b.closest('tbody');
    if (g) fold(g, g.hasAttribute('data-shut'));
    else tab.querySelectorAll('tbody[data-conn]').forEach(function (x) { fold(x, b.dataset.foldAll === 'open'); });
    foldButtons(tab);
  });
  onLoad.models = function () {
    var tab = $('tab-models');
    tab.querySelectorAll('tbody[data-conn]').forEach(function (g) { if (shut[g.dataset.conn]) fold(g, false); });
    foldButtons(tab);
  };
  on('click', 'models-onboard', function () { $('onboard-open').click(); });     // the bar's, as the rail's

  // ---- A list beside a detail (.split: Logs' logs, Clients' apps): the one chosen
  // shown beside the list (its button's aria-controls), its button pressed; kept
  // when the page is fetched again.
  var chosenIn = {};                   // not `picked`: that is the collection editor's members
  function pick(b) {
    var tab = b && b.closest('.tab');
    if (!tab) return;
    chosenIn[tab.id] = b.getAttribute('aria-controls');
    tab.querySelectorAll('.split > nav [aria-controls]').forEach(function (x) {
      x.setAttribute('aria-pressed', String(x === b));
      $(x.getAttribute('aria-controls')).hidden = x !== b;
    });
  }
  function pickAgain(id) { pick(chosenIn['tab-' + id] && $('tab-' + id).querySelector('[aria-controls="' + chosenIn['tab-' + id] + '"]')); }
  document.addEventListener('click', function (e) { pick(e.target.closest('.split > nav [aria-controls]')); });
  onLoad.logs = function () { pickAgain('logs'); };

  // Export as text (every log in one .txt, to read) and CSV (one log, its
  // own columns, for a spreadsheet). Both as the page shows them, the filter applied.
  function stampNow() {                                     // local time, as the page shows
    var now = new Date(), two = function (n) { return String(n).padStart(2, '0'); };
    return now.getFullYear() + '-' + two(now.getMonth() + 1) + '-' + two(now.getDate()) + '-' + two(now.getHours()) + two(now.getMinutes());
  }
  // A log as the page shows it (the filter applied): its header and rows, as cells.
  // A log of lines (Scheduler, Onboarding runs) is one column, Line.
  function logRows(d) {
    var cells = function (tr) {
      return Array.prototype.map.call(tr.children, function (c) { return c.textContent.trim().replace(/\s+/g, ' '); });
    };
    var head = d.querySelector('thead tr'), rows = [head ? cells(head) : ['Line']];
    d.querySelectorAll(head ? 'tbody tr' : '.log-line').forEach(function (r) {
      if (!r.hidden) rows.push(head ? cells(r) : [r.textContent.trim()]);
    });
    return rows;
  }
  // Export…: the popup picks the format and the logs. Text: every log chosen in one
  // .txt to read, tab-separated. CSV: a .csv per log (its own columns), through Share's
  // saveFile(blob, name) and csv(rows) (RFC 4180, with a BOM).
  on('click', 'logs-export', function () {              // in the bar: there before the page's own popup
    load('logs').then(function () {
      var q = ($('tab-logs').querySelector('input[data-filter]') || {}).value;
      $('lx-note').textContent = q ? 'Only the lines that match the filter, "' + q + '", as the page shows them.'
        : 'Every line the page shows: the last 200 of each log.';
      $('lx-errors').textContent = '';
      $('logs-export-dialog').showModal();
    });
  });
  on('click', 'lx-cancel', function () { $('logs-export-dialog').close(); });
  on('click', 'lx-go', function () {
    var tab = $('tab-logs'), dlg = $('logs-export-dialog');
    var format = dlg.querySelector('input[name=lx-format]:checked').value;
    var want = Array.prototype.filter.call(dlg.querySelectorAll('input[name=lx-log]'), function (x) { return x.checked; })
      .map(function (x) { return x.value; });
    if (!want.length) { $('lx-errors').textContent = 'Choose at least one log.'; return; }
    var logs = Array.prototype.filter.call(tab.querySelectorAll('section[data-log]'), function (d) {
      return want.indexOf(d.dataset.log) !== -1;
    });
    var stamp = stampNow(), q = (tab.querySelector('input[data-filter]') || {}).value;
    if (format === 'csv') {
      logs.forEach(function (d) {
        var slug = d.dataset.log.toLowerCase().replace(/[^a-z0-9]+/g, '-');
        saveFile(new Blob([csv(logRows(d))], {type: 'text/csv;charset=utf-8'}), 'benchwarmers-' + slug + '-' + stamp + '.csv');
      });
    } else {
      var out = ['Benchwarmers logs, exported ' + new Date().toString()];
      if (q) out.push('Filtered by: ' + q);
      logs.forEach(function (d) {
        out.push('', '== ' + d.dataset.log + ' ==');
        logRows(d).forEach(function (r) { out.push(r.join('\t')); });
      });
      saveFile(new Blob([out.join('\n') + '\n'], {type: 'text/plain;charset=utf-8'}), 'benchwarmers-logs-' + stamp + '.txt');
    }
    dlg.close();
  });

  // ---- retired rows: hidden unless asked for ---------------------------
  var showRetired = store('bench.retired') === '1';
  function applyRetired() {
    document.body.classList.toggle('show-retired', showRetired);
    document.querySelectorAll('input[data-retired]').forEach(function (c) { c.checked = showRetired; });
  }
  document.addEventListener('change', function (ev) {
    var c = ev.target;
    if (c.matches('input[data-retired]')) { showRetired = c.checked; store('bench.retired', showRetired ? '1' : '0'); applyRetired(); }
  });
  applyRetired();

  // ---- Share a report (serve.SHARE): its charts as an image, its table as
  // Markdown or CSV, or a post with the image on the clipboard. Only what the
  // report shows travels: the table as filtered, no key, no controls. --------
  var canClip = !!(window.isSecureContext && navigator.clipboard && window.ClipboardItem);
  document.documentElement.classList.toggle('no-clip', !canClip);
  document.documentElement.classList.toggle('no-share', !(navigator.canShare
    && navigator.canShare({files: [new File([''], 'a.png', {type: 'image/png'})]})));
  var LOOK = ['display', 'box-sizing', 'width', 'height', 'margin', 'padding', 'border', 'border-radius', 'background-color', 'color',
              'font-family', 'font-size', 'font-weight', 'font-style', 'line-height', 'letter-spacing', 'text-align',
              'white-space', 'vertical-align', 'gap', 'flex-wrap', 'align-items', 'opacity'];
  function ground(n) {           // the colour behind a report: the nearest that is not transparent
    for (; n; n = n.parentElement) {
      var bg = getComputedStyle(n).backgroundColor;
      if (bg && !/^(transparent|rgba\(.*,\s*0\))$/.test(bg)) return bg;
    }
    return '#fff';
  }
  // The logo (header and byline, serve.LOGOS) for a light or a dark ground: a data
  // URI and its width at 40 px high, or none where the install has no logo file.
  var logos = {};
  function logo(dark) {
    var name = dark ? 'logo-dark.svg' : 'logo.svg';
    return logos[name] || (logos[name] = fetch('/ui/' + name).then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (svg) {
        var box = (svg.match(/viewBox="([^"]+)"/) || [])[1], v = box ? box.split(/[\s,]+/).map(Number) : [];
        return svg && v[3] ? {src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg), width: Math.round(40 * v[2] / v[3])} : null;
      }).catch(function () { return null; }));
  }
  function dark(colour) {
    var n = (colour.match(/[\d.]+/g) || [255, 255, 255]).map(Number);
    return 0.299 * n[0] + 0.587 * n[1] + 0.114 * n[2] < 128;
  }
  // The report drawn as a PNG: a copy of it with its look written on each element
  // (the page's css does not travel), the logo under it, in an SVG foreignObject,
  // onto a canvas.
  function shareImage(panel, credit) {
    var copy = panel.cloneNode(true), from = panel.querySelectorAll('*'), to = copy.querySelectorAll('*');
    function look(a, b) {
      var cs = getComputedStyle(a);
      b.setAttribute('style', LOOK.map(function (p) { return p + ':' + cs.getPropertyValue(p); }).join(';'));
    }
    look(panel, copy);
    for (var i = 0; i < from.length; i++)                      // svg keeps its own attributes and style
      if (!(from[i] instanceof SVGElement) && !from[i].closest('details')) look(from[i], to[i]);
    var picks = panel.querySelectorAll('select');
    copy.querySelectorAll('select').forEach(function (s, k) {   // a window chosen: as words
      var o = picks[k].options[picks[k].selectedIndex];
      s.replaceWith(el('span', {style: 'font-weight:700'}, o ? o.text : ''));
    });
    copy.querySelectorAll('details, button, input, script').forEach(function (n) { n.remove(); });
    var pad = parseFloat(getComputedStyle(panel).paddingLeft) + parseFloat(getComputedStyle(panel).paddingRight);
    var W = Math.ceil(Math.max.apply(null, [panel.clientWidth].concat(Array.prototype.map.call(
      panel.querySelectorAll('.chart'), function (c) { return c.scrollWidth + pad; }))));
    var bg = ground(panel), rule = getComputedStyle(document.documentElement).getPropertyValue('--line').trim() || '#ccc';
    return logo(dark(bg)).then(function (mark) {
      var band = el('div', {style: 'display:flex;align-items:center;justify-content:space-between;gap:12px 24px;flex-wrap:wrap;'
        + 'margin:24px 0 0;padding:16px 0 0;border-top:1px solid ' + rule + ';font:13px system-ui,sans-serif'});
      if (mark) band.appendChild(el('img', {src: mark.src, alt: 'Benchwarmers, by Wendell Research',
                                            style: 'display:block;height:40px;width:' + mark.width + 'px'}));
      band.appendChild(el('span', {style: 'opacity:.75'}, (mark ? '' : 'Benchwarmers · ') + credit));
      copy.appendChild(band);
      copy.style.cssText += ';width:' + W + 'px;height:auto;box-sizing:border-box;margin:0;border-radius:0;position:fixed;left:-99999px;top:0'
        + ';background-color:' + bg;
      document.body.appendChild(copy);
      var H = Math.ceil(copy.getBoundingClientRect().height);
      copy.remove();
      copy.style.position = copy.style.left = copy.style.top = '';
      return draw('<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '">'
        + '<foreignObject width="100%" height="100%">' + new XMLSerializer().serializeToString(copy) + '</foreignObject></svg>', W, H);
    });
  }
  function draw(svg, W, H) {
    return new Promise(function (ok, fail) {
      var img = new Image(), k = Math.min(2, 16000 / Math.max(W, H));
      img.onload = function () {
        var c = el('canvas', {width: Math.round(W * k), height: Math.round(H * k)}), g = c.getContext('2d');
        g.scale(k, k);
        g.drawImage(img, 0, 0);
        c.toBlob(function (b) { if (b) ok(b); else fail(new Error('no image')); }, 'image/png');
      };
      img.onerror = function () { fail(new Error('the report could not be drawn')); };
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    });
  }
  function shareRows(panel) {     // the rows the table shows: its window, its filter and "Show retired" apply
    var t = Array.prototype.find.call(panel.querySelectorAll('table'), function (t) { return !t.closest('[hidden]'); });
    return t ? Array.prototype.filter.call(t.rows, function (r) { return getComputedStyle(r).display !== 'none'; })
      .map(function (r) {
        return Array.prototype.map.call(r.cells, function (c) {
          var k = c.cloneNode(true);                 // a model's connection in brackets, as the key writes it
          k.querySelectorAll('.conn').forEach(function (n) { n.textContent = ' (' + n.textContent + ')'; });
          return k.textContent.replace(/\s+/g, ' ').trim();
        });
      }) : [];
  }
  function markdown(rows) {
    return rows.map(function (r, i) {
      var line = '| ' + r.map(function (s) { return s.replace(/\|/g, '\\|'); }).join(' | ') + ' |';
      return i ? line : line + '\n|' + r.map(function () { return ' --- '; }).join('|') + '|';
    }).join('\n');
  }
  function csv(rows) {           // with a BOM, so a spreadsheet reads it as UTF-8
    return '﻿' + rows.map(function (r) {
      return r.map(function (s) { return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }).join(',');
    }).join('\r\n') + '\r\n';
  }
  function saveFile(blob, name) {
    var a = el('a', {href: URL.createObjectURL(blob), download: name});
    a.click();                                 // not in the page, as Export's link
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
  }
  // Where a post opens: the text and the project's link; the image is pasted in.
  var POST = {
    x: function (t, u) { return 'https://x.com/intent/tweet?text=' + encodeURIComponent(t + ' ' + u); },
    bsky: function (t, u) { return 'https://bsky.app/intent/compose?text=' + encodeURIComponent(t + ' ' + u); },
    linkedin: function (t, u) { return 'https://www.linkedin.com/sharing/share-offsite/?url=' + encodeURIComponent(u); },
    reddit: function (t, u) { return 'https://www.reddit.com/submit?url=' + encodeURIComponent(u) + '&title=' + encodeURIComponent(t); }
  };
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-share]'), menu = ev.target.closest('details.share');
    document.querySelectorAll('details.share[open]').forEach(function (d) { if (d !== menu) d.open = false; });
    if (!b) return;
    var panel = menu.closest('.panel'), what = b.dataset.share, sm = menu.querySelector('summary');
    var now = new Date(), title = panel.querySelector('h2').textContent.trim();
    var day = now.getFullYear() + '-' + ('0' + (now.getMonth() + 1)).slice(-2) + '-' + ('0' + now.getDate()).slice(-2);
    var name = 'benchwarmers-' + title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '-' + day;
    var byline = D.repo.replace(/^https:\/\//, '') + ' · ' + day, credit = title + ' · Benchwarmers, ' + byline;
    menu.open = false;
    if (panel.classList.contains('report')) drawReport(panel);    // its charts, if it was not drawn yet
    function said(text) { sm.textContent = text; setTimeout(function () { sm.textContent = 'Share'; }, 3500); }
    function failed(e) { if (!e || e.name !== 'AbortError') said('Share failed: ' + ((e && e.message) || e)); }
    if (what === 'md') {
      navigator.clipboard.writeText(markdown(shareRows(panel)) + '\n\n' + credit + '\n').then(function () { said('Table copied'); }, failed);
      return;
    }
    if (what === 'csv') { saveFile(new Blob([csv(shareRows(panel))], {type: 'text/csv'}), name + '.csv'); said('Table saved'); return; }
    var png = shareImage(panel, byline), post = POST[what];
    if (what === 'png') { png.then(function (blob) { saveFile(blob, name + '.png'); said('Image saved'); }, failed); return; }
    if (what === 'native') {
      png.then(function (blob) {
        return navigator.share({files: [new File([blob], name + '.png', {type: 'image/png'})], title: title, text: credit});
      }).then(function () { said('Shared'); }, failed);
      return;
    }
    // Copy, or a post: the image on the clipboard (saved where there is none), then the post opened.
    var put = canClip ? navigator.clipboard.write([new ClipboardItem({'image/png': png})])
                      : png.then(function (blob) { saveFile(blob, name + '.png'); });
    put.then(function () {
      if (post) window.open(post(title + ', from my model bench (Benchwarmers):', D.repo), '_blank', 'noopener');
      said(!post ? 'Image copied' : canClip ? 'Image copied: paste it into the post' : 'Image saved: attach it to the post');
    }, failed);
  });

  // ---- Discover: the catalog as data (serve.discover_data) ---------------
  // A row shows when it matches the search box and every facet with a box ticked: any of
  // the ticked values (for Can: all of them). Each box's count is what ticking it would
  // leave, given the other facets. Only the first D_PAGE matches are drawn, more as the
  // list's foot nears.
  var D_FACETS = ['new', 'price', 'reach', 'caps', 'provider'], D_PAGE = 40, NUM = new Intl.NumberFormat();
  var REACH = {'on bench': 'ok', connected: 'ok', 'easy to add': 'warn', other: ''};
  var dPick = {price: ['free']}, dLimit = D_PAGE, dData = null, dCols = [], dHits = [], dHand = '';
  try { dPick = JSON.parse(store('bench.discover')) || dPick; } catch (e) { /* not ours: the default */ }
  function escH(s) { return String(s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }
  function dRead() {                 // the page's rows, read once: their facet values and words
    var node = $('d-data');
    if (node && (!dData || dData.node !== node)) {
      dData = JSON.parse(node.textContent); dData.node = node;
      dData.rows = dData.rows.map(function (a) {
        var r = {};
        dData.fields.forEach(function (k, i) { if (a[i]) r[k] = a[i]; });
        r.f = {'new': [r['new'] ? '1' : ''], price: [r.price === 'free' ? 'free' : 'paid'], reach: [r.reach],
               caps: r.caps ? r.caps.split(' ') : [], provider: [r.provider]};
        r.q = [r.name, r['new'] ? 'new' : '', r.model, r.on, r.rank, dData.providers[r.provider], r.reach, r.price,
               r.io, r.caps, r.released, r.limits].join(' ').toLowerCase();
        return r;
      });
      dCols = Array.prototype.map.call($('d-table').tHead.rows[0].cells, function (th) { return th.className; });
      dData.boxes = Array.prototype.map.call($('d-facets').querySelectorAll('input[data-df]'), function (c) {
        return [c, c.parentNode.querySelector('.n'), c.dataset.df + '|' + c.value];
      });
      dData.near = new IntersectionObserver(function (seen) { if (seen[0].isIntersecting) more(); },
                                            {root: $('d-more').parentNode, rootMargin: '0px 0px 600px'});
    }
    return node && dData;
  }
  function dCell(r, k) {             // what sort() orders a column by: as its old cells' data-sort
    return k === 'rank' ? r.rank || 99999 : k === 'context' || k === 'output' ? r[k] || -1
      : k === 'reach' ? Object.keys(REACH).indexOf(r.reach) : k === 'provider' ? dData.providers[r.provider]
      : k === 'caps' ? r.caps || '—' : r[k] || '';
  }
  function dRow(r) {                 // its cells in serve.DISCOVER_COLS's order
    var dash = '<span class="muted">—</span>', or = function (v) { return v ? escH(v) : dash; };
    return '<tr class="pick-row' + (r.provider + '\n' + r.model === dHand ? ' in-hand' : '') + '" data-provider="'
      + escH(r.provider) + '" data-model="' + escH(r.model) + '">' + [
      '<a href="#" class="dmodel"><span class="alias">' + escH(r.name) + '</span></a>'
        + (r['new'] ? ' <span class="chip new" title="first seen ' + escH(r.seen) + '">new</span>' : '')
        + '<span class="conn">' + escH(r.model + (r.on ? ' · ' + r.on : '')) + '</span>',
      r.rank ? '<span title="' + escH(r.rank_title) + '">' + r.rank + '</span>' : dash,
      '<span title="' + escH(r.provider) + '">' + escH(dData.providers[r.provider]) + '</span>',
      // Easy to add: a button to Add, filled in with the provider's base URL (addProvider).
      r.reach === 'easy to add' && dData.apis[r.provider]
        ? '<button type="button" class="chip warn d-addep" data-provider="' + escH(r.provider) + '" title="Add '
          + escH(dData.apis[r.provider]) + ' as an endpoint">easy to add</button>'
        : '<span class="chip ' + REACH[r.reach] + '" title="' + escH(dData.apis[r.provider] || '') + '">' + r.reach + '</span>',
      escH(r.price), r.context ? NUM.format(Math.floor(r.context / 1000)) + 'k' : dash, escH(r.io),
      r.caps ? r.caps.split(' ').map(function (c) { return '<span class="chip">' + c + '</span>'; }).join(' ') : dash,
      or(r.released), or(r.limits), r.output ? NUM.format(r.output) : dash, or(r.knowledge), or(r.weights),
      or(r.listed), or(r.seen), r.add ? '<button class="btn small" type="button">Add…</button>' : ''
    ].map(function (h, i) { return '<td class="' + dCols[i] + '">' + h + '</td>'; }).join('') + '</tr>';
  }
  function dApply() {
    if (!dRead()) return;
    var picked = D_FACETS.filter(function (k) { return (dPick[k] || []).length; });
    var q = $('d-q').value.trim().toLowerCase(), counts = {}, hits = [], th = $('d-table').querySelector('th[aria-sort]');
    dData.rows.forEach(function (r) {
      if (q && r.q.indexOf(q) === -1) return;
      var fails = picked.filter(function (k) {
        return !dPick[k][k === 'caps' ? 'every' : 'some'](function (v) { return r.f[k].indexOf(v) !== -1; });
      });
      if (!fails.length) hits.push(r);
      else if (fails.length > 1) return;
      for (var i = 0; i < D_FACETS.length; i++) {
        // Failing only this facet, another of its boxes would show the row (not Can: it wants all).
        var k = D_FACETS[i], f = r.f[k];
        if (fails.length && (fails[0] !== k || k === 'caps')) continue;
        for (var j = 0; j < f.length; j++) counts[k + '|' + f[j]] = (counts[k + '|' + f[j]] || 0) + 1;
      }
    });
    dData.boxes.forEach(function (b) {                // written only where it changed: each write is a restyle
      var n = NUM.format(counts[b[2]] || 0);
      if (b[1].textContent !== n) b[1].textContent = n;
      b[0].parentNode.classList.toggle('none', n === '0' && !b[0].checked);
    });
    dHits = th ? ordered(hits.map(function (r) { return [key(String(dCell(r, th.dataset.k))), r]; }), th.getAttribute('aria-sort')) : hits;
    dDraw(0);
  }
  function dDraw(from) {             // the rows from `from` to dLimit, after those drawn, or for them
    var n = dHits.length, html = dHits.slice(from, dLimit).map(dRow).join(''), b = $('d-more'), body = $('d-table').tBodies[0];
    $('d-count').textContent = NUM.format(n) + (n === 1 ? ' model' : ' models') + (n > dLimit ? ', the first ' + dLimit + ' shown' : '');
    b.hidden = n <= dLimit;
    if (from) body.insertAdjacentHTML('beforeend', html);
    else body.innerHTML = html || '<tr><td colspan="16" class="muted">' + (dData.rows.length ? 'Nothing matches the search and the filters.'
      : 'Nothing listed yet: Refresh lists fetches the catalogs now.') + '</td></tr>';
    dData.near.unobserve(b); dData.near.observe(b);     // asked afresh: still near the foot, more come
  }
  function more() { if (dLimit < dHits.length) { dLimit += D_PAGE; dDraw(dLimit - D_PAGE); } }
  var dWide = matchMedia('(min-width: 1440px)');      // the facets: a column from 1440px, open there; a fold under
  function dFold() { if ($('d-facets') && dWide.matches) $('d-facets').open = true; }
  dWide.addEventListener('change', dFold);
  onLoad.discover = function () {
    $('d-facets').querySelectorAll('input[data-df]').forEach(function (c) {
      c.checked = (dPick[c.dataset.df] || []).indexOf(c.value) !== -1;
    });
    dFold(); dApply();
  };
  document.addEventListener('change', function (ev) {
    if (!ev.target.matches('input[data-df]')) return;
    dPick = {};
    $('d-facets').querySelectorAll('input[data-df]:checked').forEach(function (c) {
      (dPick[c.dataset.df] = dPick[c.dataset.df] || []).push(c.value);
    });
    store('bench.discover', JSON.stringify(dPick));
    dLimit = D_PAGE; dApply();
  });
  document.addEventListener('input', function (ev) {
    if (ev.target.id === 'd-q') { dLimit = D_PAGE; dApply(); }
  });
  on('click', 'd-more', more);

  // ---- live: the page follows the data ---------------------------------
  // The data's stamp (serve.data_stamp) is asked every 10 s while the page is
  // in view; when it moves, every page is marked to fetch again and the one
  // shown is swapped in place (swap keeps filters, folds and sorting). Never
  // under your hands: while a popup is open, a form on the page has input not
  // saved or the focus, text on it is selected or a chart is hovered, it
  // waits, and New results offers it. A page left with input not saved is
  // not swapped when shown again either (swap).
  var fresh = $('fresh');
  function typed(ev) {
    var t = ev.target, tab = t.closest && t.closest('.tab');
    if (tab && !t.matches(KEPT)) edited[tab.id] = true;
  }
  document.addEventListener('input', typed);
  document.addEventListener('change', typed);
  function busy(s) {
    var a = document.activeElement, sel = getSelection();
    return !!(edited[s.id] || (s.contains(a) && a.matches('input, textarea, select') && !a.matches(KEPT))
      || (sel && String(sel) && sel.containsNode(s, true)) || s.querySelector('svg:hover'));
  }
  // The docked model editor is not a popup: the page beside it follows the data.
  function held() { return !!document.querySelector('dialog[open]:not(#model-dialog)') || busy($('tab-' + shown)); }
  function poll() {
    if (document.hidden || fetching[shown]) return;          // one at a time: a slow page is not asked again meanwhile
    get('/api/stamp').then(function (s) {
      if (!(s.stamp > D.stamp + 0.5)) return;
      if (held()) { fresh.hidden = false; return; }
      fresh.hidden = true; D.stamp = s.stamp;
      refreshPanes(null, true);
    }).catch(function () {});
  }
  setInterval(poll, 10000);
  document.addEventListener('visibilitychange', poll);
  fresh.addEventListener('click', function () { fresh.hidden = true; refreshPanes(); });

  // ---- the model dialog ------------------------------------------------
  var md = $('model-dialog');
  var m = {title: $('m-title'), served: $('m-served'), editor: $('m-editor'), gone: $('m-gone'),
           alias: $('m-alias'), role: $('m-role'), roleWhy: $('m-role-why'), adhoc: $('m-adhoc'),
           danger: $('m-danger'), errors: $('m-errors'), done: $('m-done'), save: $('m-save'),
           restore: $('m-restore'), close: $('m-close'), removeBox: $('m-remove-box'),
           reason: $('m-reason'), remove: $('m-remove'), onboard: $('m-onboard'),
           qtypes: $('m-qtypes'), aliasWhy: $('m-alias-why'), roleHint: $('m-role-hint')};
  m.aliasWhy.dataset.chat = m.aliasWhy.textContent;
  var editing = null, opened = {};     // opened: what the editor opened with (not `shown`, the page's name)

  function openModel(alias) {
    var model = D.models[alias], gone = (D.retired || {})[alias];
    if (!model && !gone) return;
    editing = model ? alias : null;
    m.title.textContent = alias;
    m.errors.textContent = ''; m.done.textContent = '';
    m.editor.hidden = !model; m.save.hidden = !model; m.removeBox.hidden = !model;
    m.gone.hidden = !!model; m.restore.hidden = !!model;
    showOnboardIssue(model ? (D.onboard || {})[alias] : null);
    if (gone) {
      m.served.textContent = 'served as ' + gone.served + (gone.tag ? ' · ' + gone.tag : '');
      m.gone.textContent = '';
      m.gone.appendChild(el('strong', {}, 'Retired ' + gone.at.slice(0, 10) + (gone.by === 'auto' ? ' automatically' : ' by hand')));
      m.gone.appendChild(el('p', {style: 'margin:.3rem 0'}, gone.why || ''));
      m.gone.appendChild(el('p', {class: 'muted', style: 'margin:0'},
        'Its evidence is untouched. Restore puts back the same alias, role and endpoint; failures recorded before the restore are ignored.'));
      m.restore.dataset.alias = alias; m.restore.disabled = false; m.restore.textContent = 'Restore';
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
      // Run suite: for a chat model (a decision model has no suite; an image model no chat probe).
      $('m-rerun-box').hidden = decision || model.role === 'image';
      $('m-rerun').disabled = false; $('m-rerun').textContent = 'Run the probe and full suite now';
      $('m-rerun-errors').textContent = '';
      m.qtypes.hidden = !decision;
      m.qtypes.querySelectorAll('input').forEach(function (c) {
        c.checked = decision && model.question_types.indexOf(c.value) !== -1;
      });
      fillFacts(alias, decision); fillOptions(alias, decision);
      m.aliasWhy.textContent = decision
        ? 'The name decide.py and the gateway call it by, and every record is keyed by. Letters, digits, dot, dash, underscore; unused, and never a retired name.'
        : m.aliasWhy.dataset.chat;
      Array.prototype.forEach.call(m.role.options, function (o) {
        o.disabled = model.kind !== 'decision' && o.value === 'decision';
      });
      m.adhoc.checked = !!model.ad_hoc;
      // A decision model's suggestion from onboarding (decide.suggest_role): said, never applied.
      var hint = !decision && (D.suggested || {})[alias];
      m.roleHint.hidden = !hint;
      m.roleHint.textContent = hint ? 'Suggested when it was onboarded: ' + hint.role + ', '
        + Math.round(hint.confidence * 100) + '% sure (' + hint.by + ', from its catalog facts and description, '
        + (hint.at || '').slice(0, 10) + '). Only a suggestion: the role is the one you pick above.' : '';
      m.save.disabled = false; m.save.textContent = 'Save';
      m.reason.value = ''; m.removeBox.open = false; m.remove.disabled = false; refresh();
      // What the popup opened with: Save sends only the parts that differ from it.
      opened = {model: JSON.stringify(modelBody()), facts: factsBody(), opts: optsBody()};
    }
    dock();                            // open already: shown again as it now is (shownAgain)
  }
  $('m-rerun').addEventListener('click', function () {
    var b = this, model = D.models[editing];
    if (!model) return;
    b.disabled = true; b.textContent = 'Queueing…'; $('m-rerun-errors').textContent = '';
    post('/api/onboard-queue', {items: [{served: model.served, endpoint: model.endpoint, mode: 'rerun'}]}).then(function (res) {
      if (!res.ok) { listErrors($('m-rerun-errors'), res.errors); b.disabled = false; b.textContent = 'Run the probe and full suite now'; return; }
      b.textContent = 'Queued: the sidebar shows it';
      oqPoll();
    }).catch(function (err) { $('m-rerun-errors').textContent = String(err); b.disabled = false; });
  });
  // The editor docks beside the page (bench.css): opened by its attribute, not show(),
  // so a click leaves the focus where it was; a keyboard's goes to it, and back to the
  // link that opened it when it closes. The rows of its model are marked (markHand).
  // From a name inside a popup (a collection's members) it opens over that popup.
  var opener = null, byKey = false;
  function dock() {
    if (!md.open) {
      if (opener && opener.closest('dialog:modal')) md.showModal(); else md.setAttribute('open', '');
    }
    markHand();
    if (byKey) { byKey = false; m.title.focus(); }
  }
  function markHand() {
    var d = $('model-dialog'), alias = d && d.open && $('m-title').textContent;
    document.querySelectorAll('tr.in-hand:not(.c-row, .pick-row)').forEach(function (r) { r.classList.remove('in-hand'); });   // a collection's, an endpoint's, a catalog model's own
    if (alias) document.querySelectorAll('.tab.active a.model[data-alias="' + CSS.escape(alias) + '"]').forEach(function (a) {
      var r = a.closest('tr'); if (r) r.classList.add('in-hand');
    });
  }
  md.addEventListener('close', function () {
    markHand();
    var at = document.activeElement;
    if (opener && opener.isConnected && (md.contains(at) || at === document.body)) opener.focus();
  });
  // Escape puts away the model, else a record the page docked (Collections'); a popup
  // over them takes its own Escape, and a box that clears itself on Escape keeps it.
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || e.defaultPrevented || document.querySelector('dialog:modal')) return;
    sheet('menu', false); sheet('facts', false);
    var d = md.open ? md : document.querySelector('.tab.active dialog[open]');
    if (d) d.close();
  });
  // How its onboarding stopped (serve.onboard_issues): only while it has not
  // answered a probe since. The error as the provider gave it, and the log.
  var ONBOARD_NEXT = {
    capacity: 'A limit on the provider side, not the model. It stays registered and the daily probe tries again; once it answers, run its suite.',
    timeout: 'It did not answer within the budget. The daily probe tries again.',
    empty: 'It answered nothing, usually an output budget spent thinking: raise Max tokens under Call options.',
    paywalled: 'Its free tier has ended. Retire it below.',
    structural: 'The provider has no such model now. Retire it below.',
    entitlement: 'The account may not call it (plan, payment or permission).',
    signin: 'The key was refused: sign in again on the Endpoints page.'
  };
  function showOnboardIssue(x) {
    m.onboard.hidden = !x; m.onboard.textContent = '';
    if (!x) return;
    m.onboard.appendChild(el('strong', {}, 'Onboarding stopped at the ' + (x.step || 'probe') + ' on '
      + (x.at || '').slice(0, 10) + ': ' + x.label));
    m.onboard.appendChild(el('p', {style: 'margin:.3rem 0'}, ONBOARD_NEXT[x.failure] || x.reading || ''));
    if (x.detail) m.onboard.appendChild(el('pre', {class: 'log'}, x.detail));
    if (x.log) {
      var d = el('details'), pre = el('pre', {class: 'log'});
      d.appendChild(el('summary', {class: 'muted'}, 'Onboarding log'));
      d.appendChild(pre);
      d.addEventListener('toggle', function () {
        if (!d.open || pre.textContent) return;
        get('/api/onboard?log=' + encodeURIComponent(x.log)).then(function (s) { pre.textContent = s.ok ? (s.tail || '(empty)') : s.errors.join(' '); });
      });
      m.onboard.appendChild(d);
    }
  }
  // ---- model facts: yours in the boxes, the sources' as placeholders -----
  var FACT_NUM = ['context_window', 'max_output'], FACT_CAP = ['tools', 'vision', 'structured_output', 'reasoning'];
  function fillFacts(alias, decision) {
    var box = $('m-facts'), f = (D.facts || {})[alias], profile = $('m-profile');
    box.hidden = profile.hidden = decision || !f;
    profile.textContent = '';
    $('f-errors').textContent = '';
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
  // The facts as typed, as /api/model-facts takes them; null when the box is not shown.
  function factsBody() {
    if ($('m-facts').hidden) return null;
    var facts = {limits: {}};
    FACT_NUM.forEach(function (k) { if ($('f-' + k).value) facts[k] = parseInt($('f-' + k).value, 10); });
    FACT_CAP.forEach(function (k) { var v = $('f-' + k).value; if (v) facts[k] = v === 'yes'; });
    if ($('f-knowledge_cutoff').value.trim()) facts.knowledge_cutoff = $('f-knowledge_cutoff').value.trim();
    document.querySelectorAll('#f-limits input').forEach(function (b) { if (b.value) facts.limits[b.dataset.k] = parseInt(b.value, 10); });
    return JSON.stringify(facts);
  }
  // ---- call options: how the bench asks this model (serve.save_model_options)
  function fillOptions(alias, decision) {
    var o = (D.options || {})[alias] || {}, ceiling = ((D.facts || {})[alias] || {}).max_output;
    $('m-opts').hidden = decision;
    $('co-errors').textContent = '';
    $('co-max').value = o.max_tokens || ''; $('co-max').placeholder = "the endpoint's default";
    $('co-ceiling').textContent = ceiling ? 'At most its max output, ' + Number(ceiling).toLocaleString() + '.' : '';
  }
  function optsBody() { return $('m-opts').hidden ? null : $('co-max').value.trim(); }

  function refresh() {
    m.danger.hidden = !(editing && m.alias.value.trim() !== editing);
    m.roleWhy.textContent = D.roles[m.role.value] || '';
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a.model');
    if (a) { e.preventDefault(); opener = a; byKey = !e.detail; openModel(a.dataset.alias); }
  });
  // On Models, the arrow keys move the model in hand to the row above or below that is
  // shown; the focus goes with it, so the next key carries on from there.
  document.addEventListener('keydown', function (e) {
    var a = e.target.closest && e.target.closest('.m-table a.model'), down = e.key === 'ArrowDown';
    if (!a || !(down || e.key === 'ArrowUp') || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var shown = Array.prototype.filter.call(a.closest('table').querySelectorAll('a.model'), function (x) { return x.offsetParent; });
    var next = shown[shown.indexOf(a) + (down ? 1 : -1)];
    if (!next) return;
    e.preventDefault(); next.focus({preventScroll: true}); next.closest('tr').scrollIntoView({block: 'nearest'});
    opener = next; openModel(next.dataset.alias);
  });

  // ---- the palette: Ctrl+K (Cmd+K on a Mac), or Search in the rail. The pages, every
  // model (Models, it docked), every collection (Collections, it docked), and the last
  // five opened through it first, from what the page holds: the rail and window.BENCH.
  // Motion 3: its groups arrive one after another as it opens (bench.css .arriving).
  var pal = $('palette'), palQ = $('pal-q'), palList = $('pal-list'), palAt = 0, palFound = [];
  if (/Mac|iP/.test(navigator.platform)) document.querySelector('#pal-open kbd').textContent = '⌘ K';
  function recent() {                  // a list of {k, id}, whatever the browser has stored there
    try { var r = JSON.parse(store('bench.recent')); return Array.isArray(r) ? r.filter(Boolean) : []; } catch (e) { return []; }
  }
  function palDraw() {
    var words = palQ.value.toLowerCase().split(/\s+/).filter(Boolean), cols = C ? C.collections.map(function (r) {
      return [r.name, r.members.length]; }) : Object.keys(D.collections || {}).map(function (n) { return [n, D.collections[n]]; });
    var all = Array.prototype.map.call(tabs, function (a) {
      var n = a.querySelector('.n');
      return {k: 'page', id: a.dataset.tab, name: a.firstChild.textContent, hint: n ? n.textContent + ' open' : '', href: a.getAttribute('href')};
    }).concat(Object.keys(D.models).sort().map(function (m) {
      var d = D.models[m];
      return {k: 'model', id: m, name: m, hint: d.kind === 'decision' ? 'decision model' : (d.tag || d.endpoint || '').split('/')[0], href: '/models'};
    }), cols.map(function (c) { return {k: 'collection', id: c[0], name: 'bench/' + c[0], hint: plural(c[1], 'member'), href: '/collections'}; }));
    var fits = function (it) { var s = (it.name + ' ' + it.hint).toLowerCase(); return words.every(function (w) { return s.indexOf(w) !== -1; }); };
    var kind = function (k) { return all.filter(function (it) { return it.k === k; }); };
    var groups = [['Recent', recent().map(function (r) { return all.filter(function (it) { return it.k === r.k && it.id === r.id; })[0]; })
      .filter(Boolean)], ['Pages', kind('page')], ['Models', kind('model')], ['Collections', kind('collection')]];
    palFound = [];
    palList.innerHTML = groups.map(function (g, i) {
      var its = g[1].filter(fits).slice(0, 50);
      return its.length ? '<div role="group" aria-label="' + g[0] + '" style="--i:' + i + '"><p aria-hidden="true">' + g[0] + '</p>'
        + its.map(function (it) {
          return '<a role="option" id="pal-' + palFound.push(it) + '" href="' + it.href + '"><span'
            + (it.k === 'page' ? '>' : ' class="mono">') + escH(it.name)
            + '</span><small>' + escH(it.hint) + '</small></a>';
        }).join('') + '</div>' : '';
    }).join('') || '<p>Nothing matches “' + escH(palQ.value.trim()) + '”.</p>';
    palPick(0);
  }
  function palPick(i) {
    var opts = palList.querySelectorAll('[role=option]');
    palAt = Math.max(0, Math.min(opts.length - 1, i));
    opts.forEach(function (o, k) { o.setAttribute('aria-selected', String(k === palAt)); });
    if (opts.length) { palQ.setAttribute('aria-activedescendant', opts[palAt].id); opts[palAt].scrollIntoView({block: 'nearest'}); }
    else palQ.removeAttribute('aria-activedescendant');
  }
  function palOpen() {
    palQ.value = ''; palList.classList.add('arriving');
    pal.showModal(); palDraw();
  }
  function palGo(it) {
    store('bench.recent', JSON.stringify([{k: it.k, id: it.id}].concat(recent().filter(function (r) {
      return r.k !== it.k || r.id !== it.id; })).slice(0, 5)));
    pal.close();
    if (it.k === 'page') return go(it.id);
    if (it.k === 'collection') return go('collections').then(function () {
      if (md.open) md.close();                         // one record docked at a time
      var b = document.querySelector('#c-list .c-row[data-name="' + CSS.escape(it.id) + '"] .c-open'); if (b) b.click();
    });
    go('models').then(function () {
      var a = document.querySelector('#tab-models a.model[data-alias="' + CSS.escape(it.id) + '"]'), g = a && a.closest('tbody[data-shut]');
      if (g) { fold(g, true); foldButtons($('tab-models')); }
      if (a) a.scrollIntoView({block: 'nearest'});
      opener = a; byKey = true; openModel(it.id);
    });
  }
  document.addEventListener('keydown', function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || String(e.key).toLowerCase() !== 'k') return;
    if (pal.open) { e.preventDefault(); pal.close(); }
    else if (!document.querySelector('dialog:modal')) { e.preventDefault(); palOpen(); }
  });
  $('pal-open').addEventListener('click', palOpen);
  palQ.addEventListener('input', function () { palList.classList.remove('arriving'); palDraw(); });
  palQ.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); palPick(palAt + (e.key === 'ArrowDown' ? 1 : -1)); }
    if (e.key === 'Enter' && palFound.length) { e.preventDefault(); palGo(palFound[palAt]); }
  });
  palList.addEventListener('click', function (e) {
    var o = e.target.closest('[role=option]');
    if (o && !(e.ctrlKey || e.metaKey || e.shiftKey || e.button)) { e.preventDefault(); palGo(palFound[o.id.slice(4) - 1]); }
  });
  m.close.addEventListener('click', function () { md.close(); });
  m.alias.addEventListener('input', refresh);
  m.role.addEventListener('change', refresh);

  // A finished onboarding is said, left up for two seconds, then the popup
  // closes and the pages are fetched again with the model in them.
  function closeAndRefresh(dialog) {
    setTimeout(function () { dialog.close(); refreshPanes(); }, 2000);
  }
  // A saved change: the popup shows the model as it now is, with what was
  // done said in `node`, if it is still open on that model.
  function shownAgain(alias, node, text, saved) {
    directory().then(function () {
      if (!md.open || m.title.textContent !== alias) return;
      openModel(alias); $(node).textContent = text;
      if (saved) doneOn(m.save, 'Saved');
    }, function () { location.reload(); });
  }
  function done(res) { return 'Done: ' + (res.changed || []).join(', ') + (res.sync ? ' · ' + res.sync : '') + '.'; }
  // The tables (a role, an ad-hoc mark) follow once the popup closes (live). A rename
  // fetches every page again: every row is keyed by the old alias.
  function after(button, label, then) {
    return function (res) {
      if (!res.ok) { listErrors(m.errors, res.errors); button.disabled = false; button.textContent = label; return; }
      then(res);
    };
  }
  function modelBody() {
    var body = {new_alias: m.alias.value.trim(), new_role: m.role.value, new_ad_hoc: m.adhoc.checked};
    if (!m.qtypes.hidden) {
      body.new_question_types = Array.prototype.filter.call(m.qtypes.querySelectorAll('input'),
        function (c) { return c.checked; }).map(function (c) { return c.value; });
    }
    return body;
  }
  // The one Save: facts, then call options, then the model itself (alias, role, ad hoc,
  // question types), each only if changed. The model goes last: a rename moves the
  // facts and options saved under the old alias with it. The first refusal stops it.
  m.save.addEventListener('click', function () {
    if (!editing) return;
    var alias = editing, body = modelBody(), facts = factsBody(), opts = optsBody(), said = [];
    var steps = [];
    if (facts !== opened.facts) steps.push(function () {
      return post('/api/model-facts', {alias: alias, facts: JSON.parse(facts)}).then(function (r) {
        if (!r.ok) { listErrors($('f-errors'), r.errors); $('m-facts').open = true; throw 'refused'; }
        said.push('model facts');
      });
    });
    if (opts !== opened.opts) steps.push(function () {   // the llm CLI is told too: seconds
      return post('/api/model-options', {alias: alias, max_tokens: opts ? Number(opts) : null}).then(function (r) {
        if (!r.ok) { listErrors($('co-errors'), r.errors); $('m-opts').open = true; throw 'refused'; }
        ownChange();                         // only this popup shows them
        said.push('call options' + (r.note ? ' (' + r.note + ')' : ''));
      });
    });
    if (JSON.stringify(body) !== opened.model) steps.push(function () {
      body.alias = alias;
      return post('/api/model', body).then(function (r) {
        if (!r.ok) { listErrors(m.errors, r.errors); throw 'refused'; }
        said.push('the model (' + done(r).replace(/^Done: |\.$/g, '') + ')');
        if (body.new_alias !== alias) { m.title.textContent = body.new_alias; refreshPanes(); }
      });
    });
    m.errors.textContent = ''; m.done.textContent = ''; $('f-errors').textContent = ''; $('co-errors').textContent = '';
    if (!steps.length) { m.done.textContent = 'Nothing changed.'; return; }
    (body.new_alias === alias ? Promise.resolve(true)
      : ask('Rename ' + alias + ' to ' + body.new_alias + '? Every record keyed by the old alias follows it.', 'Rename')
    ).then(function (yes) {
      if (!yes) return;
      m.save.disabled = true; m.save.textContent = 'Saving…';
      steps.reduce(function (p, step) { return p.then(step); }, Promise.resolve()).then(function () {
        // The tables that show facts follow once the popup closes (live).
        shownAgain(body.new_alias, 'm-done', 'Saved: ' + said.join(' · ') + '.', true);
      }).catch(function (err) {
        if (err !== 'refused') m.errors.textContent = String(err);
        if (said.length) m.done.textContent = 'Saved before that: ' + said.join(' · ') + '.';
        m.save.disabled = false; m.save.textContent = 'Save';
      });
    });
  });
  // The confirm is the last word: the popup closes at once and the page
  // shows the model retired when it is done. A refusal reopens it with the reason.
  m.remove.addEventListener('click', function () {
    if (!editing) return;
    var alias = editing;
    ask('Retire ' + alias + '? The registry entry goes; the evidence stays, and Restore brings it back.', 'Retire', true)
      .then(function (yes) {
        if (!yes) return;
        m.remove.disabled = true; m.errors.textContent = ''; md.close();
        function refused(errors) { byKey = false; dock(); listErrors(m.errors, errors); m.remove.disabled = false; }
        post('/api/remove', {alias: alias, reason: m.reason.value}).then(function (res) {
          if (!res.ok) { refused(res.errors); return; }
          m.remove.disabled = false; markRetired([alias], true); refreshNeeds();
        }).catch(function (err) { refused([String(err)]); });
      });
  });
  m.restore.addEventListener('click', function () {
    var alias = m.restore.dataset.alias;
    m.restore.disabled = true; m.restore.textContent = 'Restoring…';
    // As Restore on Action needed, and the popup turns into the model's editor.
    post('/api/restore', {alias: alias}).then(after(m.restore, 'Restore', function (res) {
      markRetired([alias], false); refreshNeeds(); shownAgain(alias, 'm-done', done(res));
    })).catch(function (err) { m.errors.textContent = String(err); });
  });

  // ---- after an action: the part it changed, not the whole page ----------
  // Action needed's page and its count in the sidebar, from the server
  // (GET /api/needs, a few ms): a page reload
  // renders every tab, seconds.
  var needsAsked = 0;
  function refreshNeeds(live) {
    var mine = ++needsAsked;
    return get('/api/needs').then(function (n) {
      if (mine !== needsAsked) return;               // two actions close together: only the later answer is current
      swap('needs', n.html, live);
      var tab = document.querySelector('.tabs [data-tab=needs]'), count = tab.querySelector('.n');
      if (!n.count) { if (count) count.remove(); tab.removeAttribute('aria-label'); }
      else {
        if (!count) { count = document.createElement('span'); count.className = 'n'; count.setAttribute('aria-hidden', 'true'); tab.appendChild(count); }
        count.textContent = n.count;
        tab.setAttribute('aria-label', tab.firstChild.textContent + ', ' + n.count + ' open');
      }
      if ('first' in n) showFirst(n.first);
    }).catch(function () { location.reload(); });          // could not ask: the old way
  }
  // A retired model's rows grey out and hide behind Show retired, as the
  // server draws them (serve.tr); a restored one's come back. The popup's
  // list of models (window.BENCH) is fetched again; the rest of the page
  // follows once the popup closes (live).
  function markRetired(aliases, gone) {
    document.querySelectorAll('a.model').forEach(function (a) {
      var row = aliases.indexOf(a.dataset.alias) !== -1 && a.closest('tr');
      if (row && !row.closest('#tab-needs')) row.classList.toggle('retired', gone);
    });
    directory().catch(function () {});
  }
  function directory() {
    return get('/api/directory').then(function (d) {
      var stamp = D.stamp; Object.assign(D, d); D.stamp = stamp;
    });
  }
  // After a change: the pages it changed (`ids`, else every one, with Action
  // needed's count, At a glance's line and the popup's models) are fetched
  // again, the one shown now and the rest when next opened. What a reload
  // did, at the cost of one page. `live`: the data moved (swap).
  function refreshPanes(ids, live) {
    (ids || Array.prototype.map.call(tabs, function (b) { return b.dataset.tab; })).forEach(function (id) {
      if (ids || id !== 'needs') $('tab-' + id).setAttribute('data-lazy', (live ? 'live' : '') + ++marks);   // a new mark: load drops an older answer
    });
    if (!ids) { refreshNeeds(live); directory().catch(function () {}); }
    if (!live) { ownChange(); return show(shown); }
    // The stamp is the poll's; the page's own loader (onShow) runs only on new HTML.
    var id = shown, before = last[id];
    return load(id).then(function () { if (last[id] !== before && onShow[id] && shown === id) onShow[id](); });
  }
  // An action shown in place is not "new results" to offer a reload for.
  // ponytail: a change by something else in the same second is swallowed too.
  function ownChange() {
    get('/api/stamp').then(function (s) { D.stamp = s.stamp; }).catch(function () {});
  }

  // ---- problem actions ---------------------------------------------------
  // On the document: refreshNeeds swaps these buttons for new ones.
  document.addEventListener('click', function (e) {
      var b = e.target.closest('[data-act]');
      if (!b) return;
      var act = b.dataset.act, aliases = (b.dataset.aliases || b.dataset.alias || '').split(' ').filter(Boolean);
      var status = b.closest('[data-row]').querySelector('.why');   // a model's line, or its group's head
      if (act === 'dismiss') {               // a notice: gone once seen
        b.disabled = true;
        post('/api/alerts/dismiss', {key: b.dataset.key}).then(function (res) {
          if (!res.ok) { status.textContent = res.errors.join('; '); status.classList.add('err'); b.disabled = false; return; }
          refreshNeeds(); ownChange();
        }).catch(function (err) { status.textContent = String(err); b.disabled = false; });
        return;
      }
      if (act === 'retry') {                 // no confirm: one call each, and only an answer changes anything
        var label = b.textContent, said = [], failed = [];
        b.disabled = true; b.textContent = 'Asking…';
        aliases.reduce(function (chain, alias) {
          return chain.then(function () {
            return post('/api/retry', {alias: alias}).then(function (res) {
              (res.ok ? said : failed).push(res.ok ? res.changed.join(' · ') : res.errors.join('; '));
            });
          });
        }, Promise.resolve()).then(function () {
          // A group's Retry all says how many answered; each failure is on hover, not ten lines in its heading.
          var told = aliases.length > 1 ? said.length + ' of ' + aliases.length + ' answered' : said.concat(failed).join(' · ');
          status.title = failed.join('\n');
          if (!said.length) { status.textContent = told; status.classList.add('err'); b.disabled = false; b.textContent = label; return; }
          status.textContent = told + '.'; refreshNeeds();
        }).catch(function (err) { status.textContent = String(err); b.disabled = false; b.textContent = label; });
        return;
      }
      var verb = act === 'restore' ? 'Restore' : 'Retire';
      ask(verb + ' ' + aliases.join(', ') + '?' + (act === 'restore' ? '' : ' The evidence stays and Restore brings it back.'),
          verb, act !== 'restore').then(function (yes) {
        if (!yes) return;
        b.disabled = true; b.textContent = verb === 'Restore' ? 'Restoring…' : 'Retiring…';
        var chain = Promise.resolve([]);
        aliases.forEach(function (alias) {
          chain = chain.then(function (errs) {
            var call = act === 'restore' ? post('/api/restore', {alias: alias})
              : post('/api/remove', {alias: alias, reason: 'from the dashboard: ' + (b.dataset.why || 'retired by hand')});
            return call.then(function (res) { return errs.concat(res.ok ? [] : res.errors); });
          });
        });
        chain.then(function (errs) {
          if (errs.length) { status.textContent = errs.join('; '); status.classList.add('err'); b.disabled = false; b.textContent = verb; return; }
          markRetired(aliases, act !== 'restore');
          status.textContent = 'Done.'; refreshNeeds();
        });
      });
  });

  // ---- onboarding --------------------------------------------------------
  var od = $('onboard-dialog');
  var o = {find: $('o-find'), picker: $('o-picker'), errors: $('o-errors'), start: $('o-start'),
           close: $('o-close'), choose: $('o-choose'), run: $('o-run'), steps: $('o-steps'),
           log: $('o-log'), title: $('o-title'), result: $('o-result'), dismiss: $('o-dismiss')};
  var STEPS = ['Served by the endpoint','Registered', 'Synced to the llm CLI', 'Availability probe, 4 calls', 'Internal suite'];
  var timer = null, data = null;
  // What is ticked, by the candidate's key (served@endpoint): kept while the
  // search redraws the list, so you can search, tick, search again, tick.
  var chosen = {};
  // Folded groups, per browser: openrouter (direct) alone is hundreds of rows.
  var NEW = 'New since the daily check', folded = {};
  try { (JSON.parse(store('bench.picker.folded') || '[]') || []).forEach(function (g) { folded[g] = 1; }); } catch (e) { folded = {}; }

  function armStart() {
    var n = Object.keys(chosen).length;
    o.start.disabled = o.dismiss.disabled = !n;
    o.start.textContent = n > 1 ? 'Onboard ' + n + ' models' : 'Onboard model';
    o.dismiss.textContent = n > 1 ? 'Dismiss ' + n + ' models' : 'Dismiss model';
  }
  // Rows move between the lists here rather than by asking every endpoint again.
  function moveRows(keys, from, to) {
    data[from] = data[from].filter(function (c) { if (keys.indexOf(c.key) === -1) return true; data[to].push(c); return false; });
    data[to].sort(function (a, b) { return a.served < b.served ? -1 : a.served > b.served ? 1 : 0; });
  }
  function pickRow(c, dismissed) {
    var row = el('label', {class: 'pick'});
    var box = el('input', {type: 'checkbox', name: 'o-pick', value: c.served, 'data-endpoint': c.endpoint});
    if (dismissed) box.disabled = true;
    box.checked = !dismissed && !!chosen[c.key];
    row.appendChild(box);
    var name = el('span', {class: 'alias'}, c.served);
    // What it takes besides text (every model takes text, so that says
    // nothing), and whether it makes images: an image generator is no seat.
    // One order on every row, so the rows scan alike.
    var ORDER = ['image', 'pdf', 'file', 'audio', 'video'];
    (c.inputs || []).filter(function (m) { return m !== 'text'; }).sort(function (a, b) {
      return (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99);
    }).forEach(function (m) { name.appendChild(el('span', {class: 'chip mod', title: 'takes ' + m}, m)); });
    if ((c.outputs || []).indexOf('image') !== -1) {
      name.appendChild(el('span', {class: 'chip mod warn', title: 'an image generator: never a panel seat'}, 'makes images'));
    }
    row.appendChild(name);
    var side = el('span');
    if (c.new && !dismissed) side.appendChild(el('span', {class: 'muted'}, c.group));   // under New: where it comes from
    if (dismissed) {
      var b = el('button', {type: 'button', class: 'linkish', style: 'margin-left:.6rem'}, 'Bring back');
      b.addEventListener('click', function (e) {
        e.preventDefault();
        post('/api/dismiss', {served: c.key, undo: true}).then(function (res) {
          if (!res.ok) { listErrors(o.errors, res.errors); return; }
          moveRows([c.key], 'dismissed', 'candidates'); renderPicker();
        });
      });
      side.appendChild(b);
    }
    row.appendChild(side);
    box.addEventListener('change', function () {
      if (box.checked) chosen[c.key] = {served: c.served, endpoint: c.endpoint};
      else delete chosen[c.key];
      armStart();
    });
    return row;
  }
  // Search ignores case, spaces, dots, dashes, slashes and underscores: "glm 5-3 flash" finds
  // z-ai/glm-5-3-flash, and "glm 5.3" finds glm-5-3 as well as glm-5.3.
  function squash(s) { return String(s || '').toLowerCase().replace(/[\s._\/:-]+/g, ''); }
  function hit(q, fields) { return !q || fields.some(function (s) { return squash(s).indexOf(q) !== -1; }); }
  function renderPicker() {
    o.picker.textContent = '';
    if (!data) return;
    var q = squash(o.find.value);
    var groups = {};
    // Retired ones a search finds come first: onboarding refuses them, Restore is the way back.
    var old = (data.retired || []).filter(function (c) { return hit(q, [c.served, c.alias, c.endpoint]); });
    if (old.length && q) o.picker.appendChild(retiredGroup(old, true));
    data.candidates.forEach(function (c) {
      // The endpoint too: "Onboard a model from it" filters on it, and the default
      // endpoint's models are grouped by connection, not by its name.
      if (!hit(q, [c.served, c.group, c.endpoint])) return;
      // New ones in a group of their own, first: just added to the hub is what
      // you came to onboard, and it is there without scrolling or unfolding.
      var g = c.new ? NEW : c.group;
      (groups[g] = groups[g] || []).push(c);
    });
    var names = Object.keys(groups).sort(function (a, b) { return (b === NEW) - (a === NEW) || (a < b ? -1 : a > b ? 1 : 0); });
    if (!names.length && !(old.length && q)) o.picker.appendChild(el('p', {class: 'empty'}, data.candidates.length ? 'Nothing matches.' : 'Everything the endpoints serve is already on the bench, retired or dismissed.'));
    names.forEach(function (g) {
      // Folds, remembered per browser; a search opens every group it matches in.
      var d = el('details', {class: 'grp'});
      d.open = !!q || !folded[g];
      d.appendChild(el('summary', {}, g + ' · ' + groups[g].length));
      groups[g].forEach(function (c) { d.appendChild(pickRow(c, false)); });
      d.addEventListener('toggle', function () {
        if (q) return;                                   // opened by the search, not by you
        if (d.open) delete folded[g]; else folded[g] = 1;
        store('bench.picker.folded', JSON.stringify(Object.keys(folded)));
      });
      o.picker.appendChild(d);
    });
    if (data.dismissed.length) {
      var d = el('details'); d.appendChild(el('summary', {}, data.dismissed.length + ' dismissed'));
      data.dismissed.forEach(function (c) { d.appendChild(pickRow(c, true)); });
      o.picker.appendChild(d);
    }
    if (old.length && !q) o.picker.appendChild(retiredGroup(old, false));
  }
  // Served, but retired from the bench (by hand or by the daily check): when and why, and
  // Restore, which puts back its alias, role and endpoint; then its probe and suite again.
  function retiredGroup(old, open) {
    var r = el('details', {class: 'retired-grp'}); r.open = open;
    r.appendChild(el('summary', {}, old.length + ' retired: Restore brings one back'));
    old.forEach(function (c) { r.appendChild(retiredRow(c)); });
    return r;
  }
  function retiredRow(c) {
    var row = el('div', {class: 'pick retired'});
    var name = el('span', {class: 'alias'}, c.served);
    name.appendChild(el('small', {}, 'retired ' + c.at + ' as ' + c.alias + ': ' + c.why));
    row.appendChild(name);
    var side = el('span'), b = el('button', {type: 'button', class: 'linkish'}, 'Restore');
    side.appendChild(b); row.appendChild(side);
    // Restore lifts the retirement only (serve.unretire): the model goes back into its
    // provider's group, ticked, to be onboarded like the rest. A decision model is put
    // back on the bench whole instead, so it leaves the list.
    b.addEventListener('click', function () {
      b.disabled = true;
      post('/api/unretire', {alias: c.alias}).then(function (res) {
        if (!res.ok) { b.disabled = false; listErrors(o.errors, res.errors); return; }
        data.retired = data.retired.filter(function (x) { return x.key !== c.key; });
        if ((res.changed || []).indexOf('config/decision-models.json') === -1) {
          var back = Object.assign({}, c); delete back.alias; delete back.at; delete back.why;
          data.candidates.push(back);
          data.candidates.sort(function (a, b) { return a.served < b.served ? -1 : a.served > b.served ? 1 : 0; });
          chosen[c.key] = {served: c.served, endpoint: c.endpoint};
          delete folded[c.group];
        }
        renderPicker(); armStart();
      });
    });
    return row;
  }
  // The server keeps each endpoint's list a few minutes (serve.LISTED_FOR);
  // Refresh asks them all again.
  function showAge(s) {
    var age = $('o-age');
    age.hidden = s == null;
    if (s == null) return;
    age.firstChild.textContent = s < 60 ? 'Listed just now.' : 'Listed ' + Math.round(s / 60) + ' min ago.';
  }
  function loadCandidates(fresh) {
    showAge(null);
    o.picker.textContent = ''; o.picker.appendChild(el('p', {class: 'empty'}, 'Please wait. Gathering models from your endpoints…'));
    o.start.disabled = o.dismiss.disabled = true;
    // Typing before the list arrives is lost, or lands on a row; the mode waits with it.
    o.find.readOnly = true; $('o-mode').disabled = $('o-vision').disabled = true;
    function ready() { o.find.readOnly = false; $('o-mode').disabled = $('o-vision').disabled = false; }
    get('/api/candidates' + (fresh ? '?fresh=1' : '')).then(function (res) {
      ready(); o.find.focus();
      if (!res.ok) { o.picker.textContent = ''; listErrors(o.errors, res.errors); return; }
      if (res.errors.length) listErrors(o.errors, res.errors);   // one endpoint down; the rest listed
      data = res; showAge(res.age);
      // Only what is still offered stays ticked.
      var offered = {};
      res.candidates.forEach(function (c) { offered[c.key] = 1; });
      Object.keys(chosen).forEach(function (k) { if (!offered[k]) delete chosen[k]; });
      renderPicker(); armStart();
    }).catch(function (err) { ready(); o.errors.textContent = String(err); });
  }
  $('onboard-open').addEventListener('click', function () {
    preStep = null;
    o.errors.textContent = ''; o.find.value = ''; o.choose.hidden = false; o.run.hidden = true;
    $('o-queue').hidden = true; chosen = {}; waitToken++; $('o-vision').checked = false;
    o.start.hidden = o.dismiss.hidden = false; armStart(); o.title.textContent = 'Onboard a model';
    od.showModal(); o.find.focus(); loadCandidates();
  });
  o.find.addEventListener('input', renderPicker);
  $('o-refresh').addEventListener('click', function () { o.errors.textContent = ''; loadCandidates(true); });
  o.close.addEventListener('click', function () { od.close(); });
  od.addEventListener('close', function () { if (timer) { clearInterval(timer); timer = null; } });

  // Discover's Add through a hub: the hub's own steps come before onboard.py's
  // five, as one more step at the top, {label, state: now|done|fail, note}.
  var preStep = null;
  function renderSteps(text, finished, exit) {
    var reached = 0;
    for (var i = 1; i <= 5; i++) if (text.indexOf('[' + i + '/5]') !== -1) reached = i;
    var stopped = /STOPPING|is not served|cannot reach/.test(text) || (finished && exit !== 0);
    var lines = text.split('\n'), was = o.steps.querySelectorAll('li.done').length;
    o.steps.textContent = '';
    Promise.resolve().then(function () { ticks(o.steps, was); });     // once the list below is built
    if (preStep) {
      var li0 = el('li', {class: preStep.state}, preStep.label);
      if (preStep.note) li0.appendChild(el('small', {}, preStep.note));
      o.steps.appendChild(li0);
      if (preStep.state !== 'done') {             // onboard.py has not started: its steps wait
        STEPS.forEach(function (label) { o.steps.appendChild(el('li', {}, label)); });
        return;
      }
    }
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
  // Everything ticked goes to the queue (serve.queue_onboard), which runs one
  // model at a time. The popup closes: the sidebar's chip shows the batch, and
  // its Details the queue. Refused, it stays open with the reasons.
  o.start.addEventListener('click', function () {
    var mode = $('o-mode').value, vision = $('o-vision').checked;
    var items = Object.keys(chosen).map(function (k) { return Object.assign({mode: mode, vision: vision}, chosen[k]); });
    if (!items.length) return;
    o.errors.textContent = ''; o.start.disabled = o.dismiss.disabled = true; o.start.textContent = 'Starting…';
    post('/api/onboard-queue', {items: items}).then(function (res) {
      if (!res.ok) { listErrors(o.errors, res.errors); armStart(); return; }
      od.close(); oqPoll();
    }).catch(function (err) { o.errors.textContent = String(err); armStart(); });
  });
  // Every ticked model at once (serve.dismiss); the dismissed list brings one back.
  o.dismiss.addEventListener('click', function () {
    var keys = Object.keys(chosen);
    if (!keys.length) return;
    o.errors.textContent = ''; o.start.disabled = o.dismiss.disabled = true;
    post('/api/dismiss', {served: keys}).then(function (res) {
      if (!res.ok) { listErrors(o.errors, res.errors); armStart(); return; }
      moveRows(keys, 'candidates', 'dismissed'); chosen = {}; renderPicker(); armStart();
    }).catch(function (err) { o.errors.textContent = String(err); armStart(); });
  });
  // After Discover's Add queues a model: with nothing ahead of it, it is
  // followed step by step, otherwise the queue shows.
  var waitToken = 0;
  function afterQueued(res, one, title, before) {
    oqPoll();
    var at = -1;            // its LAST entry: an earlier, finished one may share its name
    res.items.forEach(function (i, n) { if (one && i.served === one.served && i.endpoint === one.endpoint) at = n; });
    var ahead = at === -1 ? [] : res.items.slice(0, at).filter(function (i) { return i.state === 'queued' || i.state === 'running'; });
    if (one && at !== -1 && !ahead.length) waitAndFollow(one, at, title || 'Onboarding ' + one.served + ' from ' + one.endpoint, before || '', ++waitToken, 20);
    else showQueue(res);
  }
  // The queue starts it within a moment; follow its log once it has one,
  // unless the popup was closed or reopened meanwhile (the sidebar has it then).
  function waitAndFollow(one, at, title, before, token, tries) {
    if (token !== waitToken || !od.open) return;
    get('/api/onboard-queue').then(function (q) {
      if (!q.ok || token !== waitToken || !od.open) return;
      var it = q.items[at];
      if (!it || it.served !== one.served || it.endpoint !== one.endpoint) { showQueue(q); return; }
      if (it.log) { follow(it.log, title, before); return; }
      if (it.state !== 'queued' && it.state !== 'running' || tries <= 0) { showQueue(q); return; }
      setTimeout(function () { waitAndFollow(one, at, title, before, token, tries - 1); }, 500);
    });
  }

  // ---- the onboarding queue: progress in the sidebar (serve.onboard_queue) -
  // Polled while it runs, and once on every page load, so a batch started
  // before a reload still shows. Its results stay until Clear.
  var oq = {box: $('oq'), cap: $('oq-cap'), bar: $('oq-bar'), models: $('oq-models'), stop: $('oq-stop'),
            clear: $('oq-clear'),
            view: $('o-queue'), sum: $('o-queue-sum'), list: $('o-queue-list')};
  var OQ_CHIP = {queued: '', running: 'new', done: 'ok', 'no answer': 'warn', failed: 'err', skipped: ''};
  var oqTimer = null, oqLast = '';
  function oqCount(q, state) { return q.items.filter(function (i) { return i.state === state; }).length; }
  function oqRender(q) {
    var n = q.items.length;
    oq.box.hidden = !n;
    var now = q.items.filter(function (i) { return i.state === 'running'; })[0];
    var finished = n - oqCount(q, 'queued') - (now ? 1 : 0);
    // The bar fills as models finish, and by fifths of the running one as its
    // log reaches each of onboard.py's five steps (serve.onboard_queue `step`):
    // a step is half a fifth while it runs, so step 5/5 is 90%, and full
    // means finished. At the end it is green when every one is on the bench,
    // red when any failed or did not answer. It fills; it never shakes.
    var step = now && now.step ? now.step : 0;
    // In the suite, by its tasks: the fifth fifth fills as they are asked.
    var part = step === 5 && now.suite && +now.suite[1] ? 4 + now.suite[0] / now.suite[1] : step ? step - 0.5 : 0;
    oq.bar.style.setProperty('--fill', n ? ((finished + part / 5) / n).toFixed(3) : 0);
    var bad = oqCount(q, 'failed') + oqCount(q, 'no answer');
    oq.bar.classList.toggle('done', !q.running && n > 0 && oqCount(q, 'done') === n);
    oq.bar.classList.toggle('fail', !q.running && bad > 0);
    oq.cap.textContent = q.running
      ? (q.stopping ? 'Stopping after this one' : 'Onboarding ' + (finished + 1) + ' of ' + n)
      : 'Onboarded ' + oqCount(q, 'done') + ' of ' + n;
    oq.stop.hidden = !q.running || q.stopping; oq.clear.hidden = q.running;
    var sig = JSON.stringify(q);
    if (sig !== oqLast) { oqLast = sig; oqLive = null; oqModels(q); if (!oq.view.hidden) oqList(q); }
    // The log grows between steps, which the queue's state does not show.
    if (oqLive && !oq.view.hidden && od.open && oqLive.d.open) oqTail(oqLive.log, oqLive.pre);
  }
  // Each model of the batch a line, in the order they run; the running one's
  // five steps under it, done ones ticked, the one it is on bold.
  var OQ_STEPS = ['On the endpoint', 'Registered', 'Client set up', 'Availability probed', 'Suite run'];
  var OQ_LINE = {done: ['done', ''], running: ['now', ''], queued: ['', ''], failed: ['bad', 'failed'],
                 'no answer': ['bad', 'did not answer'], skipped: ['skip', 'skipped']};
  function oqModels(q) {
    oq.models.textContent = '';
    q.items.forEach(function (it) {
      var how = OQ_LINE[it.state] || ['', it.state], li = document.createElement('li'), name = document.createElement('span');
      li.className = how[0]; name.textContent = it.served; li.appendChild(name);
      var said = how[1] || (it.mode === 'rerun' ? 'probe and suite again' : '');
      if (said) { var s = document.createElement('small'); s.textContent = said; li.appendChild(s); }
      if (it.state === 'running') {
        var ol = document.createElement('ol'); ol.className = 'oq-steps';
        OQ_STEPS.forEach(function (label, i) {
          var st = document.createElement('li'); st.textContent = label;
          st.className = i + 1 < (it.step || 0) ? 'done' : i + 1 === it.step ? 'now' : '';
          // The suite says which task it is on (serve.onboard_queue `suite`: at, of, task).
          if (i === 4 && it.step === 5 && it.suite) {
            var sub = document.createElement('small'); sub.className = 'oq-sub';
            sub.textContent = 'task ' + it.suite[0] + ' of ' + it.suite[1] + ': ' + it.suite[2];
            st.appendChild(sub);
          }
          ol.appendChild(st);
        });
        li.appendChild(ol);
      }
      oq.models.appendChild(li);
    });
  }
  function oqList(q) {
    oq.sum.textContent = q.running
      ? 'One at a time, in this order. Closing this does not stop it; Stop at the foot of the sidebar lets the model running now finish and skips the rest.'
      : q.items.length ? 'Finished. Each log is in runs/.' : 'Nothing queued.';
    oq.list.textContent = '';
    q.items.forEach(function (i) {
      var li = el('li'), head = el('div', {class: 'what'});
      head.appendChild(el('span', {class: 'alias'}, i.served));
      head.appendChild(el('span', {class: 'chip ' + (OQ_CHIP[i.state] || '')}, i.state));
      if (i.alias && i.state !== 'failed') head.appendChild(el('span', {class: 'muted'}, 'as ' + i.alias));
      li.appendChild(head);
      if (i.note) li.appendChild(el('small', {}, i.note));
      if (i.log) {
        var d = el('details'), pre = el('pre', {class: 'log'});
        d.appendChild(el('summary', {class: 'muted'}, 'Log'));
        d.appendChild(pre);
        d.addEventListener('toggle', function () {
          if (i.state === 'running') { if (d.open) delete oqFolded[i.log]; else oqFolded[i.log] = 1; }
          if (d.open) oqTail(i.log, pre);
        });
        // The running one's log is open and follows along (oqRender), unless
        // you folded it; queued ones have none yet, finished ones stay folded.
        if (i.state === 'running') { oqLive = {log: i.log, pre: pre, d: d}; d.open = !oqFolded[i.log]; }
        li.appendChild(d);
      }
      oq.list.appendChild(li);
    });
  }
  var oqLive = null, oqFolded = {};
  function oqTail(log, pre) {
    get('/api/onboard?log=' + encodeURIComponent(log)).then(function (s) {
      var end = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;   // at the bottom: stay there
      pre.textContent = s.ok ? (s.tail || '(no output yet)') : s.errors.join(' ');
      if (end) pre.scrollTop = pre.scrollHeight;
    });
  }
  function showQueue(q) {
    if (timer) { clearInterval(timer); timer = null; }   // a model followed step by step: the queue view replaces it
    waitToken++;
    o.errors.textContent = ''; o.choose.hidden = true; o.run.hidden = true; o.start.hidden = o.dismiss.hidden = true;
    oq.view.hidden = false; o.title.textContent = 'Onboarding queue';
    if (!od.open) od.showModal();
    oqLast = ''; oqRender(q);
  }
  function oqPoll() {
    get('/api/onboard-queue').then(function (q) {
      if (!q.ok) return;
      oqRender(q);
      if (q.running && !oqTimer) oqTimer = setInterval(oqPoll, 3000);
      if (!q.running && oqTimer) { clearInterval(oqTimer); oqTimer = null; }
    });
  }
  function oqAct(path) {
    post(path, {}).then(function (r) { if (r.ok) oqRender(r); else oq.cap.textContent = r.errors.join(' '); });
  }
  $('oq-open').addEventListener('click', function () {
    get('/api/onboard-queue').then(function (q) { showQueue(q); oqPoll(); });
  });
  oq.stop.addEventListener('click', function () { oqAct('/api/onboard-queue/stop'); });
  oq.clear.addEventListener('click', function () { oqAct('/api/onboard-queue/clear'); if (!oq.view.hidden) od.close(); });
  oqPoll();
  // ---- Discover: a row opens its model beside the list, with every route to add it by ----
  // The rest of what is known comes from /api/discover-model, so the page does
  // not carry 2,500 models' details it will mostly never show.
  function dd() { return $('discover-dialog'); }       // in the page: new with each fetch of it
  function price(x) {
    var c = x.cost || {};
    return x.free ? 'free' : x.free == null ? 'price unknown'
      : (c.input != null ? '$' + c.input + ' in / $' + c.output + ' out per 1M tokens' : 'paid');
  }
  function limits(l) {
    return Object.keys(l || {}).map(function (k) { return Number(l[k]).toLocaleString() + ' ' + k; }).join(' · ');
  }
  function addVia(x, b) {
    var direct = x.via === 'direct';
    ask('Add ' + x.model + ' via ' + x.label + (x.free ? '' : ' (PAID)') + '?\n\n'
      + (direct ? 'The bench only: register it on the ' + x.endpoint + ' endpoint and onboard it as chosen below.'
        // A LiteLLM or CLI Proxy API route says its own steps: only what that hub takes.
        : x.steps ? x.steps.map(function (s, i) { return (i + 1) + '. ' + s; }).join('\n')
        : '1. Open WebUI: add it to the ' + x.connection + ' connection\'s Model IDs (if that connection lists them)\n'
        + '2. Open WebUI: a public model record, tagged and with the logo of the connection\'s other models\n'
        + '3. The bench: register it and onboard it as chosen below'), 'Add', false, true).then(function (yes) {
      if (yes) addNow(x, b, direct, {mode: $('ask-mode').value, vision: $('ask-vision').checked});
    });
  }
  function addNow(x, b, direct, how) {
    var title = 'Adding ' + x.model + ' via ' + x.label;
    $('d-errors').textContent = '';
    if (direct) {
      b.disabled = true; b.textContent = 'Adding…';
      post('/api/onboard-queue', {items: [Object.assign({served: x.model, endpoint: x.endpoint}, how)]}).then(function (res) {
        if (!res.ok) { listErrors($('d-errors'), res.errors); b.disabled = false; b.textContent = 'Add via this'; return; }
        dd().close(); preStep = null; o.errors.textContent = ''; od.showModal();
        afterQueued(res, {served: x.model, endpoint: x.endpoint}, title, '');
      }).catch(function (err) { $('d-errors').textContent = String(err); b.disabled = false; b.textContent = 'Add via this'; });
      return;
    }
    // A hub's own steps take a while before the onboarding is even queued, so
    // the progress starts now: the onboarding popup with the hub as its first
    // step, and the sidebar's bar. The queue's own poll takes the bar over.
    dd().close(); waitToken++;
    preStep = {label: 'Added to ' + x.label, state: 'now', note: ''};
    runView(title, 'Adding it to ' + x.label + '…');
    oq.box.hidden = false; oq.bar.classList.remove('done', 'fail'); oq.bar.style.setProperty('--fill', '.03');
    oq.cap.textContent = title; oq.models.textContent = ''; oqLast = ''; oq.stop.hidden = oq.clear.hidden = true;
    post('/api/discover/add', Object.assign({connection: x.connection, model: x.model, endpoint: x.endpoint}, how)).then(function (res) {
      var hubDone = !!(res.served && res.steps && res.steps.length);   // in the hub, even if queueing it was refused
      preStep.state = hubDone ? 'done' : 'fail';
      preStep.note = (res.steps || []).join('. ') + (hubDone ? '' : (res.steps && res.steps.length ? '. ' : '') + res.errors.join(' '));
      renderSteps('', false, null);
      if (!res.ok) {
        o.result.textContent = hubDone ? res.errors.join(' ') : 'Not added. Nothing was queued.';
        o.result.className = 'errors';
        oqPoll();                                   // the chip back to what the queue holds
        return;
      }
      afterQueued(res, {served: res.served || x.model, endpoint: res.endpoint || x.endpoint}, title, '');
    }).catch(function (err) {
      preStep.state = 'fail'; preStep.note = String(err); renderSteps('', false, null); oqPoll();
    });
  }
  // The onboarding popup in its step-by-step view, before there is a log.
  function runView(title, message) {
    o.choose.hidden = true; o.run.hidden = false; o.start.hidden = o.dismiss.hidden = true; $('o-queue').hidden = true;
    o.title.textContent = title; o.result.className = 'served'; o.result.textContent = message; o.errors.textContent = '';
    o.log.textContent = ''; delete o.log.dataset.opened; o.log.parentNode.open = false;
    renderSteps('', false, null);
    if (!od.open) od.showModal();
  }
  // Opened by its attribute, as an endpoint is: docked, the list beside it stays live, its
  // row marked (dRow keeps the mark through a search or a filter). Its facts come with the answer.
  var dAsked = 0;
  function openDiscover(row, keyboard) {
    var d = dd(), mine = ++dAsked, provider = row.dataset.provider, model = row.dataset.model;
    dHand = provider + '\n' + model;
    $('d-table').querySelectorAll('tr.in-hand').forEach(function (r) { r.classList.remove('in-hand'); });
    row.classList.add('in-hand');
    $('d-title').textContent = row.querySelector('.alias').textContent;
    $('d-errors').textContent = $('d-profile').textContent = '';
    $('d-routes').textContent = 'Asking…';
    d.setAttribute('open', '');
    if (keyboard) $('d-title').focus();
    get('/api/discover-model?provider=' + encodeURIComponent(provider) + '&model=' + encodeURIComponent(model))
      .then(function (res) {
        if (mine !== dAsked || !d.open) return;                 // another row asked since, or put away
        var list = $('d-routes');
        list.textContent = '';
        if (!res.ok) { listErrors($('d-errors'), res.errors); return; }
        if (res.errors.length) listErrors($('d-errors'), res.errors);   // a hub that could not be asked
        var r = res.model;
        $('d-title').textContent = r.name;
        // Each source a link to its page for the model: FreeLLMAPI's where its sitemap has one, models.dev's by its
        // canonical id. The provider's own list has no page, only its connection.
        var from = el('span');
        r.sources.forEach(function (s, i) {
          if (i) from.appendChild(document.createTextNode(' and '));
          if (s === 'provider') { from.appendChild(document.createTextNode(r.provider_name + '\'s own list (' + r.connection + ' connection)')); return; }
          var fl = s === 'freellmapi';
          from.appendChild(el('a', {href: fl ? r.page || 'https://freellmapi.co/models' : r.md_page || 'https://models.dev/',
                                    target: '_blank', rel: 'noopener'}, fl ? 'FreeLLMAPI' : 'models.dev'));
        });
        var can = [['tools', r.tools], ['vision', r.vision], ['reasons', r.reasoning]]
          .filter(function (x) { return x[1]; }).map(function (x) { return x[0]; });
        var facts = [['Model id', el('span', {class: 'mono'}, r.model)], ['Provider', r.provider_name], ['Listed by', from],
          ['On the bench as', r.on_bench_as], ['Also on it as', (r.also_on || []).join(', ')],
          ['Context window', r.context ? Number(r.context).toLocaleString() + ' tokens' : 'unknown'],
          ['Max output', r.output ? Number(r.output).toLocaleString() + ' tokens' : ''],
          ['Takes in', (r.in_mods || []).join(', ') || 'unknown'], ['Gives out', (r.out_mods || []).join(', ') || 'unknown'],
          ['Can', can.join(', ') || 'nothing known'], ['Price', price(r)], ['Limits', limits(r.limits)],
          ['A month', r.budget ? r.budget + ' tokens' : ''],
          ['On FreeLLMAPI', r.rank != null ? 'smartest #' + r.rank + (r.speed ? ', fastest #' + r.speed : '') + (r.size ? ', ' + r.size : '') : ''],
          ['Released', r.released], ['Knowledge to', r.knowledge],
          ['Weights', r.open_weights == null ? '' : r.open_weights ? 'open' : 'closed'],
          ['First seen', r.first_seen === 'baseline' ? 'before the list began' : r.first_seen]]
          // FreeLLMAPI's operating notes for this provider, e.g. thinking tokens that eat the output cap.
          .concat((r.quirks || []).map(function (q) { return [q.title, q.body || '']; }));
        facts.forEach(function (f) {
          if (!f[1]) return;
          $('d-profile').appendChild(el('dt', {}, f[0]));
          $('d-profile').appendChild(el('dd')).append(f[1]);           // words, or a node (Listed by's links)
        });
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
      }).catch(function (err) { if (mine === dAsked) { $('d-routes').textContent = ''; $('d-errors').textContent = String(err); } });
  }
  document.addEventListener('click', function (e) {
    var row = e.target.closest('tr.pick-row');           // the whole row: its name, its Add…, anywhere
    if (!row) return;
    e.preventDefault(); openDiscover(row, !e.detail);    // from a keyboard: the focus to it
  });
  on('click', 'd-close', function () { dd().close(); });
  // Put away (Close, Escape, an Add): its row unmarked, and the focus back to it from the panel.
  $('tab-discover').addEventListener('close', function (ev) {      // caught on its way down: close does not bubble
    if (ev.target.id !== 'discover-dialog') return;
    var row = $('d-table').querySelector('tr.in-hand'), at = document.activeElement;
    dHand = '';
    if (!row) return;
    row.classList.remove('in-hand');
    if (ev.target.contains(at) || at === document.body) row.querySelector('a').focus();
  }, true);
  // Refresh lists (in the bar): every list fetched now (serve.discover_refresh), then the page with them.
  on('click', 'd-refresh', function () {
    var dr = this, said = function (text) { if ($('d-refreshed')) $('d-refreshed').textContent = text; };
    dr.disabled = true; dr.textContent = 'Fetching…'; said('fetching, about half a minute');
    function idle() { dr.disabled = false; dr.textContent = 'Refresh lists'; }
    post('/api/discover/refresh', {}).then(function (res) {
      // Fetched now even if you went to another page meanwhile, so its errors are there when you come back.
      refreshPanes(['discover']); load('discover').then(function () {          // any errors beside the new page's list age
        idle();
        if (res.errors.length) said(res.errors.join(' · ') + ' · ' + $('d-refreshed').textContent);
      });
    }).catch(function (err) { said(String(err)); idle(); });
  });
  function follow(log, title, before) {
    runView(title, before + 'Running in the background; closing this does not stop it. Log: runs/' + log);
    timer = setInterval(function () {
      get('/api/onboard?log=' + encodeURIComponent(log)).then(function (s) {
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
            closeAndRefresh(od);
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
  // Settings' cards and the rail's switch are the same buttons: each shows what the other picked.
  function theme(name) {
    document.documentElement.setAttribute('data-theme', name);
    document.querySelectorAll('[data-theme-pick]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.themePick === name)); });
    drawCharts();
    post('/api/theme', {theme: name}).then(function (r) {
      if (r.ok && location.search.indexOf('theme=') !== -1) history.replaceState(null, '', location.pathname + location.hash);   // drop a ?theme preview
    });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-theme-pick]');
    if (b) theme(b.dataset.themePick);
  });

  // ---- gateway keys: shown once, then only their names ---------------------
  on('click', 'k-make', function () {
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
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-revoke-key]');
    if (!b) return;
    var name = b.dataset.revokeKey;
    ask('Revoke the gateway key ' + name + '? Anything using it is refused from now on.', 'Revoke', true).then(function (yes) {
      if (!yes) return;
      post('/api/gateway-key/remove', {name: name}).then(function (r) {
        if (!r.ok) { listErrors($('k-errors'), r.errors); return; }
        var body = b.closest('tbody');
        b.closest('tr').remove(); ownChange();
        if (!body.rows.length) body.innerHTML = '<tr><td colspan=6 class=muted>No keys yet.</td></tr>';   // as serve.gateway_keys_card
      });
    });
  });

  // ---- Connect (serve.clients_page): an app's choices remake its set-up
  // (/api/client-setup); "Does it work?" asks for its newest call
  // (/api/client-seen) every few seconds while its set-up is shown. A key made
  // here fills the blocks marked data-key (shell lines, fields typed into an
  // app; never a file that may sync). Held in memory only: a reload forgets it,
  // as Settings' does.
  var appKeys = {}, seenTimer = null;
  function appChoice(sec, c) { var x = sec.querySelector('[data-c="' + c + '"]'); return x ? x.value : ''; }
  function setupAgain(sec) {
    var app = sec.dataset.app, way = sec.querySelector('.ways input:checked');
    var q = new URLSearchParams({app: app, way: way ? way.value : '', collection: appChoice(sec, 'collection'),
                                 where: appChoice(sec, 'where'), name: appChoice(sec, 'name'),
                                 keyed: appKeys[app] || appChoice(sec, 'key') === '1' ? '1' : ''});
    sec.querySelector('.errors').textContent = '';
    return get('/api/client-setup?' + q).then(function (r) {
      if (!r.ok) { listErrors(sec.querySelector('.errors'), r.errors); return; }
      sec.querySelector('.setup').innerHTML = r.html;
      if (appKeys[app]) sec.querySelectorAll('.setup pre[data-key]').forEach(function (p) {
        p.textContent = p.textContent.split('<your gateway key>').join(appKeys[app]);   // serve.KEY_HOLDER
      });
    });
  }
  function seenTick() {
    clearTimeout(seenTimer);
    var sec = document.querySelector('#tab-clients.active section.client[data-app]:not([hidden])');
    var asked = sec && !document.hidden ? get('/api/client-seen?name=' + encodeURIComponent(appChoice(sec, 'name')))
      .then(function (r) {
        var name = appChoice(sec, 'name'), list = sec.querySelector('ul.seen'), li = el('li', r.seen ? {} : {class: 'warn'});
        li.appendChild(el('span', {'aria-hidden': 'true'}, r.seen ? '✓' : '…'));
        li.appendChild(el('span', {}, r.seen
          ? name + ' called ' + r.model + (r.by ? ', answered by ' + r.by : '') + (r.tools ? ', ' + plural(r.tools, 'tool call') : '')
          : 'No call from ' + name + ' yet. Start it with its new set-up and ask it anything: this updates by itself.'));
        li.appendChild(el('span', {}, r.when || ''));
        list.textContent = ''; list.appendChild(li);
      }) : Promise.resolve();
    asked.catch(function () {}).then(function () { seenTimer = setTimeout(seenTick, 5000); });
  }
  onLoad.clients = function () { pickAgain('clients'); seenTick(); };
  document.addEventListener('click', function (e) { if (e.target.closest('#tab-clients .split > nav')) seenTick(); });
  document.addEventListener('change', function (e) {
    var sec = e.target.closest('#tab-clients section.client[data-app]');
    if (!sec || !e.target.closest('.connect')) return;
    if (e.target.dataset.c === 'rpm') return;
    if (e.target.dataset.c === 'key') { sec.querySelector('.key-make').hidden = e.target.value !== 'make'; return; }
    setupAgain(sec).then(e.target.dataset.c === 'name' ? seenTick : null);
  });
  document.addEventListener('click', function (e) {
    var b = e.target.closest('#tab-clients [data-make-key]');
    if (!b) return;
    var sec = b.closest('section.client'), app = sec.dataset.app, name = appChoice(sec, 'name'), col = appChoice(sec, 'collection');
    sec.querySelector('.errors').textContent = '';
    b.disabled = true;
    post('/api/gateway-key', {name: name, collections: [col], rpm: appChoice(sec, 'rpm')}).then(function (r) {
      if (!r.ok) { listErrors(sec.querySelector('.errors'), r.errors); b.disabled = false; return; }
      appKeys[app] = r.key;
      var sel = sec.querySelector('[data-c="key"]');
      sel.textContent = ''; sel.appendChild(el('option', {value: '1'}, name + "'s key")); sel.disabled = true;
      sec.querySelector('.key-make').hidden = true;
      var done = sec.querySelector('.key-done');
      done.textContent = 'Key made for ' + name + ', for ' + col + ' only' + (appChoice(sec, 'rpm') ? ', ' + appChoice(sec, 'rpm') + ' a minute' : '')
        + '. It is in the lines below that take it: copy them now, as it is shown this once. Settings lists it and can revoke it.';
      done.hidden = false;
      setupAgain(sec); refreshPanes(['settings']);
    }).catch(function (err) { listErrors(sec.querySelector('.errors'), [String(err)]); b.disabled = false; });
  });
  // Copy: the clipboard where the page may write it (https, localhost), else the
  // old way, else the text left selected for the reader's own Ctrl+C.
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-copy]');
    if (!b) return;
    var pre = $(b.dataset.copy);
    (window.isSecureContext && navigator.clipboard ? navigator.clipboard.writeText(pre.textContent) : Promise.reject())
      .catch(function () {
        var sel = getSelection();
        sel.selectAllChildren(pre);
        if (!document.execCommand('copy')) throw new Error('no copy');
        sel.removeAllRanges();
      })
      .then(function () { doneOn(b, 'Copied'); }, function () {
        b.textContent = 'Selected: press Ctrl+C'; setTimeout(function () { b.textContent = 'Copy'; }, 2500);
      });
  });

  // ---- scheduler: its mode and jobs (serve.save_scheduler) -----------------
  function modeWhy() { $('s-mode-why').textContent = $('s-mode').selectedOptions[0].dataset.why; }
  on('change', 's-mode', modeWhy); onLoad.settings = modeWhy;
  on('click', 's-save', function () {
    var sMode = $('s-mode'), jobs = {};
    document.querySelectorAll('input[name=s-job]').forEach(function (c) { jobs[c.value] = c.checked; });
    (sMode.value === 'run' && sMode.dataset.was !== 'run'
      ? ask('Run the jobs for real? They make model calls on their own schedule. If something else'
            + ' also runs them (cron, Windows Task Scheduler), turn it off now, or every job runs twice.', 'Run them')
      : Promise.resolve(true)).then(function (yes) {
      if (!yes) return;
      $('s-errors').textContent = ''; $('s-done').textContent = '';
      post('/api/scheduler', {mode: sMode.value, jobs: jobs}).then(function (r) {
        if (!r.ok) { listErrors($('s-errors'), r.errors); return; }
        sMode.dataset.was = sMode.value; $('s-done').textContent = 'Saved. It applies within a minute.';
      });
    });
  });

  // ---- model client (serve.save_client): the next call uses it -------------
  on('click', 'mc-save', function () {
    $('mc-errors').textContent = ''; $('mc-done').textContent = 'Saving…';   // llm: its registry synced too, seconds
    post('/api/client', {client: $('mc-client').value}).then(function (r) {
      if (!r.ok) { $('mc-done').textContent = ''; listErrors($('mc-errors'), r.errors); return; }
      $('mc-done').textContent = 'Saved' + (r.sync ? ' · ' + r.sync : '') + '. The next model call uses it.';
    });
  });

  // ---- free only in their own app (serve.save_own_client_free) -------------
  on('click', 'oc-save', function () {
    var providers = {}, add = $('oc-add').value.trim().toLowerCase();
    document.querySelectorAll('input[name=oc-prov]').forEach(function (c) { providers[c.value] = c.checked; });
    if (add) providers[add] = true;
    $('oc-errors').textContent = ''; $('oc-done').textContent = '';
    post('/api/own-client-free', {providers: providers}).then(function (r) {
      if (!r.ok) { listErrors($('oc-errors'), r.errors); return; }
      // A new provider gets its row here; Discover's prices follow it when next opened.
      refreshPanes(add ? ['settings', 'discover'] : ['discover']).then(function () {
        $('oc-done').textContent = 'Saved. Discover uses it on its next load.';
      });
    });
  });

  // ---- endpoints: keys go in, never come back out -------------------------
  // The Endpoints page lists them (serve.endpoints_page, each row's data in
  // data-ep); one opens docked beside the list, its key changed there; Add an
  // endpoint is a blank one, docked the same way. The pages are
  // drawn by the server, so a change fetches them again (refreshPanes): an
  // endpoint's models are on most of them.
  var ed = $('endpoint-dialog');
  var e = {name: $('e-name'), base: $('e-base'), key: $('e-key'),
           kind: $('e-kind'), hub: $('e-hub'), admin: $('e-admin'),
           errors: $('e-errors'), done: $('e-done'), save: $('e-save'), changed: false};
  e.kind.addEventListener('change', function () {
    $('e-hub-row').hidden = $('e-admin-row').hidden = e.kind.value !== 'hub';
  });
  ed.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', function () { ed.close(); }); });
  ed.addEventListener('close', function () { if (e.changed) { e.changed = false; again(epOpen); } });
  document.addEventListener('keydown', function (ev) {      // Add is docked beside any page: Escape puts it away
    if (ev.key === 'Escape' && ed.open && !document.querySelector('dialog:modal')) ed.close();
  });
  // Add's Provider box: models.dev's providers (serve.endpoint_providers), once a page load.
  // One picked fills the name and base URL; the key is what is left to paste.
  var provs = null;
  function loadProviderList() {
    if (provs) return;
    provs = {};
    get('/api/endpoint/providers').then(function (r) {
      var list = $('e-provs');
      r.providers.forEach(function (p) {
        provs[p.name] = p;
        list.appendChild(el('option', {value: p.name}, p.have ? 'already added as ' + p.have : p.base));
      });
    });
  }
  $('e-prov').addEventListener('input', function () {
    var p = provs && provs[this.value], said = $('e-prov-said');
    if (!p) return;
    e.name.value = p.id; e.base.value = p.base; e.kind.value = 'provider';
    said.textContent = p.have ? 'Already added as ' + p.have + '; another account needs its own name.' : 'Its base URL is filled in: paste its key.';
    if (p.doc) { said.appendChild(document.createTextNode(' ')); said.appendChild(el('a', {href: p.doc, target: '_blank', rel: 'noopener'}, 'Its docs')); }
    e.key.focus();
  });
  // An endpoint opens docked beside the list, as a collection does: its row marked,
  // Escape or Close puts it away and the focus goes back to its row.
  var epOpen = '';
  function epRow(name) { return document.querySelector('#tab-endpoints tr.ep-row[data-name="' + CSS.escape(name) + '"]'); }
  function openEp(name, keyboard) {
    var d = document.querySelector('#tab-endpoints dialog.dock[data-name="' + CSS.escape(name) + '"]');
    if (!d) return;
    document.querySelectorAll('#tab-endpoints dialog.dock[open]').forEach(function (o) { if (o !== d) o.close(); });
    if (ed.open) ed.close();
    epOpen = name;
    d.setAttribute('open', '');                       // docked, not a popup: the list stays live
    if (epRow(name)) epRow(name).classList.add('in-hand');
    if (keyboard) d.querySelector('.dock-head').focus();
  }
  $('tab-endpoints').addEventListener('close', function (ev) {      // caught on its way down: close does not bubble
    var d = ev.target, row = d.matches('dialog.dock') && epRow(d.dataset.name), at = document.activeElement;
    if (!row) return;
    row.classList.remove('in-hand');
    if (d.contains(at) || at === document.body) row.querySelector('button').focus();
    if (epOpen === d.dataset.name) epOpen = '';
  }, true);
  // After a change: the pages fetched again (refreshPanes). The endpoint in hand is put
  // away first, since a page holding an open record waits for it (load), and opened
  // again once its page is new, if it is still there.
  function again(name) {
    var d = document.querySelector('#tab-endpoints dialog.dock[open]');
    if (d) d.close();
    return refreshPanes().then(function () {
      if (name && shown === 'endpoints') openEp(name, document.activeElement === document.body);   // the focus, lost with the old rows
    });
  }
  function refreshSaying(message, keep) {
    $('el-errors').textContent = ''; $('el-done').textContent = message;
    again(keep ? epOpen : '').then(function () { $('el-done').textContent = message; });     // on the new page too
  }
  // A record's own messages, in its foot.
  function epSaid(name, ok, text) {
    var d = document.querySelector('#tab-endpoints dialog.dock[data-name="' + CSS.escape(name) + '"]');
    if (!d) return;
    d.querySelector('.dock-foot .errors').textContent = ok ? '' : text;
    d.querySelector('.dock-foot .done').textContent = ok ? text : '';
  }
  function checkSays(r) {
    return r.state === 'refused' || r.state === 'down' ? r.said
      : plural(r.models, 'model') + ' listed. ' + (r.said || (r.state === 'none' ? 'No key is kept for it.' : ''));
  }
  var rowActs = {
    // Check now (serve.check_endpoint): its list with its key, then without; kept for the page.
    check: function (x, b) {
      b.disabled = true; b.textContent = 'Checking…';
      post('/api/endpoint/check', {name: x.name}).then(function (r) {
        if (!r.ok) { epSaid(x.name, false, r.errors.join(' ')); b.disabled = false; b.textContent = 'Check now'; return; }
        var ok = r.state !== 'refused' && r.state !== 'down';
        again(x.name).then(function () { epSaid(x.name, ok, 'Checked: ' + checkSays(r)); });
      });
    },
    // A new key in the record: checked against its list before it is kept (serve.save_endpoint).
    key: function (x, b) {
      var d = b.closest('dialog'), key = d.querySelector('[data-k=key]'), admin = d.querySelector('[data-k=admin]');
      if (!key.value.trim() && !(admin && admin.value.trim())) { epSaid(x.name, false, 'Paste the new key first.'); return; }
      b.disabled = true; b.textContent = 'Checking…';
      post('/api/endpoint', {name: x.name, base: x.base, key: key.value, admin: admin ? admin.value : ''}).then(function (r) {
        b.disabled = false; b.textContent = 'Check and save';
        if (!r.ok) { epSaid(x.name, false, r.errors.join(' ')); return; }
        post('/api/endpoint/check', {name: x.name}).then(function () {
          again(x.name).then(function () { epSaid(x.name, true, 'Saved: ' + plural(r.served, 'model') + ' listed. ' + (r.note || '')); });
        });
      }).catch(function (err) { b.disabled = false; b.textContent = 'Check and save'; epSaid(x.name, false, String(err)); });
    },
    again: function (x) {          // this endpoint, not a new one
      openEndpoint('again', x);
      connectStart({id: x.connect.provider, flow: x.connect.flow, label: x.connect.label}, x.name);
    },
    'default': function (x) {
      ask('Make ' + x.name + ' the default endpoint? The default is where a model is taken'
          + ' to be when nothing says otherwise, and the one endpoint that cannot be removed.', 'Make default').then(function (yes) {
        if (!yes) return;
        post('/api/endpoint/default', {name: x.name}).then(function (r) {
          if (!r.ok) { listErrors($('el-errors'), r.errors); return; }
          refreshSaying(r.note + '.', true);
        });
      });
    },
    // Its models go with it, after saying how many: each is retired as by
    // hand (evidence kept, restorable once the endpoint is back).
    remove: function (x) {
      var n = x.models, them = plural(n, 'model');
      ask(n ? 'Remove the ' + x.name + ' endpoint and the ' + them + ' on the bench through it?\n\n'
              + 'The ' + them + ' will be retired too. Their results and history are kept, and'
              + ' Restore brings them back if an endpoint named ' + x.name + ' is added again.'
            : 'Remove the ' + x.name + ' endpoint?', 'Remove', true).then(function (yes) {
        if (!yes) return;
        post('/api/endpoint/remove', {name: x.name, with_models: n > 0}).then(function (r) {
          if (!r.ok) { listErrors($('el-errors'), r.errors); return; }
          refreshSaying('Removed ' + x.name + '; ' + r.note + '.');
        });
      });
    }
  };
  $('tab-endpoints').addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-ep] button[data-ep-act]'), row = ev.target.closest('tr.ep-row');
    if (b) rowActs[b.dataset.epAct](JSON.parse(b.closest('[data-ep]').dataset.ep), b);
    else if (ev.target.closest('[data-dock-close]')) ev.target.closest('dialog').close();
    else if (row && !ev.target.closest('a')) openEp(row.dataset.name, !ev.detail);   // from a keyboard: the focus to it
  });
  // Rate limits per connection: a row each, its name and models, a box per limit, its Save.
  function loadLimits() {
    var list = $('l-list');
    get('/api/connections').then(function (res) {
      list.textContent = '';
      if (!res.connections.length) { list.appendChild(el('p', {class: 'muted'}, 'None yet: a connection is listed once a model on the bench comes from it.')); return; }
      var table = el('table', {class: 'l-table'}), head = el('tr'), body = el('tbody');
      head.appendChild(el('th', {}, 'Connection'));
      res.keys.forEach(function (k) { head.appendChild(el('th', {class: 'num', title: (res.labels || {})[k] || k}, k)); });
      head.appendChild(el('th', {}, ''));
      table.appendChild(el('thead')).appendChild(head); table.appendChild(body); list.appendChild(table);
      res.connections.forEach(function (c) {
        var row = el('tr'), left = el('td');
        left.appendChild(el('div', {class: 'alias'}, c.name));
        left.appendChild(el('div', {class: 'conn'}, c.models.join(', ')));
        row.appendChild(left);
        var inputs = [];
        res.keys.forEach(function (k) {
          var box = el('input', {type: 'number', min: '1', 'data-k': k, placeholder: '—',
                                 'aria-label': k + ' for ' + c.name, title: (res.labels || {})[k] || k});
          if (c.limits[k]) box.value = c.limits[k];
          inputs.push(box); row.appendChild(el('td', {class: 'num'})).appendChild(box);
        });
        // What the bench worked out from this connection's 429s (learn.py):
        // kept to where no value of yours is set, until Forget or 14 days.
        res.keys.forEach(function (k) {
          var ev = (c.learned || {})[k], once = (c.pending || {})[k];
          if (!ev && !once) return;
          var on = function (iso) { return new Date(iso).toLocaleDateString(undefined, {day: 'numeric', month: 'short'}); };
          var note = el('div', {class: 'conn'}, ev
            ? 'learned ' + ev.value + ' ' + k + ' · 429s on ' + on(ev.at)
              + (ev.confirmed_at && on(ev.confirmed_at) !== on(ev.at) ? ', last on ' + on(ev.confirmed_at) : '')
              + ' · ' + (c.limits[k] ? 'not enforced: yours wins' : 'enforced (yours wins)') + ' '
            : 'met once: ' + once.n + ' ' + k + ' · a 429 on ' + on(once.at) + ' · not enforced unless met again ');
          var forget = el('button', {class: 'btn small', type: 'button',
                                     'aria-label': 'Forget the learned ' + k + ' for ' + c.name}, 'Forget');
          forget.addEventListener('click', function () {
            $('l-errors').textContent = ''; $('l-done').textContent = '';
            post('/api/connection-learned/forget', {connection: c.name, axis: k}).then(function (r) {
              if (!r.ok) { listErrors($('l-errors'), r.errors); return; }
              $('l-done').textContent = 'Forgot the learned ' + k + ' for ' + c.name + '.';
              loadLimits();
            });
          });
          note.appendChild(forget); left.appendChild(note);
        });
        var save = el('button', {class: 'btn small', type: 'button', 'aria-label': 'Save the limits for ' + c.name}, 'Save');
        save.addEventListener('click', function () {
          var limits = {};
          inputs.forEach(function (b) { if (b.value) limits[b.dataset.k] = parseInt(b.value, 10); });
          $('l-errors').textContent = ''; $('l-done').textContent = '';
          post('/api/connection-limits', {connection: c.name, limits: limits}).then(function (r) {
            if (!r.ok) { listErrors($('l-errors'), r.errors); return; }
            $('l-done').textContent = 'Saved limits for ' + c.name + '.';
          });
        });
        row.appendChild(el('td', {class: 'acts'})).appendChild(save);
        body.appendChild(row);
      });
      applyFilter($('tab-endpoints'));                 // the page's filter, on rows that came after it
    });
  }
  onShow.endpoints = function () {             // its own-app and rate-limit sections, and what runs here
    $('cn-app-errors').textContent = $('cn-app-done').textContent = '';
    loadProviders(); loadLimits(); loadFound();
  };
  // Model servers running on this machine that are not endpoints yet
  // (serve.connect_local): asked each time the page is shown, since a look
  // takes a second or two; nothing shows when there is none.
  function loadFound() {
    get('/api/connect/local').then(function (res) {
      var list = $('e-found-list');
      if (!list) return;
      list.textContent = '';
      (res.found || []).forEach(function (f) {
        var row = el('div', {class: 'endpoint'}), left = el('div'), acts = el('div', {class: 'acts'});
        left.appendChild(el('div', {class: 'what'}, f.label + ' on :' + f.port));
        left.appendChild(el('div', {class: 'base'}, (f.key ? 'lists its models only with a key'
          : plural(f.models, 'model')) + ' · ' + f.base));
        var add = el('button', {class: 'btn small', type: 'button', 'aria-label': 'Add ' + f.label + ' on :' + f.port}, 'Add');
        add.addEventListener('click', function () { addFound(f); });
        acts.appendChild(add); row.appendChild(left); row.appendChild(acts);
        list.appendChild(row);
      });
      $('e-found').hidden = !list.children.length;
    }).catch(function () {});
  }
  // The Add dialog, filled in: the key form (Test, then Save, as ever), or
  // for Ollama its own choice, which gives it the stand-in key llm needs.
  function addFound(f) {
    var loaded = openEndpoint('add');
    if (f.provider) {
      loaded.then(function () {
        var b = cn.list.querySelector('[data-choice="' + f.provider + '"]');
        if (b && !b.disabled) b.click();
      });
      return;
    }
    e.name.value = f.name; e.base.value = f.base; e.kind.value = f.kind;
    if (f.hub) e.hub.value = f.hub;
    $('e-hub-row').hidden = $('e-admin-row').hidden = f.kind !== 'hub';
    e.done.textContent = f.label + ' answers here' + (f.key ? '; it wants a key: paste one, then Check and add.' : ': Check and add.');
    (f.key ? e.key : e.save).focus();
  }
  // The docked Add, in one of two modes. add: the choices, API key first (a
  // provider from models.dev fills it), then each provider's sign-in; again: a
  // sign-in under way for an endpoint that has one. A key changes in its record.
  function openEndpoint(mode, x) {
    cn.run++;
    e.adding = mode === 'add';     // Add refuses a name in use
    $('e-title').textContent = mode === 'again' ? 'Sign in to ' + x.connect.label + ' again' : 'Add an endpoint';
    $('e-blurb').hidden = cn.list.hidden = $('e-kinds').hidden = $('e-prov-row').hidden = !e.adding;
    e.key.placeholder = 'blank if it needs none, as a local runtime';
    cn.wait.hidden = true; $('e-after').hidden = true;
    resetKeyForm();
    choose(mode === 'again' ? null : 'key');
    var loaded = e.adding ? loadProviders() : Promise.resolve();
    if (e.adding) loadProviderList();
    // Docked, as an endpoint's own record: that one is put away, the page stays live.
    document.querySelectorAll('.tab.active dialog.dock[open]').forEach(function (o) { o.close(); });
    ed.setAttribute('open', '');
    (e.adding ? $('e-prov') : $('e-title')).focus();
    return loaded;                   // the choices, once listed
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
    e.name.value = e.base.value = e.key.value = e.admin.value = $('e-prov').value = ''; e.name.readOnly = false;
    e.kind.value = 'provider'; e.hub.selectedIndex = 0; $('e-hub-row').hidden = $('e-admin-row').hidden = true;
    $('e-prov-said').textContent = "From models.dev's list of providers with an OpenAI-compatible API.";
  }
  $('e-form').addEventListener('input', function () { e.done.textContent = ''; });
  function keyBody() {
    var body = {name: e.name.value, base: e.base.value, key: e.key.value, new: e.adding,
                admin: $('e-admin-row').hidden ? '' : e.admin.value};
    if (e.adding) {                     // Change key keeps its kind and API
      body.kind = e.kind.value; body.hub = e.kind.value === 'hub' ? e.hub.value : ''; body.api = $('e-api').value;
    }
    return body;
  }
  on('click', 'e-add', function () { openEndpoint('add'); });
  // Discover's "easy to add": Add, docked beside the catalog, its provider's name and base URL filled in.
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('button.d-addep');
    if (!b) return;
    ev.stopPropagation(); ev.preventDefault();
    var id = b.dataset.provider, name = dData.providers[id] || id;
    openEndpoint('add');
    e.name.value = id; e.base.value = dData.apis[id]; $('e-prov').value = name;
    $('e-prov-said').textContent = name + "'s base URL is filled in from models.dev: paste its key.";
    e.key.focus();
  }, true);
  // Check all: each endpoint in turn (one at a time: a hub fronts many), then the page again.
  on('click', 'e-check-all', function () {
    var b = this, names = Array.prototype.map.call(document.querySelectorAll('#tab-endpoints tr.ep-row'),
                                                   function (r) { return r.dataset.name; }), bad = 0;
    b.disabled = true; $('el-errors').textContent = '';
    names.reduce(function (p, name, i) {
      return p.then(function () {
        $('el-done').textContent = 'Checking ' + (i + 1) + ' of ' + names.length + ': ' + name + '…';
        return post('/api/endpoint/check', {name: name}).then(function (r) { if (!r.ok || r.state === 'refused' || r.state === 'down') bad++; });
      });
    }, Promise.resolve()).then(function () {
      b.disabled = false;
      refreshSaying('Checked ' + plural(names.length, 'endpoint') + (bad ? ': ' + bad + ' with a problem, first in the list.' : ': all answer.'));
    });
  });
  // Check and add: the server asks its list first and keeps nothing unless it answers.
  e.save.addEventListener('click', function () {
    e.errors.textContent = ''; e.done.textContent = ''; e.save.disabled = true; e.save.textContent = 'Checking…';
    function end() { e.save.textContent = 'Check and add'; e.save.disabled = false; }
    post('/api/endpoint', keyBody()).then(function (r) {
      end();
      if (!r.ok) { listErrors(e.errors, r.errors); return; }
      var name = e.name.value.trim().toLowerCase();
      post('/api/endpoint/check', {name: name});      // its first check, for the list's Key and Checked
      endpointDone('Added. ' + name + ' lists ' + r.served + ' models; they appear in the onboarding picker. '
               + (r.note || ''), e.adding ? name : '');
    }).catch(function (err) { end(); e.errors.textContent = String(err); });
  });
  // Saved or signed in: the forms fold, the message stays, with the next step.
  function endpointDone(message, endpoint) {
    resetKeyForm(); choose(null);
    cn.list.hidden = $('e-blurb').hidden = cn.wait.hidden = true; $('e-after').hidden = false;
    e.done.textContent = message;
    if (endpoint) e.done.appendChild(onboardFrom(endpoint));
    e.changed = true;              // the pages are fetched again when the dialog closes
  }

  // ---- connect: sign in on the provider's page (metrics/connect.py) --------
  // `run` counts the dialog's openings: a sign-in still finishing after the
  // dialog was closed or opened again reports on the Endpoints page instead,
  // and fetches the pages again once nothing is open over it.
  var cn = {list: $('cn-list'), wait: $('cn-wait'), msg: $('cn-msg'), timer: null, run: 0};
  // Reached from another machine (a hosted install): a callback to this
  // machine's 127.0.0.1 could not arrive, so sign-ins go by code instead.
  var remote = !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  function connectDone(r, run) {
    clearInterval(cn.timer); cn.timer = null;
    var said = r.ok && r.label + ' is connected as ' + r.endpoint + ': it lists ' + r.served + ' models. ' + (r.note || '');
    if (!ed.open || run !== cn.run) {
      if (!r.ok) listErrors($('el-errors'), r.errors);
      else if (ed.open) { $('el-done').textContent = said; e.changed = true; }
      else refreshSaying(said);
      return;
    }
    cn.wait.hidden = true;
    if (!r.ok) {                   // back to the provider's form to try again, or just Close
      $('cn-form').hidden = !cn.pick; $('e-after').hidden = !!cn.pick;
      listErrors(e.errors, r.errors); return;
    }
    endpointDone(said, r.endpoint);
  }
  // The next step for a new endpoint, signed in or pasted: onboard what it serves.
  function onboardFrom(endpoint) {
    var go = el('button', {class: 'btn small', type: 'button', style: 'margin-left:.6rem'}, 'Onboard a model from it');
    go.addEventListener('click', function () {
      // No reload on the way: onboarding opens over the page, which follows
      // the files once it closes (live).
      e.changed = false; ed.close(); $('onboard-open').click(); o.find.value = endpoint;   // the picker filters on it once loaded
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
    return get('/api/connect').then(function (res) {
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

  // ---- first run: no endpoint yet, no model, no collection (serve.first_run, in
  // Benchmarks' place; an empty page's button is the same step, serve.step_button).
  // A step done (an endpoint added, a model onboarded, a collection saved) moves it on
  // at once, its tick drawn: refreshNeeds hands its next step, or "" when the install is
  // set up, when Benchmarks is fetched again for its reports.
  function showFirst(html) {
    var now = $('first-run');
    if ((now ? now.outerHTML : '') === (html || '')) return;
    if (now && html) { var was = now.querySelectorAll('li.done').length; now.outerHTML = html; ticks($('first-run'), was); return; }
    $('tab-suite').setAttribute('data-lazy', ++marks);
    if (shown === 'suite') load('suite');
  }
  // Each step waits for its page: it may not have arrived yet.
  function firstStep(step) {
    if (step === 'onboard') { $('onboard-open').click(); return; }
    // The first collection comes filled in as bench/core, the name the docs, CLIENTS.md and
    // the Clients page use: up to four of the models just onboarded ticked (two to four is
    // what a panel wants), never an image generator. Nothing is made until it is saved.
    if (step === 'collection') {
      go('collections').then(function () {
        var members = C.models.filter(function (a) { return fact(a).role !== 'image'; }).slice(0, 4);
        fillForm(null, {name: 'core', description: 'General second opinions: any question, several models at once.',
                        tags: ['general', 'second-opinion'], members: members});
        c.name.focus();
      });
      return;
    }
    go('endpoints').then(function () { openEndpoint('add'); });
  }
  document.addEventListener('click', function (e) {       // on the document: a new step brings a new button
    var b = e.target.closest('[data-first]');
    if (b) firstStep(b.dataset.first);
  });

  // ---- collections: what the bench gateway runs together (its own tab) -----
  // Its elements, the popup's too, are in the page: found again each time it arrives.
  var c = {};
  onLoad.collections = function () {
    c = {list: $('c-list'), name: $('c-name'), members: $('c-members'), count: $('c-count'),
         shape: $('c-shape'), desc: $('c-desc'), tags: $('c-tags'), errors: $('c-errors'), done: $('c-done'),
         head: $('c-form-head'), maxCalls: $('g-max-calls'), maxExp: $('g-max-explorers'),
         pick: $('g-pick'), pickConf: $('g-pick-conf'),
         gErrors: $('g-errors'), gDone: $('g-done'), form: $('c-form'), status: $('c-status'),
         del: $('c-delete'), reply: $('c-reply'), merger: $('c-merger'), find: $('c-member-filter'),
         seats: $('c-seats'), prov: $('c-member-provider'), show: $('c-member-show'), adHoc: $('c-ad-hoc'),
         ownCalls: $('c-max-calls'), ownExp: $('c-max-explorers')};
    return loadCollections();                  // the list came with the page: the editor's data only
  };
  var C = null, open = null;         // the collection whose details are showing
  // What the dialog's two limits come to: empty follows the gateway settings.
  function limit(input, fallback) { return input.value === '' ? fallback : parseInt(input.value, 10); }
  function maxCalls() { return limit(c.ownCalls, C.settings.max_calls); }
  function explorersFor(n) {
    return Math.max(0, Math.min(limit(c.ownExp, C.settings.max_explorers), maxCalls() - n));
  }
  function picked() {
    return Array.prototype.filter.call(c.members.querySelectorAll('input'), function (x) { return x.checked; })
      .map(function (x) { return x.value; });
  }
  function recount() {
    var n = picked().length, ex = explorersFor(n);
    var rows = c.members.querySelectorAll('label.check'), shown = c.members.querySelectorAll('label.check:not([hidden])');
    var greyed = c.members.querySelectorAll('label.check.blocked:not(.clash):not([hidden])').length;
    var clash = c.members.querySelectorAll('label.check.clash').length;
    var paid = c.members.querySelectorAll('label.check[data-paid="1"][hidden]').length;
    c.count.textContent = plural(n, 'member') + ' + ' + plural(ex, 'explorer') +
      ' = ' + (n + ex) + ' calls per request' + (n && (n < 2 || n > 4) ? ' · two to four members is recommended' : '') +
      (shown.length < rows.length ? ' · showing ' + shown.length + ' of ' + rows.length + ' models' : '') +
      (paid ? ' (' + paid + ' paid hidden by the free tag)' : '') +
      (greyed ? ' · ' + greyed + ' greyed out by the tags (hover one for why)' : '') +
      (clash ? ' · ' + clash + ' ticked member' + (clash === 1 ? ' does' : 's do') + ' not fit the tags' : '') +
      twice().map(function (t) {
        return ' · ' + t.name + ' is ticked on ' + t.on.join(' and ') + ': one model agrees with itself, it does not check itself';
      }).join('');
    c.members.querySelectorAll('input').forEach(function (x) {
      x.disabled = !x.checked && (n >= maxCalls() || !!x.dataset.blocked);
    });
    drawSeats(ex);
  }
  function fact(alias) { return (C.facts || {})[alias] || {}; }
  // The same model ticked on two providers (route.family_of, as the gateway
  // keeps explorers off a member's model): allowed, and said.
  function twice() {
    var by = {};
    picked().forEach(function (a) { var f = fact(a).family || a; (by[f] = by[f] || []).push(a); });
    return Object.keys(by).filter(function (f) { return by[f].length > 1; }).map(function (f) {
      var row = c.members.querySelector('.m-row[data-family="' + CSS.escape(f) + '"]');
      return {name: row ? row.dataset.name : by[f][0], on: by[f].map(function (a) { return fact(a).provider || a; })};
    });
  }
  // The panel as it will sit, as the Collections list draws it; a member's ×
  // unticks it, as unticking its chip does.
  function drawSeats(ex) {
    c.seats.textContent = '';
    picked().forEach(function (a) {
      var seat = el('span', {class: 'seat'}, a);
      if (fact(a).provider) seat.appendChild(el('span', {class: 'k after'}, fact(a).provider));
      var x = el('button', {type: 'button', class: 'seat-x', 'aria-label': 'Remove ' + a + ' from the collection',
                            title: 'Remove from the collection'}, '×');
      x.addEventListener('click', function () {
        var box = c.members.querySelector('input[value="' + CSS.escape(a) + '"]');
        if (box) { box.checked = false; applyTags(); }
      });
      seat.appendChild(x);
      c.seats.appendChild(seat);
    });
    for (var i = 0; i < ex; i++) {
      c.seats.appendChild(el('span', {class: 'seat explorer'}, 'explorer' + (c.shape.value ? ' · ' + c.shape.value : '')));
    }
  }
  // Tags that say what every member must do: why a model does not fit them
  // (none: it fits). From collections_view's facts; an unknown context is
  // let through, an unknown capability is not, as the gateway's vision seats.
  var CAP_TAGS = {vision: ['vision', 'take images'], tools: ['tools', 'call tools'], json: ['structured_output', 'give structured output']};
  function tagBlocks(alias) {
    var f = (C.facts || {})[alias], why = [];
    if (!f) return why;
    if (f.role === 'image') why.push('an image generator: it never answers a panel');
    tagList().forEach(function (t) {
      var cap = CAP_TAGS[t], roles = (C.shape_roles || {})[t];
      if (cap && !f.caps[cap[0]]) {
        why.push(f.caps[cap[0]] === false ? 'does not ' + cap[1]
          : 'the catalog does not say it can ' + cap[1] + '; tick it in its model popup if it does');
      }
      else if (t === 'long-context' && f.context && f.context < C.long_context) {
        why.push('holds ' + Math.round(f.context / 1000) + 'k tokens, under ' + Math.round(C.long_context / 1000) + 'k');
      }
      // general admits only the general role, but as a tag it means "for anything".
      else if (roles && t !== 'general' && roles.indexOf(f.role) === -1) {
        why.push('role ' + f.role + ': not for ' + t + ' tasks');
      }
    });
    return why;
  }
  // Greyed out, not hidden, so you see what the tags rule out; a ticked one
  // stays tickable (marked) so it can be taken off. `free` hides the paid ones.
  function applyTags() {
    var free = tagList().indexOf('free') !== -1;
    c.members.querySelectorAll('label.check').forEach(function (lab) {
      var box = lab.querySelector('input'), why = tagBlocks(box.value);
      var paid = free && ((C.facts || {})[box.value] || {}).price === 'paid';
      if (paid) why.push('paid, and the collection is tagged free');
      lab.classList.toggle('blocked', why.length > 0);
      lab.classList.toggle('clash', why.length > 0 && box.checked);
      lab.dataset.paid = paid ? '1' : '';
      if (why.length) box.dataset.blocked = '1'; else delete box.dataset.blocked;
      // A chip says only the provider: the alias and served name are its first line.
      var served = lab.dataset.served;
      lab.title = [box.value + (served && served !== box.value ? ' (' + served + ')' : '')].concat(why).join('\n');
    });
    filterMembers();
  }
  // The member search: the filter boxes' match (any text, case-insensitive),
  // over the model's name and each chip's alias, served name and provider,
  // and the provider select beside it. It only hides chips, ticked ones too:
  // the seats above the list always show what is picked, each with its ×, and
  // every count still includes it. A row goes when all its chips do.
  function filterMembers() {
    var q = c.find.value.trim().toLowerCase(), prov = c.prov.value, show = c.show.value;
    c.members.querySelectorAll('.m-row').forEach(function (row) {
      var named = !q || row.dataset.name.toLowerCase().indexOf(q) !== -1, any = false;
      row.querySelectorAll('label.check').forEach(function (lab) {
        var box = lab.querySelector('input');
        var text = (box.value + ' ' + (lab.dataset.served || '') + ' ' + (fact(box.value).connection || '') + ' ' +
                    lab.textContent).toLowerCase();
        lab.hidden = !box.checked && !!lab.dataset.paid || !named && text.indexOf(q) === -1
                     || !!prov && (fact(box.value).provider || '') !== prov
                     || show === 'ticked' && !box.checked || show === 'fits' && !!box.dataset.blocked;
        any = any || !lab.hidden;
      });
      row.hidden = !any;
    });
    recount();
  }
  on('input', 'c-member-filter', filterMembers);
  on('change', 'c-member-provider', filterMembers);
  on('change', 'c-member-show', filterMembers);
  // Refresh model details: the models and their facts fetched again, the list drawn
  // anew with the same ticks, search and choices. The model popup's directory too.
  function refreshMembers() {
    var chosen = picked(), out = $('c-refresh-out'), btn = $('c-refresh');
    btn.disabled = true; out.textContent = 'Refreshing…';
    return Promise.all([get('/api/collections'), directory()]).then(function (res) {
      C = res[0];
      drawMembers(chosen);
      applyTags();
      out.textContent = 'Up to date.';
    }).catch(function (err) { out.textContent = String(err); })
      .then(function () { btn.disabled = false; });
  }
  on('click', 'c-refresh', refreshMembers);
  // A model opened from the list may have been changed in its popup: what it now is, on close.
  md.addEventListener('close', function () { if (c.form && c.form.open) refreshMembers(); });
  // Escape in a box with text clears it; only an empty box lets Escape put the collection
  // away. Caught on its way down, before the document's Escape.
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && e.target.id === 'c-member-filter' && c.find.value) { e.preventDefault(); c.find.value = ''; filterMembers(); }
  }, true);
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
        c.tags.value = now.join(', '); drawTagPicks(); applyTags();
      });
      box.appendChild(b);
    });
  }
  on('input', 'c-tags', function () { drawTagPicks(); applyTags(); });
  function showMerger() { $('c-merger-row').hidden = c.reply.value !== 'merged'; }
  on('change', 'c-reply', showMerger);
  on('change', 'c-shape', recount);          // the explorer seats say their shape
  on('input', 'c-max-calls', recount);
  on('input', 'c-max-explorers', recount);
  // A collection opens docked beside the list, as a model does (dock): the one
  // clicked, its row marked, or a new one from New collection. Escape or Close
  // puts it away, and the focus goes back to its row.
  function closeForm() { if (c.form && c.form.open) c.form.close(); }
  function rowOf(name) { return name && c.list.querySelector('.c-row[data-name="' + CSS.escape(name) + '"]'); }
  on('close', 'c-form', function () {
    var row = rowOf(open), at = document.activeElement;
    if (row) row.classList.remove('in-hand');
    if (c.form.contains(at) || at === document.body) (row ? row.querySelector('button') : $('c-new')).focus();
    open = null;
  });
  // `row`: a saved collection, opened to edit. `draft`: a new one's values filled in
  // beforehand (first run's core), still new until saved.
  function fillForm(row, draft) {
    c.errors.textContent = ''; c.done.textContent = ''; c.status.textContent = '';
    if (rowOf(open)) rowOf(open).classList.remove('in-hand');
    open = row ? row.name : null; c.del.hidden = !row;
    if (rowOf(open)) rowOf(open).classList.add('in-hand');
    c.head.textContent = row ? 'bench/' + row.name : 'New collection';
    c.form.setAttribute('open', '');                  // docked, not a popup: no backdrop, the list stays live
    if ($('request-dock')) $('request-dock').removeAttribute('open');
    // A saved collection: the call to test it from your own app (serve.collections_tab #c-try).
    $('c-try').hidden = !row;
    if (row) $('c-try-snip').textContent = 'curl ' + $('c-try').dataset.gateway + "/chat/completions \\\n  -H 'Content-Type: application/json' \\\n"
      + "  -d '{\"model\": \"bench/" + row.name + "\", \"messages\": [{\"role\": \"user\", \"content\": \"Say hello in one line.\"}]}'";
    var src = row || draft;
    c.name.value = src ? src.name : ''; c.name.readOnly = !!row;
    c.desc.value = src ? src.description : ''; c.tags.value = src ? src.tags.join(', ') : '';
    c.adHoc.checked = !!row && row.ad_hoc;
    c.ownCalls.value = row && row.max_calls !== undefined ? row.max_calls : '';
    c.ownExp.value = row && row.max_explorers !== undefined ? row.max_explorers : '';
    c.ownCalls.placeholder = C.settings.max_calls; c.ownExp.placeholder = C.settings.max_explorers;
    drawTagPicks();
    c.find.value = ''; c.show.value = ''; c.prov.value = '';
    $('c-refresh-out').textContent = '';
    drawMembers(src ? src.members : []);
    c.shape.textContent = '';
    c.shape.appendChild(el('option', {value: ''}, 'automatic'));
    C.shapes.forEach(function (s) {
      var o = el('option', {value: s}, s); if (row && row.shape === s) o.selected = true; c.shape.appendChild(o);
    });
    c.reply.value = row ? row.reply : 'all';
    c.merger.textContent = '';
    c.merger.appendChild(el('option', {value: ''}, 'automatic'));
    C.models.forEach(function (alias) {
      var o = el('option', {value: alias}, alias + (fact(alias).provider ? ' · ' + fact(alias).provider : ''));
      if (row && row.merger === alias) o.selected = true; c.merger.appendChild(o);
    });
    showMerger(); applyTags();
  }
  // The member list: a table to scan down, one row per model (named by its shortest
  // alias; the name opens its popup, as everywhere), its context and what it can do
  // in columns of their own, then a chip per provider it is on, each ticking that
  // alias. A chip whose window differs from the row's says its own. `chosen`: ticked.
  function drawMembers(chosen) {
    var keepProv = c.prov.value;
    c.members.textContent = '';
    c.prov.length = 1;                                 // "Every provider", then each one a model is on
    Array.from(new Set(C.models.map(function (a) { return fact(a).provider; }).filter(Boolean))).sort()
      .forEach(function (p) { c.prov.appendChild(el('option', {value: p}, p)); });
    c.prov.value = keepProv;
    var head = el('div', {class: 'm-head', 'aria-hidden': 'true'});
    ['Model', 'Context', 'Can', 'On, tick one'].forEach(function (h) { head.appendChild(el('span', null, h)); });
    c.members.appendChild(head);
    var groups = {};
    C.models.forEach(function (a) { var f = fact(a).family || a; (groups[f] = groups[f] || []).push(a); });
    Object.keys(groups).map(function (f) {
      var routes = groups[f].slice().sort(function (x, y) { return x.length - y.length || (x < y ? -1 : 1); });
      return {family: f, name: routes[0], routes: routes};
    }).sort(function (x, y) { return x.name.toLowerCase() < y.name.toLowerCase() ? -1 : 1; }).forEach(function (g) {
      var first = fact(g.name), caps = first.caps || {};
      var line = el('div', {class: 'm-row', role: 'group', 'aria-label': g.name, 'data-family': g.family, 'data-name': g.name});
      var name = el('div', {class: 'm-name'});
      // The same link as every model name elsewhere (a.model > .alias: the highlight on hover).
      var link = el('a', {href: '#', class: 'model', 'data-alias': g.name, title: 'Open ' + g.name});
      link.appendChild(el('span', {class: 'alias'}, g.name));
      name.appendChild(link);
      line.appendChild(name);
      line.appendChild(el('span', {class: 'm-ctx'}, first.context ? tokens(first.context) : '—'));
      var can = el('span', {class: 'm-can'});
      [['vision', 'vision'], ['tools', 'tools'], ['reasoning', 'reasons']].forEach(function (k) {
        if (caps[k[0]]) can.appendChild(el('span', {class: 'chip'}, k[1]));
      });
      if (first.price === 'free') can.appendChild(el('span', {class: 'chip ok'}, 'free'));
      line.appendChild(can);
      var chips = el('div', {class: 'm-routes'});
      g.routes.forEach(function (alias) {
        var f = fact(alias);
        var box = el('input', {type: 'checkbox', value: alias, 'aria-label': alias + ' on ' + (f.provider || 'its endpoint')});
        box.checked = chosen.indexOf(alias) !== -1;
        box.addEventListener('change', applyTags);      // ticked or not changes what a misfit shows as
        var lab = el('label', {class: 'check', 'data-served': (C.served || {})[alias] || ''});
        lab.appendChild(box);
        lab.appendChild(el('span', null, (f.provider || alias) +
          (f.context && first.context && f.context !== first.context ? ' · ' + tokens(f.context) : '')));
        chips.appendChild(lab);
      });
      line.appendChild(chips);
      c.members.appendChild(line);
    });
  }
  function tokens(n) {                                 // 512k, 1M (2^20), 1.9M
    return n >= 1000000 ? String(Math.round(n / 104857.6) / 10).replace(/\.0$/, '') + 'M'
                        : Math.floor(n / 1000).toLocaleString() + 'k';
  }
  // The editor's data (C): what each collection holds, what may join it.
  function loadCollections() { return get('/api/collections').then(function (res) { C = res; }); }
  // After a change, the page from the server again (serve.collections_tab: its
  // list, its facts, the gateway settings), as any page is fetched (load, swap).
  // A collection open beside it is closed first: the page waits for it (load).
  function fetchAgain() {
    $('tab-collections').setAttribute('data-lazy', ++marks);
    ownChange();
    return load('collections');
  }
  // The whole row opens the collection, once the editor's data is in (onLoad);
  // from a keyboard, the focus goes to it.
  document.addEventListener('click', function (e) {
    var b = e.target.closest('#c-list .c-row');
    if (b) load('collections').then(function () {
      var row = C.collections.filter(function (r) { return r.name === b.dataset.name; })[0];
      if (row) { fillForm(row); if (!e.detail) c.head.focus(); }
    });
  });
  on('click', 'c-new', function () { load('collections').then(function () { fillForm(null); c.name.focus(); }); });
  on('click', 'c-close', closeForm);
  // Done: say so beside Save, put it away a moment later and fetch the page again;
  // the message stays in the page's bar, as onboarding and retiring do.
  function finished(message, name) {
    c.done.textContent = message; c.status.textContent = message;
    refreshNeeds();                                       // first run's last step may be done
    setTimeout(function () {
      closeForm();
      fetchAgain().then(function () {                     // the focus, lost with the old rows, to its new row
        if (document.activeElement === document.body) (rowOf(name) ? rowOf(name).querySelector('button') : $('c-new')).focus();
      });
    }, 1200);
  }
  on('click', 'c-delete', function () {
    var name = open;
    if (!name) return;
    ask('Delete bench/' + name + '? Callers asking for it will get an error.', 'Delete', true).then(function (yes) {
      if (!yes) return;
      post('/api/collection/remove', {name: name}).then(function (r) {
        if (!r.ok) { listErrors(c.errors, r.errors); return; }
        finished('Deleted bench/' + name + '.');
      });
    });
  });
  on('click', 'c-save', function () {
    c.errors.textContent = ''; c.done.textContent = '';
    var name = c.name.value.trim().replace(/^bench\//, '');
    post('/api/collection', {name: name, members: picked(), shape: c.shape.value, description: c.desc.value,
                             tags: c.tags.value.split(','), reply: c.reply.value,
                             merger: c.merger.value, ad_hoc: c.adHoc.checked,
                             max_calls: c.ownCalls.value === '' ? null : Number(c.ownCalls.value),
                             max_explorers: c.ownExp.value === '' ? null : Number(c.ownExp.value)}).then(function (r) {
      if (!r.ok) { listErrors(c.errors, r.errors); return; }
      doneOn($('c-save'), 'Saved');
      finished('Saved bench/' + name + '.', name);
    }).catch(function (err) { c.errors.textContent = String(err); });
  });
  on('change', 'g-pick', function () { c.pickConf.disabled = !c.pick.checked; });
  // Saved: the page again (each collection's explorers follow), the message and the focus on the new Save.
  on('click', 'g-save', function () {
    c.gErrors.textContent = ''; c.gDone.textContent = '';
    post('/api/gateway-settings', {max_calls: parseInt(c.maxCalls.value, 10),
                                   max_explorers: parseInt(c.maxExp.value, 10), pick_collection: c.pick.checked,
                                   pick_confidence: c.pickConf.value === '' ? null : Number(c.pickConf.value)}).then(function (r) {
      if (!r.ok) { listErrors(c.gErrors, r.errors); return; }
      var had = document.activeElement === $('g-save');
      return fetchAgain().then(function () { c.gDone.textContent = 'Settings saved.'; if (had) $('g-save').focus(); });
    }).catch(function (err) { c.gErrors.textContent = String(err); });
  });

  // ---- a click on the backdrop closes a popup, as its Close button does ----
  // Pressed AND released outside: a text selection dragged out of an input
  // must not throw the form away. On the document: two popups are inside
  // pages that arrive later (Collections', Quotas').
  var downOutside = false;
  function outside(e) {
    var d = e.target;
    if (d.tagName !== 'DIALOG') return false;
    var r = d.getBoundingClientRect();
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  }
  document.addEventListener('pointerdown', function (e) { downOutside = outside(e); });
  document.addEventListener('click', function (e) { if (downOutside && outside(e)) e.target.close(); downOutside = false; });

  // ---- a gateway request, docked beside Collections' list (serve.gateway_card,
  // serve.gateway_request): every seat's answer with Useful / Not useful beside
  // it; Read opens all of it with its claims to mark. Under them, the request
  // to send again from your own app.
  var rq = null;                        // the request shown
  function rqLines(box) { return box.value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean); }
  function rqSnip(lang) {
    var body = {model: rq.model, messages: [{role: 'user', content: rq.prompt}]}, url = rq.gateway + '/chat/completions';
    $('rq-snip').textContent = lang === 'python'
      ? '# pip install openai\nfrom openai import OpenAI\n\nbench = OpenAI(base_url=' + JSON.stringify(rq.gateway)
        + ', api_key="local", timeout=900)\nreply = bench.chat.completions.create(model=' + JSON.stringify(rq.model)
        + ',\n    messages=[{"role": "user", "content": ' + JSON.stringify(rq.prompt) + '}])\nprint(reply.choices[0].message.content)'
      : "curl " + url + " \\\n  -H 'Content-Type: application/json' \\\n  -d '"
        + JSON.stringify(body).split("'").join("'\\''") + "'";
    $('rq-langs').querySelectorAll('[data-lang]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.lang === lang)); });
  }
  function rqSeat(s) {
    var li = el('li', {'data-call': s.call}), head = el('span');
    head.appendChild(el('span', {class: 'alias'}, s.alias));
    if (s.seat !== 'member') head.appendChild(el('span', {class: 'chip'}, s.seat));
    head.appendChild(el('span', {class: 'chip ' + ({ok: 'ok', empty: 'warn', error: 'err'}[s.status] || '')},
                        s.status === 'ok' ? Math.round(s.seconds) + ' s' : s.status));
    var acts = el('span', {class: 'acts'});
    if (s.status === 'error') acts.appendChild(el('span', {class: 'muted'}, 'nothing to judge'));
    else if (s.useful === null) {
      [['Useful', true], ['Not useful', false]].forEach(function (u) {
        var b = el('button', {type: 'button', class: 'btn small', 'data-useful': String(u[1])}, u[0]);
        acts.appendChild(b);
      });
    } else acts.appendChild(el('span', {class: 'chip ' + (s.useful ? 'ok' : 'err')}, s.useful ? '✓ Useful' : '✗ Not useful'));
    if (s.status !== 'error') acts.appendChild(el('button', {type: 'button', class: 'linkish', 'data-read': ''}, 'Read'));
    li.appendChild(head); li.appendChild(acts);
    li.appendChild(el('span', {class: 'why'}, (s.answer || s.error || 'Empty reply: usually a token budget, not incapacity.').slice(0, 280)));
    return li;
  }
  function rqShow(panel) {
    $('rq-errors').textContent = '';
    return get('/api/gateway-request?panel=' + encodeURIComponent(panel)).then(function (r) {
      if (!r.ok) { listErrors($('rq-errors'), r.errors); return; }
      rq = r;
      $('rq-title').textContent = r.key + ' asked ' + r.model;
      $('rq-meta').textContent = [r.at.slice(0, 16).replace('T', ' '), plural(r.seats.length, 'seat'), r.shape, r.panel]
        .filter(Boolean).join(' · ') + (r.skipped.length ? ' · not asked: ' + r.skipped.join(', ') : '');
      $('rq-prompt').textContent = r.prompt;
      var list = $('rq-seats'); list.textContent = '';
      r.seats.forEach(function (s) { list.appendChild(rqSeat(s)); });
      rqSnip('curl');
      document.querySelectorAll('#g-requests tr.g-row').forEach(function (tr) { tr.classList.toggle('in-hand', tr.dataset.panel === panel); });
      var c = $('c-form'); if (c && c.open) c.close();
      $('request-dock').setAttribute('open', '');
    });
  }
  function rqOwed(tr, change) {             // the row's Owed, and the card's count, after a verdict
    var owed = $('g-owed'), cell = tr && tr.querySelector('td.num[data-sort]');
    if (!cell || !change) return;
    var n = Math.max(0, Number(cell.dataset.sort) + change);
    cell.dataset.sort = n; cell.innerHTML = n ? '<span class="chip warn">' + n + '</span>' : '<span class="muted">—</span>';
    if (owed) { owed.dataset.n = Number(owed.dataset.n) + change; owed.textContent = plural(Number(owed.dataset.n), 'verdict') + ' owed'; }
  }
  function rqJudge(li, body) {
    var s = rq.seats.find(function (x) { return x.call === li.dataset.call; }), was = s.useful;
    $('rq-errors').textContent = '';
    return post('/api/verdict', Object.assign({call: s.call, note: '', real: [], false: []}, body)).then(function (r) {
      if (!r.ok) { listErrors($('rq-errors'), r.errors); return; }
      Object.assign(s, body);
      li.replaceWith(rqSeat(s));
      rqOwed(document.querySelector('#g-requests tr.in-hand'), (s.useful === null) - (was === null));
      ownChange();
    }).catch(function (err) { $('rq-errors').textContent = String(err); });
  }
  document.addEventListener('click', function (e) {
    var tr = e.target.closest('#g-requests tr.g-row');
    if (tr) { rqShow(tr.dataset.panel); return; }
    var dock = e.target.closest('#request-dock');
    if (!dock) return;
    var li = e.target.closest('li[data-call]'), b = e.target.closest('button');
    if (!b) return;
    if (b.id === 'rq-close') { dock.close(); dock.removeAttribute('open'); return; }
    if (b.dataset.lang) { rqSnip(b.dataset.lang); return; }
    if (b.dataset.useful) { rqJudge(li, {useful: b.dataset.useful === 'true'}); return; }
    if (b.hasAttribute('data-read')) {
      var open = li.querySelector('.read');
      if (open) { open.remove(); return; }
      var s = rq.seats.find(function (x) { return x.call === li.dataset.call; });
      var box = el('div', {class: 'read'}), id = 'rq-' + s.call.replace(/\W/g, '-');
      box.appendChild(el('pre', {class: 'log answer'}, s.answer || s.error || 'Empty reply.'));
      var pickBox = el('div', {class: 'choices'});
      [['Useful', true], ['Not useful', false]].forEach(function (u) {
        pickBox.appendChild(el('button', {type: 'button', class: 'btn small', 'aria-pressed': String(s.useful === u[1]), 'data-pick': String(u[1])}, u[0]));
      });
      box.appendChild(pickBox);
      [['note', 'Note', s.note, 'input'], ['real', 'Findings it got right, verified', s.real.join('\n'), 'textarea'],
       ['false', 'Claims it made that are not true', s.false.join('\n'), 'textarea']].forEach(function (f) {
        box.appendChild(el('label', {class: 'field', for: id + '-' + f[0]}, f[1]));
        var x = el(f[3], {id: id + '-' + f[0], 'data-f': f[0], autocomplete: 'off'}); x.value = f[2]; if (f[3] === 'textarea') x.rows = 2;
        box.appendChild(x);
      });
      box.appendChild(el('p', {class: 'rules'}, 'One sentence per line. suite/mine.py turns these into benchmark tasks.'));
      box.appendChild(el('button', {type: 'button', class: 'btn primary small', 'data-save': ''}, 'Save verdict'));
      li.appendChild(box);
      return;
    }
    if (b.dataset.pick) { b.parentNode.querySelectorAll('[data-pick]').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); }); return; }
    if (b.hasAttribute('data-save')) {
      var chosen = li.querySelector('[data-pick][aria-pressed="true"]');
      if (!chosen) { $('rq-errors').textContent = 'Choose Useful or Not useful first.'; return; }
      var f = function (k) { return li.querySelector('[data-f="' + k + '"]'); };
      rqJudge(li, {useful: chosen.dataset.pick === 'true', note: f('note').value, real: rqLines(f('real')), false: rqLines(f('false'))});
    }
  });
  document.addEventListener('keydown', function (e) {
    var tr = e.key === 'Enter' && e.target.closest && e.target.closest('#g-requests tr.g-row');
    if (tr) rqShow(tr.dataset.panel);
  });

  // ---- Quota (serve.quotas_page): a tile per connection, from quota-data
  // (serve.quota_tiles, metrics/quotaview.py). Each takes its provider's shape:
  // groups of windows (a 5-hour and a weekly limit boxed together), one pool
  // (OpenRouter's free requests a day, a bar per key) or, where nothing is
  // stated, each model's last day. A meter is ten segments filled like a bar:
  // 85% is eight and a half. A tile's Models opens its connection docked beside
  // the tiles (#q-models): how it is reached, and its models.
  var QD = null, qBox = null, qOpen = '';   // the page's data and tiles, once it arrives (onLoad.quotas); the one docked
  function qWhen(iso) {
    if (!iso) return '';
    var d = new Date(iso), s = (d - Date.now()) / 1000;
    if (s <= 0) return 'now';
    if (s < 86400) {
      var h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
      return 'in ' + (h ? h + 'h ' : '') + m + 'm';
    }
    if (s < 6 * 86400) return d.toLocaleDateString([], {weekday: 'short'}) + ' ' + d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    return d.toLocaleDateString([], {month: 'numeric', day: 'numeric'}) + ' ' + d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
  }
  function qAgo(iso) {
    var s = (Date.now() - new Date(iso)) / 1000;
    return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago';
  }
  function qTone(pct) { return pct == null ? 'none' : pct >= 70 ? 'ok' : pct >= 25 ? 'warn' : 'err'; }
  // `pct` null draws the ten segments empty: nothing to count yet. Calls
  // ("answered") fill green over red, the failed share showing as red. One
  // element filled to `pct`, cut into its segments by bench.css (.segs): a
  // segment an element was 800 of them on a page.
  function qSegs(pct, what) {
    var tone = pct != null && what === 'answered' ? 'calls' : qTone(pct);
    return el('div', {class: 'segs ' + tone, role: 'img', style: '--fill:' + Math.min(Math.max(pct || 0, 0), 100) + '%',
                      'aria-label': pct == null ? 'nothing yet' : pct + '% ' + (what || 'left')});
  }
  function qNum(n) { return Number(n).toLocaleString(); }
  function qAlias(a) {                                 // as serve.named draws a model: its panel on a click
    var link = el('a', {href: '#', class: 'model', 'data-alias': a, draggable: 'false', title: a});
    link.appendChild(el('span', {class: 'alias mono'}, a));
    return link;
  }
  // 189, 19,997, 494k, 12.5M: whole below 100,000, then short.
  function qShort(n) {
    n = Number(n);
    if (n < 1e5) return Math.round(n * 100) / 100 >= 1000 ? Math.round(n).toLocaleString() : String(Math.round(n * 100) / 100);
    if (n < 1e6) return Math.round(n / 1e3) + 'k';
    return String(n >= 1e8 ? Math.round(n / 1e6) : Math.round(n / 1e5) / 10).replace(/\.0$/, '') + 'M';
  }
  // Every figure on a tile is one meter: what is left of what, in the
  // provider's own unit, on the left (it may wrap); when it resets on the
  // right (it never does); the segments under it.
  function qMeter(label, value, pct, what, title) {
    var box = el('div', {class: 'q-meter'}), row = el('div', {class: 'q-line'});
    var l = typeof label === 'string' ? el('span', {class: 'q-label'}) : label;
    if (typeof label === 'string') l.innerHTML = label;
    row.appendChild(l);
    var v = el('span', {class: 'q-val'}); v.innerHTML = value; row.appendChild(v);
    if (title) box.title = title;
    box.appendChild(row); box.appendChild(qSegs(pct, what));
    return box;
  }
  function qReset(iso) {
    return iso ? '<span title="Resets ' + new Date(iso).toLocaleString() + '">' + qWhen(iso) + '</span>' : '';
  }
  function qWindow(w) {
    var raw = w.of != null && w.remaining != null;
    // The percentage leads every meter, raw or not, so they line up. A label is the provider's words: escaped.
    return qMeter('<b>' + w.left + '%</b> left · ' + (raw ? '<b>' + qShort(w.remaining) + '</b> of '
                  + qShort(w.of) + ' ' + escH(w.label) : escH(w.label)),
                  qReset(w.reset_at), w.left, 'left',
                  (raw ? qNum(w.remaining) + ' of ' + qNum(w.of) + ' left, ' : '') + w.left + '%');
  }
  // How much of a last day answered: a model's (its link), or `label`'s.
  function qCalls(m, label) {
    var n = m.ok + m.failed, pct = n ? Math.round(100 * m.ok / n) : null;
    var value = qResting(m) ? '<span class="warn">resting · ' + qWhen(m.until) + '</span>'
              : n ? '<b>' + m.ok + '</b> of ' + n + ' answered' : '<span class="muted">no calls</span>';
    return qMeter(label || qAlias(m.alias), value, pct, 'answered',
                  n ? m.ok + ' answered, ' + m.failed + ' failed in the last 24 hours' : 'not called in the last 24 hours');
  }
  function qResting(m) { return m.until && new Date(m.until) > Date.now(); }
  function qTile(t) {
    var node = el('article', {class: 'q-tile' + (t.out ? ' out' : ''), draggable: 'true', tabindex: '0',
                              'data-id': t.id, 'aria-label': t.name + ': drag, or use the arrow keys, to move it'});
    var head = el('div', {class: 'q-head'});
    head.appendChild(el('b', {class: 'nm', title: t.name}, t.name));
    var f = t.fresh, how = {live: 'read ', seen: 'seen ', answered: 'answered '}[f.how] || '';
    head.appendChild(el('span', {class: 'chip ' + (f.how === 'live' ? 'ok' : 'via'),
      title: {live: 'Last asked of the provider', seen: 'Last stated in a reply\'s headers',
              answered: 'Last answer from one of its models'}[f.how] || 'Nothing known yet'},
      f.at ? how + qAgo(f.at) : 'nothing yet'));
    node.appendChild(head);
    // Every word about the figures in one place, under the name: where they
    // come from, its plan, your per-minute caps, what a live read said. The
    // connection's own name and address are in Models.
    var about = el('div', {class: 'q-about'});
    var src = t.source || [];
    about.appendChild(el('div', {class: 'q-from'}, (src.length && src[0] !== 'nothing stated'
      ? 'From ' + src.join('; ') : 'No limit stated') + (t.plan ? ' · ' + t.plan + ' plan' : '')));
    if (t.conn_limit) about.appendChild(el('div', {}, 'Your cap: ' + t.conn_limit + '.'));
    (t.notes || []).forEach(function (n) { about.appendChild(el('div', {}, n)); });
    node.appendChild(about);
    var body = el('div', {class: 'q-body'});
    // Day and month caps, counted by the bench: yours, else what the provider
    // documents. The From line names the source; the title says the rest.
    (t.caps || []).forEach(function (g) {
      var box = el('section', {class: 'q-group bare', title: g.title || ''});
      g.windows.forEach(function (w) { box.appendChild(qWindow(w)); });
      body.appendChild(box);
    });
    if (t.kind === 'groups') {
      t.groups.forEach(function (g) {
        var box = el('section', {class: g.label ? 'q-group' : 'q-group bare'});
        if (g.label) box.appendChild(el('h4', {}, g.label));
        g.windows.forEach(function (w) { box.appendChild(qWindow(w)); });
        body.appendChild(box);
      });
    } else if (t.kind === 'pool') {
      t.pool.forEach(function (p) {
        body.appendChild(p.of
          ? qMeter('<b>' + p.left + '%</b> left · <b>' + qShort(Math.max(0, p.of - p.used)) + '</b> of '
                   + qShort(p.of) + ' ' + escH(p.label),
                   qReset(p.reset_at), p.left, 'left')
          : qMeter('<b>' + qShort(p.used) + '</b> ' + escH(p.label) + ' · limit not known', qReset(p.reset_at), null));
      });
    } else if (!(t.caps || []).length) {
      // Nothing stated to count against: how the provider's calls went, all
      // its models together (each one's are in Models).
      var all = t.models.reduce(function (s, m) { return {ok: s.ok + m.ok, failed: s.failed + m.failed}; }, {ok: 0, failed: 0});
      body.appendChild(qCalls({alias: '', ok: all.ok, failed: all.failed, until: null}, 'Calls, last 24 hours'));
    }
    if (t.kind !== 'models') {
      t.models.filter(qResting).forEach(function (m) {
        var r = el('div', {class: 'q-rest'});
        r.appendChild(qAlias(m.alias)); r.appendChild(document.createTextNode(' resting · back ' + qWhen(m.until)));
        body.appendChild(r);
      });
    }
    node.appendChild(body);
    node.appendChild(el('div', {class: 'q-msg', 'aria-live': 'polite'}));
    // One Refresh: the server picks a free read, one short call or the records.
    var foot = el('div', {class: 'q-foot'});
    var rb = el('button', {class: 'btn small', type: 'button', 'aria-label': 'Refresh ' + t.name,
      title: {live: 'Asks the provider what is left (free)',
              check: 'Sends one 16-token request; the reply\'s headers say what is left'}[t.refresh]
             || 'Reads the bench\'s own records again (this provider states no limits to ask about)'});
    rb.innerHTML = '&#x21bb; Refresh';
    rb.addEventListener('click', function () { qAct([t.id]); });
    foot.appendChild(rb);
    var mb = el('button', {class: 'btn small q-open', type: 'button', 'aria-label': t.name + ' models, '
                                + t.live + ' of ' + t.bench + ' free to call'}, 'Models ');
    mb.appendChild(el('span', {class: 'chip ' + (t.live === t.bench ? 'ok' : t.live ? 'warn' : 'err')}, t.live + '/' + t.bench));
    mb.title = t.live + ' of the ' + t.bench + ' bench models on it are free to call';
    mb.addEventListener('click', function (e) { qModels(t, !e.detail); });
    foot.appendChild(mb);
    node.appendChild(foot);
    return node;
  }
  function qNode(id) { return qBox && qBox.querySelector('.q-tile[data-id="' + CSS.escape(id) + '"]'); }
  function qMark() {                                   // the tile docked carries the accent, as a row in hand does
    if (qBox) qBox.querySelectorAll('.q-tile').forEach(function (n) { n.classList.toggle('in-hand', n.dataset.id === qOpen); });
  }
  function qModels(t, keyboard) {
    qOpen = t.id;
    $('q-models-h').textContent = t.name;
    $('q-models-sub').textContent = [t.live + ' of its ' + plural(t.bench, 'model') + ' free to call now'].concat(
      t.plan ? [t.plan + ' plan'] : [], t.conn_limit ? ['your cap: ' + t.conn_limit] : [], t.notes || []).join(' · ');
    // How it is reached, asked as it opens (it reads Open WebUI's connections).
    var reach = $('q-models-about');
    function reachLine(after) {
      reach.textContent = '';
      var line = el('div'); line.appendChild(el('span', {class: 'mono'}, t.tag));
      if (after) line.appendChild(document.createTextNode(' · ' + after));
      reach.appendChild(line);
    }
    reachLine('finding how it is reached…');
    post('/api/quota/about', {tag: t.id}).then(function (r) {
      if (qOpen !== t.id) return;
      if (!r.ok) { reachLine(r.errors.join('; ')); return; }
      reachLine(r.routes.length ? '' : 'no endpoint or Open WebUI connection by this name');
      r.routes.forEach(function (x) {
        var d = el('div', {}, 'Through ' + x.via + ': ');
        d.appendChild(el('code', {}, x.url)); reach.appendChild(d);
      });
    }).catch(function (e) { reachLine(String(e)); });
    // A model a row: its name and whether it is free to call, how its last day went under them.
    var body = el('tbody');
    t.models.forEach(function (m) {
      var tr = body.appendChild(el('tr')), n = m.ok + m.failed;
      tr.appendChild(el('td')).appendChild(qAlias(m.alias));
      tr.appendChild(el('td', {}, [n ? m.ok + ' of ' + n + ' answered in 24 h' : 'not called in 24 h'].concat(
        m.last_ok ? ['last answered ' + qAgo(m.last_ok)] : [], m.limit ? ['limit: ' + m.limit] : []).join(' · ')));
      tr.appendChild(el('td')).appendChild(el('span', {class: 'chip ' + (qResting(m) ? 'warn' : 'ok')},
                                               qResting(m) ? 'resting · ' + qWhen(m.until) : 'ready'));
    });
    var list = $('q-models-list'); list.textContent = '';
    list.appendChild(el('table', {class: 'serves'})).appendChild(body);
    $('q-models').setAttribute('open', ''); qMark();      // docked, not a popup: the tiles stay live
    if (keyboard) $('q-models-h').focus();
  }
  on('click', 'q-models-close', function () { $('q-models').close(); });
  on('close', 'q-models', function () {                // Escape or Close: the focus back to its tile's Models
    var tile = qNode(qOpen), at = document.activeElement;
    if (tile && (this.contains(at) || at === document.body)) tile.querySelector('.q-open').focus();
    qOpen = ''; qMark();
  });
  function qSay(id, text, bad) {                       // a tile's own line: what its refresh said
    var m = qNode(id) && qNode(id).querySelector('.q-msg');
    if (m) { m.className = 'q-msg ' + (bad ? 'errors' : 'muted'); m.textContent = text; }
  }
  // Refresh, a tile's own or every one that tells for free (Refresh all live), one after
  // another; then the page again, so the tiles, the charts and the limits are as now, the
  // connection still docked (a page holding an open record waits for it to close: load),
  // each tile saying what its refresh said and the focus back on the Refresh pressed.
  function qAct(tags) {
    var open = qOpen, said = {}, at = document.activeElement, from = at && at.closest && at.closest('.q-tile');
    $('q-errors').textContent = '';
    $('q-done').textContent = tags.length === 1 ? '' : 'Refreshing ' + tags.length + '…';
    return tags.reduce(function (p, tag) {
      return p.then(function () {
        qSay(tag, 'Refreshing…');
        return post('/api/quota/refresh', {tag: tag}).then(function (r) {
          said[tag] = r.ok ? [r.note || '', false] : [r.errors.join('; '), true];
        }, function (e) { said[tag] = [String(e), true]; });
      });
    }, Promise.resolve()).then(function () {
      if ($('q-models').open) $('q-models').close();
      return refreshPanes(['quotas']);
    }).then(function () {
      tags.forEach(function (tag) { qSay(tag, said[tag][0], said[tag][1]); });
      if (tags.length !== 1) $('q-done').textContent = 'Refreshed ' + tags.filter(function (g) { return !said[g][1]; }).length + ' of ' + tags.length + '.';
      var t = open && QD.tiles.filter(function (x) { return x.id === open; })[0], back = from && qNode(from.dataset.id);
      if (t) qModels(t);
      if (back) back.querySelector('.q-foot button').focus();
    }).catch(function (e) { $('q-errors').textContent = String(e); });
  }
  on('click', 'q-all', function () {                   // in the bar: there before the page's data
    load('quotas').then(function () {
      qAct(QD.tiles.filter(function (t) { return t.refresh === 'live'; }).map(function (t) { return t.id; }));
    });
  });
  function qDefault(a, b) {
    return (b.out - a.out) || ((a.next_reset || '9999') < (b.next_reset || '9999') ? -1 : (a.next_reset || '9999') > (b.next_reset || '9999') ? 1 : 0)
           || (a.id < b.id ? -1 : 1);
  }
  function qDraw() {
    if (!qBox) return;
    qBox.textContent = '';
    QD.tiles.forEach(function (t) { qBox.appendChild(qTile(t)); });
    if (!QD.tiles.length) qBox.appendChild(el('p', {class: 'muted'}, 'No model on the bench yet.'));
    qMark();
  }
  // The order the tiles stand in, kept (config/ui.json); every POST also drops the page the
  // server kept (serve.wrote), so it is never answered in the order before.
  function qSaveOrder() {
    var ids = Array.prototype.map.call(qBox.querySelectorAll('.q-tile'), function (n) { return n.dataset.id; });
    var at = {}; ids.forEach(function (id, i) { at[id] = i; });
    QD.tiles.sort(function (a, b) { return at[a.id] - at[b.id]; });
    post('/api/quota/order', {order: ids}).then(function (r) {
      $('q-done').textContent = r.ok ? 'Order kept.' : ''; $('q-errors').textContent = r.ok ? '' : r.errors.join('; ');
    });
  }
  on('click', 'q-reorder', function () {               // in the bar, as Refresh all live
    load('quotas').then(function () {
      QD.tiles.sort(qDefault); qDraw();
      post('/api/quota/order', {order: []}).then(function (r) {
        $('q-done').textContent = r.ok ? 'Sorted: out first, then the soonest reset.' : '';
        $('q-errors').textContent = r.ok ? '' : r.errors.join('; ');
      });
    });
  });
  // Run each time the page arrives: its elements are new, so bound here.
  onLoad.quotas = function () {
    QD = JSON.parse($('quota-data').textContent); qBox = $('q-tiles');
    if (!qBox) return;                         // nothing on the bench: the page says so (serve.empty_state)
    qDraw();
    var dragged = null;
    qBox.addEventListener('dragstart', function (e) {
      dragged = e.target.closest && e.target.closest('.q-tile');
      if (!dragged) return;
      dragged.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragged.dataset.id);
    });
    qBox.addEventListener('dragover', function (e) {
      if (!dragged) return;
      e.preventDefault();
      var over = e.target.closest && e.target.closest('.q-tile');
      if (!over || over === dragged) return;
      var r = over.getBoundingClientRect();
      var after = e.clientX > r.left + r.width / 2;
      over.parentNode.insertBefore(dragged, after ? over.nextSibling : over);
    });
    qBox.addEventListener('dragend', function () {
      if (!dragged) return;
      dragged.classList.remove('dragging'); dragged = null; qSaveOrder();
    });
    qBox.addEventListener('keydown', function (e) {
      var t = e.target;
      if (!t.classList || !t.classList.contains('q-tile')) return;
      var back = e.key === 'ArrowLeft' || e.key === 'ArrowUp', fwd = e.key === 'ArrowRight' || e.key === 'ArrowDown';
      if (!back && !fwd) return;
      e.preventDefault();
      var sib = back ? t.previousElementSibling : t.nextElementSibling;
      if (!sib) return;
      qBox.insertBefore(t, back ? sib : sib.nextSibling);
      t.focus(); qSaveOrder();
    });
    watchReports('quotas'); drawCharts('quotas'); drawNear();
  };
  // Its charts drawn again at their own new width: drawn while another page was shown (a
  // theme picked), or the window or the docked panel changed the page's width.
  var qRedraw = 0;
  function qFit() {
    var box = $('chart-resets');
    if (box && box.dataset.w && Math.abs(box.clientWidth - box.dataset.w) > 1) {
      clearTimeout(qRedraw); qRedraw = setTimeout(function () { drawCharts('quotas'); }, 150);
    }
  }
  onShow.quotas = function () { qFit(); drawNear(); };
  if (window.ResizeObserver) new ResizeObserver(qFit).observe($('tab-quotas'));

  // ---- Benchmarks charts: Observable Plot, data from serve.charts_data ------
  // d3 and Observable Plot (pinned, from jsDelivr) are asked the first time a page
  // with charts is drawn, not by every page: until they arrive, or fail to (each
  // chart then says so), the charts wait, and all are drawn when they do.
  var PLOT = ['https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js',
              'https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.16/dist/plot.umd.min.js'], plotAsked = 0;
  function needPlot() {
    if (window.Plot || plotAsked === 2) return false;
    if (!plotAsked) {
      plotAsked = 1;
      PLOT.forEach(function (src, i) {
        var s = el('script', {src: src});
        s.async = false;                               // fetched together, run in order: Plot needs d3
        if (i) s.onload = s.onerror = function () { plotAsked = 2; drawCharts(); };
        document.head.appendChild(s);
      });
    }
    return true;
  }
  // A report's charts are drawn when it first comes within a window's height of
  // the window, not all eleven at load; drawn again (a theme, a width, a window
  // chosen) only once it has been. Until then each waits in `queued`, by its id.
  // Undrawn, every report is short and all of them sit near the top, so the
  // observer only says when to look: drawNear measures each in turn, as the ones
  // before it are drawn and grow. Benchmarks' and Quota's, each page its own observer.
  var shownReports = {}, queued = {};
  function nearer(page) {
    return window.IntersectionObserver && new IntersectionObserver(function (entries) {
      if (entries.some(function (e) { return e.isIntersecting; })) drawNear();
    }, {root: $('tab-' + page), rootMargin: '100% 0px'});
  }
  var nearBy = {suite: nearer('suite'), quotas: nearer('quotas')};
  function drawNear() {
    var page = document.querySelector('.tab.active');
    if (!page) return;
    var view = page.getBoundingClientRect(), reach = view.height;
    page.querySelectorAll('.panel.report').forEach(function (r) {
      var b = !shownReports[r.id] && r.getBoundingClientRect();
      // Near: drawn once Plot is in (or failed to come: each chart then says so), asked for if not yet.
      if (b && b.bottom > view.top - reach && b.top < view.bottom + reach && !needPlot()) drawReport(r);
    });
  }
  function drawReport(report) {
    if (shownReports[report.id]) return;
    shownReports[report.id] = true;
    var watch = nearBy[report.closest('.tab').id.slice(4)];
    if (watch) watch.unobserve(report);
    report.querySelectorAll('.chart[id]').forEach(function (box) {
      var draw = queued[box.id];
      delete queued[box.id];
      if (draw) draw();
    });
  }
  // Each time a page with reports arrives: its reports are new elements; one drawn before
  // is drawn at once again (no empty frame after a live refresh), the rest when near.
  function watchReports(page) {
    var watch = nearBy[page];
    if (watch) watch.disconnect();
    document.querySelectorAll('#tab-' + page + ' .panel.report').forEach(function (r) {
      if (shownReports[r.id] || !watch) shownReports[r.id] = true;
      else watch.observe(r);
    });
  }
  // A function, not run once: a theme switched on Settings redraws them in its
  // colours. `part` ('suite', 'uptime', 'trend' or 'quotas'): only that part, as it arrives.
  var zoomed = {};                     // Who to trust (overview) and Performance (speed) zoomed to their zone
  function drawCharts(part) {
    var holder = $('charts-data');
    // No page with charts yet, or only Quota, which asks for Plot once a chart of its comes near (drawNear).
    if ((!holder && (!QD || !plotAsked)) || needPlot()) return;
    var css = getComputedStyle(document.documentElement);
    function tok(name, fallback) { return (css.getPropertyValue(name) || '').trim() || fallback; }
    // `good` is the theme's OK colour, not its brand: in a theme whose brand is
    // red (Hardwood), a found bug drawn in the brand colour would read as bad.
    var ink = tok('--ink', '#24331f'), good = tok('--ok', '#327a16'), err = tok('--err', '#a12b2b'),
        warn = tok('--warn', '#7a4d00'),
        signal = tok('--signal', '#fae66c'), sky = tok('--sky', '#69bdeb'), muted = tok('--muted', '#6b685a'),
        line = tok('--line', '#e4dfd2'), halo = tok('--paper-2', '#ffffff');
    var style = {background: 'transparent', color: ink, fontSize: '13px', overflow: 'visible'};
    // Benchmarks shown: a report's own width inside its padding, its column's (columns);
    // another page: that page's. Benchmarks drawn while hidden is drawn again when shown.
    function width() {
      var report = document.querySelector('.tab.active .panel.report');
      if (report && report.clientWidth) {
        var cs = getComputedStyle(report);
        drawnAt = report.clientWidth;
        return Math.max(520, drawnAt - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight));
      }
      var col = document.querySelector('.tab.active') || document.querySelector('main');
      return Math.max(520, (col.clientWidth || 900) - 50);
    }
    // `own` (Quota's): drawn at its own box's width, kept on it (bench.js qFit), not at
    // Benchmarks' column's (width), which would move the width Benchmarks was drawn at.
    function put(id, make, own) {
      var box = $(id), report = box && box.closest('.report');
      if (!box) return;
      if (report && !shownReports[report.id]) { queued[id] = function () { put(id, make, own); }; return; }
      if (!window.Plot) {
        box.className += ' muted';
        box.textContent = 'Charts need Observable Plot from cdn.jsdelivr.net, which did not load; the table below has the same numbers.';
        return;
      }
      box.textContent = '';                      // a redraw replaces the chart
      if (own) box.dataset.w = box.clientWidth;
      try { box.appendChild(make(own ? Math.max(320, box.clientWidth || 900) : width())); } catch (e) { box.textContent = 'Chart failed: ' + e; }
    }
    // A label's width as Plot sets it (system-ui, 13px unless `size`), so a margin is
    // sized to its longest label, not guessed at so many px a character.
    var pen = document.createElement('canvas').getContext('2d');
    function textW(s, size, bold) {
      pen.font = (bold ? '700 ' : '') + (size || 13) + 'px system-ui, sans-serif';
      return pen.measureText(s).width;
    }
    function widest(labels, size) { return Math.ceil(Math.max.apply(null, [0].concat(labels.map(function (s) { return textW(s, size); })))); }
    // The room at the left for a column of labels: the widest, the tick and its gap,
    // up to `cap` (a longer one is cut by clip). capL: a third of the chart, 300 px at most.
    function room(labels, cap) { return Math.min(cap, widest(labels) + 12); }
    function capL(w) { return Math.round(Math.min(300, w * 0.36)); }
    // Pixel offsets that keep scatter labels apart: each label, in order, moves up or
    // down until it clears the ones already placed, and is anchored at its end or start
    // where centred it would cross the chart's edge (xpx: from the svg's left; w: its
    // width). Null when no place is free: that one is left to its hover, not drawn over another.
    function spread(points, xpx, ypx, w) {
      var boxes = [], tries = [-12, 14, -26, 28, -40, 42, -54, 56];
      return points.map(function (d) {
        var cx = xpx(d), tw = textW(d.model, 11) + 4;
        var anchor = cx + tw / 2 > w ? 'end' : cx - tw / 2 < 0 ? 'start' : 'middle';
        var x1 = anchor === 'end' ? cx - tw : anchor === 'start' ? cx : cx - tw / 2;
        for (var k = 0; k < tries.length; k++) {
          var cy = ypx(d) + tries[k];
          if (!boxes.some(function (b) { return x1 < b.x2 + 4 && b.x1 < x1 + tw + 4 && Math.abs(b.y - cy) < 12; })) {
            boxes.push({x1: x1, x2: x1 + tw, y: cy});
            return {dy: tries[k], anchor: anchor};
          }
        }
        return null;
      });
    }
    // A scatter's names (spread placed them), a halo in the panel's colour keeping each
    // legible over dots, grid lines and the zone.
    function names(labels, x) {
      return ['start', 'middle', 'end'].map(function (a) {
        return Plot.text(labels.filter(function (l) { return l.anchor === a; }), {x: x, y: 'ty', text: 'model',
          textAnchor: a, fontSize: 11, fill: ink, stroke: halo, strokeWidth: 3.5, strokeOpacity: 0.9, paintOrder: 'stroke'});
      });
    }
    function rows(n) { return 20 * n + 80; }
    // A name too long for its margin: `over` characters, and one for the …, out
    // of its middle; its hover keeps it whole. clip: cut until it is `px` wide.
    function middle(s, over) {
      var keep = s.length - over - 1;
      return over <= 0 ? s : s.slice(0, Math.ceil(keep / 2)) + '…' + s.slice(s.length - Math.floor(keep / 2));
    }
    function clip(s, px) {
      for (var n = s.length, t = s; n > 6 && textW(t) > px; t = middle(s, s.length - --n));
      return t;
    }

    // Quota's two reports (serve.quotas_page), from quota-data, in the theme's colours;
    // the names' room measured, as above.
    if (QD && (!part || part === 'quotas')) {
      // Resets, next 7 days: a dot where a limit resets (a daily one every day), a bar while
      // a model rests, a line for now; a row a connection with either.
      put('chart-resets', function (w) {
        var now = Date.now(), end = now + 7 * 86400e3, dots = [], bars = [];
        QD.tiles.forEach(function (t) {
          var wins = [];
          t.groups.forEach(function (g) { g.windows.forEach(function (x) { wins.push({label: (g.label ? g.label + ' · ' : '') + x.label, at: x.reset_at}); }); });
          (t.caps || []).forEach(function (g) { g.windows.forEach(function (x) { wins.push({label: x.label + ' (' + g.label + ')', at: x.reset_at, daily: / day$/.test(x.label)}); }); });
          t.pool.forEach(function (p) { if (p.reset_at) wins.push({label: p.label, at: p.reset_at, daily: true}); });
          wins.forEach(function (x) {
            var at = +new Date(x.at), daily = x.daily || / day$/.test(x.label);
            for (; at && at <= end; at += 86400e3) {
              if (at >= now) dots.push({tile: t.name, at: new Date(at), what: x.label});
              if (!daily) break;
            }
          });
          t.models.filter(qResting).forEach(function (m) {
            bars.push({tile: t.name, x1: new Date(now), x2: new Date(Math.min(+new Date(m.until), end)), what: m.alias + ' resting until ' + new Date(m.until).toLocaleString()});
          });
        });
        var names = QD.tiles.map(function (t) { return t.name; }).filter(function (n) {
          return dots.some(function (d) { return d.tile === n; }) || bars.some(function (b) { return b.tile === n; });
        });
        if (!names.length) return el('p', {class: 'hint'}, 'No reset or rest known for the next 7 days.');
        var left = room(names, capL(w));
        return Plot.plot({
          width: w, height: 26 * names.length + 60, style: style, marginLeft: left, marginRight: 24,
          // A tick each local midnight (every other one on a phone), each on one line: Wed 19.
          x: {domain: [new Date(now), new Date(end)], label: null, grid: true, ticks: d3.timeDay.every(w < 640 ? 2 : 1),
              tickFormat: function (d) { return d.toLocaleDateString([], {weekday: 'short', day: 'numeric'}); }},
          y: {domain: names, label: null, tickFormat: function (n) { return clip(n, left - 12); }},
          marks: [
            Plot.barX(bars, {x1: 'x1', x2: 'x2', y: 'tile', fill: warn, fillOpacity: 0.35, insetTop: 6, insetBottom: 6, title: 'what'}),
            Plot.dot(dots, {x: 'at', y: 'tile', r: 4, fill: good, stroke: ink, strokeWidth: 0.5,
              title: function (d) { return d.what + ' resets ' + d.at.toLocaleString(); }}),
            Plot.ruleX([new Date(now)], {stroke: err, strokeWidth: 1.5}),
            Plot.ruleY(names, {stroke: line, strokeOpacity: 0.5})
          ]});
      }, true);
      // What the bench sent: a row a connection, most this month first; this month's
      // requests pale, the last 24 hours' dark inside them, both in the label at the end.
      put('chart-sent', function (w) {
        var R = QD.sent;
        if (!R.length) return el('p', {class: 'hint'}, 'No call in the ledger this month.');
        function say(r) { return qNum(r.day) + ' / ' + qNum(r.month); }
        var box = el('div'), key = box.appendChild(el('div', {class: 'q-legend'}));
        [['This month, requests', 0.3], ['Last 24 hours, requests', 1]].forEach(function (k) {
          var s = key.appendChild(el('span')), sw = s.appendChild(el('i'));
          sw.style.background = good; sw.style.opacity = k[1];
          s.appendChild(document.createTextNode(k[0]));
        });
        var names = R.map(function (r) { return r.provider; }), left = room(names, capL(w));
        box.appendChild(Plot.plot({
          // Room under the ticks for the axis title, which beside the last tick met it on a wide screen.
          width: w, height: 22 * R.length + 74, style: style, marginLeft: left, marginRight: 12 + widest(R.map(say)), marginBottom: 44,
          x: {label: 'Requests →', grid: true},
          y: {domain: names, label: null, tickFormat: function (n) { return clip(n, left - 12); }},
          marks: [
            Plot.barX(R, {x: 'month', y: 'provider', fill: good, fillOpacity: 0.3,
              title: function (r) { return r.provider + '\nthis month: ' + qNum(r.month) + '\nlast 24 hours: ' + qNum(r.day); }}),
            Plot.barX(R, {x: 'day', y: 'provider', fill: good, insetTop: 4, insetBottom: 4}),
            Plot.text(R, {x: 'month', y: 'provider', text: say, dx: 6, textAnchor: 'start', fill: ink}),
            Plot.ruleX([0], {stroke: ink})
          ]}));
        return box;
      }, true);
    }
    if (!holder || part === 'quotas') { drawNear(); return; }
    var C = JSON.parse(holder.textContent);
    var FEW = 10;                       // under this many tasks or answers, a row is drawn faint
    // The 95% Wilson interval of k successes in n: [low, high]. Honest at small n and at
    // 0 or n of n, where k/n ± 1.96·se says nothing (3 of 3 is not "100% ± 0").
    function wilson(k, n) {
      if (!n) return [0, 1];
      var z = 1.96, p = k / n, z2 = z * z, den = 1 + z2 / n;
      var mid = p + z2 / (2 * n), half = z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n));
      return [Math.max(0, (mid - half) / den), Math.min(1, (mid + half) / den)];
    }

    // Trend: who moved, which way, by how much and how sure (serve.trend_data),
    // in the window the card's select says. A row per model with a task to
    // compare, grouped by its flag, the biggest move first (serve._trend_order,
    // which the table follows): a bar from 0 to its change, none when there is
    // too little to say, the evidence beside it, each task's move in its hover.
    var T = C.trend, tpick = document.querySelector('select[data-trend-days]');
    if (T && part !== 'uptime') put('chart-trend', function (w) {
      if (!T.models.length) return el('p', {class: 'hint'}, 'No model has been scored twice on the same task yet.');
      var win = tpick ? tpick.value : String(T.choices[0]);
      function pts(c) { return (c >= 0 ? '+' : '') + Math.round(c * 100); }
      var keys = [], heads = [], M = [];               // the rows in order; a group's heading; a model's row
      [['lower', 'Lower', err, err], ['higher', 'Higher', good, good], ['steady', 'Steady', ink, muted],
       ['too little', 'Too little to say', muted, null]].forEach(function (g) {
        var R = T.models.filter(function (m) { return m.w[win].tasks && m.w[win].state === g[0]; })
          .map(function (m) { return {key: m.name, m: m, s: m.w[win], bar: g[3]}; })
          .sort(function (a, b) {
            return (g[3] ? Math.abs(b.s.change) - Math.abs(a.s.change) : 0) || (a.key < b.key ? -1 : 1);
          });
        if (!R.length) return;
        heads.push({key: '\u0000' + g[0], text: g[1] + ' (' + R.length + ')', c: g[2]});
        keys.push('\u0000' + g[0]);
        R.forEach(function (r) { M.push(r); keys.push(r.key); });
      });
      var none = T.models.filter(function (m) { return !m.w[win].tasks; }).map(function (m) { return m.name; });
      if (!M.length) return el('p', {class: 'hint'}, 'No model was scored on the same task both in the last ' + win
        + ' days and before them.');
      var B = M.filter(function (r) { return r.bar; });
      function change(r) { return r.s.change; }
      function tip(r) {
        var s = r.s;
        return r.key + ' · last ' + win + ' days\n' + s.says + '\nIn the window: ' + s.runsDown + ' of ' + plural(s.runs, 'run')
          + ' below its earlier scores, ' + s.runsUp + ' above\nEach task, in the window against before:\n'
          + s.per.map(function (p) { return '  ' + p[0] + '  ' + pts(p[1]) + ' pts'; }).join('\n') + '\nLast scored ' + r.m.last;
      }
      function evidence(r) {
        var s = r.s;
        return plural(s.tasks, 'task') + ': ' + s.down + ' down, ' + s.up + ' up · ' + plural(s.runs, 'run') + ' vs ' + s.before + ' before';
      }
      // The names (indented under their group's heading, up to 260 px: a longer one loses
      // its middle, its hover keeps it whole), the bars (260 px at least), the evidence.
      // On a phone, no evidence (the table has it) and shorter names; narrower still, the
      // chart scrolls sideways.
      var nameW = Math.min(260, Math.max(28 + widest(M.map(function (r) { return r.key; })),
                                         16 + Math.ceil(Math.max.apply(null, heads.map(function (h) { return textW(h.text, 13, true); })))));
      var evW = 16 + widest(M.map(evidence), 12);
      w = $('chart-trend').clientWidth || w;          // its own box: a phone's is narrower than width()'s 520
      // Without the evidence column the names take what the bars do not need (260 px), not a
      // fixed 130 px: at the dashboard's width that cut "muse-glimmer-30b" to "muse-gl…mer-30b".
      if (w < nameW + evW + 260) { evW = 0; nameW = Math.min(nameW, Math.max(130, w - 280)); }
      var right = evW || 20, wide = w < nameW + right + 140;
      w = Math.max(w, nameW + right + 140);
      // Symmetric, to the biggest bar and room for its number; 20 pts at least.
      var top = Math.max(0.2, Math.ceil((Math.max.apply(null, [0].concat(B.map(function (r) { return Math.abs(r.s.change); }))) + 0.05) * 10) / 10);
      function quiet(r) { return r.bar ? ink : muted; }
      var plot = Plot.plot({
        width: w, height: 24 * keys.length + 52, style: wide ? Object.assign({}, style, {maxWidth: 'none'}) : style,
        marginLeft: nameW, marginRight: right, marginTop: 44, marginBottom: 8,
        x: {domain: [-top, top], axis: 'top', label: evW ? '← lower · pts against its own earlier scores · higher →' : '← lower · pts · higher →',
            labelAnchor: 'center', labelArrow: 'none', ticks: Math.max(2, Math.floor((w - nameW - right) / 60)), tickFormat: pts},
        y: {domain: keys, axis: null},
        marks: [
          Plot.barX(B, {x1: -T.move, x2: T.move, y: 'key', fill: line, fillOpacity: 0.6}),     // within 10 pts
          Plot.ruleX([0], {stroke: ink, strokeOpacity: 0.5}),
          Plot.barX(B, {x1: 0, x2: change, y: 'key', fill: 'bar', insetTop: 5, insetBottom: 5, title: tip,
            fillOpacity: function (r) { return r.s.state === 'steady' ? 0.6 : 1; }}),        // a flag stands out first
          Plot.text(B.filter(function (r) { return r.s.change < 0; }), {x: change, y: 'key', text: function (r) { return pts(r.s.change); },
            textAnchor: 'end', dx: -4, fill: ink}),
          Plot.text(B.filter(function (r) { return r.s.change >= 0; }), {x: change, y: 'key', text: function (r) { return pts(r.s.change); },
            textAnchor: 'start', dx: 4, fill: ink}),
          Plot.text(heads, {y: 'key', frameAnchor: 'left', dx: -nameW, textAnchor: 'start', fontWeight: 700, fill: 'c', text: 'text'}),
          Plot.text(M, {y: 'key', frameAnchor: 'left', dx: -nameW + 12, textAnchor: 'start', fill: quiet, title: tip,
            text: function (r) { return clip(r.key, nameW - 20); }}),
          evW ? Plot.text(M, {y: 'key', frameAnchor: 'right', dx: 12, textAnchor: 'start', fontSize: 12, fill: quiet, title: tip,
            text: evidence}) : null
        ]
      });
      if (!none.length) return plot;
      var box = el('div', {});
      box.appendChild(plot);
      box.appendChild(el('p', {class: 'hint'}, 'Nothing to compare in the last ' + win + ' days (no task scored both in them'
        + ' and before them): ' + none.join(', ') + '.'));
      return box;
    });
    if (part === 'trend') return;

    // Uptime: a cell a day per provider, then per model, worst first, over the
    // days the card's select says (serve.uptime_data). A day is [up, down, left out, why] or 0.
    var U = C.uptime, pick = document.querySelector('select[data-uptime-days]');
    if (U) {
      var win = pick ? +pick.value : U.choices[0], days = U.days.slice(-win);
      var day = function (s) { return new Date(s + 'T12:00:00'); };
      var uptime = function (list, w) {
        var R = [], cells = [], by = {};
        list.forEach(function (r) {
          var c = r.cells.slice(-win), up = 0, n = 0;
          c.forEach(function (d) { if (d) { up += d[0]; n += d[0] + d[1]; } });
          if (!n) return;                          // nothing counted in these days: no row
          R.push(by[r.name] = {name: r.name, n: n, pct: up / n});
          c.forEach(function (d, i) { cells.push({name: r.name, day: days[i], d: d}); });
        });
        if (!R.length) return el('p', {class: 'hint'}, 'No counted calls in the last ' + win + ' days.');
        R.sort(function (a, b) { return a.pct - b.pct || (a.name < b.name ? -1 : 1); });
        function counted(c) { return c.d && c.d[0] + c.d[1] > 0; }
        function tip(c) {
          var head = c.name + '\n' + day(c.day).toLocaleDateString(undefined, {weekday: 'short', day: 'numeric', month: 'short'});
          if (!c.d) return head + '\nno calls';
          var n = c.d[0] + c.d[1];
          return head + '\n' + (n ? c.d[0] + ' of ' + n + ' answered' : 'no counted calls')
            + (c.d[3] ? '\n' + c.d[3].split('; ').join('\n') : '');
        }
        // Room for the longest "name  (n)"; a longer name than the room loses its middle
        // (its hover keeps it whole).
        function label(r) { return r.name + '  (' + r.n + ')'; }
        var left = room(R.map(label), capL(w)), right = 56, cellW = (w - left - right) / win;
        return Plot.plot({
          width: w, height: 22 * R.length + 50, style: style, marginLeft: left, marginRight: right, marginTop: 28,
          padding: 0.12,
          x: {domain: days, axis: 'top', label: null, tickSize: 0, tickFormat: function (d) {
            var i = days.indexOf(d);           // 30 days: every fifth, counted back from today
            return win > 7 && (win - 1 - i) % 5 ? '' : day(d).toLocaleDateString(undefined,
              win > 7 ? {day: 'numeric', month: 'short'} : cellW < 48 ? {day: 'numeric'} : {weekday: 'short', day: 'numeric'});
          }},
          y: {domain: R.map(function (r) { return r.name; }), label: null, tickSize: 0, tickFormat: function (m) {
            var n = '  (' + by[m].n + ')';
            return clip(m, left - 8 - textW(n)) + n;
          }},
          marks: [
            // A day with nothing counted is hollow: absent, not failing.
            Plot.cell(cells.filter(function (c) { return !counted(c); }), {x: 'day', y: 'name', fill: 'none',
              stroke: line, inset: 1, title: tip}),
            Plot.cell(cells.filter(counted), {x: 'day', y: 'name', title: tip, fill: function (c) {
              var p = c.d[0] / (c.d[0] + c.d[1]);
              return p === 1 ? good : p >= 0.8 ? signal : err;
            }}),
            Plot.text(R, {x: function () { return days[days.length - 1]; }, y: 'name', textAnchor: 'start', fill: ink,
              dx: (w - left - right) / win / 2 + 6, text: function (r) { return (r.pct * 100).toFixed(1) + '%'; }})
          ]
        });
      };
      // One view at a time, as the card's By says: by provider (the default) or by model.
      var view = (document.querySelector('select[data-uptime-by]') || {}).value || 'providers';
      ['providers', 'models'].forEach(function (k) {
        var box = $('chart-uptime-' + k);
        if (!box) return;
        box.hidden = k !== view;
        if (k === view) put('chart-uptime-' + k, function (w) { return uptime(U[k], w); });
      });
    }
    if (part === 'uptime') return;
    // Decision models: each model's answers grouped by the probability it gave
    // them (x) against how often they were right (y), a panel per set
    // (serve.decision_data). On the diagonal, its 80% answers come true 80% of
    // the time; under it, it is surer than it should be. Panels stack on a phone.
    var DC = C.decide;
    if (DC) put('chart-decide', function (w) {
      if (!(DC.scores || []).length) return el('p', {class: 'hint'}, 'No decision model has answered a benchmark question yet.');
      var names = {};
      DC.sets.forEach(function (s) { names[s[0]] = s[1]; });
      var panels = DC.sets.map(function (s) { return s[1]; });
      // One colour per model across both charts, by name, never by rank.
      var models = DC.scores.map(function (p) { return p.model; })
        .filter(function (m, i, all) { return all.indexOf(m) === i; }).sort();
      var colors = {domain: models, range: [sky, good, signal, warn, muted, ink, err]};
      var box = el('div');
      box.appendChild(el('h3', {class: 'chart-head'}, 'Right of asked, per set'));
      box.appendChild(decideBars(w, names, panels, models, colors));
      // Calibration only says something once a model's answers spread over two or more
      // levels of sureness; while each sits in one top bin it is one dot in a corner.
      var levels = DC.points.some(function (p, i, all) {
        return all.some(function (q) { return q !== p && q.model === p.model && q.set === p.set; });
      });
      if (levels) {
        box.appendChild(el('h3', {class: 'chart-head'}, 'Calibration: said against right'));
        box.appendChild(decideCalibration(w, names, panels, models, colors));
      } else {
        box.appendChild(el('p', {class: 'hint'}, 'Calibration shows once a model gives answers at two or more levels'
          + ' of sureness on a set; so far each answers at one, so the table\'s Brier and calibration gap say it.'));
      }
      return box;
    });
    // Right of asked as bars from 0: a small chart per set, a row per model asked on it,
    // best first. A model under the ranking floor (DC.low items) is drawn faint: shown,
    // not yet ranked.
    function decideBars(w, names, panels, models, colors) {
      var out = el('div'), left = room(models, capL(w));     // one margin for every set, so the bars line up
      DC.sets.forEach(function (s) {
        var rows = DC.scores.filter(function (r) { return r.set === s[0]; }).map(function (r) {
          return Object.assign({share: r.correct / r.asked, ranked: r.asked >= DC.low}, r);
        }).sort(function (a, b) { return b.share - a.share || b.asked - a.asked || (a.model < b.model ? -1 : 1); });
        out.appendChild(el('p', {class: 'chart-label'}, s[1]));
        if (!rows.length) { out.appendChild(el('p', {class: 'hint'}, 'No model asked on this set yet.')); return; }
        out.appendChild(Plot.plot({
          width: w, height: rows.length * 26 + 40, style: style,
          marginLeft: left, marginRight: 50, marginTop: 4, marginBottom: 30,
          x: {domain: [0, 1], label: null, tickFormat: '.0%', ticks: 5, grid: true},
          y: {domain: rows.map(function (r) { return r.model; }), label: null,
              tickFormat: function (m) { return clip(m, left - 12); }},
          color: colors,
          marks: [
            Plot.barX(rows, {x: 'share', y: 'model', fill: 'model', insetTop: 4, insetBottom: 4, rx: 2,
              fillOpacity: function (r) { return r.ranked ? 1 : 0.4; },
              title: function (r) {
                return r.model + ' · ' + s[1] + '\n' + r.correct + ' right of ' + r.asked + ' asked ('
                  + Math.round(r.share * 100) + '%)' + (r.brier != null ? '\nBrier ' + r.brier.toFixed(3) : '')
                  + (r.ranked ? '' : '\nnot ranked yet: under ' + DC.low + ' items');
              }}),
            Plot.text(rows, {x: 'share', y: 'model', dx: 6, textAnchor: 'start', fill: ink,
              text: function (r) { return r.correct + '/' + r.asked; }}),
            Plot.ruleX([0], {stroke: ink})
          ]
        }));
      });
      return out;
    }
    function decideCalibration(w, names, panels, models, colors) {
      var pts = DC.points.map(function (p) { return Object.assign({panel: names[p.set]}, p); })
        .sort(function (a, b) { return a.said - b.said; });
      // Both axes start just under the lowest answer, not at 0%: a decision model is
      // rarely under 50% sure of its own answer, so a 0-100% frame left every dot in
      // one corner under a long empty diagonal. Same floor on both, so it stays 45°.
      var low = Math.min.apply(null, pts.map(function (p) { return Math.min(p.said, p.right); }));
      var lo = Math.max(0, Math.min(0.9, Math.floor((low - 0.05) * 20) / 20));
      var diag = [];
      panels.forEach(function (s) { diag.push({panel: s, x: lo, y: lo}, {panel: s, x: 1, y: 1}); });
      function tip(p) {
        return p.model + ' · ' + names[p.set] + '\n' + plural(p.n, 'answer') + ' it was about '
          + Math.round(p.said * 100) + '% sure of\n' + Math.round(p.right * 100) + '% of them right';
      }
      var narrow = w < 700, f = narrow ? 'fy' : 'fx', opts = {}, by = {};
      opts[f] = {domain: panels, label: null};
      by[f] = 'panel';
      return Plot.plot(Object.assign(opts, {
        width: narrow ? Math.min(w, 520) : w, height: narrow ? 3 * 230 + 70 : 320, style: style,
        // Stacked, each set's name at the right of its panel: room for the longest.
        marginLeft: 52, marginRight: narrow ? 16 + widest(panels) : 20,
        marginBottom: 44, marginTop: 28,
        inset: 14,                         // a dot at 100% clear of the frame
        x: {domain: [lo, 1], label: 'Probability it gave its answer →', tickFormat: '.0%', ticks: 5, grid: true},
        y: {domain: [lo, 1], label: '↑ Share right', tickFormat: '.0%', ticks: 5, grid: true},
        r: {range: [3, 13]},
        color: Object.assign({legend: true}, colors),
        marks: [
          Plot.frame({stroke: line}),
          Plot.line(diag, Object.assign({x: 'x', y: 'y', z: 'panel', stroke: muted, strokeDasharray: '4 3'}, by)),
          Plot.line(pts, Object.assign({x: 'said', y: 'right', z: 'model', stroke: 'model', strokeOpacity: 0.55}, by)),
          Plot.dot(pts, Object.assign({x: 'said', y: 'right', r: 'n', fill: 'model', stroke: ink, strokeWidth: 0.5,
                                       title: tip}, by))
        ]
      }));
    }
    // Who to trust: finds real bugs (right) vs makes things up (up = less).
    put('chart-overview', function (w) {
      if (!C.overview.length) return el('p', {class: 'hint'}, 'No model has both suite scores and answers yet.');
      var worst = Math.max(0.15, Math.max.apply(null, C.overview.map(function (d) { return d.wrong; })));
      var x0 = Math.max(0, Math.floor((Math.min.apply(null, C.overview.map(function (d) { return d.recall; })) - 0.05) * 20) / 20);
      // The frame: 44 px left, 50 right, 30 top, 46 under (the axis label under the tick
      // labels, which it ran into), and 16 inside; the names are placed in those pixels.
      var pxW = w - 44 - 50 - 32, pxH = 456 - 30 - 46 - 32;
      // Named: the models in or next to the zone (the ones to pick from), then the clearest
      // warnings (most made up). The rest are on hover: named all at once they ran together.
      // The zone: finds 90% or more of the real bugs, makes up 15% or less.
      var zoneX = Math.max(x0, 0.9), zoneY = 0.15;
      // Zoomed (the heading's Zoom to the zone): the zone and a margin round it, every model there named.
      var D = C.overview;
      if (zoomed.overview) {
        x0 = Math.max(0, zoneX - (1 - zoneX) * 0.5); worst = zoneY * 1.5;
        D = D.filter(function (d) { return d.recall >= x0 && d.wrong <= worst; });
      }
      var near = function (d) { return d.recall >= zoneX - (1 - x0) * 0.08 && d.wrong <= zoneY * 1.4; };
      var worstFew = D.slice().sort(function (a, b) { return b.wrong - a.wrong; }).slice(0, 3);
      var pts = D.filter(function (d) { return zoomed.overview || near(d) || d.new || worstFew.indexOf(d) !== -1; })
        .sort(function (a, b) { return a.wrong - b.wrong || b.recall - a.recall; });
      var offs = spread(pts, function (d) { return 60 + (d.recall - x0) / (1 - x0) * pxW; },
                        function (d) { return d.wrong / worst * pxH; }, w);
      var labels = pts.map(function (d, i) {
        return offs[i] && {model: d.model, recall: d.recall, ty: d.wrong + offs[i].dy * worst / pxH, anchor: offs[i].anchor};
      }).filter(Boolean);
      return Plot.plot({
        width: w, height: 456, style: style, grid: true, inset: 16, marginLeft: 44, marginRight: 50, marginTop: 30, marginBottom: 46,
        x: {label: 'Finds real bugs in our code →', domain: [x0, 1], tickFormat: '.0%'},
        y: {label: '↑ Makes fewer things up', domain: [0, worst], reverse: true, tickFormat: '.0%'},
        marks: [
          // The zone a reviewer should be in: outlined, so it reads on a dark ground too.
          Plot.rect([{x1: zoneX, x2: 1, y1: 0, y2: zoneY}], {x1: 'x1', x2: 'x2', y1: 'y1', y2: 'y2',
            fill: good, fillOpacity: 0.12, stroke: good, strokeOpacity: 0.5, strokeDasharray: '4 3'}),
          // New to the report (first scored in the last NEW_DAYS): the signal colour, ringed and named.
          Plot.dot(D, {x: 'recall', y: 'wrong', r: 6, fill: function (d) { return d.new ? signal : good; }, stroke: ink, strokeWidth: 0.5}),
          Plot.dot(D.filter(function (d) { return d.new; }), {x: 'recall', y: 'wrong', r: 10, fill: 'none', stroke: signal, strokeWidth: 2}),
          names(labels, 'recall'),
          Plot.tip(D, Plot.pointer({x: 'recall', y: 'wrong',
            title: function (d) { return d.model + (d.new ? ' (new)' : '') + '\nfinds ' + d.found + ' real findings\nconfidently wrong in ' + d.wrongN + ' answers'; }}))
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
        // Ranked on what is proven: the low end of the found share's 95% range less the HIGH
        // end of the false alarms' (never shown clean files: up to 100%), so a few tasks, or
        // none on clean code, cannot top the chart.
        score[h.model] = wilson(h.found, bugs)[0] - wilson(h['false alarm'], clean)[1];
        segs.push({model: h.model, part: 'false alarm', x1: -fa, x2: 0, n: h['false alarm'] + ' of ' + clean + ' clean files'});
        segs.push({model: h.model, part: 'found', x1: 0, x2: f, n: h.found + ' of ' + bugs + ' bugs'});
        segs.push({model: h.model, part: 'wrong line', x1: f, x2: f + wl, n: h['wrong line'] + ' of ' + bugs + ' bugs'});
        segs.push({model: h.model, part: 'missed', x1: f + wl, x2: f + wl + ms, n: h.missed + ' of ' + bugs + ' bugs'});
      });
      var models = Object.keys(score).sort(function (a, b) { return score[b] - score[a] || tasks[b] - tasks[a]; });
      function count(m) { return '  (' + tasks[m] + ')'; }      // 1 task is not the same evidence as 38
      var left = room(models.map(function (m) { return m + count(m); }), capL(w));
      return Plot.plot({
        width: w, height: rows(models.length) + 16, style: style, marginLeft: left, marginBottom: 46,
        x: {domain: [-1, 1], label: '← false alarms on clean files · bugs found →', grid: true,
            tickFormat: function (d) { return Math.abs(Math.round(d * 100)) + '%'; }},
        y: {domain: models, label: null, tickFormat: function (m) { return clip(m, left - 12 - textW(count(m))) + count(m); }},
        color: {legend: true, domain: ['found', 'wrong line', 'missed', 'false alarm'], range: [good, signal, line, err]},
        marks: [
          // Under FEW tasks a model's bars are faint: shown, not much to go on.
          Plot.barX(segs.filter(function (s) { return s.x2 > s.x1; }), {x1: 'x1', x2: 'x2', y: 'model', fill: 'part',
            fillOpacity: function (d) { return tasks[d.model] < FEW ? 0.4 : 1; },
            title: function (d) { return d.model + ': ' + d.part + ', ' + d.n + (tasks[d.model] < FEW ? '\nfew tasks: little to go on' : ''); }}),
          Plot.ruleX([0], {stroke: ink, strokeWidth: 1.5})
        ]
      });
    });

    // Real work: how often each model helped, with how sure that is. The dot is the share
    // helped, its size how many were judged, the line the range the true share likely sits
    // in (95% Wilson interval: wide for 3 of 3, narrow for 40 of 50). Ranked by the low end
    // of that range, so volume counts: 3 of 3 no longer outranks 40 of 50. The dashed line
    // is every model's offloads pooled.
    put('chart-work', function (w) {
      if (!C.work.length) return el('p', {class: 'hint'}, 'No judged offloads yet.');
      var R = C.work.map(function (r) {
        var b = wilson(r.useful, r.judged), p = r.useful / r.judged;
        var verdict = b[0] >= 0.5 ? 'helps' : b[1] < 0.5 ? 'does not help' : 'too few to say';
        return Object.assign({p: p, lo: b[0], hi: b[1], verdict: verdict}, r);
      }).sort(function (a, b) { return b.lo - a.lo || b.judged - a.judged; });
      var all = R.reduce(function (s, r) { return [s[0] + r.useful, s[1] + r.judged]; }, [0, 0]);
      var pooled = all[0] / all[1];
      function n(r) { return r.useful + '/' + r.judged; }
      var nameW = room(R.map(function (r) { return r.model; }), capL(w));
      // Room on the right for a count beside a range that reaches 100%.
      var right = 12 + widest(R.map(n), 11);
      return Plot.plot({
        width: w, height: 24 * R.length + 106, style: style, marginLeft: nameW, marginRight: right, marginTop: 34, marginBottom: 46,
        x: {domain: [0, 1], label: 'Helped, share of judged offloads →', tickFormat: '.0%', ticks: 5, grid: true},
        y: {domain: R.map(function (r) { return r.model; }), label: null,
            tickFormat: function (m) { return clip(m, nameW - 12); }},
        r: {domain: [0, Math.max.apply(null, R.map(function (r) { return r.judged; }))], range: [3, 11]},
        color: {domain: ['helps', 'too few to say', 'does not help'], range: [good, muted, err], legend: true},
        marks: [
          Plot.ruleX([0.5], {stroke: line}),
          Plot.ruleX([pooled], {stroke: ink, strokeDasharray: '4 3', strokeOpacity: 0.6}),
          Plot.text([pooled], {x: function (d) { return d; }, frameAnchor: 'top', dy: -20, fill: muted, fontSize: 11,
            text: function () { return 'all models ' + Math.round(pooled * 100) + '%'; }}),
          Plot.ruleY(R, {y: 'model', x1: 'lo', x2: 'hi', stroke: 'verdict', strokeWidth: 2, strokeOpacity: 0.55}),
          Plot.dot(R, {x: 'p', y: 'model', r: 'judged', fill: 'verdict', stroke: halo, strokeWidth: 1.5,
            title: function (r) {
              return r.model + '\nhelped ' + r.useful + ' of ' + r.judged + ' judged (' + Math.round(r.p * 100) + '%; '
                + r.offloads + ' offloads)\nlikely between ' + Math.round(r.lo * 100) + '% and ' + Math.round(r.hi * 100) + '%'
                + (r.real || r.false ? '\nverified findings ' + r.real + ', false claims ' + r.false : '');
            }}),
          Plot.text(R, {x: 'hi', y: 'model', dx: 8, textAnchor: 'start', fill: ink, fontSize: 11, text: n})
        ]
      });
    });

    // Speed vs quality: median seconds on our own tasks against findings located.
    put('chart-speed', function (w) {
      if (!C.speed.length) return el('p', {class: 'hint'}, 'No suite timings yet.');
      var s = C.speed.map(function (d) { return d.seconds; });
      var lo = Math.max(1, Math.min.apply(null, s) * 0.8), hi = Math.max.apply(null, s) * 1.25;
      var y0 = Math.max(0, Math.floor((Math.min.apply(null, C.speed.map(function (d) { return d.recall; })) - 0.05) * 20) / 20);
      var pxW = w - 44 - 50 - 32, pxH = 436 - 30 - 46 - 32;     // the frame, as on Who to trust
      // The zone: 90% or more of the findings, in 40 seconds or less. Named: the models in
      // or next to it, as on Who to trust; the rest are on hover.
      var zoneS = Math.min(hi, Math.max(lo, 40)), zoneR = Math.max(y0, 0.9);
      // Zoomed: the zone and a margin round it, every model there named.
      var D = C.speed;
      if (zoomed.speed) {
        hi = Math.max(zoneS * 1.6, lo * 2); y0 = Math.max(0, zoneR - (1 - zoneR) * 0.5);
        D = D.filter(function (d) { return d.seconds <= hi && d.recall >= y0; });
      }
      var pts = D.filter(function (d) { return zoomed.speed || d.new || d.seconds <= zoneS * 1.25 && d.recall >= zoneR - (1 - y0) * 0.08; })
        .sort(function (a, b) { return b.recall - a.recall; });
      var offs = spread(pts, function (d) { return 60 + (Math.log(d.seconds) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)) * pxW; },
                        function (d) { return (1 - d.recall) / (1 - y0) * pxH; }, w);
      var labels = pts.map(function (d, i) {
        return offs[i] && {model: d.model, seconds: d.seconds, ty: d.recall - offs[i].dy * (1 - y0) / pxH, anchor: offs[i].anchor};
      }).filter(Boolean);
      return Plot.plot({
        width: w, height: 436, style: style, grid: true, inset: 16, marginLeft: 44, marginRight: 50, marginTop: 30, marginBottom: 46,
        x: {type: 'log', domain: [lo, hi], label: 'Median seconds per answer on our tasks →'},
        y: {domain: [y0, 1], label: '↑ Findings located', tickFormat: '.0%'},
        marks: [
          // Outlined, so it reads on a dark ground too.
          Plot.rect([{x1: lo, x2: zoneS, y1: zoneR, y2: 1}], {x1: 'x1', x2: 'x2', y1: 'y1', y2: 'y2',
            fill: good, fillOpacity: 0.12, stroke: good, strokeOpacity: 0.5, strokeDasharray: '4 3'}),
          Plot.dot(D, {x: 'seconds', y: 'recall', r: 6, fill: function (d) { return d.new ? signal : sky; }, stroke: ink, strokeWidth: 0.5}),
          Plot.dot(D.filter(function (d) { return d.new; }), {x: 'seconds', y: 'recall', r: 10, fill: 'none', stroke: signal, strokeWidth: 2}),
          names(labels, 'seconds'),
          Plot.tip(D, Plot.pointer({x: 'seconds', y: 'recall',
            title: function (d) { return d.model + (d.new ? ' (new)' : '') + '\nmedian ' + d.seconds.toFixed(0) + 's over ' + d.tasks + ' tasks\nfinds ' + Math.round(d.recall * 100) + '%'; }}))
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
      // Each group's name over its first task, on two staggered rows: side by side they
      // ran into one another wherever a group is only a few tasks wide.
      var heads = C.families.filter(function (f) { return f in first; })
        .map(function (f, i) { return {task: first[f], name: f, row: i % 2}; });
      // By total found, so a model that played three tasks does not top one that played forty.
      // A model with no scored task (only grey) gets no row: it is named under the chart.
      var sum = {}, scored = {};
      C.heat.forEach(function (d) {
        sum[d.model] = (sum[d.model] || 0) + (d.rate || 0);
        if (d.rate != null) scored[d.model] = true;
      });
      var models = Object.keys(sum).filter(function (m) { return scored[m]; }).sort(function (a, b) { return sum[b] - sum[a]; });
      var idle = Object.keys(sum).filter(function (m) { return !scored[m]; }).sort();
      var shown = C.heat.filter(function (d) { return scored[d.model]; });
      var nameW = room(models, capL(w));
      function tip(d) { return d.model + '\n' + d.task + ' (' + d.family + ')\n' + d.label + (d.invented ? '\ninvented something' : ''); }
      var box = el('div');
      box.appendChild(Plot.plot({
        width: w, height: 22 * models.length + 84, style: style, marginLeft: nameW, marginTop: 44, padding: 0.08,
        x: {domain: tasks, axis: null},
        y: {domain: models, label: null, tickSize: 0, tickFormat: function (m) { return clip(m, nameW - 12); }},
        color: {type: 'linear', domain: [0, 0.5, 1], range: [err, signal, good]},
        marks: [
          Plot.cell(shown.filter(function (d) { return d.rate == null; }), {x: 'task', y: 'model', fill: line, title: tip}),
          Plot.cell(shown.filter(function (d) { return d.rate != null; }), {x: 'task', y: 'model', fill: 'rate', title: tip}),
          Plot.dot(shown.filter(function (d) { return d.invented; }), {x: 'task', y: 'model', r: 2.4, fill: ink}),
          Plot.ruleX(heads, {x: 'task', stroke: ink, strokeOpacity: 0.25, dx: -2}),
          Plot.text(heads.filter(function (h) { return !h.row; }), {x: 'task', frameAnchor: 'top', dy: -30, dx: -2,
            text: 'name', textAnchor: 'start', fontSize: 11, fill: ink}),
          Plot.text(heads.filter(function (h) { return h.row; }), {x: 'task', frameAnchor: 'top', dy: -14, dx: -2,
            text: 'name', textAnchor: 'start', fontSize: 11, fill: ink})
        ]
      }));
      if (idle.length) box.appendChild(el('p', {class: 'hint'}, 'No scored task yet, so no row: ' + idle.join(', ') + '.'));
      return box;
    });

    // Vision tasks: one square per field and model, the task's name over its first field.
    put('chart-vision-fields', function (w) {
      var V = C.vision;
      if (!V.heat.length) return el('p', {class: 'hint'}, 'No vision task answers yet: the suite runs them for models'
        + ' that take images, at onboarding (Full) or through the scheduled passes.');
      // Grouped by where the pictures come from, the group's name over its first field.
      var order = ['made', 'own', 'receipts', 'screens', 'tables', 'objects'], keys = [], head = {};
      Object.keys(V.tasks).sort(function (a, b) {
        return order.indexOf(V.tasks[a].family) - order.indexOf(V.tasks[b].family) || (a < b ? -1 : 1);
      }).forEach(function (t) {
        var fam = V.tasks[t].family;
        V.tasks[t].fields.forEach(function (f) {
          if (!keys.some(function (k) { return head[k] === fam; })) head[t + '·' + f] = fam;
          keys.push(t + '·' + f);
        });
      });
      var S = V.summary;
      function share(m) { return S[m].fields ? S[m].right / S[m].fields : 0; }
      var models = Object.keys(S).sort(function (a, b) { return share(b) - share(a) || S[a].taken - S[b].taken; });
      var colour = {right: good, misread: signal, 'left out': line, 'trap taken': err, 'no answer': muted};
      function tip(d) { return d.model + '\n' + d.task.replace(/^vision-/, '') + ': ' + d.field + (d.trap ? ' (a trap)' : '') + '\n' + d.state; }
      function count(m) { return '  (' + S[m].right + '/' + S[m].fields + ')'; }
      var left = room(models.map(function (m) { return m + count(m); }), capL(w));
      return Plot.plot({
        // The group names tilted: a narrow group (a few tables) would run into its neighbour's.
        width: w, height: 22 * models.length + 140, style: style, marginLeft: left, marginTop: 80, padding: 0.08,
        x: {domain: keys, axis: 'top', label: null, tickSize: 0, tickRotate: -60, textAnchor: 'start',
            tickFormat: function (k) { return head[k] || ''; }},
        y: {domain: models, label: null, tickSize: 0,
            tickFormat: function (m) { return clip(m, left - 12 - textW(count(m))) + count(m); }},
        color: {legend: true, domain: V.states, range: V.states.map(function (s) { return colour[s]; })},
        marks: [
          Plot.cell(V.heat, {x: 'key', y: 'model', fill: 'state', title: tip}),
          Plot.dot(V.heat.filter(function (d) { return d.trap; }), {x: 'key', y: 'model', r: 1.8, fill: ink, fillOpacity: 0.55}),
          Plot.ruleX(Object.keys(head), {x: function (k) { return k; }, stroke: ink, strokeOpacity: 0.25, dx: -2})
        ]
      });
    });

    // Vision, real work: read as Real work is. The one question: when its seat was judged,
    // did it help? The dot is the share judged useful (checked and helped, or useful with no
    // claims to check), sized by how many were judged; the line where that share likely sits
    // (95% Wilson), ranked by its low end. Beside it, in words: how often it answered at all,
    // and the claims checked by hand. Seats never judged or never answered say nothing of
    // its worth: they are in the words and the table, not the dot.
    put('chart-vision-work', function (w) {
      var W = C.vision.work, kinds = C.vision.seatKinds, R = [], none = [];
      Object.keys(W).forEach(function (m) {
        var v = W[m], n = function (k) { return v[k] || 0; }, total = 0;
        kinds.forEach(function (k) { total += n(k); });
        if (!total) return;
        var judged = n('checked, helped') + n('checked, did not help') + n('useful, no claims') + n('no use, no claims');
        var useful = n('checked, helped') + n('useful, no claims');
        var r = {model: m, total: total, judged: judged, useful: useful, answered: total - n('no answer'),
                 real: v.real || 0, wrongC: v.false || 0};
        if (!judged) { none.push(r); return; }
        var b = wilson(useful, judged);
        r.p = useful / judged; r.lo = b[0]; r.hi = b[1];
        r.verdict = b[0] >= 0.5 ? 'helps' : b[1] < 0.5 ? 'does not help' : 'too few to say';
        R.push(r);
      });
      if (!R.length && !none.length) return el('p', {class: 'hint'}, 'No vision panels through the gateway yet.');
      R.sort(function (a, b) { return b.lo - a.lo || b.judged - a.judged; });
      var narrowV = w < 560, box = el('div');
      function words(r) {
        return r.useful + '/' + r.judged + (narrowV ? '' : ' · ' + Math.round(r.answered / r.total * 100) + '% answered'
          + (r.real + r.wrongC ? ' · claims ' + r.real + '/' + (r.real + r.wrongC) : ''));
      }
      var nameW = room(R.map(function (r) { return r.model; }), capL(w));
      if (R.length) box.appendChild(Plot.plot({
        width: w, height: 24 * R.length + 106, style: style, marginLeft: nameW, marginTop: 34, marginBottom: 46,
        marginRight: 16 + widest(R.map(words), 11),
        x: {domain: [0, 1], label: 'Judged useful, share of its judged seats →', tickFormat: '.0%', ticks: 5, grid: true},
        y: {domain: R.map(function (r) { return r.model; }), label: null,
            tickFormat: function (m) { return clip(m, nameW - 12); }},
        r: {domain: [0, Math.max.apply(null, R.map(function (r) { return r.judged; }))], range: [3, 11]},
        color: {domain: ['helps', 'too few to say', 'does not help'], range: [good, muted, err], legend: true},
        marks: [
          Plot.ruleX([0.5], {stroke: line}),
          Plot.ruleY(R, {y: 'model', x1: 'lo', x2: 'hi', stroke: 'verdict', strokeWidth: 2, strokeOpacity: 0.55}),
          Plot.dot(R, {x: 'p', y: 'model', r: 'judged', fill: 'verdict', stroke: halo, strokeWidth: 1.5,
            title: function (r) {
              return r.model + '\njudged useful ' + r.useful + ' of ' + r.judged + ' judged seats ('
                + Math.round(r.p * 100) + '%), likely between ' + Math.round(r.lo * 100) + '% and ' + Math.round(r.hi * 100)
                + '%\nanswered ' + r.answered + ' of ' + r.total + ' seats on image panels'
                + (r.real + r.wrongC ? '\nclaims checked by hand: ' + r.real + ' right of ' + (r.real + r.wrongC) : '');
            }}),
          Plot.text(R, {x: 1, y: 'model', dx: 10, textAnchor: 'start', fill: ink, fontSize: 11, text: words})
        ]
      }));
      if (none.length) box.appendChild(el('p', {class: 'hint'}, 'No seat judged yet, so no dot: '
        + none.map(function (r) { return r.model + ' (' + r.total + ' seats, answered ' + r.answered + ')'; }).join(', ')
        + '. The table has every seat.'));
      return box;
    });

    // Confidently wrong: stacked by kind. A thin line runs over each bar for the range the
    // true share likely sits in (95% Wilson), so 0 of 2 shows how bad it could be, not a
    // clean record. Worst proven at the top: by the low end of that range. Few answers: faint.
    put('chart-wrong', function (w) {
      var T = C.wrongTotals, R = {};
      Object.keys(T).forEach(function (m) {
        var b = wilson(T[m].wrong, T[m].answers);
        R[m] = {model: m, lo: b[0], hi: b[1], few: T[m].answers < FEW};
      });
      var models = Object.keys(T).sort(function (a, b) {
        return R[b].lo - R[a].lo || T[b].rate - T[a].rate || T[b].answers - T[a].answers;
      });
      var ranges = models.map(function (m) { return R[m]; });
      var top = Math.min(1, Math.max.apply(null, ranges.map(function (r) { return r.hi; })) + 0.02);
      // The count beside each name: 1 of 2 is not the same evidence as 17 of 26.
      function count(m) { return '  (' + T[m].answers + ')'; }
      var left = room(models.map(function (m) { return m + count(m); }), capL(w));
      return Plot.plot({
        width: w, height: 20 * models.length + 86, style: style, marginLeft: left, marginBottom: 46,
        x: {domain: [0, top], label: 'Confidently wrong, share of answers that could have been →', tickFormat: '.0%', grid: true},
        y: {domain: models, label: null, tickFormat: function (m) { return clip(m, left - 12 - textW(count(m))) + count(m); }},
        color: {legend: true, domain: C.kinds, range: [err, signal, sky, muted]},
        marks: [
          Plot.barX(C.wrong, {x: 'rate', y: 'model', fill: 'kind', insetTop: 3, insetBottom: 3,
            fillOpacity: function (d) { return R[d.model].few ? 0.4 : 1; },
            title: function (d) { return d.model + ': ' + d.kind + ' ' + d.n; }}),
          Plot.ruleY(ranges, {y: 'model', x1: 'lo', x2: 'hi', stroke: ink, strokeOpacity: 0.45, strokeWidth: 1.5,
            title: function (r) {
              return r.model + ': ' + T[r.model].wrong + ' of ' + T[r.model].answers + ' confidently wrong\nlikely between '
                + Math.round(r.lo * 100) + '% and ' + Math.round(r.hi * 100) + '%' + (r.few ? '\nfew answers: little to go on' : '');
            }}),
          Plot.ruleX([0], {stroke: ink})
        ]
      });
    });
    drawNear();                           // what is near the window now (the rest wait in `queued`)
  }
  onLoad.suite = function () { columns(); watchReports('suite'); drawCharts('suite'); jumpSpy(); };
  onShow.suite = function () {            // drawn while another page was shown (a theme picked on Settings)
    var r = document.querySelector('#tab-suite .panel.report');
    if (r && drawnAt && Math.abs(r.clientWidth - drawnAt) > 1) drawCharts('suite');
    else drawNear();
  };
  // Benchmarks' reports in as many columns as fit, each kept whole: one under 1100px of
  // room, three from 1640. In two or three they are a grid of 8px rows, each report
  // spanning its own height, so the grid sets each one under the shortest column. The
  // charts are drawn again at a new column width.
  // The page is watched, not the grid, whose height the spans change: the grid's
  // width follows the page's.
  var drawnAt = 0, redraw = 0, laid = window.ResizeObserver && new ResizeObserver(function (entries) {
    entries.forEach(function (e) {
      var t = e.target;
      if (t.classList.contains('tab')) fit(t.querySelector('.cols'));
      else if (t.offsetHeight) t.style.gridRowEnd = 'span ' + Math.ceil(t.offsetHeight / 8);
    });
    var r = document.querySelector('.tab.active .panel.report');
    if (r && drawnAt && Math.abs(r.clientWidth - drawnAt) > 1) { clearTimeout(redraw); redraw = setTimeout(function () { drawCharts('suite'); }, 150); }
  });
  function fit(g) {
    var w = g && g.clientWidth, n = String(w >= 1640 ? 3 : w >= 1100 ? 2 : 1);
    // Not while the page is not shown: hidden (content-visibility) it has the width of a page
    // without its facts, and laid out again in more columns, every chart was drawn again on return.
    if (w && g.closest('.tab.active') && g.dataset.n !== n) g.dataset.n = n;
  }
  function columns() {
    var g = document.querySelector('#tab-suite .cols');
    if (!g || !laid) return;
    fit(g);                                            // now, so the charts are drawn once
    laid.disconnect(); laid.observe($('tab-suite'));
    g.querySelectorAll('.panel.report').forEach(function (p) { laid.observe(p); });
  }
  // "On this page": the report in view is marked (aria-current). Set up again after a
  // live swap (setUp, onLoad).
  var spy = null;
  function jumpSpy() {
    if (spy) spy.disconnect();
    var nav = document.querySelector('#tab-suite .jump');
    if (!nav || !window.IntersectionObserver) return;
    var links = {}, seen = {}, current = null;
    nav.querySelectorAll('a').forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
    // A report counts as in view while it crosses a thin band 15-20% down the window: just
    // under where a jump lands it, so the one jumped to is the one marked.
    spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { seen[e.target.id] = e.isIntersecting; });
      var id = Object.keys(links).filter(function (k) { return seen[k]; })[0];
      if (!id || id === current) return;
      if (current) links[current].removeAttribute('aria-current');
      current = id;
      links[id].setAttribute('aria-current', 'true');
    }, {rootMargin: '-15% 0px -80% 0px'});
    document.querySelectorAll('#tab-suite .panel.report').forEach(function (p) { spy.observe(p); });
  }
  // A jump scrolls smoothly (unless motion is reduced) and moves focus to the report, so
  // a keyboard user carries on from there; the address keeps the report's #, as a link would.
  document.addEventListener('click', function (e) {
    var a = e.target.closest('.jump a');
    if (!a) return;
    var target = $(a.getAttribute('href').slice(1));
    if (!target) return;
    e.preventDefault();
    // The reports up to it are drawn first, so none grows under the jump and moves it.
    var all = Array.prototype.slice.call(document.querySelectorAll('#tab-suite .panel.report'));
    all.slice(0, all.indexOf(target) + 1).forEach(drawReport);
    var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    target.scrollIntoView({behavior: still ? 'auto' : 'smooth', block: 'start'});
    target.setAttribute('tabindex', '-1');
    target.focus({preventScroll: true});
    history.replaceState(null, '', a.getAttribute('href'));
  });
  // Who to trust's and Performance's Zoom to the zone: that chart drawn again, closer in (drawCharts).
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('button[data-zoom]');
    if (!b) return;
    var on = zoomed[b.dataset.zoom] = !zoomed[b.dataset.zoom];
    b.setAttribute('aria-pressed', String(on));
    b.textContent = on ? 'Show all' : 'Zoom to the zone';
    drawCharts();
  });
  // Uptime's or Trend's window: that window's tables (and Uptime's list) shown, its charts drawn again.
  document.addEventListener('change', function (ev) {
    var s = ev.target, part = s.matches('select[data-uptime-days]') ? 'uptime' : s.matches('select[data-trend-days]') ? 'trend' : '';
    if (s.matches('select[data-uptime-by]')) { drawCharts('uptime'); return; }      // the other view, same window
    if (!part) return;
    s.closest('.panel').querySelectorAll('[data-' + part + '-window]').forEach(function (d) {
      d.hidden = d.getAttribute('data-' + part + '-window') !== s.value;
    });
    drawCharts(part);
  });
})();
