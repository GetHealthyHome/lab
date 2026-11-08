/**
 * Pure helpers for the dashboard: no Google services in here, so `node --test dashboard/test` can run them.
 * Rows are plain objects keyed by the Sheet's header names (see toObjects).
 */

var DEFAULT_SETTINGS = [
  // key, default, what it does (shown on the Settings page)
  ['loan_hours', '4', 'Standard loan length, in hours (K16)'],
  ['lab_close', '21:00', 'Lab closing time, 24-hour clock, kiosk local time (K16)'],
  ['grace_minutes', '15', 'Minutes after the due time before a tool counts as overdue (K16)'],
  ['hold_overdue', 'yes', 'Stop a student with overdue tools from borrowing more (K16)'],
  ['require_pin', 'yes', 'Ask for a 4-digit PIN after the ID scan (K11)'],
  ['snapshot_interval_hours', '1', 'Hours between scheduled cage snapshots (C1)'],
  ['alert_email', '', 'Instructor on duty: gets an email when a missing-tool alert opens. Commas for several (D9)'],
  ['digest_email', '', 'Who gets the weekday morning digest. Commas for several (D10)'],
  ['digest_hour', '7', 'Hour of the morning digest, Monday to Friday (D10)'],
];

var OPEN_ALERT = ['open', 'acknowledged'];

function toObjects(values) {
  if (!values || !values.length) return [];
  var header = values[0].map(function (h) { return String(h).trim(); });
  var out = [];
  for (var i = 1; i < values.length; i++) {
    var row = values[i], o = { _row: i + 1 }, filled = false;
    header.forEach(function (h, c) {
      if (!h) return;
      var v = row[c] === undefined || row[c] === null ? '' : row[c];
      o[h] = v;
      if (v !== '') filled = true;
    });
    if (filled) out.push(o);  // skip blank rows staff leave between entries
  }
  return out;
}

function ms(v) {
  if (v === '' || v === null || v === undefined) return NaN;
  return v instanceof Date ? v.getTime() : Date.parse(String(v));
}

function truthy(v) {
  return ['false', '0', 'no', 'n', 'off', ''].indexOf(String(v).trim().toLowerCase()) < 0;
}

function indexBy(rows, key) {
  var m = {};
  rows.forEach(function (r) { m[String(r[key])] = r; });
  return m;
}

function settingsMap(rows) {
  var m = {};
  DEFAULT_SETTINGS.forEach(function (s) { m[s[0]] = s[1]; });
  rows.forEach(function (r) { if (String(r.key).trim()) m[String(r.key).trim()] = String(r.value); });
  return m;
}

/** Drive file id from a webViewLink, so the page can show a thumbnail. Local Pi paths give null. */
function driveId(link) {
  var m = /\/d\/([\w-]{10,})/.exec(String(link)) || /[?&]id=([\w-]{10,})/.exec(String(link));
  return m ? m[1] : null;
}

/** Latest transaction per asset. Order by timestamp, then Sheet row, so a late offline sync can't jump ahead. */
function latestTxn(txns) {
  var last = {};
  txns.forEach(function (t) {
    var cur = last[t.asset_id];
    if (!cur || ms(t.ts) > ms(cur.ts) || (ms(t.ts) === ms(cur.ts) && t._row > cur._row)) last[t.asset_id] = t;
  });
  return last;
}

/** txn_id -> newest extended due time (K16: instructors can extend a loan to a set date). */
function extensionsByTxn(extensions) {
  var m = {};
  extensions.forEach(function (e) {
    var cur = m[e.txn_id];
    if (!cur || ms(e.ts) >= ms(cur.ts)) m[e.txn_id] = e;
  });
  var out = {};
  Object.keys(m).forEach(function (k) { out[k] = m[k].due_at; });
  return out;
}

/** D1: every tool out right now, who has it, for how long, and whether it's overdue. Overdue first. */
function toolsOut(d, now) {
  var people = indexBy(d.people, 'person_id');
  var last = latestTxn(d.txns);
  var ext = extensionsByTxn(d.extensions || []);
  var grace = (parseFloat(settingsMap(d.settings || []).grace_minutes) || 0) * 60000;
  var out = [];
  d.tools.forEach(function (t) {
    var tx = last[t.asset_id];
    if (!tx || tx.action !== 'out') return;
    var due = ext[tx.txn_id] || tx.due_at || '';
    var p = people[tx.person_id];
    out.push({
      asset_id: t.asset_id, name: t.name, person_id: tx.person_id, person: p ? p.name : tx.person_id,
      since: tx.ts, minutes_out: Math.max(0, Math.round((now - ms(tx.ts)) / 60000)),
      due_at: due, overdue: !isNaN(ms(due)) && now > ms(due) + grace,
      gate_photo: tx.gate_photo, txn_id: tx.txn_id, battery: tx.paired_battery_id || '', kit_id: tx.kit_id || '',
    });
  });
  out.sort(function (a, b) { return (b.overdue - a.overdue) || (ms(a.since) - ms(b.since)); });
  return out;
}

