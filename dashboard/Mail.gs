/**
 * Alert emails (D9) and the weekday morning digest (D10), run by time triggers.
 * Turn them on once from the Settings page ("Turn on emails"), which calls installTriggers().
 * They send as the account that deployed the script, from the Sheet's own data, so an alert the Pi
 * queued while offline is emailed as soon as it reaches the Sheet.
 */

/** Every 5 minutes: email the instructor on duty about each new missing-tool alert, once. */
function emailNewAlerts() {
  var to = recipients(settingsMap(read_('Settings')).alert_email);
  var pending = alertsToEmail(read_('Alerts'));
  if (!to || !pending.length) return 0;
  var url = ScriptApp.getService().getUrl();
  pending.forEach(function (a) {
    var m = alertEmail(a, url);
    MailApp.sendEmail({ to: to, subject: m.subject, htmlBody: m.htmlBody });
    // Marked straight away, so a failure on the next one never re-sends this one.
    withLock_(function () { update_('Alerts', 'alert_id', a.alert_id, { emailed_at: new Date().toISOString() }, SYSTEM_, 'Alert emailed to ' + to); });
  });
  return pending.length;
}

/** Hourly: at digest_hour on Monday to Friday, send the digest once that day. */
function morningDigest(force) {
  var tz = Session.getScriptTimeZone(), now = new Date();
  var s = settingsMap(read_('Settings'));
  var day = Utilities.formatDate(now, tz, 'u');  // 1 = Monday ... 7 = Sunday
  var hour = parseInt(Utilities.formatDate(now, tz, 'H'), 10);
  var today = Utilities.formatDate(now, tz, 'yyyy-MM-dd');
  var props = PropertiesService.getScriptProperties();
  if (!force && (day > '5' || hour !== (parseInt(s.digest_hour, 10) || 7) || props.getProperty('digest_day') === today)) return null;
  var to = recipients(s.digest_email);
  if (!to) return null;
  var since = parseInt(props.getProperty('digest_at'), 10) || now.getTime() - 24 * 3600000;
  var lastNight = Utilities.formatDate(new Date(now.getTime() - 24 * 3600000), tz, 'yyyy-MM-dd');
  var d = load_(['tools', 'txns', 'people', 'extensions', 'alerts', 'settings', 'damage', 'reprints', 'reconciliations']);
  var m = digestEmail(digestData(d, now.getTime(), since, lastNight), ScriptApp.getService().getUrl());
  MailApp.sendEmail({ to: to, subject: m.subject, htmlBody: m.htmlBody });
  props.setProperties({ digest_day: today, digest_at: String(now.getTime()) });
  return m;
}

/** Replaces this script's email triggers with fresh ones. Safe to run again. */
function installTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (['emailNewAlerts', 'morningDigest'].indexOf(t.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('emailNewAlerts').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('morningDigest').timeBased().everyHours(1).create();
}

var SYSTEM_ = { email: 'email-trigger', person_id: '', name: 'Email trigger', role: 'system' };
