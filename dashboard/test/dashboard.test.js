// Runs the dashboard's .gs files in node against a fake Sheet: node --test dashboard/test
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class FakeSheet {
  constructor(name, rows) { this.name = name; this.rows = rows; }
  getLastColumn() { return this.rows.reduce((m, r) => Math.max(m, r.length), 0); }
  getDataRange() { const w = this.getLastColumn(); return { getValues: () => this.rows.map((r) => [...r, ...Array(w - r.length).fill('')]) }; }
  getRange(r, c, nr = 1, nc = 1) {
    return {
      getValues: () => this.getDataRange().getValues().slice(r - 1, r - 1 + nr).map((row) => row.slice(c - 1, c - 1 + nc)),
      setValues: (vals) => vals.forEach((v, i) => { const row = (this.rows[r - 1 + i] ||= []); v.forEach((x, j) => { row[c - 1 + j] = x; }); }),
      setValue: (v) => { const row = (this.rows[r - 1] ||= []); row[c - 1] = v; },
    };
  }
  appendRow(v) { this.rows.push(v); }
  deleteRow(i) { this.rows.splice(i - 1, 1); }
}

function load(tabs, email = 'boss@lab.org', owner = 'owner@lab.org') {
  const sheets = Object.fromEntries(Object.entries(tabs).map(([k, v]) => [k, new FakeSheet(k, structuredClone(v))]));
  const sent = [];
  const ctx = {
    sheets, sent, console, JSON, Date, Math,
    SpreadsheetApp: { getActive: () => ({ getSheetByName: (n) => sheets[n] || null, insertSheet: (n) => (sheets[n] = new FakeSheet(n, [])) }) },
    Session: { getActiveUser: () => ({ getEmail: () => email }), getEffectiveUser: () => ({ getEmail: () => owner }), getScriptTimeZone: () => 'UTC' },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    Utilities: { getUuid: () => 'aaaa-bbbb', formatDate: (d, tz, f) => (f === 'Z' ? '+0000' : d.toISOString().slice(0, 10)) },
    MailApp: { sendEmail: (o) => sent.push(o) },
  };
  vm.createContext(ctx);
  for (const f of fs.readdirSync(path.join(__dirname, '..')).filter((f) => f.endsWith('.gs')).sort()) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', f), 'utf8'), ctx, { filename: f });
  }
  return ctx;
}

const iso = (minAgo) => new Date(Date.now() - minAgo * 60000).toISOString();
const PEOPLE = [['person_id', 'id_code', 'name', 'role', 'email', 'active', 'pin_hash'],
  ['P-1', 'ID-1001', 'Jordan', 'student', '', 'yes', '$argon2id$x'],
  ['P-9', 'ID-9', 'Ms. Rivera', 'instructor', 'rivera@lab.org', 'yes', ''],
  ['P-10', 'ID-10', 'Boss', 'supervisor', 'boss@lab.org', 'TRUE', '']];
const TOOLS = [['asset_id', 'qr_code', 'name', 'spot_id', 'status'],
  ['HHA-1', 'T-1', 'Multimeter', 'LOC-S01-B01-H01', 'available'],
  ['HHA-2', 'T-2', 'Drill', 'LOC-S01-B01-H02', 'available'],
  ['HHA-3', 'T-3', 'Saw', 'LOC-S01-B01-H03', 'quarantined']];
const TXNS = [['txn_id', 'ts', 'asset_id', 'person_id', 'action', 'gate_photo', 'due_at'],
  ['t1', iso(300), 'HHA-1', 'P-1', 'out', 'https://drive.google.com/file/d/abcdefghijk1/view', iso(60)],
  ['t2', iso(30), 'HHA-2', 'P-1', 'out', '', iso(-60)],
  ['t3', iso(200), 'HHA-2', 'P-1', 'in', '', '']];  // older return listed later: must not win

test('home lists tools out, overdue first, using timestamps not row order', () => {
  const g = load({ People: PEOPLE, Tools: TOOLS, Transactions: TXNS });
  const out = g.api('home').data.out;
  assert.deepStrictEqual(out.map((o) => [o.asset_id, o.overdue, o.person]), [['HHA-1', true, 'Jordan'], ['HHA-2', false, 'Jordan']]);
});