/** Tools.status in the PRD's words: out and missing are derived from transactions and alerts, never typed. */
function toolStatus(tool, last, openAlerts) {
  var s = String(tool.status || 'available').toLowerCase();
  if (s === 'retired' || s === 'quarantined') return s;
  var tx = last[tool.asset_id];
  if (tx && tx.action === 'out') return 'out';
  if (openAlerts.some(function (a) { return a.asset_id === tool.asset_id && a.type === 'missing_tool'; })) return 'missing';
  return 'available';
}

function openAlerts(alerts) {
  return alerts.filter(function (a) { return OPEN_ALERT.indexOf(String(a.status || 'open').toLowerCase()) >= 0; });
}

/** D2: open alerts first (newest first), then the 50 most recent closed ones. */
function alertQueue(alerts) {
  var newest = function (a, b) { return ms(b.opened) - ms(a.opened); };
  var open = openAlerts(alerts).sort(newest);
  var closed = alerts.filter(function (a) { return open.indexOf(a) < 0; }).sort(newest).slice(0, 50);
  return { open: open, closed: closed };
}

/** D3: search by student or tool; full history for every match, newest first. */
function search(d, q) {
  q = String(q || '').trim().toLowerCase();
  if (!q) return { people: [], tools: [], history: [] };
  var hit = function (r, fields) { return fields.some(function (f) { return String(r[f] || '').toLowerCase().indexOf(q) >= 0; }); };
  var people = d.people.filter(function (p) { return hit(p, ['person_id', 'id_code', 'name', 'email']); });
  var tools = d.tools.filter(function (t) { return hit(t, ['asset_id', 'qr_code', 'name', 'serial']); });
  var pIds = indexBy(people, 'person_id'), tIds = indexBy(tools, 'asset_id');
  var pAll = indexBy(d.people, 'person_id'), tAll = indexBy(d.tools, 'asset_id');
  var history = d.txns.filter(function (t) { return pIds[t.person_id] || tIds[t.asset_id]; }).map(function (t) {
    return {
      ts: t.ts, action: t.action, asset_id: t.asset_id, tool: (tAll[t.asset_id] || {}).name || t.asset_id,
      person: (pAll[t.person_id] || {}).name || t.person_id, gate_photo: t.gate_photo,
      override_by: t.override_by ? ((pAll[t.override_by] || {}).name || t.override_by) : '', kiosk_id: t.kiosk_id,
    };
  }).sort(function (a, b) { return ms(b.ts) - ms(a.ts); });
  var damage = (d.damage || []).filter(function (r) { return tIds[r.asset_id] || pIds[r.reported_by]; });
  return { people: people, tools: tools, history: history, damage: damage };
}

/** D4: one line per spot, pre-filled with the camera's latest view; `ok` is the suggested answer. */
function reconcileRows(d) {
  var last = latestTxn(d.txns);
  var people = indexBy(d.people, 'person_id');
  var snap = d.snapshots.slice().sort(function (a, b) { return ms(b.ts) - ms(a.ts); })[0];
  var seen = {};
  try { seen = snap ? JSON.parse(snap.spots || '{}') : {}; } catch (e) { seen = {}; }
  var byspot = {};
  d.tools.forEach(function (t) { if (t.spot_id && String(t.status).toLowerCase() !== 'retired') byspot[t.spot_id] = t; });
  var open = openAlerts(d.alerts);
  return d.spots.map(function (s) {
    var t = byspot[s.spot_id];
    var status = t ? toolStatus(t, last, open) : 'no tool';
    var tx = t && last[t.asset_id];
    var holder = status === 'out' && tx ? ' (' + ((people[tx.person_id] || {}).name || tx.person_id) + ')' : '';
    var camera = seen[s.spot_id] || '';
    var expectEmpty = status !== 'available' && status !== 'missing';
    var ok = camera === '' ? null : camera === (expectEmpty ? 'empty' : 'full');
    return {
      spot_id: s.spot_id, label: s.label, asset_id: t ? t.asset_id : '', tool: t ? t.name : '',
      record: status + holder, camera: camera, ok: ok,
    };
  });
}

