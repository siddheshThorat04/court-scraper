/**
 * Court scraper - central data store.  Paste into Extensions > Apps Script of ONE Google Sheet,
 * set the script property SECRET, run setup() once, then Deploy > Web app (Execute as: Me, Access: Anyone).
 * Workers only know the web-app URL + SECRET. They can append rows and read the small names/pages
 * sheets; they can NOT read or delete the cases.
 */
var SHEETS = {
  cases:  { cols: ['court_code','case_id','party_name','party_type','case_title','filed_date','case_status','balance','court','year_of_birth','search_last_name','page_number','scraped_at'], text: 11, textExtra: [13] },
  names:  { cols: ['court_code','last_name','total_records','total_pages','status','counted_at'], text: 2 },
  pages:  { cols: ['court_code','last_name','page','case_count','scraped_at'], text: 2 },
  failed: { cols: ['task_key','payload','error','attempts','failed_at'], text: 3 },
  captchas: { cols: ['machine','captcha_count','last_captcha_at'], text: 1, textExtra: [3] }
};
var READABLE = { names: true, pages: true };   // 'cases' is deliberately not readable through the web app

function setup() { initSheets_(); }

function initSheets_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  Object.keys(SHEETS).forEach(function (name) {
    var def = SHEETS[name], sh = ss.getSheetByName(name);
    if (!sh) sh = ss.insertSheet(name);
    if (sh.getMaxColumns() < def.cols.length) sh.insertColumnsAfter(sh.getMaxColumns(), def.cols.length - sh.getMaxColumns());
    // The header row is rewritten on every run, so a column added later (scraped_at) also appears on existing sheets.
    sh.getRange(1, 1, 1, def.cols.length).setValues([def.cols]).setFontWeight('bold');
    sh.setFrozenRows(1);
    // Every cell counts toward Google's 10,000,000-cell limit: drop unused default columns.
    if (sh.getMaxColumns() > def.cols.length) sh.deleteColumns(def.cols.length + 1, sh.getMaxColumns() - def.cols.length);
  });
  var blank = ss.getSheetByName('Sheet1');
  if (blank && blank.getLastRow() === 0 && ss.getSheets().length > 1) ss.deleteSheet(blank);
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function doGet() { return out_({ ok: true, hint: 'POST only' }); }

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    var secret = PropertiesService.getScriptProperties().getProperty('SECRET');
    if (!secret || body.secret !== secret) return out_({ ok: false, error: 'unauthorized' });
    switch (body.action) {
      case 'ping':   return out_({ ok: true });
      case 'init':   initSheets_(); return out_({ ok: true });
      case 'write':  return out_(write_(body));
      case 'failed': return out_(failed_(body));
      case 'captcha': return out_(captcha_(body));
      case 'read':   return out_(read_(body));
      case 'stats':  return out_(stats_());
      default:       return out_({ ok: false, error: 'unknown action' });
    }
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  }
}

function toRows_(rows, textCols, extra) {
  return rows.map(function (r) {
    return r.map(function (v, i) {
      return (i < textCols || (extra && extra.indexOf(i + 1) >= 0)) ? String(v == null ? '' : v) : v;
    });
  });
}

function appendRows_(sh, rows, textCols, extra) {
  if (!rows.length) return;
  var start = sh.getLastRow() + 1, need = start + rows.length - 1;
  if (need > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), need - sh.getMaxRows() + 500);
  // Plain-text format first, so Sheets never turns "01/02/2025" into a date, "00123" into 123, or "=x" into a formula.
  sh.getRange(start, 1, rows.length, textCols).setNumberFormat('@');
  (extra || []).forEach(function (c) { if (c <= rows[0].length) sh.getRange(start, c, rows.length, 1).setNumberFormat('@'); });
  sh.getRange(start, 1, rows.length, rows[0].length).setValues(toRows_(rows, textCols, extra));
}

function write_(b) {
  var lock = LockService.getScriptLock();
  lock.waitLock(60000);
  try {
    var cache = CacheService.getScriptCache();
    if (b.batch_id && cache.get('b:' + b.batch_id)) return { ok: true, duplicate: true, inserted: 0 };
    var ss = SpreadsheetApp.getActiveSpreadsheet(), now = new Date().toISOString();
    var cases = b.cases || [], pages = b.pages || [], summaries = b.summaries || [];
    appendRows_(ss.getSheetByName('names'), summaries.map(function (r) { return r.concat([now]); }), SHEETS.names.text);
    appendRows_(ss.getSheetByName('cases'), cases, SHEETS.cases.text, SHEETS.cases.textExtra);
    appendRows_(ss.getSheetByName('pages'), pages.map(function (r) { return r.concat([now]); }), SHEETS.pages.text);
    SpreadsheetApp.flush();
    if (b.batch_id) cache.put('b:' + b.batch_id, '1', 21600);   // a retried identical batch is ignored for 6 h
    return { ok: true, inserted: cases.length };
  } finally {
    lock.releaseLock();
  }
}

function failed_(b) {
  var lock = LockService.getScriptLock();
  lock.waitLock(60000);
  try {
    var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('failed');
    appendRows_(sh, [[b.task_key, b.payload, b.error, b.attempts, new Date().toISOString()]], SHEETS.failed.text);
    return { ok: true };
  } finally {
    lock.releaseLock();
  }
}

function captcha_(b) {
  // One row per machine: the worker sends its running total, so we set (not add) it - a retry cannot double count.
  var lock = LockService.getScriptLock();
  lock.waitLock(60000);
  try {
    var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('captchas'), n = sh.getLastRow() - 1, row = -1;
    if (n > 0) {
      var names = sh.getRange(2, 1, n, 1).getValues();
      for (var i = 0; i < n; i++) if (String(names[i][0]) === String(b.machine)) { row = i + 2; break; }
    }
    if (row < 0) appendRows_(sh, [[b.machine, b.count, b.at]], SHEETS.captchas.text, SHEETS.captchas.textExtra);
    else sh.getRange(row, 2, 1, 2).setValues([[b.count, String(b.at)]]);
    return { ok: true };
  } finally {
    lock.releaseLock();
  }
}

function read_(b) {
  if (!READABLE[b.sheet]) return { ok: false, error: 'sheet not readable' };
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(b.sheet);
  var n = sh.getLastRow() - 1;
  if (n < 1) return { ok: true, rows: [] };
  var cols = Math.min(b.cols || SHEETS[b.sheet].cols.length, SHEETS[b.sheet].cols.length);
  return { ok: true, rows: sh.getRange(2, 1, n, cols).getValues() };
}

function stats_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet(), cells = 0;
  ss.getSheets().forEach(function (s) { cells += s.getMaxRows() * s.getMaxColumns(); });
  return {
    ok: true, cases: ss.getSheetByName('cases').getLastRow() - 1,
    failed: ss.getSheetByName('failed').getLastRow() - 1, cells: cells
  };
}