test('grace period and extensions push the overdue line out', () => {
  const g = load({ People: PEOPLE, Tools: TOOLS, Transactions: TXNS, Settings: [['key', 'value'], ['grace_minutes', '90']] });
  assert.strictEqual(g.api('home').data.out[0].overdue, false);
  const g2 = load({ People: PEOPLE, Tools: TOOLS, Transactions: TXNS,
    Extensions: [['txn_id', 'due_at', 'ts'], ['t1', iso(-600), iso(5)]] });
  assert.strictEqual(g2.api('home').data.out.find((o) => o.asset_id === 'HHA-1').overdue, false);
});

test('only staff on the People list (or the owner) get in', () => {
  assert.throws(() => load({ People: PEOPLE }, 'stranger@x.com').api('home'), /not on the staff list/);
  assert.strictEqual(load({ People: PEOPLE }, 'owner@lab.org').api('settings').me.role, 'supervisor');
  assert.throws(() => load({ People: PEOPLE }, 'rivera@lab.org').api('saveSettings', { values: { loan_hours: '2' } }), /Only supervisors/);
});

test('alert actions need a note and are audited; dashboard columns only', () => {
  const g = load({ People: PEOPLE, Alerts: [['alert_id', 'opened', 'type', 'status', 'spot_label'], ['a1', iso(5), 'missing_tool', 'open', 'Shelf 1']] }, 'rivera@lab.org');
  assert.throws(() => g.api('alertAction', { alert_id: 'a1', status: 'resolved', note: '' }), /Add a note/);
  const q = g.api('alertAction', { alert_id: 'a1', status: 'false_alarm', note: 'hand in view' }).data;
  assert.strictEqual(q.open.length, 0);
  const a = g.toObjects(g.sheets.Alerts.rows)[0];
  assert.deepStrictEqual([a.status, a.resolved_by, a.resolution_note, a.spot_label], ['false_alarm', 'P-9', 'hand in view', 'Shelf 1']);
  const audit = g.sheets.AuditLog.rows;
  assert.deepStrictEqual(audit[0], ['ts', 'who', 'tab', 'row', 'field', 'old_value', 'new_value', 'reason']);
  assert.ok(audit.slice(1).some((r) => r[1] === 'rivera@lab.org' && r[4] === 'status' && r[5] === 'open' && r[6] === 'false_alarm'));
});

test('editing a record requires a reason, validates choices, and audits each changed cell', () => {
  const g = load({ People: PEOPLE, Tools: TOOLS });
  assert.throws(() => g.api('saveRecord', { tab: 'Tools', id: 'HHA-1', values: { name: 'DMM' } }), /Say why/);
  assert.throws(() => g.api('saveRecord', { tab: 'Tools', id: 'HHA-1', values: { status: 'broken' }, reason: 'x' }), /status must be one of/);
  g.api('saveRecord', { tab: 'Tools', id: 'HHA-1', values: { asset_id: 'HHA-X', name: 'DMM', status: 'available' }, reason: 'typo' });
  assert.strictEqual(g.sheets.Tools.rows[1][0], 'HHA-1');  // the key is never renamed
  assert.strictEqual(g.sheets.Tools.rows[1][2], 'DMM');
  const audit = g.sheets.AuditLog.rows.slice(1);
  assert.strictEqual(audit.length, 1);
  assert.deepStrictEqual(audit[0].slice(2), ['Tools', 'HHA-1', 'name', 'Multimeter', 'DMM', 'typo']);
  assert.throws(() => g.api('saveRecord', { tab: 'Tools', isNew: true, values: { asset_id: 'HHA-2', name: 'x' } }), /already exists/);
  g.api('saveRecord', { tab: 'Tools', isNew: true, values: { asset_id: 'HHA-4', qr_code: 'T-4', name: 'Level', needs_battery: 'no' } });
  assert.ok(g.sheets.Tools.rows[0].includes('needs_battery'));  // new column added to the header
});

test('People records never send PIN hashes to the page; reset clears it without logging it', () => {
  const g = load({ People: PEOPLE });
  const rows = g.api('records', { tab: 'People' }).data.rows;
  assert.ok(rows.every((r) => !('pin_hash' in r)));
  assert.strictEqual(rows[0].has_pin, true);
  g.api('resetPin', { person_id: 'P-1', reason: 'forgot it' });
  assert.strictEqual(g.sheets.People.rows[1][6], '');
  assert.ok(!JSON.stringify(g.sheets.AuditLog.rows).includes('argon2'));
});

