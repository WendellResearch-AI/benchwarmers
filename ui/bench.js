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

  // ---- restart (serve.restart): the page reloads once the new one answers ----
  function restart(force) {
    var rs = $('restart'), out = $('restart-out');
    rs.disabled = true; out.textContent = 'Restarting…';
    post('/api/restart', {force: force}).then(function (res) {
      if (res.ok) return back(Date.now());
      rs.disabled = false; out.textContent = '';
      if (!res.busy) { listErrors(out, res.errors); return; }
      ask('Still in progress: ' + res.errors.join('; ') + '. Restart anyway and cut them off?', 'Restart anyway', true)
        .then(function (yes) { if (yes) restart(true); });
    }).catch(function (err) { rs.disabled = false; out.textContent = String(err); });
  }
  // The old one stops half a second after answering; /health answers again once the new one is up.
  function back(since) {
    setTimeout(function () {
      fetch('/health', {cache: 'no-store'}).then(function (r) { if (!r.ok) throw r.status; location.reload(); })
        .catch(function () {
          if (Date.now() - since < 120000) back(since);
          else $('restart-out').textContent = 'Not back after two minutes: look at whatever runs it.';
        });
    }, 1500);
  }
  on('click', 'restart', function () {
    ask('Restart Benchwarmers? The dashboard and the gateway on this port stop for a few seconds.', 'Restart')
      .then(function (yes) { if (yes) restart(false); });
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
        var keys = r.keys ? ', and ' + r.keys + ' API key' + (r.keys === 1 ? '' : 's') : '';
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

  // ---- tabs, remembered per browser ----------------------------------
  // The server renders only the one shown (serve.tabs, told by the bench_tab
  // cookie set here); the others are fetched when first opened (GET
  // /api/pane), and again after a change to them (refreshPanes).
  var tabs = document.querySelectorAll('.tabs button[data-tab]');      // not the sidebar's own toggle
  var onShow = {};                     // a page's loader, run each time it is shown
  var onLoad = {};                     // a page's set-up, run each time its HTML arrives
  var shown = null, fetching = {}, ready = {}, marks = 0, last = {}, edited = {};
  var KEPT = 'input[data-filter], select[data-facet], input[data-retired], input[data-paid], [data-kept]';    // controls a swap keeps (or may reset)
  function show(id) {
    shown = id;
    tabs.forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.tab === id)); });
    document.querySelectorAll('.tab').forEach(function (s) { s.classList.toggle('active', s.id === 'tab-' + id); });
    store('bench.tab', id);
    document.cookie = 'bench_tab=' + id + '; path=/; max-age=31536000; samesite=strict';
    return load(id).then(function () { if (onShow[id] && shown === id) onShow[id](); });
  }
  // A page in place and set up: fetched if it has no HTML yet, or only old HTML
  // (data-lazy), which stays on screen until the new arrives.
  function load(id) {
    var s = $('tab-' + id);
    if (!s.hasAttribute('data-lazy')) return ready[id] || Promise.resolve();
    if (!fetching[id]) {
      var asked = s.getAttribute('data-lazy');
      if (!/^live/.test(asked)) document.body.classList.add('loading');     // a live refresh shows only what changed
      fetching[id] = fetch('/api/pane?tab=' + id, {cache: 'no-store'}).then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      }).catch(function (err) { location.reload(); throw err; })         // could not ask: the old way
        .then(function (p) {
          delete fetching[id];
          if (!Object.keys(fetching).length) document.body.classList.remove('loading');
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
  // New HTML for a page, keeping what its filter boxes and facets say, which
  // sections are open and which column each table is sorted by. `live` (the
  // data moved, nobody acted): the same HTML as last time is left alone, and
  // so is a page someone is at work on (busy), with New results offered.
  function swap(id, html, live) {
    var s = $('tab-' + id), boxes = 'input[data-filter], select[data-facet]', old = s.querySelectorAll(boxes);
    if (live && (html === last[id] || (s.hasChildNodes() && busy(s)))) {
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
    s.innerHTML = last[id] = html; s.removeAttribute('data-lazy');
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
    applyRetired(); applyPaid();
    return (ready[id] = Promise.resolve(onLoad[id] && onLoad[id]()));
  }
  tabs.forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.tab); }); });
  // The page the server rendered is set up as a fetched one is, once every
  // onLoad below is defined.
  var rendered = document.querySelector('.tab:not([data-lazy])');
  Promise.resolve().then(function () { if (rendered) setUp(rendered.id.slice(4)); });
  var wanted = store('bench.tab');            // else the one rendered: storage blocked or cleared, the cookie kept
  show(wanted && $('tab-' + wanted) ? wanted : rendered ? rendered.id.slice(4) : 'glance');

  // ---- sortable tables -------------------------------------------------
  function key(cell) {
    var raw = cell.dataset.sort != null ? cell.dataset.sort : cell.textContent.trim();
    var n = parseFloat(raw);
    return isNaN(n) || !/^-?[\d.]+/.test(raw) ? raw.toLowerCase() : n;
  }
  function sort(th, dir) {
    var table = th.closest('table'), body = table.tBodies[0];
    var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
    dir = dir || (th.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending');
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
    scope.querySelectorAll('tbody tr, .log-line').forEach(function (r) {
      r.hidden = (q && r.textContent.toLowerCase().indexOf(q) === -1)
        || facets.some(function (s) { return r.dataset[s.dataset.facet] !== s.value; });
    });
    // A match inside a folded list (an endpoint's models) opens it.
    if (q) scope.querySelectorAll('details.models').forEach(function (d) {
      if (d.querySelector('tbody tr:not([hidden])')) d.open = true;
    });
  }
  function scopeOf(c) {
    var box = c.closest('.toolbar') ? c.closest('.toolbar').querySelector('input[data-filter]') : c;
    return box && box.dataset.filter === 'page' ? c.closest('.tab') : c.closest('.panel');
  }
  document.addEventListener('input', function (ev) { if (ev.target.matches('input[data-filter]')) applyFilter(scopeOf(ev.target)); });
  document.addEventListener('change', function (ev) { if (ev.target.matches('select[data-facet]')) applyFilter(scopeOf(ev.target)); });

  // ---- a link to another page (At a glance's line to Action needed) ----
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-go]');
    if (b) { show(b.dataset.go); scrollTo(0, 0); }
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
    document.body.appendChild(a);
    a.click();
    a.remove();
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
    var credit = title + ' · Benchwarmers, ' + D.repo.replace(/^https:\/\//, '') + ' · ' + day;
    menu.open = false;
    function said(text) { sm.textContent = text; setTimeout(function () { sm.textContent = 'Share'; }, 3500); }
    function failed(e) { if (!e || e.name !== 'AbortError') said('Share failed: ' + ((e && e.message) || e)); }
    if (what === 'md') {
      navigator.clipboard.writeText(markdown(shareRows(panel)) + '\n\n' + credit + '\n').then(function () { said('Table copied'); }, failed);
      return;
    }
    if (what === 'csv') { saveFile(new Blob([csv(shareRows(panel))], {type: 'text/csv'}), name + '.csv'); said('Table saved'); return; }
    var png = shareImage(panel, D.repo.replace(/^https:\/\//, '') + ' · ' + day), post = POST[what];
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

  // ---- Discover: paid models hidden unless asked for -------------------
  var showPaid = store('bench.paid') === '1';
  function applyPaid() {
    document.body.classList.toggle('show-paid', showPaid);
    document.querySelectorAll('input[data-paid]').forEach(function (c) { c.checked = showPaid; });
  }
  document.addEventListener('change', function (ev) {
    var c = ev.target;
    if (c.matches('input[data-paid]')) { showPaid = c.checked; store('bench.paid', showPaid ? '1' : '0'); applyPaid(); }
  });
  applyPaid();

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
  function held() { return !!document.querySelector('dialog[open]') || busy($('tab-' + shown)); }
  function poll() {
    if (document.hidden || fetching[shown]) return;          // one at a time: a slow page is not asked again meanwhile
    fetch('/api/stamp', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (s) {
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
  var editing = null;

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
    }
    if (!md.open) md.showModal();      // open already: shown again as it now is (shownAgain)
  }
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
        fetch('/api/onboard?log=' + encodeURIComponent(x.log)).then(function (r) { return r.json(); })
          .then(function (s) { pre.textContent = s.ok ? (s.tail || '(empty)') : s.errors.join(' '); });
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
    var alias = editing;
    post('/api/model-facts', {alias: alias, facts: facts}).then(function (r) {
      if (!r.ok) { listErrors($('f-errors'), r.errors); return; }
      // The tables that show facts (At a glance, Models, Quotas) follow once the popup closes (live).
      shownAgain(alias, 'f-done', 'Saved. The tables show it once this closes.');
    });
  });
  // ---- call options: how the bench asks this model (serve.save_model_options)
  function fillOptions(alias, decision) {
    var o = (D.options || {})[alias] || {}, ceiling = ((D.facts || {})[alias] || {}).max_output;
    $('m-opts').hidden = decision;
    $('co-errors').textContent = ''; $('co-done').textContent = '';
    $('co-max').value = o.max_tokens || ''; $('co-max').placeholder = "the endpoint's default";
    $('co-ceiling').textContent = ceiling ? 'At most its max output, ' + Number(ceiling).toLocaleString() + '.' : '';
  }
  $('co-save').addEventListener('click', function () {
    if (!editing) return;
    var v = $('co-max').value.trim();
    $('co-errors').textContent = ''; $('co-done').textContent = 'Saving…';   // the llm CLI is told too: seconds
    var alias = editing;
    post('/api/model-options', {alias: alias, max_tokens: v ? Number(v) : null}).then(function (r) {
      if (!r.ok) { $('co-done').textContent = ''; listErrors($('co-errors'), r.errors); return; }
      ownChange();                         // only this popup shows them
      shownAgain(alias, 'co-done', 'Saved' + (r.note ? '; ' + r.note : '') + '.');
    });
  });

  function refresh() {
    m.danger.hidden = !(editing && m.alias.value.trim() !== editing);
    m.roleWhy.textContent = D.roles[m.role.value] || '';
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a.model');
    if (a) { e.preventDefault(); openModel(a.dataset.alias); }
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
  function shownAgain(alias, node, text) {
    directory().then(function () {
      if (!md.open || m.title.textContent !== alias) return;
      openModel(alias); $(node).textContent = text;
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
  m.save.addEventListener('click', function () {
    if (!editing) return;
    var body = {alias: editing, new_alias: m.alias.value.trim(), new_role: m.role.value, new_ad_hoc: m.adhoc.checked};
    if (!m.qtypes.hidden) {
      body.new_question_types = Array.prototype.filter.call(m.qtypes.querySelectorAll('input'),
        function (c) { return c.checked; }).map(function (c) { return c.value; });
    }
    (body.new_alias === editing ? Promise.resolve(true)
      : ask('Rename ' + editing + ' to ' + body.new_alias + '? Every record keyed by the old alias follows it.', 'Rename')
    ).then(function (yes) {
      if (!yes) return;
      m.save.disabled = true; m.save.textContent = 'Saving…'; m.errors.textContent = '';
      post('/api/model', body).then(after(m.save, 'Save', function (res) {
        if (body.new_alias !== body.alias) { m.title.textContent = body.new_alias; refreshPanes(); }
        shownAgain(body.new_alias, 'm-done', done(res));
      })).catch(function (err) { m.errors.textContent = String(err); m.save.disabled = false; m.save.textContent = 'Save'; });
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
        function refused(errors) { md.showModal(); listErrors(m.errors, errors); m.remove.disabled = false; }
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
  // Action needed's page, its count in the sidebar and At a glance's line
  // about it, from the server (GET /api/needs, a few ms): a page reload
  // renders every tab, seconds.
  var needsAsked = 0;
  function refreshNeeds(live) {
    var mine = ++needsAsked;
    return fetch('/api/needs', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (n) {
      if (mine !== needsAsked) return;               // two actions close together: only the later answer is current
      swap('needs', n.html, live);
      var tab = document.querySelector('.tabs [data-tab=needs]'), count = tab.querySelector('.n');
      if (!n.count) { if (count) count.remove(); tab.removeAttribute('aria-label'); }
      else {
        if (!count) { count = document.createElement('span'); count.className = 'n'; count.setAttribute('aria-hidden', 'true'); tab.appendChild(count); }
        count.textContent = n.count;
        tab.setAttribute('aria-label', tab.firstChild.textContent + ', ' + n.count + ' open');
      }
      var line = document.querySelector('#tab-glance > .a-line');
      if (line) line.remove();
      // Not into a page not fetched yet: it says Loading… while empty, and comes with its line.
      if (n.line && $('tab-glance').firstChild) $('tab-glance').insertAdjacentHTML('afterbegin', n.line);
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
    return fetch('/api/directory', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (d) {
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
    fetch('/api/stamp', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (s) { D.stamp = s.stamp; }).catch(function () {});
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
  function renderPicker() {
    o.picker.textContent = '';
    if (!data) return;
    var q = o.find.value.trim().toLowerCase();
    var groups = {};
    data.candidates.forEach(function (c) {
      // The endpoint too: "Onboard a model from it" filters on it, and the default
      // endpoint's models are grouped by connection, not by its name.
      if (q && [c.served, c.group, c.endpoint].every(function (s) { return s.toLowerCase().indexOf(q) === -1; })) return;
      // New ones in a group of their own, first: just added to the hub is what
      // you came to onboard, and it is there without scrolling or unfolding.
      var g = c.new ? NEW : c.group;
      (groups[g] = groups[g] || []).push(c);
    });
    var names = Object.keys(groups).sort(function (a, b) { return (b === NEW) - (a === NEW) || (a < b ? -1 : a > b ? 1 : 0); });
    if (!names.length) o.picker.appendChild(el('p', {class: 'empty'}, data.candidates.length ? 'Nothing matches.' : 'Everything the endpoints serve is already on the bench, retired or dismissed.'));
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
    fetch('/api/candidates' + (fresh ? '?fresh=1' : '')).then(function (r) { return r.json(); }).then(function (res) {
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
    var lines = text.split('\n');
    o.steps.textContent = '';
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
  // model at a time. The popup closes: the header's chip shows the batch, and
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
  // unless the popup was closed or reopened meanwhile (the header has it then).
  function waitAndFollow(one, at, title, before, token, tries) {
    if (token !== waitToken || !od.open) return;
    fetch('/api/onboard-queue').then(function (r) { return r.json(); }).then(function (q) {
      if (!q.ok || token !== waitToken || !od.open) return;
      var it = q.items[at];
      if (!it || it.served !== one.served || it.endpoint !== one.endpoint) { showQueue(q); return; }
      if (it.log) { follow(it.log, title, before); return; }
      if (it.state !== 'queued' && it.state !== 'running' || tries <= 0) { showQueue(q); return; }
      setTimeout(function () { waitAndFollow(one, at, title, before, token, tries - 1); }, 500);
    });
  }

  // ---- the onboarding queue: progress in the header (serve.onboard_queue) --
  // Polled while it runs, and once on every page load, so a batch started
  // before a reload still shows. Its results stay until Clear.
  var oq = {box: $('oq'), text: $('oq-text'), chip: $('oq-chip'), stop: $('oq-stop'), clear: $('oq-clear'),
            view: $('o-queue'), sum: $('o-queue-sum'), list: $('o-queue-list')};
  var OQ_CHIP = {queued: '', running: 'new', done: 'ok', 'no answer': 'warn', failed: 'err', skipped: ''};
  var oqTimer = null, oqLast = '';
  function oqCount(q, state) { return q.items.filter(function (i) { return i.state === state; }).length; }
  function oqRender(q) {
    var n = q.items.length;
    oq.box.hidden = !n;
    var now = q.items.filter(function (i) { return i.state === 'running'; })[0];
    var finished = n - oqCount(q, 'queued') - (now ? 1 : 0);
    // The chip fills as models finish, and by fifths of the running one as its
    // log reaches each of onboard.py's five steps (serve.onboard_queue `step`):
    // a step is half a fifth while it runs, so step 5/5 is 90%, and full
    // means finished. At the end it is green when every one is on the bench,
    // red (with one shake) when any failed or did not answer.
    var step = now && now.step ? now.step : 0;
    oq.chip.style.setProperty('--fill', (n ? Math.round(100 * (finished + (step ? step - 0.5 : 0) / 5) / n) : 0) + '%');
    var bad = oqCount(q, 'failed') + oqCount(q, 'no answer');
    oq.chip.classList.toggle('done', !q.running && n > 0 && oqCount(q, 'done') === n);
    oq.chip.classList.toggle('fail', !q.running && bad > 0);
    if (q.running) {
      oq.text.textContent = (q.stopping ? 'Stopping after ' : 'Onboarding ' + (finished + 1) + ' of ' + n
        + (step ? ', step ' + step + '/5' : '') + ': ') + (now ? now.served : '…');
    } else {
      oq.text.textContent = 'Onboarded ' + oqCount(q, 'done') + ' of ' + n
        + [['failed', 'failed'], ['no answer', 'did not answer'], ['skipped', 'skipped']].map(function (s) {
          var c = oqCount(q, s[0]); return c ? ' · ' + c + ' ' + s[1] : '';
        }).join('');
    }
    oq.text.title = oq.text.textContent;                 // cut short beside the button; whole on hover
    oq.stop.hidden = !q.running || q.stopping; oq.clear.hidden = q.running;
    var sig = JSON.stringify(q);
    if (sig !== oqLast) { oqLast = sig; oqLive = null; if (!oq.view.hidden) oqList(q); }
    // The log grows between steps, which the queue's state does not show.
    if (oqLive && !oq.view.hidden && od.open && oqLive.d.open) oqTail(oqLive.log, oqLive.pre);
  }
  function oqList(q) {
    oq.sum.textContent = q.running
      ? 'One at a time, in this order. Closing this does not stop it; Stop in the header lets the model running now finish and skips the rest.'
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
    fetch('/api/onboard?log=' + encodeURIComponent(log)).then(function (r) { return r.json(); }).then(function (s) {
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
    fetch('/api/onboard-queue').then(function (r) { return r.json(); }).then(function (q) {
      if (!q.ok) return;
      oqRender(q);
      if (q.running && !oqTimer) oqTimer = setInterval(oqPoll, 3000);
      if (!q.running && oqTimer) { clearInterval(oqTimer); oqTimer = null; }
    });
  }
  function oqAct(path) {
    post(path, {}).then(function (r) { if (r.ok) oqRender(r); else oq.text.textContent = r.errors.join(' '); });
  }
  $('oq-open').addEventListener('click', function () {
    fetch('/api/onboard-queue').then(function (r) { return r.json(); }).then(function (q) { showQueue(q); oqPoll(); });
  });
  oq.stop.addEventListener('click', function () { oqAct('/api/onboard-queue/stop'); });
  oq.clear.addEventListener('click', function () { oqAct('/api/onboard-queue/clear'); if (!oq.view.hidden) od.close(); });
  oqPoll();
  // ---- Discover: a row opens its model, with every route to add it by -----
  // The rest of what is known comes from /api/discover-model, so the page does
  // not carry 1,200 models' details it will mostly never show.
  var dd = $('discover-dialog');
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
        dd.close(); preStep = null; o.errors.textContent = ''; od.showModal();
        afterQueued(res, {served: x.model, endpoint: x.endpoint}, title, '');
      }).catch(function (err) { $('d-errors').textContent = String(err); b.disabled = false; b.textContent = 'Add via this'; });
      return;
    }
    // A hub's own steps take a while before the onboarding is even queued, so
    // the progress starts now: the onboarding popup with the hub as its first
    // step, and the header chip. The queue's own poll takes the chip over.
    dd.close(); waitToken++;
    preStep = {label: 'Added to ' + x.label, state: 'now', note: ''};
    runView(title, 'Adding it to ' + x.label + '…');
    oq.box.hidden = false; oq.chip.classList.remove('done', 'fail'); oq.chip.style.setProperty('--fill', '3%');
    oq.text.textContent = oq.text.title = title; oq.stop.hidden = oq.clear.hidden = true;
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
  function openDiscover(provider, model) {
    $('d-errors').textContent = '';
    fetch('/api/discover-model?provider=' + encodeURIComponent(provider) + '&model=' + encodeURIComponent(model))
      .then(function (r) { return r.json(); }).then(function (res) {
        if (!res.ok) { listErrors($('d-errors'), res.errors); dd.showModal(); return; }
        if (res.errors.length) listErrors($('d-errors'), res.errors);   // a hub that could not be asked
        var r = res.model, p = $('d-profile'), list = $('d-routes');
        $('d-title').textContent = r.name;
        // Each source a link to its page for the model: FreeLLMAPI's where its sitemap has one, models.dev's by its
        // canonical id. The provider's own list has no page, only its connection.
        var sv = $('d-served');
        sv.textContent = r.provider_name + ' · ' + r.model + ' · listed by ';
        r.sources.forEach(function (s, i) {
          if (i) sv.appendChild(document.createTextNode(' and '));
          if (s === 'provider') { sv.appendChild(document.createTextNode(r.provider_name + '\'s own list (' + r.connection + ' connection)')); return; }
          var fl = s === 'freellmapi';
          sv.appendChild(el('a', {href: fl ? r.page || 'https://freellmapi.co/models' : r.md_page || 'https://models.dev/',
                                  target: '_blank', rel: 'noopener'}, fl ? 'FreeLLMAPI' : 'models.dev'));
        });
        if (r.on_bench_as) sv.appendChild(document.createTextNode(' · on the bench as ' + r.on_bench_as));
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
          r.rank != null ? 'smartest #' + r.rank + (r.speed ? ', fastest #' + r.speed : '') + ' on FreeLLMAPI' : '',
          r.size || '',
          r.context ? 'context ' + Number(r.context).toLocaleString() + ' tokens' : 'context unknown',
          r.output ? 'output up to ' + Number(r.output).toLocaleString() : '',
          can.length ? 'can: ' + can.join(', ') : '',
          price(r),
          r.limits ? 'limits ' + limits(r.limits) : '',
          r.budget ? r.budget + ' tokens a month' : '',
          r.released ? 'released ' + r.released : '',
          r.knowledge ? 'knowledge to ' + r.knowledge : '',
          r.open_weights != null ? (r.open_weights ? 'open weights' : 'closed weights') : '',
          'first seen ' + (r.first_seen === 'baseline' ? 'before the list began' : r.first_seen)];
        p.appendChild(el('div', {class: 'prof'}, facts.filter(Boolean).join(' · ')));
        // FreeLLMAPI's operating notes for this provider, e.g. thinking tokens that eat the output cap.
        (r.quirks || []).forEach(function (q) {
          var row = el('div', {class: 'prof'});
          row.appendChild(el('strong', {}, q.title + ': '));
          row.appendChild(document.createTextNode(q.body || ''));
          p.appendChild(row);
        });
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
  // Refresh lists: every list fetched now (serve.discover_refresh), then the page with them.
  on('click', 'd-refresh', function () {
    var dr = this, said = $('d-refreshed');
    dr.disabled = true; dr.textContent = 'Fetching…'; said.textContent = 'about half a minute';
    post('/api/discover/refresh', {}).then(function (res) {
      // Fetched now even if you went to another page meanwhile, so its errors are there when you come back.
      refreshPanes(['discover']); load('discover').then(function () {          // any errors beside the new page's list age
        if (res.errors.length) $('d-refreshed').textContent = res.errors.join(' · ') + ' · ' + $('d-refreshed').textContent;
      });
    }).catch(function (err) { said.textContent = String(err); dr.disabled = false; dr.textContent = 'Refresh lists'; });
  });
  function follow(log, title, before) {
    runView(title, before + 'Running in the background; closing this does not stop it. Log: runs/' + log);
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
  on('change', 'themes', function (ev) {
    var input = ev.target;
    document.documentElement.setAttribute('data-theme', input.value);
    $('t-errors').textContent = ''; $('t-done').textContent = '';
    if (typeof drawCharts === 'function') drawCharts();
    post('/api/theme', {theme: input.value}).then(function (r) {
      if (!r.ok) { listErrors($('t-errors'), r.errors); return; }
      $('t-done').textContent = 'Saved: ' + input.value + ' is the theme from now on.';
      if (location.search.indexOf('theme=') !== -1) history.replaceState(null, '', '/');   // drop a ?theme preview
    });
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

  // ---- Clients (serve.clients_page): a key made there fills the blocks marked
  // data-key (shell lines, fields typed into an app; never a file that may sync).
  // Held in memory only, so a page swapped in fresh is filled again, and a reload
  // forgets it, as Settings' does.
  var clientKey = null;
  function fillKey() {
    if (!clientKey) return;
    document.querySelectorAll('#tab-clients pre[data-key]').forEach(function (p) {
      p.textContent = p.textContent.split('<your gateway key>').join(clientKey);      // serve.KEY_HOLDER
    });
  }
  onLoad.clients = fillKey;
  on('click', 'cl-make', function () {
    $('cl-errors').textContent = '';
    post('/api/gateway-key', {name: $('cl-name').value}).then(function (r) {
      if (!r.ok) { listErrors($('cl-errors'), r.errors); return; }
      clientKey = r.key; fillKey();
      $('cl-done').hidden = false; $('cl-make').disabled = true;
      refreshPanes(['settings']);                // its list of keys, when next opened
    }).catch(function (e) { listErrors($('cl-errors'), [String(e)]); });
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
      .then(function () { b.textContent = 'Copied'; }, function () { b.textContent = 'Selected: press Ctrl+C'; })
      .then(function () { setTimeout(function () { b.textContent = 'Copy'; }, 2500); });
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
  // data-ep); Add an endpoint and Change key are one dialog. The pages are
  // drawn by the server, so a change fetches them again (refreshPanes): an
  // endpoint's models are on most of them.
  var ed = $('endpoint-dialog');
  var e = {name: $('e-name'), base: $('e-base'), key: $('e-key'),
           kind: $('e-kind'), hub: $('e-hub'), admin: $('e-admin'),
           errors: $('e-errors'), done: $('e-done'), test: $('e-test'), save: $('e-save'), changed: false};
  e.kind.addEventListener('change', function () {
    $('e-hub-row').hidden = $('e-admin-row').hidden = e.kind.value !== 'hub';
  });
  ed.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', function () { ed.close(); }); });
  ed.addEventListener('close', function () { if (e.changed) { e.changed = false; refreshPanes(); } });
  function refreshSaying(message) {
    $('el-errors').textContent = ''; $('el-done').textContent = message;
    refreshPanes().then(function () { $('el-done').textContent = message; });     // on the new page too
  }
  var rowActs = {
    change: function (x) { openEndpoint('change', x); },
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
          refreshSaying(r.note + '.');
        });
      });
    },
    // Its models go with it, after saying how many: each is retired as by
    // hand (evidence kept, restorable once the endpoint is back).
    remove: function (x) {
      var n = x.models, them = n + ' model' + (n === 1 ? '' : 's');
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
    var b = ev.target.closest('.ep button[data-ep-act]');
    if (b) rowActs[b.dataset.epAct](JSON.parse(b.closest('.ep').dataset.ep));
  });
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
          var lab = el('label', {title: (res.labels || {})[k] || k}, k + ' ');
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
  onShow.endpoints = function () {             // its own-app and rate-limit sections, and what runs here
    $('cn-app-errors').textContent = $('cn-app-done').textContent = '';
    loadProviders(); loadLimits(); loadFound();
  };
  // Model servers running on this machine that are not endpoints yet
  // (serve.connect_local): asked each time the page is shown, since a look
  // takes up to a second; nothing shows when there is none.
  function loadFound() {
    fetch('/api/connect/local', {cache: 'no-store'}).then(function (r) { return r.json(); }).then(function (res) {
      var list = $('e-found-list');
      if (!list) return;
      list.textContent = '';
      (res.found || []).forEach(function (f) {
        var row = el('div', {class: 'endpoint'}), left = el('div'), acts = el('div', {class: 'acts'});
        left.appendChild(el('div', {class: 'what'}, f.label + ' on :' + f.port));
        left.appendChild(el('div', {class: 'base'}, (f.key ? 'lists its models only with a key'
          : f.models + ' model' + (f.models === 1 ? '' : 's')) + ' · ' + f.base));
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
    e.done.textContent = f.label + ' answers here' + (f.key ? '; it wants a key: paste one, then Test.' : ': Test it, then Save.');
    (f.key ? e.key : e.test).focus();
  }
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
    var loaded = e.adding ? loadProviders() : Promise.resolve();
    if (!ed.open) ed.showModal();
    (mode === 'change' ? e.key : e.name).focus();
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
    if (e.adding) {                     // Change key keeps its kind and API
      body.kind = e.kind.value; body.hub = e.kind.value === 'hub' ? e.hub.value : ''; body.api = $('e-api').value;
    }
    return body;
  }
  on('click', 'e-add', function () { openEndpoint('add'); });
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
      endpointDone('Saved. ' + name + ' lists ' + r.served + ' models; they appear in the onboarding picker. '
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
    return fetch('/api/connect').then(function (r) { return r.json(); }).then(function (res) {
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

  // ---- first run: no endpoint yet, no model, no collection (serve.first_run) -
  // Each waits for its page: it may not have arrived yet.
  var first = $('first-run');
  function firstStep() {
    if (first.dataset.step === 'onboard') { $('onboard-open').click(); return; }
    if (first.dataset.step === 'collection') { show('collections').then(function () { $('c-new').click(); }); return; }
    show('endpoints').then(function () { openEndpoint('add'); });
  }
  if (first) {
    $('first-go').addEventListener('click', firstStep);
    if (first.dataset.step === 'endpoint') firstStep();   // straight into Add: an empty bench offers nothing else
  }

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
         seats: $('c-seats'), prov: $('c-member-provider'), adHoc: $('c-ad-hoc'),
         ownCalls: $('c-max-calls'), ownExp: $('c-max-explorers')};
    return loadCollections();
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
    c.count.textContent = n + ' member' + (n === 1 ? '' : 's') + ' + ' + ex + ' explorer' + (ex === 1 ? '' : 's') +
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
    var q = c.find.value.trim().toLowerCase(), prov = c.prov.value;
    c.members.querySelectorAll('.m-row').forEach(function (row) {
      var named = !q || row.dataset.name.toLowerCase().indexOf(q) !== -1, any = false;
      row.querySelectorAll('label.check').forEach(function (lab) {
        var box = lab.querySelector('input');
        var text = (box.value + ' ' + (lab.dataset.served || '') + ' ' + (fact(box.value).connection || '') + ' ' +
                    lab.textContent).toLowerCase();
        lab.hidden = !box.checked && !!lab.dataset.paid || !named && text.indexOf(q) === -1
                     || !!prov && (fact(box.value).provider || '') !== prov;
        any = any || !lab.hidden;
      });
      row.hidden = !any;
    });
    recount();
  }
  on('input', 'c-member-filter', filterMembers);
  on('change', 'c-member-provider', filterMembers);
  // Escape in a box with text clears it; only an empty box lets Escape close the popup.
  on('keydown', 'c-member-filter', function (e) {
    if (e.key === 'Escape' && c.find.value) { e.preventDefault(); e.stopPropagation(); c.find.value = ''; filterMembers(); }
  });
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
  // A collection opens in a popup, as every other edit on the dashboard does:
  // the one clicked, or a new one from New collection.
  function closeForm() {
    if (c.form.open) c.form.close();
    open = null;
  }
  on('close', 'c-form', function () { open = null; });
  function fillForm(row) {
    c.errors.textContent = ''; c.done.textContent = ''; c.status.textContent = '';
    open = row ? row.name : null; c.del.hidden = !row;
    c.head.textContent = row ? 'bench/' + row.name : 'New collection';
    if (!c.form.open) c.form.showModal();
    c.name.value = row ? row.name : ''; c.name.readOnly = !!row;
    c.desc.value = row ? row.description : ''; c.tags.value = row ? row.tags.join(', ') : '';
    c.adHoc.checked = !!row && row.ad_hoc;
    c.ownCalls.value = row && row.max_calls !== undefined ? row.max_calls : '';
    c.ownExp.value = row && row.max_explorers !== undefined ? row.max_explorers : '';
    c.ownCalls.placeholder = C.settings.max_calls; c.ownExp.placeholder = C.settings.max_explorers;
    drawTagPicks();
    c.members.textContent = ''; c.find.value = '';
    c.prov.length = 1;                                 // "Every provider", then each one a model is on
    Array.from(new Set(C.models.map(function (a) { return fact(a).provider; }).filter(Boolean))).sort()
      .forEach(function (p) { c.prov.appendChild(el('option', {value: p}, p)); });
    // One row per model, named by its shortest alias, with what the first
    // route says it holds and can do; each provider it is on is a chip that
    // ticks that alias. A chip whose window differs from the row's says its own.
    var groups = {};
    C.models.forEach(function (a) { var f = fact(a).family || a; (groups[f] = groups[f] || []).push(a); });
    Object.keys(groups).map(function (f) {
      var routes = groups[f].slice().sort(function (x, y) { return x.length - y.length || (x < y ? -1 : 1); });
      return {family: f, name: routes[0], routes: routes};
    }).sort(function (x, y) { return x.name.toLowerCase() < y.name.toLowerCase() ? -1 : 1; }).forEach(function (g) {
      var first = fact(g.name), caps = first.caps || {};
      var can = ['vision', 'tools'].filter(function (k) { return caps[k]; });
      var line = el('div', {class: 'm-row', role: 'group', 'aria-label': g.name, 'data-family': g.family, 'data-name': g.name});
      var name = el('div', {class: 'm-name'});
      name.appendChild(el('span', {class: 'mono'}, g.name));
      name.appendChild(el('span', {class: 'm-facts'}, [first.context ? tokens(first.context) : '', can.join(', '),
        first.price === 'unknown' ? '' : first.price].filter(Boolean).join(' · ')));
      line.appendChild(name);
      var chips = el('div', {class: 'm-routes'});
      g.routes.forEach(function (alias) {
        var f = fact(alias);
        var box = el('input', {type: 'checkbox', value: alias, 'aria-label': alias + ' on ' + (f.provider || 'its endpoint')});
        box.checked = !!row && row.members.indexOf(alias) !== -1;
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
  // What a collection's members can all do, in the words the tag checks use.
  var CAN = {reasoning: 'reason'};
  Object.keys(CAP_TAGS).forEach(function (t) { CAN[CAP_TAGS[t][0]] = CAP_TAGS[t][1]; });
  function tokens(n) {                                 // 512k, 1M (2^20), 1.9M
    return n >= 1000000 ? String(Math.round(n / 104857.6) / 10).replace(/\.0$/, '') + 'M'
                        : Math.floor(n / 1000).toLocaleString() + 'k';
  }
  function loadCollections(keep) {
    return fetch('/api/collections').then(function (r) { return r.json(); }).then(function (res) {
      C = res; c.list.textContent = '';
      res.collections.forEach(function (row) {
        // The whole row opens the collection: no tight button pair on it.
        var box = el('button', {class: 'endpoint c-row', type: 'button', 'data-name': row.name,
                                'aria-label': 'Open bench/' + row.name});
        var left = el('div');
        var title = el('div', {class: 'top'});
        title.appendChild(el('span', {class: 'alias'}, 'bench/' + row.name));
        if (row.ad_hoc) title.appendChild(el('span', {class: 'chip'}, 'ad hoc: only when called by name'));
        row.tags.forEach(function (t) { title.appendChild(el('span', {class: 'chip'}, t)); });
        left.appendChild(title);
        if (row.description) left.appendChild(el('p', {class: 'desc'}, row.description));
        // The panel as it sits: a seat per member, an empty one per explorer,
        // and the merger after an arrow.
        var seats = el('div', {class: 'seats'});
        row.members.forEach(function (m) { seats.appendChild(el('span', {class: 'seat'}, m)); });
        for (var i = 0; i < row.explorers; i++) {
          seats.appendChild(el('span', {class: 'seat explorer'}, 'explorer' + (row.shape ? ' · ' + row.shape : '')));
        }
        if (row.reply === 'merged') {
          var merge = el('span', {class: 'merge'}), seat = el('span', {class: 'seat merger'});
          merge.appendChild(el('span', {'aria-hidden': 'true'}, '→'));
          seat.appendChild(el('span', {class: 'k'}, 'merger'));
          seat.appendChild(document.createTextNode(row.merger || 'chosen by verdicts'));
          merge.appendChild(seat); seats.appendChild(merge);
        }
        left.appendChild(seats);
        var t = row.together;
        if (t) {
          var lim = Object.keys(t.limits || {}).filter(function (k) { return t.limits[k]; })
            .map(function (k) { return t.limits[k].toLocaleString() + ' ' + k; });
          var can = t.capabilities.map(function (k) { return CAN[k] || k; });
          var n = t.connections.length + ' connection' + (t.connections.length === 1 ? '' : 's');
          left.appendChild(el('p', {class: 'together'},
            (t.context_window ? 'Prompts up to ' + tokens(t.context_window) + ' tokens' : 'Context window unknown') +
            (t.context_unknown.length ? ' (unknown for ' + t.context_unknown.join(', ') + '). ' : '. ') +
            (can.length ? 'All can ' + (can.length > 1 ? can.slice(0, -1).join(', ') + ' and ' : '') + can[can.length - 1] + '. '
                        : 'Nothing known they can all do. ') +
            (lim.length ? 'Tightest limit ' + lim.join(', ') + ' across ' + n + '.' : 'No limits known across ' + n + '.')));
        }
        box.appendChild(left);
        box.appendChild(el('span', {class: 'open-hint', 'aria-hidden': 'true'}, 'Open ›'));
        box.addEventListener('click', function () { fillForm(row); });
        c.list.appendChild(box);
      });
      if (!res.collections.length) c.list.appendChild(el('p', {class: 'empty'}, 'No collections yet.'));
      c.maxCalls.value = res.settings.max_calls; c.maxExp.value = res.settings.max_explorers;
      // The collection pick (serve.save_gateway_settings): empty confidence is the default.
      c.pick.checked = res.pick.pick_collection; c.pickConf.placeholder = res.confident;
      c.pickConf.value = res.pick.pick_confidence == null ? '' : res.pick.pick_confidence;
      c.pickConf.disabled = !c.pick.checked;
      if (!keep) closeForm();
    });
  }
  on('click', 'c-new', function () { fillForm(null); c.name.focus(); });
  on('click', 'c-close', closeForm);
  // Done: say so in the popup, close it a moment later, and keep the message
  // above the list, as onboarding and retiring do.
  function finished(message) {
    c.done.textContent = message; c.status.textContent = message; loadCollections(true);
    setTimeout(closeForm, 1200);
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
      finished('Saved bench/' + name + '.');
    }).catch(function (err) { c.errors.textContent = String(err); });
  });
  on('change', 'g-pick', function () { c.pickConf.disabled = !c.pick.checked; });
  on('click', 'g-save', function () {
    c.gErrors.textContent = ''; c.gDone.textContent = '';
    post('/api/gateway-settings', {max_calls: parseInt(c.maxCalls.value, 10),
                                   max_explorers: parseInt(c.maxExp.value, 10), pick_collection: c.pick.checked,
                                   pick_confidence: c.pickConf.value === '' ? null : Number(c.pickConf.value)}).then(function (r) {
      if (!r.ok) { listErrors(c.gErrors, r.errors); return; }
      c.gDone.textContent = 'Settings saved.'; loadCollections(true);
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
    var useful = pick ? pick.value === 'yes' : null, call = vcall;
    post('/api/verdict', {call: call, useful: useful, note: $('v-note').value,
                          real: lines('v-real'), false: lines('v-false')}).then(function (r) {
      if (!r.ok) { listErrors($('v-errors'), r.errors); return; }
      // The seat's chip as serve.gateway_card draws it: ✓ useful, ✗ not, no mark still owed.
      var said = {true: ['useful', ' ✓'], false: ['not useful', ' ✗'], null: ['verdict owed', '']}[useful];
      document.querySelectorAll('button.seat').forEach(function (chip) {
        if (chip.dataset.call !== call) return;
        var owed = $('g-owed'), was = / [✓✗]$/.test(chip.textContent);
        chip.textContent = chip.textContent.replace(/ [✓✗]$/, '') + said[1];
        chip.title = chip.title.replace(/(useful|not useful|verdict owed)( · [^·]*)$/, said[0] + '$2');
        if (owed && was !== (useful !== null)) {
          var n = Number(owed.dataset.n) + (was ? 1 : -1);
          owed.dataset.n = n; owed.textContent = n + ' verdict' + (n === 1 ? '' : 's') + ' owed';
        }
      });
      vd.close(); ownChange();
    }).catch(function (err) { $('v-errors').textContent = String(err); });
  });

  // ---- Quota: a tile per provider (serve.quota_tiles, metrics/quotaview.py) ----
  // Each tile takes its provider's shape: groups of windows (a 5-hour and a
  // weekly limit boxed together), one pool (OpenRouter's free requests a day,
  // a bar per key) or, where nothing is stated, each model's last day. A
  // meter is ten segments filled like a bar: 85% is eight and a half.
  var QD = null, qBox = null;          // the page's data and tiles, once it arrives (onLoad.quotas)
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
  // ("answered") fill green over red, the failed share showing as red.
  function qSegs(pct, what) {
    var tone = pct != null && what === 'answered' ? 'calls' : qTone(pct);
    var box = el('div', {class: 'segs ' + tone, role: 'img',
                         'aria-label': pct == null ? 'nothing yet' : pct + '% ' + (what || 'left')});
    for (var i = 0; i < 10; i++) {
      var s = el('span'), f = el('i');
      f.style.width = Math.round(Math.min(Math.max((pct || 0) / 10 - i, 0), 1) * 100) + '%';
      s.appendChild(f); box.appendChild(s);
    }
    return box;
  }
  function qNum(n) { return Number(n).toLocaleString(); }
  function qAlias(a) {                                 // as serve.named draws a model: its popup on a click
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
    // The percentage leads every meter, raw or not, so they line up.
    return qMeter('<b>' + w.left + '%</b> left · ' + (raw ? '<b>' + qShort(w.remaining) + '</b> of '
                  + qShort(w.of) + ' ' + w.label : w.label),
                  qReset(w.reset_at), w.left, 'left',
                  (raw ? qNum(w.remaining) + ' of ' + qNum(w.of) + ' left, ' : '') + w.left + '%');
  }
  // How much of a last day answered: a model's (its link), or `label`'s.
  function qCalls(m, label) {
    var n = m.ok + m.failed, pct = n ? Math.round(100 * m.ok / n) : null;
    var value = qResting(m) ? '<span class="warn-t">resting · ' + qWhen(m.until) + '</span>'
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
                   + qShort(p.of) + ' ' + p.label,
                   qReset(p.reset_at), p.left, 'left')
          : qMeter('<b>' + qShort(p.used) + '</b> ' + p.label + ' · limit not known', qReset(p.reset_at), null));
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
    var msg = el('div', {class: 'q-msg', 'aria-live': 'polite'});
    node.appendChild(msg);
    // One Refresh: the server picks a free read, one short call or the records.
    var foot = el('div', {class: 'q-foot'});
    var rb = el('button', {class: 'btn small', type: 'button', 'aria-label': 'Refresh ' + t.name,
      title: {live: 'Asks the provider what is left (free)',
              check: 'Sends one 16-token request; the reply\'s headers say what is left'}[t.refresh]
             || 'Reads the bench\'s own records again (this provider states no limits to ask about)'});
    rb.innerHTML = '&#x21bb; Refresh';
    rb.addEventListener('click', function () { qAct(t.id, node); });
    foot.appendChild(rb);
    var mb = el('button', {class: 'btn small', type: 'button'}, 'Models ');
    mb.appendChild(el('span', {class: 'chip ' + (t.live === t.bench ? 'ok' : t.live ? 'warn' : 'err')}, t.live + '/' + t.bench));
    mb.title = t.live + ' of the ' + t.bench + ' bench models on it are free to call';
    mb.addEventListener('click', function () { qModels(t); });
    foot.appendChild(mb);
    node.appendChild(foot);
    return node;
  }
  function qAct(id, node) {
    var msg = node.querySelector('.q-msg');
    msg.className = 'q-msg muted'; msg.textContent = 'Refreshing…';
    return post('/api/quota/refresh', {tag: id}).then(function (r) {
      if (!r.ok) { msg.className = 'q-msg errors'; msg.textContent = r.errors.join('; '); return; }
      var i = QD.tiles.findIndex(function (t) { return t.id === id; });
      if (r.tile && i >= 0) {
        QD.tiles[i] = r.tile;
        var fresh = qTile(r.tile);
        node.replaceWith(fresh);
        if (r.note) { var m = fresh.querySelector('.q-msg'); m.className = 'q-msg muted'; m.textContent = r.note; }
      }
      drawCharts();
    }).catch(function (e) { msg.className = 'q-msg errors'; msg.textContent = String(e); });
  }
  function qModels(t) {
    $('q-models-h').textContent = t.name + ': ' + t.bench + ' on the bench';
    $('q-models-sub').textContent = t.live + ' free to call now · the last 24 hours of calls';
    // How it is reached, asked when the popup opens (it reads Open WebUI's connections).
    var reach = $('q-models-about');
    function reachLine(after) {
      reach.textContent = '';
      var line = el('div'); line.appendChild(el('span', {class: 'mono'}, t.tag));
      if (after) line.appendChild(document.createTextNode(' · ' + after));
      reach.appendChild(line);
    }
    reachLine('finding how it is reached…');
    post('/api/quota/about', {tag: t.id}).then(function (r) {
      if (!r.ok) { reachLine(r.errors.join('; ')); return; }
      reachLine(r.routes.length ? '' : 'no endpoint or Open WebUI connection by this name');
      r.routes.forEach(function (x) {
        var d = el('div', {}, 'Through ' + x.via + ': ');
        d.appendChild(el('code', {}, x.url)); reach.appendChild(d);
      });
    }).catch(function (e) { reachLine(String(e)); });
    var list = $('q-models-list'); list.textContent = '';
    var tb = el('table'), hd = el('tr');
    ['Model', 'Now', 'Last 24 h', 'Last answered', 'Known limit'].forEach(function (h) { hd.appendChild(el('th', {}, h)); });
    tb.appendChild(el('thead')).appendChild(hd);
    var body = tb.appendChild(el('tbody'));
    t.models.forEach(function (m) {
      var tr = el('tr'), name = el('td');
      name.appendChild(qAlias(m.alias)); tr.appendChild(name);
      var now = el('td'); now.appendChild(el('span', {class: 'chip ' + (qResting(m) ? 'warn' : 'ok')}, qResting(m) ? 'resting · ' + qWhen(m.until) : 'ready'));
      tr.appendChild(now);
      var n = m.ok + m.failed, calls = el('td', {class: 'q-calls', title: m.failed + ' failed'});
      calls.appendChild(qSegs(n ? Math.round(100 * m.ok / n) : null, 'answered'));
      calls.appendChild(el('span', {}, n ? m.ok + ' of ' + n + ' answered' : 'no calls'));
      tr.appendChild(calls);
      tr.appendChild(el('td', {}, m.last_ok ? qAgo(m.last_ok) : '—'));
      tr.appendChild(el('td', {}, m.limit || '—'));
      body.appendChild(tr);
    });
    list.appendChild(tb);
    $('q-models').showModal();
  }
  function qDefault(a, b) {
    return (b.out - a.out) || ((a.next_reset || '9999') < (b.next_reset || '9999') ? -1 : (a.next_reset || '9999') > (b.next_reset || '9999') ? 1 : 0)
           || (a.id < b.id ? -1 : 1);
  }
  function qDraw() {
    qBox.textContent = '';
    QD.tiles.forEach(function (t) { qBox.appendChild(qTile(t)); });
    if (!QD.tiles.length) qBox.appendChild(el('p', {class: 'muted'}, 'No model on the bench yet.'));
  }
  function qSaveOrder() {
    var ids = Array.prototype.map.call(qBox.querySelectorAll('.q-tile'), function (n) { return n.dataset.id; });
    var at = {}; ids.forEach(function (id, i) { at[id] = i; });
    QD.tiles.sort(function (a, b) { return at[a.id] - at[b.id]; });
    post('/api/quota/order', {order: ids}).then(function (r) {
      $('q-done').textContent = r.ok ? 'Order kept.' : ''; $('q-errors').textContent = r.ok ? '' : r.errors.join('; ');
    });
  }
  // Run each time the page arrives: its elements are new, so bound here.
  onLoad.quotas = function () {
    QD = JSON.parse($('quota-data').textContent); qBox = $('q-tiles');
    qDraw();
    $('q-models-close').addEventListener('click', function () { $('q-models').close(); });
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
    $('q-reorder').addEventListener('click', function () {
      QD.tiles.sort(qDefault); qDraw();
      post('/api/quota/order', {order: []}).then(function (r) { $('q-done').textContent = r.ok ? 'Sorted: out first, then the soonest reset.' : ''; });
    });
    $('q-all').addEventListener('click', function () {
      var live = QD.tiles.filter(function (t) { return t.refresh === 'live'; });
      $('q-done').textContent = 'Refreshing ' + live.length + '…';
      live.reduce(function (p, t) {
        return p.then(function () {
          var node = qBox.querySelector('.q-tile[data-id="' + CSS.escape(t.id) + '"]');
          return node ? qAct(t.id, node) : null;
        });
      }, Promise.resolve()).then(function () { $('q-done').textContent = 'Refreshed ' + live.length + '.'; });
    });
    drawCharts('quotas');
  };

  // The Quota page's two charts, in the theme's colours like the Benchmarks ones.
  function drawQuotaCharts(tok, style, width) {
    if (!QD) return;
    if (!window.Plot) {
      ['chart-sent', 'chart-resets'].forEach(function (id) {
        if ($(id)) $(id).textContent = 'Charts need Observable Plot from cdn.jsdelivr.net, which did not load.';
      });
      return;
    }
    var ink = tok('--ink', '#24331f'), good = tok('--ok', '#327a16'), warn = tok('--warn', '#7a4d00'), line = tok('--line', '#e4dfd2');
    var sent = $('chart-sent');
    if (sent) {
      var R = QD.sent.slice().sort(function (a, b) { return b.month - a.month; });
      sent.textContent = '';
      var lg = el('div', {class: 'q-legend'});
      [['This month, requests', 0.3], ['Last 24 hours, requests', 1]].forEach(function (k) {
        var s = el('span'), sw = el('i'); sw.style.background = good; sw.style.opacity = k[1];
        s.appendChild(sw); s.appendChild(document.createTextNode(k[0])); lg.appendChild(s);
      });
      sent.appendChild(lg);
      sent.appendChild(R.length ? Plot.plot({
        width: width(), height: 22 * R.length + 60, style: style, marginLeft: 120, marginRight: 90,
        x: {label: 'Requests →', grid: true}, y: {domain: R.map(function (r) { return r.provider; }), label: null},
        marks: [
          Plot.barX(R, {x: 'month', y: 'provider', fill: good, fillOpacity: 0.3,
            title: function (r) { return r.provider + '\nthis month: ' + qNum(r.month) + '\nlast 24 hours: ' + qNum(r.day); }}),
          Plot.barX(R, {x: 'day', y: 'provider', fill: good, insetTop: 4, insetBottom: 4}),
          Plot.text(R, {x: 'month', y: 'provider', text: function (r) { return qNum(r.day) + ' / ' + qNum(r.month); }, dx: 6, textAnchor: 'start', fill: ink}),
          Plot.ruleX([0], {stroke: ink})
        ]}) : el('p', {class: 'hint'}, 'No call in the ledger this month.'));
    }
    var res = $('chart-resets');
    if (res) {
      var now = Date.now(), end = now + 7 * 86400e3, dots = [], bars = [];
      QD.tiles.forEach(function (t) {
        var wins = [];
        t.groups.forEach(function (g) { g.windows.forEach(function (w) { wins.push({label: (g.label ? g.label + ' · ' : '') + w.label, at: w.reset_at}); }); });
        (t.caps || []).forEach(function (g) { g.windows.forEach(function (w) { wins.push({label: w.label + ' (' + g.label + ')', at: w.reset_at, daily: / day$/.test(w.label)}); }); });
        t.pool.forEach(function (p) { if (p.reset_at) wins.push({label: p.label, at: p.reset_at, daily: true}); });
        wins.forEach(function (w) {
          var at = +new Date(w.at), daily = w.daily || / day$/.test(w.label);
          for (; at && at <= end; at += 86400e3) {
            if (at >= now) dots.push({tile: t.name, at: new Date(at), what: w.label});
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
      res.textContent = '';
      res.appendChild(names.length ? Plot.plot({
        width: width(), height: 26 * names.length + 60, style: style, marginLeft: 120,
        x: {domain: [new Date(now), new Date(end)], label: null, grid: true}, y: {domain: names, label: null},
        marks: [
          Plot.barX(bars, {x1: 'x1', x2: 'x2', y: 'tile', fill: warn, fillOpacity: 0.35, insetTop: 6, insetBottom: 6, title: 'what'}),
          Plot.dot(dots, {x: 'at', y: 'tile', r: 4, fill: good, stroke: ink, strokeWidth: 0.5,
            title: function (d) { return d.what + ' resets ' + d.at.toLocaleString(); }}),
          Plot.ruleX([new Date(now)], {stroke: tok('--err', '#a12b2b'), strokeWidth: 1.5}),
          Plot.ruleY(names, {stroke: line, strokeOpacity: 0.5})
        ]}) : el('p', {class: 'hint'}, 'No reset or rest known for the next 7 days.'));
    }
  }

  // ---- Benchmarks charts: Observable Plot, data from serve.charts_data ------
  // A function, not run once: a theme switched on Settings redraws them in its
  // colours. `part` ('suite' or 'quotas'): only that page's, as it arrives.
  function drawCharts(part) {
    var holder = $('charts-data');
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
    if (part !== 'suite' && part !== 'uptime' && part !== 'trend') drawQuotaCharts(tok, style, width);
    if (!holder || part === 'quotas') return;
    var C = JSON.parse(holder.textContent);
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
      function n(k, what) { return k + ' ' + what + (k === 1 ? '' : 's'); }
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
        return r.key + ' · last ' + win + ' days\n' + s.says + '\nIn the window: ' + s.runsDown + ' of ' + n(s.runs, 'run')
          + ' below its earlier scores, ' + s.runsUp + ' above\nEach task, in the window against before:\n'
          + s.per.map(function (p) { return '  ' + p[0] + '  ' + pts(p[1]) + ' pts'; }).join('\n') + '\nLast scored ' + r.m.last;
      }
      function evidence(r) {
        var s = r.s;
        return n(s.tasks, 'task') + ': ' + s.down + ' down, ' + s.up + ' up · ' + n(s.runs, 'run') + ' vs ' + s.before + ' before';
      }
      // The name (at about 7 px a character, up to 260 px: a longer one loses its
      // middle, its hover keeps it whole), the bars (260 px at least), the
      // evidence. On a phone, no evidence (the table has it) and shorter names;
      // narrower still, the chart scrolls sideways.
      var nameW = Math.min(260, Math.max(150, 7 * Math.max.apply(null, M.map(function (r) { return r.key.length; })) + 24));
      var evW = 320;
      w = $('chart-trend').clientWidth || w;          // its own box: a phone's is narrower than width()'s 520
      if (w < nameW + evW + 260) { evW = 0; nameW = Math.min(nameW, 130); }
      var fits = Math.floor((nameW - 24) / 7), right = evW || 20, wide = w < nameW + right + 140;
      function cut(m) {
        var k = m.length - fits;
        return k <= 0 ? m : m.slice(0, Math.ceil((m.length - k - 1) / 2)) + '…' + m.slice(m.length - Math.floor((m.length - k - 1) / 2));
      }
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
            text: function (r) { return cut(r.key); }}),
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
        // Room for the longest "name  (n)" at about 7 px a character, up to 300 px; a
        // longer name loses its middle (its hover keeps it whole).
        function label(r) { return r.name + '  (' + r.n + ')'; }
        var left = Math.min(300, Math.max(160, 7 * Math.max.apply(null, R.map(function (r) { return label(r).length; })) + 10));
        var right = 70, cellW = (w - left - right) / win, fits = Math.floor((left - 10) / 7);
        return Plot.plot({
          width: w, height: 22 * R.length + 50, style: style, marginLeft: left, marginRight: right, marginTop: 28,
          padding: 0.12,
          x: {domain: days, axis: 'top', label: null, tickSize: 0, tickFormat: function (d) {
            var i = days.indexOf(d);           // 30 days: every fifth, counted back from today
            return win > 7 && (win - 1 - i) % 5 ? '' : day(d).toLocaleDateString(undefined,
              win > 7 ? {day: 'numeric', month: 'short'} : cellW < 48 ? {day: 'numeric'} : {weekday: 'short', day: 'numeric'});
          }},
          y: {domain: R.map(function (r) { return r.name; }), label: null, tickSize: 0, tickFormat: function (m) {
            var s = label(by[m]), cut = s.length - fits;
            return cut <= 0 ? s : m.slice(0, Math.ceil((m.length - cut - 1) / 2)) + '…'
              + m.slice(m.length - Math.floor((m.length - cut - 1) / 2)) + '  (' + by[m].n + ')';
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
      put('chart-uptime-providers', function (w) { return uptime(U.providers, w); });
      put('chart-uptime-models', function (w) { return uptime(U.models, w); });
    }
    if (part === 'uptime') return;
    // Decision models: each model's answers grouped by the probability it gave
    // them (x) against how often they were right (y), a panel per set
    // (serve.decision_data). On the diagonal, its 80% answers come true 80% of
    // the time; under it, it is surer than it should be. Panels stack on a phone.
    var DC = C.decide;
    if (DC) put('chart-decide', function (w) {
      if (!DC.points.length) return el('p', {class: 'hint'}, 'No decision model has answered a benchmark question yet.');
      var names = {};
      DC.sets.forEach(function (s) { names[s[0]] = s[1]; });
      var panels = DC.sets.map(function (s) { return s[1]; });
      var pts = DC.points.map(function (p) { return Object.assign({panel: names[p.set]}, p); })
        .sort(function (a, b) { return a.said - b.said; });
      var models = pts.map(function (p) { return p.model; })
        .filter(function (m, i, all) { return all.indexOf(m) === i; }).sort();
      var diag = [];
      panels.forEach(function (s) { diag.push({panel: s, x: 0, y: 0}, {panel: s, x: 1, y: 1}); });
      function tip(p) {
        return p.model + ' · ' + names[p.set] + '\n' + p.n + ' answer' + (p.n === 1 ? '' : 's') + ' it was about '
          + Math.round(p.said * 100) + '% sure of\n' + Math.round(p.right * 100) + '% of them right';
      }
      var narrow = w < 700, f = narrow ? 'fy' : 'fx', opts = {}, by = {};
      opts[f] = {domain: panels, label: null};
      by[f] = 'panel';
      return Plot.plot(Object.assign(opts, {
        width: narrow ? Math.min(w, 520) : w, height: narrow ? 3 * 230 + 70 : 320, style: style,
        marginLeft: 52, marginRight: narrow ? 90 : 20, marginBottom: 44, marginTop: 28,
        x: {domain: [0, 1], label: 'Probability it gave its answer →', tickFormat: '.0%', ticks: 5, grid: true},
        y: {domain: [0, 1], label: '↑ Share right', tickFormat: '.0%', ticks: 5, grid: true},
        r: {range: [3, 13]},
        color: {legend: true, domain: models, range: [sky, good, signal, warn, muted, ink, err]},
        marks: [
          Plot.frame({stroke: line}),
          Plot.line(diag, Object.assign({x: 'x', y: 'y', z: 'panel', stroke: muted, strokeDasharray: '4 3'}, by)),
          Plot.line(pts, Object.assign({x: 'said', y: 'right', z: 'model', stroke: 'model', strokeOpacity: 0.55}, by)),
          Plot.dot(pts, Object.assign({x: 'said', y: 'right', r: 'n', fill: 'model', stroke: ink, strokeWidth: 0.5,
                                       title: tip}, by))
        ]
      }));
    });
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
      return Plot.plot({
        // The group names tilted: a narrow group (a few tables) would run into its neighbour's.
        width: w, height: 22 * models.length + 140, style: style, marginLeft: 300, marginTop: 80, padding: 0.08,
        x: {domain: keys, axis: 'top', label: null, tickSize: 0, tickRotate: -60, textAnchor: 'start',
            tickFormat: function (k) { return head[k] || ''; }},
        y: {domain: models, label: null, tickSize: 0,
            tickFormat: function (m) { return m + '  (' + S[m].right + '/' + S[m].fields + ')'; }},
        color: {legend: true, domain: V.states, range: V.states.map(function (s) { return colour[s]; })},
        marks: [
          Plot.cell(V.heat, {x: 'key', y: 'model', fill: 'state', title: tip}),
          Plot.dot(V.heat.filter(function (d) { return d.trap; }), {x: 'key', y: 'model', r: 1.8, fill: ink, fillOpacity: 0.55}),
          Plot.ruleX(Object.keys(head), {x: function (k) { return k; }, stroke: ink, strokeOpacity: 0.25, dx: -2})
        ]
      });
    });

    // Vision, real work: each model's seats on image panels by how they were judged.
    put('chart-vision-work', function (w) {
      var W = C.vision.work, kinds = C.vision.seatKinds, data = [], total = {};
      Object.keys(W).forEach(function (m) {
        kinds.forEach(function (k) { if (W[m][k]) data.push({model: m, kind: k, count: W[m][k]}); total[m] = (total[m] || 0) + W[m][k]; });
      });
      if (!data.length) return el('p', {class: 'hint'}, 'No vision panels through the gateway yet.');
      // The models doing the most image work first: their bars are the evidence.
      var models = Object.keys(total).sort(function (a, b) { return total[b] - total[a] || (a < b ? -1 : 1); });
      var colour = {'checked, helped': good, 'checked, did not help': err, 'useful, no claims': sky,
                    'no use, no claims': warn, 'not judged': line, 'no answer': muted};
      // The claims checked by hand, beside the bar: the only right-or-wrong count real work has.
      var claims = models.filter(function (m) { return W[m].real + W[m].false; }).map(function (m) {
        return {model: m, text: W[m].real + ' of ' + (W[m].real + W[m].false) + ' claims right'};
      });
      return Plot.plot({
        width: w, height: rows(models.length), style: style, marginLeft: 300, marginRight: 150,
        x: {label: 'Share of its seats on image panels →', tickFormat: '.0%', grid: true, domain: [0, 1]},
        y: {domain: models, label: null, tickFormat: function (m) { return m + '  (' + total[m] + ')'; }},
        color: {legend: true, domain: kinds, range: kinds.map(function (k) { return colour[k]; })},
        marks: [
          Plot.barX(data, Plot.stackX({x: 'count', y: 'model', fill: 'kind', offset: 'normalize', order: kinds,
            title: function (d) { return d.model + ': ' + d.kind + ', ' + d.count + ' of ' + total[d.model] + ' seats'; }})),
          Plot.text(claims, {x: 1, y: 'model', text: 'text', dx: 8, textAnchor: 'start', fill: ink})
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
  onLoad.suite = function () { drawCharts('suite'); };
  // Uptime's days: the tables and the list for that window shown, its charts drawn again.
  document.addEventListener('change', function (ev) {
    var s = ev.target;
    if (!s.matches('select[data-uptime-days]')) return;
    s.closest('.panel').querySelectorAll('[data-uptime-window]').forEach(function (d) {
      d.hidden = d.getAttribute('data-uptime-window') !== s.value;
    });
    drawCharts('uptime');
  });
  // Trend's window: that window's table shown, its chart drawn again.
  document.addEventListener('change', function (ev) {
    var s = ev.target;
    if (!s.matches('select[data-trend-days]')) return;
    s.closest('.panel').querySelectorAll('[data-trend-window]').forEach(function (d) {
      d.hidden = d.getAttribute('data-trend-window') !== s.value;
    });
    drawCharts('trend');
  });
})();
