/**
 * Lab Toolbox staff dashboard: an Apps Script web app bound to the Lab Toolbox Sheet.
 * The Pi writes the logbook (Transactions, Snapshots, Alerts...); this page reads it and writes staff answers
 * (alert status, sign-offs, tool and people edits), which the Pi pulls back down. Every edit lands in AuditLog.
 */

// Tabs staff edit here. key: the column that names a row. choices: allowed values. Supervisors only unless noted.
var SCHEMA = {
  Tools: { key: 'asset_id', cols: ['asset_id', 'qr_code', 'name', 'category', 'spot_id', 'status', 'notes', 'serial', 'brand',
    'model', 'needs_battery', 'battery_group_id', 'is_battery'],
    choices: { status: ['available', 'quarantined', 'retired'] } },
  People: { key: 'person_id', cols: ['person_id', 'id_code', 'name', 'role', 'email', 'active'],
    choices: { role: ['student', 'instructor', 'supervisor'] } },
  Spots: { key: 'spot_id', cols: ['spot_id', 'label', 'box', 'reference_photo'] },
  BatteryGroups: { key: 'group_id', cols: ['group_id', 'name'] },
  Kits: { key: 'kit_id', cols: ['kit_id', 'case_code', 'name', 'spot_id', 'check_mode', 'status'],
    choices: { check_mode: ['standard', 'verified'], status: ['available', 'out', 'incomplete'] } },
  KitItems: { key: null, cols: ['kit_id', 'asset_id'], deletable: true },
};

// Tabs the dashboard writes that the Pi never does, so their headers may need creating here.
var OWN_TABS = {
  Reconciliations: ['date', 'instructor', 'spots_confirmed', 'exceptions', 'signed_at', 'details'],
  AuditLog: ['ts', 'who', 'tab', 'row', 'field', 'old_value', 'new_value', 'reason'],
  Settings: ['key', 'value'],
};

/**
 * Run once from the Apps Script editor (and again after adding a column): gives the fixed-choice columns a dropdown,
 * so nobody has to remember that an admin's role is spelled "supervisor".
 */
function addDropdowns() {
  var yesNo = { People: ['active'], Tools: ['needs_battery', 'is_battery'] }, done = [];
  Object.keys(SCHEMA).forEach(function (tab) {
    var s = sheet_(tab);
    if (!s || !s.getLastColumn() || s.getMaxRows() < 2) return;
    var header = s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0].map(function (h) { return String(h).trim(); });
    var lists = Object.assign({}, SCHEMA[tab].choices);
    (yesNo[tab] || []).forEach(function (c) { lists[c] = ['yes', 'no']; });
    Object.keys(lists).forEach(function (col) {
      var i = header.indexOf(col);
      if (i < 0) return;
      // Warn, don't refuse: the Pi and this page write these cells too, and a refused write would stop them.
      var rule = SpreadsheetApp.newDataValidation().requireValueInList(lists[col], true).setAllowInvalid(true).build();
      s.getRange(2, i + 1, s.getMaxRows() - 1, 1).setDataValidation(rule);
      done.push(tab + '.' + col);
    });
  });
  console.log('Dropdowns added: ' + (done.join(', ') || 'none (no matching column headers yet)'));
  return done;
}