test('reconciliation pre-fills from the latest snapshot and signs off with exceptions', () => {
  const spots = [['spot_id', 'label'], ['LOC-S01-B01-H01', 'Shelf 1 hook 1'], ['LOC-S01-B01-H02', 'Shelf 1 hook 2'], ['LOC-S01-B01-H03', 'Shelf 1 hook 3']];
  const snaps = [['snapshot_id', 'ts', 'spots'],
    ['s1', iso(10), JSON.stringify({ 'LOC-S01-B01-H01': 'full' })],
    ['s0', iso(100), JSON.stringify({ 'LOC-S01-B01-H01': 'empty', 'LOC-S01-B01-H03': 'empty' })]];
  const g = load({ People: PEOPLE, Tools: TOOLS, Transactions: TXNS, Spots: spots, Snapshots: snaps }, 'rivera@lab.org');
  const rows = g.api('reconcile').data.rows;
  assert.deepStrictEqual(rows.map((r) => [r.camera, r.ok]), [['full', false], ['', null], ['', null]]);  // HHA-1 is out but the camera saw it
  assert.match(rows[0].record, /out \(Jordan\)/);
  assert.throws(() => g.api('signOff', { entries: [{ spot_id: 'LOC-S01-B01-H01', ok: false, note: '' }] }), /Add a note/);
  const row = g.api('signOff', { entries: [{ spot_id: 'LOC-S01-B01-H01', ok: false, note: 'returned unscanned' }, { spot_id: 'LOC-S01-B01-H02', ok: true }] }).data;
  assert.strictEqual(row.spots_confirmed, 1);
  assert.strictEqual(row.instructor, 'Ms. Rivera');
  assert.strictEqual(g.sheets.Reconciliations.rows[1][3], 'LOC-S01-B01-H01: returned unscanned');
});

test('mark returned writes a dashboard return the Pi will pull', () => {
  const g = load({ People: PEOPLE, Tools: TOOLS, Transactions: TXNS }, 'rivera@lab.org');
  g.api('markReturned', { asset_id: 'HHA-1', reason: 'saw it on the hook' });
  const t = g.toObjects(g.sheets.Transactions.rows).pop();
  assert.deepStrictEqual([t.asset_id, t.action, t.person_id, t.override_by, t.kiosk_id], ['HHA-1', 'in', 'P-1', 'P-9', 'dashboard']);
  assert.throws(() => g.api('markReturned', { asset_id: 'HHA-3', reason: 'x' }), /not checked out/);
});

test('quarantine triage releases the tool and closes its damage reports', () => {
  const dmg = [['report_id', 'asset_id', 'reported_by', 'severity', 'opened', 'resolved_by', 'resolution', 'resolved_at'],
    ['d1', 'HHA-3', 'P-1', 'safety_hazard', iso(60), '', '', '']];
  const g = load({ People: PEOPLE, Tools: TOOLS, DamageReports: dmg }, 'rivera@lab.org');
  assert.strictEqual(g.api('quarantine').data[0].reports[0].reporter, 'Jordan');
  assert.deepStrictEqual(g.api('triage', { asset_id: 'HHA-3', outcome: 'release', note: 'new blade' }).data, []);
  assert.strictEqual(g.sheets.Tools.rows[3][4], 'available');
  assert.match(g.sheets.DamageReports.rows[1][6], /release: new blade/);
});

test('reprint batch gives one CSV line per tool and marks every flag printed', () => {
  const rq = [['reprint_id', 'asset_id', 'condition', 'printed', 'flagged_at'],
    ['r1', 'HHA-1', 'Faded', 'no', iso(100)], ['r2', 'HHA-1', 'Torn', '', iso(50)], ['r3', 'HHA-2', 'Peeling', 'yes', iso(10)]];
  const g = load({ People: PEOPLE, Tools: TOOLS, ReprintQueue: rq }, 'rivera@lab.org');
  const q = g.api('reprints').data;
  assert.deepStrictEqual(q.map((r) => [r.asset_id, r.condition]), [['HHA-1', 'Torn']]);
  const b = g.api('printBatch', {}).data;
  assert.strictEqual(b.csv, 'asset_id,qr_code,name,spot_id\nHHA-1,T-1,Multimeter,LOC-S01-B01-H01');
  assert.deepStrictEqual(g.sheets.ReprintQueue.rows.slice(1).map((r) => r[3]), ['yes', 'yes', 'yes']);
});