/** Reconciliation row from the instructor's answers: [{spot_id, ok, note}]. */
function signOffRow(entries, instructor, now, date) {
  var bad = entries.filter(function (e) { return !e.ok; });
  return {
    date: date, instructor: instructor, spots_confirmed: entries.length - bad.length,
    exceptions: bad.map(function (e) { return e.spot_id + ': ' + (e.note || 'not as recorded'); }).join('; '),
    signed_at: new Date(now).toISOString(), details: JSON.stringify(entries),
  };
}

function sameDay(v, date, tzOffsetMs) {
  var t = ms(v);
  return !isNaN(t) && new Date(t + tzOffsetMs).toISOString().slice(0, 10) === date;
}

/** D5: what went wrong today, for the printable end-of-day report. date is YYYY-MM-DD in lab time. */
function exceptionsReport(d, now, date, tzOffsetMs) {
  var out = toolsOut(d, now);
  var people = indexBy(d.people, 'person_id');
  var tools = indexBy(d.tools, 'asset_id');
  var name = function (id) { return (people[id] || {}).name || id || ''; };
  var rec = (d.reconciliations || []).filter(function (r) { return String(r.date).slice(0, 10) === date; }).pop() || null;
  return {
    date: date,
    out: out,
    overdue: out.filter(function (o) { return o.overdue; }),
    alerts: openAlerts(d.alerts).concat(d.alerts.filter(function (a) {
      return OPEN_ALERT.indexOf(String(a.status).toLowerCase()) < 0 && sameDay(a.opened, date, tzOffsetMs);
    })),
    damage: (d.damage || []).filter(function (r) { return sameDay(r.opened, date, tzOffsetMs); }).map(function (r) {
      return Object.assign({}, r, { tool: (tools[r.asset_id] || {}).name || r.asset_id, reporter: name(r.reported_by) });
    }),
    quarantined: d.tools.filter(function (t) { return String(t.status).toLowerCase() === 'quarantined'; }),
    reconciliation: rec,
  };
}

/** D11: quarantined tools with the damage reports that put them there, newest report first. */
function quarantineList(d) {
  var people = indexBy(d.people, 'person_id');
  return d.tools.filter(function (t) { return String(t.status).toLowerCase() === 'quarantined'; }).map(function (t) {
    var reports = (d.damage || []).filter(function (r) { return r.asset_id === t.asset_id && !r.resolved_at; })
      .sort(function (a, b) { return ms(b.opened) - ms(a.opened); })
      .map(function (r) { return Object.assign({}, r, { reporter: (people[r.reported_by] || {}).name || r.reported_by }); });
    return { asset_id: t.asset_id, name: t.name, reports: reports };
  });
}

/** D12: flagged labels not printed yet, one per asset (the newest flag wins). */
function reprintQueue(d) {
  var tools = indexBy(d.tools, 'asset_id');
  var seen = {};
  return (d.reprints || []).filter(function (r) { return !truthy(r.printed); }).reverse().filter(function (r) {
    if (seen[r.asset_id]) return false;
    seen[r.asset_id] = true;
    return true;
  }).map(function (r) {
    var t = tools[r.asset_id] || {};
    return { reprint_id: r.reprint_id, asset_id: r.asset_id, name: t.name || '', qr_code: t.qr_code || '',
      spot_id: t.spot_id || '', condition: r.condition, flagged_at: r.flagged_at };
  });
}

function csv(rows, cols) {
  var cell = function (v) { v = String(v === undefined ? '' : v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
  return [cols.join(',')].concat(rows.map(function (r) { return cols.map(function (c) { return cell(r[c]); }).join(','); })).join('\n');
}

/** Spot a value that a cell can't take: keeps typos like "quarantine" out of the Sheet. Returns an error or ''. */
function checkValue(schema, field, value) {
  var allowed = (schema.choices || {})[field];
  if (allowed && value !== '' && allowed.indexOf(String(value)) < 0) return field + ' must be one of: ' + allowed.join(', ');
  if (field === 'box' && value !== '') {
    try {
      var b = JSON.parse(value);
      if (!(b instanceof Array) || b.length !== 4 || b.some(function (n) { return typeof n !== 'number' || n < 0; })) throw 0;
    } catch (e) { return 'box must look like [x, y, width, height]'; }
  }
  return '';
}