function doGet() {
  return HtmlService.createTemplateFromFile('Index').evaluate()
    .setTitle('Lab Toolbox')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** The page's only door: google.script.run.api(name, args). Checks who is asking before anything runs. */
function api(name, args) {
  var me = currentStaff_();
  var fn = API[name];
  if (!fn) throw new Error('Unknown action: ' + name);
  if (fn.supervisor && me.role !== 'supervisor') throw new Error('Only supervisors can do that.');
  var result = fn.write ? withLock_(function () { return fn.run(me, args || {}); }) : fn.run(me, args || {});
  return JSON.parse(JSON.stringify({ me: me, data: result }));  // Dates -> strings; google.script.run can't return Dates
}

var API = {
  home: { run: function (me) {
    var d = load_(['tools', 'txns', 'people', 'extensions', 'alerts', 'settings', 'health', 'snapshots']);
    return { out: toolsOut(d, Date.now()), open_alerts: openAlerts(d.alerts).length, health: d.health,
      last_snapshot: d.snapshots.reduce(function (m, s) { return ms(s.ts) > ms(m) ? s.ts : m; }, '') };
  } },

  alerts: { run: function () { return alertQueue(read_('Alerts')); } },

  alertAction: { write: true, run: function (me, a) {  // D2
    if (['acknowledged', 'resolved', 'false_alarm'].indexOf(a.status) < 0) throw new Error('Pick acknowledge, resolve or false alarm.');
    if (a.status !== 'acknowledged' && !String(a.note || '').trim()) throw new Error('Add a note saying what happened.');
    var changes = { status: a.status, resolution_note: a.note || '' };
    if (a.status !== 'acknowledged') changes.resolved_by = me.person_id || me.email;
    update_('Alerts', 'alert_id', a.alert_id, changes, me, 'Alert ' + a.status.replace('_', ' ') + ': ' + (a.note || ''));
    return alertQueue(read_('Alerts'));
  } },

  // D7 correction for "returned without scanning": a return record the Pi pulls down, so its records agree again.
  markReturned: { write: true, run: function (me, a) {
    if (!String(a.reason || '').trim()) throw new Error('Say why (for the audit log).');
    var d = load_(['tools', 'txns']);
    var tx = latestTxn(d.txns)[a.asset_id];
    if (!tx || tx.action !== 'out') throw new Error(a.asset_id + ' is not checked out.');
    var txn = { txn_id: Utilities.getUuid().replace(/-/g, ''), ts: new Date().toISOString(), asset_id: a.asset_id,
      person_id: tx.person_id, action: 'in', session_id: 'dashboard-' + Date.now(), gate_photo: '',
      override_by: me.person_id || me.email, kiosk_id: 'dashboard' };
    append_('Transactions', txn);
    audit_(me, 'Transactions', txn.txn_id, 'action', 'out', 'in', a.reason);
    return { ok: true };
  } },

  search: { run: function (me, a) {
    return search(load_(['tools', 'txns', 'people', 'damage']), a.q);
  } },

  reconcile: { run: function () {  // D4
    var d = load_(['tools', 'txns', 'people', 'spots', 'snapshots', 'alerts', 'reconciliations']);
    return { rows: reconcileRows(d), today: today_(), signed: d.reconciliations.filter(function (r) {
      return String(r.date).slice(0, 10) === today_(); }) };
  } },

  signOff: { write: true, run: function (me, a) {
    if (!a.entries || !a.entries.length) throw new Error('Nothing to sign off.');
    var missingNote = a.entries.filter(function (e) { return !e.ok && !String(e.note || '').trim(); });
    if (missingNote.length) throw new Error('Add a note for ' + missingNote.map(function (e) { return e.spot_id; }).join(', '));
    var row = signOffRow(a.entries, me.name, Date.now(), today_());
    append_('Reconciliations', row);
    return row;
  } },

  report: { run: function (me, a) {  // D5
    var d = load_(['tools', 'txns', 'people', 'extensions', 'alerts', 'damage', 'reconciliations', 'settings']);
    return exceptionsReport(d, Date.now(), a.date || today_(), tzOffset_());
  } },

  quarantine: { run: function () { return quarantineList(load_(['tools', 'damage', 'people'])); } },

  triage: { write: true, run: function (me, a) {  // D11: release or retire, closing its damage reports
    if (['release', 'retire'].indexOf(a.outcome) < 0) throw new Error('Pick release or retire.');
    if (!String(a.note || '').trim()) throw new Error('Add a note saying what you found.');
    var status = a.outcome === 'release' ? 'available' : 'retired';
    update_('Tools', 'asset_id', a.asset_id, { status: status }, me, 'Quarantine ' + a.outcome + ': ' + a.note);
    var now = new Date().toISOString();
    read_('DamageReports').forEach(function (r) {
      if (r.asset_id === a.asset_id && !r.resolved_at) {
        update_('DamageReports', 'report_id', r.report_id,
          { resolved_by: me.person_id || me.email, resolution: a.outcome + ': ' + a.note, resolved_at: now }, me, 'Quarantine triage');
      }
    });
    return quarantineList(load_(['tools', 'damage', 'people']));
  } },

  reprints: { run: function () { return reprintQueue(load_(['tools', 'reprints'])); } },

  printBatch: { write: true, run: function (me, a) {  // D12: one batch file for the label printer, then marked printed
    var queue = reprintQueue(load_(['tools', 'reprints']));
    var pick = queue.filter(function (r) { return !a.asset_ids || a.asset_ids.indexOf(r.asset_id) >= 0; });
    if (!pick.length) throw new Error('No labels waiting.');
    var ids = indexBy(pick, 'asset_id'), now = new Date().toISOString();
    read_('ReprintQueue').forEach(function (r) {
      if (ids[r.asset_id] && !truthy(r.printed)) update_('ReprintQueue', 'reprint_id', r.reprint_id, { printed: 'yes', printed_at: now }, me, 'Label reprinted');
    });
    return { csv: csv(pick, ['asset_id', 'qr_code', 'name', 'spot_id']), count: pick.length };
  } },

  records: { run: function (me, a) {  // D6, D13: the tables staff manage
    var s = SCHEMA[a.tab];
    if (!s) throw new Error('Unknown tab ' + a.tab);
    var rows = read_(a.tab);
    if (a.tab === 'People') rows.forEach(function (r) { r.has_pin = !!String(r.pin_hash || ''); delete r.pin_hash; });
    return { tab: a.tab, key: s.key, cols: s.cols, choices: s.choices || {}, deletable: !!s.deletable, rows: rows };
  } },

  saveRecord: { write: true, supervisor: true, run: function (me, a) {  // D6, D7, D13
    var s = SCHEMA[a.tab];
    if (!s) throw new Error('Unknown tab ' + a.tab);
    var values = {};
    s.cols.forEach(function (c) { if (a.values[c] !== undefined) values[c] = String(a.values[c]).trim(); });
    var errors = s.cols.map(function (c) { return values[c] === undefined ? '' : checkValue(s, c, values[c]); }).filter(String);
    if (errors.length) throw new Error(errors.join('. '));
    if (a.isNew) {
      if (s.key && !values[s.key]) throw new Error(s.key + ' is required.');
      if (s.key && read_(a.tab).some(function (r) { return String(r[s.key]) === values[s.key]; })) throw new Error(values[s.key] + ' already exists.');
      append_(a.tab, values);
      audit_(me, a.tab, s.key ? values[s.key] : JSON.stringify(values), '(new row)', '', JSON.stringify(values), a.reason || 'Added');
    } else {
      if (!String(a.reason || '').trim()) throw new Error('Say why you are changing it (for the audit log).');
      delete values[s.key];  // the key names the row; renaming it would orphan its history
      update_(a.tab, s.key, a.id, values, me, a.reason);
    }
    return API.records.run(me, { tab: a.tab });
  } },

  deleteRecord: { write: true, supervisor: true, run: function (me, a) {  // only KitItems: the Pi never writes it, so rows can't shift under it
    var s = SCHEMA[a.tab];
    if (!s || !s.deletable) throw new Error('Rows in ' + a.tab + ' are retired, not deleted.');
    var sh = sheet_(a.tab), values = sh.getDataRange().getValues(), h = values[0].map(String);
    for (var i = values.length - 1; i >= 1; i--) {
      if (s.cols.every(function (c) { return String(values[i][h.indexOf(c)]) === String(a.values[c]); })) {
        sh.deleteRow(i + 1);
        audit_(me, a.tab, JSON.stringify(a.values), '(row deleted)', JSON.stringify(a.values), '', a.reason || 'Removed');
        return API.records.run(me, { tab: a.tab });
      }
    }
    throw new Error('Row not found; reload and try again.');
  } },

  resetPin: { write: true, supervisor: true, run: function (me, a) {  // D6: the person picks a new PIN at the kiosk
    if (!String(a.reason || '').trim()) throw new Error('Say why (for the audit log).');
    update_('People', 'person_id', a.person_id, { pin_hash: '' }, me, 'PIN reset: ' + a.reason, true);
    return API.records.run(me, { tab: 'People' });
  } },

  settings: { run: function () {
    var m = settingsMap(read_('Settings'));
    return DEFAULT_SETTINGS.map(function (s) { return { key: s[0], value: m[s[0]], help: s[2], options: s[3] }; });
  } },

  saveSettings: { write: true, supervisor: true, run: function (me, a) {  // D14 and friends
    var current = settingsMap(read_('Settings'));
    Object.keys(a.values).forEach(function (k) {
      var v = String(a.values[k]).trim();
      if (!DEFAULT_SETTINGS.some(function (s) { return s[0] === k; }) || v === String(current[k])) return;
      if (/_(hours|minutes|hour)$/.test(k) && !(parseFloat(v) >= 0)) throw new Error(k + ' must be a number.');
      var opts = DEFAULT_SETTINGS.filter(function (s) { return s[0] === k; })[0][3];
      if (opts && opts.indexOf(v) < 0) throw new Error(k + ' must be ' + opts.join(' or ') + '.');
      if (k === 'lab_close' && !/^([01]?\d|2[0-3]):[0-5]\d$/.test(v)) throw new Error('lab_close must look like 21:00.');
      setSetting_(k, v, me, 'Settings page');
    });
    return API.settings.run(me);
  } },

  calibration: { supervisor: true, run: function () {  // C2
    var newest = function (x, y) { return ms(y.ts) - ms(x.ts); };
    return { references: read_('References').sort(newest).slice(0, 5), spots: read_('Spots'),
      tools: read_('Tools').filter(function (t) { return String(t.status).toLowerCase() !== 'retired'; })
        .map(function (t) { return { asset_id: t.asset_id, name: t.name, spot_id: t.spot_id }; }),
      request: settingsMap(read_('Settings')).reference_request || '' };
  } },

  requestReference: { write: true, supervisor: true, run: function (me) {
    setSetting_('reference_request', new Date().toISOString(), me, 'Reference photo requested for calibration');
    return { ok: true };
  } },

  // C2: boxes drawn on one reference photo, each spot linked to its tool. a: {reference_photo, spots: [{spot_id, box, asset_id}]}
  saveCalibration: { write: true, supervisor: true, run: function (me, a) {
    if (!driveId(a.reference_photo)) throw new Error('Pick a reference photo first.');
    var tools = read_('Tools');
    a.spots.forEach(function (s) {
      var box = s.box ? JSON.stringify(s.box.map(Math.round)) : '';
      var err = checkValue({}, 'box', box);
      if (err) throw new Error(s.spot_id + ': ' + err);
      update_('Spots', 'spot_id', s.spot_id, box ? { box: box, reference_photo: a.reference_photo } : { box: '' }, me, 'Calibration');
      if (!s.asset_id) return;
      tools.forEach(function (t) {  // one tool per spot: the tool that used to live here loses the spot
        if (t.spot_id === s.spot_id && t.asset_id !== s.asset_id) update_('Tools', 'asset_id', t.asset_id, { spot_id: '' }, me, 'Calibration: spot given to ' + s.asset_id);
      });
      update_('Tools', 'asset_id', s.asset_id, { spot_id: s.spot_id }, me, 'Calibration');
    });
    return API.calibration.run(me);
  } },

  installEmails: { supervisor: true, run: function () { installTriggers(); return { ok: true }; } },  // D9, D10 (Mail.gs)

  testDigest: { supervisor: true, run: function () {  // "Send a digest now" on the Settings page
    var m = morningDigest(true);
    if (!m) throw new Error('Add an address to digest_email first.');
    return { subject: m.subject };
  } },

  health: { run: function () {  // D8
    var snaps = read_('Snapshots').sort(function (a, b) { return ms(b.ts) - ms(a.ts); });
    return { health: read_('Health'), snapshots: snaps.slice(0, 10), request: settingsMap(read_('Settings')).snapshot_request || '' };
  } },

  requestSnapshot: { write: true, run: function (me) {  // C1 on demand: the Pi sees the new value on its next pull
    setSetting_('snapshot_request', new Date().toISOString(), me, 'Snapshot requested from dashboard');
    return { ok: true };
  } },
};

// -- Sheet access --------------------------------------------------------------------------------

var LOADERS = {
  tools: 'Tools', txns: 'Transactions', people: 'People', extensions: 'Extensions', alerts: 'Alerts', settings: 'Settings',
  health: 'Health', snapshots: 'Snapshots', spots: 'Spots', reconciliations: 'Reconciliations', damage: 'DamageReports',
  reprints: 'ReprintQueue', kits: 'Kits', kitItems: 'KitItems', groups: 'BatteryGroups',
};

function load_(names) {
  var d = {};
  names.forEach(function (n) { d[n] = read_(LOADERS[n]); });
  return d;
}

function sheet_(name) { return SpreadsheetApp.getActive().getSheetByName(name); }

/** A missing tab reads as empty, so the dashboard works before the Pi has written to it. */
function read_(name) {
  var s = sheet_(name);
  return s ? toObjects(s.getDataRange().getValues()) : [];
}

function header_(s) {
  return s.getLastColumn() ? s.getRange(1, 1, 1, s.getLastColumn()).getValues()[0].map(String) : [];
}

/** The tab with at least these columns, created or widened as needed (like the Pi does when it writes). */
function ensure_(name, cols) {
  var s = sheet_(name) || SpreadsheetApp.getActive().insertSheet(name);
  var h = header_(s);
  var add = cols.filter(function (c, i) { return h.indexOf(c) < 0 && cols.indexOf(c) === i; });
  if (add.length) s.getRange(1, h.length + 1, 1, add.length).setValues([add]);
  return s;
}

function append_(name, obj) {
  var s = ensure_(name, (OWN_TABS[name] || (SCHEMA[name] || {}).cols || []).concat(Object.keys(obj)));
  s.appendRow(header_(s).map(function (c) { return obj[c] === undefined || obj[c] === null ? '' : obj[c]; }));
}

/** Change some cells of one row, one audit line per changed cell. Only the changed cells are written. */
function update_(name, key, id, changes, me, reason, hideValues) {
  var s = ensure_(name, Object.keys(changes));
  var values = s.getDataRange().getValues(), h = values[0].map(String), k = h.indexOf(key);
  for (var i = 1; i < values.length; i++) {
    if (String(values[i][k]) !== String(id)) continue;
    Object.keys(changes).forEach(function (f) {
      var c = h.indexOf(f), old = values[i][c];
      if (String(old) === String(changes[f])) return;
      s.getRange(i + 1, c + 1).setValue(changes[f]);
      audit_(me, name, id, f, hideValues ? '(hidden)' : old, hideValues ? '(hidden)' : changes[f], reason);
    });
    return;
  }
  throw new Error(name + ' has no row ' + id + '; reload and try again.');
}

function audit_(me, tab, row, field, oldValue, newValue, reason) {
  append_('AuditLog', { ts: new Date().toISOString(), who: me.email, tab: tab, row: row, field: field,
    old_value: oldValue instanceof Date ? oldValue.toISOString() : oldValue, new_value: newValue, reason: reason });
}

function setSetting_(key, value, me, reason) {
  var exists = read_('Settings').some(function (r) { return String(r.key) === key; });
  if (exists) update_('Settings', 'key', key, { value: value }, me, reason);
  else { append_('Settings', { key: key, value: value }); audit_(me, 'Settings', key, 'value', '', value, reason); }
}

function withLock_(fn) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try { return fn(); } finally { lock.releaseLock(); }
}