test('settings validate and the snapshot request is a setting the Pi watches', () => {
  const g = load({ People: PEOPLE, Settings: [['key', 'value'], ['loan_hours', '4']] });
  assert.throws(() => g.api('saveSettings', { values: { lab_close: '9pm' } }), /21:00/);
  g.api('saveSettings', { values: { loan_hours: '3', grace_minutes: '15' } });  // 15 is the default: not written
  assert.deepStrictEqual(g.sheets.Settings.rows, [['key', 'value'], ['loan_hours', '3']]);
  g.api('requestSnapshot');
  assert.strictEqual(g.sheets.Settings.rows[2][0], 'snapshot_request');
});

test('kit items can be removed; other tabs cannot delete rows', () => {
  const g = load({ People: PEOPLE, Tools: TOOLS, KitItems: [['kit_id', 'asset_id'], ['K1', 'HHA-1'], ['K1', 'HHA-2']] });
  g.api('deleteRecord', { tab: 'KitItems', values: { kit_id: 'K1', asset_id: 'HHA-1' }, reason: 'moved' });
  assert.deepStrictEqual(g.sheets.KitItems.rows, [['kit_id', 'asset_id'], ['K1', 'HHA-2']]);
  assert.throws(() => g.api('deleteRecord', { tab: 'Tools', values: {} }), /retired, not deleted/);
});

test('end-of-day report collects the day', () => {
  const g = load({ People: PEOPLE, Tools: TOOLS, Transactions: TXNS,
    Alerts: [['alert_id', 'opened', 'type', 'status'], ['a1', iso(5), 'missing_tool', 'open'], ['a2', '2020-01-01T00:00:00Z', 'check_camera', 'resolved']] });
  const r = g.api('report').data;
  assert.strictEqual(r.out.length, 2);
  assert.strictEqual(r.overdue.length, 1);
  assert.deepStrictEqual(r.alerts.map((a) => a.alert_id), ['a1']);
  assert.strictEqual(r.reconciliation, null);
  assert.deepStrictEqual(r.quarantined.map((t) => t.asset_id), ['HHA-3']);
});

test('calibration saves boxes on one reference photo and gives each spot one tool', () => {
  const link = 'https://drive.google.com/file/d/REFabcdefghij/view';
  const g = load({ People: PEOPLE, Tools: TOOLS,
    Spots: [['spot_id', 'label', 'box', 'reference_photo'], ['LOC-S01-B01-H01', 'hook 1', '', ''], ['LOC-S01-B01-H02', 'hook 2', '[1,1,5,5]', 'old']],
    References: [['reference_id', 'ts', 'reference_photo', 'width', 'height'], ['r1', iso(5), link, 1280, 720]] });
  assert.throws(() => load({ People: PEOPLE }, 'rivera@lab.org').api('calibration'), /Only supervisors/);
  assert.strictEqual(g.api('calibration').data.references[0].width, 1280);
  assert.throws(() => g.api('saveCalibration', { reference_photo: 'C:/x.jpg', spots: [] }), /reference photo/);
  g.api('saveCalibration', { reference_photo: link, spots: [
    { spot_id: 'LOC-S01-B01-H01', box: [10.4, 20.6, 100, 80], asset_id: 'HHA-2' },  // the drill moves to hook 1
    { spot_id: 'LOC-S01-B01-H02', box: null, asset_id: '' }] });
  const spots = g.toObjects(g.sheets.Spots.rows);
  assert.deepStrictEqual([spots[0].box, spots[0].reference_photo], ['[10,21,100,80]', link]);
  assert.strictEqual(spots[1].box, '');
  const tools = g.toObjects(g.sheets.Tools.rows);
  assert.deepStrictEqual([...tools.map((t) => t.spot_id)], ['', 'LOC-S01-B01-H01', 'LOC-S01-B01-H03']);
  g.api('requestReference');
  assert.ok(g.toObjects(g.sheets.Settings.rows).some((r) => r.key === 'reference_request'));
});