// -- who's asking ----------------------------------------------------------------------------------

/**
 * Staff are People rows with an email and role instructor or supervisor. The Sheet's owner is always a
 * supervisor, so a brand-new Sheet with no emails yet can still be set up.
 */
function currentStaff_() {
  var email = String(Session.getActiveUser().getEmail() || '').toLowerCase();
  if (!email) throw new Error('Open this page signed in with your staff Google account.');
  var p = read_('People').filter(function (r) {
    return String(r.email).trim().toLowerCase() === email && truthy(r.active === '' ? 'yes' : r.active)
      && ['instructor', 'supervisor'].indexOf(String(r.role).toLowerCase()) >= 0;
  })[0];
  if (p) return { email: email, person_id: String(p.person_id), name: String(p.name), role: String(p.role).toLowerCase() };
  // The Sheet's owner, not the account the script runs as: deployed to run as whoever opens it (personal Google
  // accounts), that account is the visitor, and every visitor would be a supervisor. A Shared Drive has no owner.
  var owner = SpreadsheetApp.getActive().getOwner() || Session.getEffectiveUser();
  if (email === String(owner.getEmail() || '').toLowerCase()) {
    return { email: email, person_id: '', name: email, role: 'supervisor' };
  }
  throw new Error(email + ' is not on the staff list. Ask a supervisor to add your email to your People row.');
}

function today_() { return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd'); }

function tzOffset_() {
  var z = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'Z');  // like -0500
  var sign = z[0] === '-' ? -1 : 1;
  return sign * (parseInt(z.slice(1, 3), 10) * 60 + parseInt(z.slice(3, 5), 10)) * 60000;
}
