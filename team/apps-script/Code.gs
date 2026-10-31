// Apps Script entry points for the team progress backend: the web app,
// Sheet reads and writes, Google Chat messages and the weekly trigger.
// The rules live in Logic.gs. Script Properties: TEAM_TOKEN, ALLOWED_DOMAIN,
// CHAT_WEBHOOK_URL, and SHEET_ID when the script is not bound to the Sheet.
// Setup, deployment and the event schema: team/README.md.

var LOCK_WAIT_MS = 30000;
var DASHBOARD_FIRST_ROW = 22;
var DASHBOARD_ROWS = 40;
var HELPER_COLUMN = 26; // Z:AE, hidden: Progress sorted by pct (Z:AD) and the chart label (AE). Formulas name them.

function doGet() {
  return json_({ok: true, service: 'claude-cert-squad'});
}

function doPost(e) {
  var outcome;
  try {
    outcome = handlePost_(e && e.postData ? e.postData.contents : '');
  } catch (err) {
    console.error(err && err.stack ? err.stack : err);
    outcome = {reply: {ok: false, error: 'server_error'}, messages: []};
  }
  outcome.messages.forEach(sendChat_);
  return json_(outcome.reply);
}

function handlePost_(body) {
  var event;
  try {
    event = JSON.parse(body);
  } catch (err) {
    return {reply: {ok: false, error: 'bad_json'}, messages: []};
  }
  var props = PropertiesService.getScriptProperties();
  var check = validateEvent(event, {
    token: props.getProperty('TEAM_TOKEN'),
    domain: String(props.getProperty('ALLOWED_DOMAIN') || '').trim().toLowerCase()
  });
  if (!check.ok) return {reply: check, messages: []};
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(LOCK_WAIT_MS)) return {reply: {ok: false, error: 'busy'}, messages: []};
  try {
    return recordEvent_(event, new Date());
  } finally {
    lock.releaseLock();
  }
}

function recordEvent_(event, now) {
  var book = book_();
  var eventsTab = tab_(book, 'Events');
  if (hasValue_(eventsTab, 2, event.id)) return {reply: {ok: true, duplicate: true}, messages: []};
  var row = eventRow(event, now);
  eventsTab.appendRow(row);
  var current = rowEvent(row);
  var events = readEvents_(book);
  var member = upsertMember_(book, event, now);
  if (current.track) writeProgress_(book, computeProgress(events, current.email, current.track, current.routeTotal));

  var awards = detectAwards(events, current, {keys: awardKeys_(book), now: now, share: member.share, name: member.name});
  var awardsTab = tab_(book, 'Awards');
  var templates = readTemplates_(book);
  var messages = [];
  awards.forEach(function (award) {
    awardsTab.appendRow([award.key, current.email, award.type, now]);
    if (award.situation) messages.push(renderTemplate(pickTemplate(templates, award.situation, Math.random), award.values));
  });
  return {reply: {ok: true, awards: awards.length}, messages: messages};
}

// Time trigger, Sunday 22:00-23:00. The window runs from the previous run to now, so no event is skipped.
function weeklySummary() {
  var props = PropertiesService.getScriptProperties();
  var lock = LockService.getScriptLock();
  lock.waitLock(LOCK_WAIT_MS);
  try {
    var now = new Date();
    var from = Number(props.getProperty('LAST_WEEKLY_AT')) || now.getTime() - 7 * DAY_MS;
    var book = book_();
    var rows = weeklyRanking(readEvents_(book), {from: from, to: now.getTime()});
    if (rows.length) {
      var ranking = renderRanking(rows, readMembers_(book), readProgress_(book));
      var text = renderTemplate(pickTemplate(readTemplates_(book), 'weekly', Math.random), {ranking: ranking});
      if (!sendChat_(text)) throw new Error('Weekly summary was not delivered. The window stays open, so run weeklySummary again.');
      var awardsTab = tab_(book, 'Awards');
      var day = localDay(now.toISOString());
      rows.forEach(function (row) { awardsTab.appendRow(['weekly:' + day + ':' + row.email, row.email, 'weekly', now]); });
    }
    props.setProperty('LAST_WEEKLY_AT', String(now.getTime()));
  } finally {
    lock.releaseLock();
  }
}

// Run once from the editor: creates the tabs, seeds Templates and builds Dashboard. Safe to run again.
function setupSheets() {
  var book = book_();
  Object.keys(SHEET_COLUMNS).forEach(function (name) { tab_(book, name); });
  var templates = tab_(book, 'Templates');
  if (templates.getLastRow() < 2) {
    var rows = [];
    Object.keys(DEFAULT_TEMPLATES).forEach(function (situation) {
      DEFAULT_TEMPLATES[situation].forEach(function (text) { rows.push([situation, text]); });
    });
    templates.getRange(2, 1, rows.length, 2).setValues(rows);
    templates.setColumnWidth(2, 720);
  }
  if (!book.getSheetByName('Dashboard')) rebuildDashboard();
}

// Run once from the editor after each change of time zone or schedule.
function installWeeklyTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === 'weeklySummary') ScriptApp.deleteTrigger(trigger);
  });
  ScriptApp.newTrigger('weeklySummary').timeBased().onWeekDay(ScriptApp.WeekDay.SUNDAY).atHour(22).everyWeeks(1).create();
}

// Run from the editor after deleting rows from Events by hand. Awards already sent stay recorded.
function rebuildProgress() {
  var lock = LockService.getScriptLock();
  lock.waitLock(LOCK_WAIT_MS);
  try {
    var book = book_();
    var events = readEvents_(book);
    var sheet = tab_(book, 'Progress');
    if (sheet.getLastRow() > 1) sheet.getRange(2, 1, sheet.getLastRow() - 1, SHEET_COLUMNS.Progress.length).clearContent();
    var rows = progressPairs(events).map(function (pair) {
      return progressRow_(computeProgress(events, pair.email, pair.track, pair.total));
    });
    if (rows.length) sheet.getRange(2, 1, rows.length, rows[0].length).setValues(rows);
  } finally {
    lock.releaseLock();
  }
}

// Run from the editor to send one test line to the Chat space.
function testChat() {
  if (!sendChat_('👋 Kết nối Google Chat của team đã sẵn sàng.')) throw new Error('Chat webhook failed, see the execution log.');
}

function rebuildDashboard() {
  var book = book_();
  var sheet = book.getSheetByName('Dashboard') || book.insertSheet('Dashboard', 0);
  sheet.getCharts().forEach(function (chart) { sheet.removeChart(chart); });
  sheet.clear();
  sheet.clearConditionalFormatRules();

  var first = DASHBOARD_FIRST_ROW;
  var last = first + DASHBOARD_ROWS - 1;
  sheet.getRange('A1').setValue('Tiến độ học chứng chỉ Claude').setFontSize(16).setFontWeight('bold');
  sheet.getRange(first - 1, 1, 1, 7)
    .setValues([['Member', 'Track', 'Tiến độ', 'Bài', 'Tuần này', 'Mock tốt nhất', 'Hoạt động gần nhất']])
    .setFontWeight('bold').setBackground('#e8eaed');
  sheet.getRange(first - 1, HELPER_COLUMN, 1, 6).setValues([['email', 'track', 'done', 'total', 'pct', 'label']]);
  sheet.getRange(first, HELPER_COLUMN)
    .setFormula('=IFERROR(SORT(FILTER(Progress!A2:E, Progress!A2:A<>""), 5, FALSE, 1, TRUE), "")');

  var formulas = [];
  var labels = [];
  for (var r = first; r <= last; r++) {
    var email = '$Z' + r;
    var track = '$AA' + r;
    var pct = '$AD' + r;
    var mine = 'Events!$D:$D,' + email + ',Events!$F:$F,' + track;
    var blank = '=IF(' + email + '="","",';
    formulas.push([
      blank + 'IFERROR(VLOOKUP(' + email + ',Members!$A:$B,2,FALSE),' + email + '))',
      blank + 'UPPER(' + track + '))',
      blank + 'SPARKLINE(' + pct + ',{"charttype","bar";"max",100;"color1","#1a73e8"}))',
      blank + '$AB' + r + '&"/"&$AC' + r + '&" ("&ROUND(' + pct + ',0)&"%)")',
      blank + 'COUNTIFS(' + mine + ',Events!$E:$E,"lesson_completed",Events!$A:$A,">="&(TODAY()-WEEKDAY(TODAY(),3))))',
      blank + 'IF(IFERROR(VLOOKUP(' + email + ',Members!$A:$D,4,FALSE),FALSE)<>TRUE,"🔒",IF(COUNTIFS(' + mine +
        ',Events!$I:$I,"mock")=0,"—",MAXIFS(Events!$H:$H,' + mine + ',Events!$I:$I,"mock")&"%")))',
      blank + 'MAXIFS(Events!$A:$A,' + mine + '))'
    ]);
    labels.push([blank + '$A' + r + '&" · "&$B' + r + ')']);
  }
  sheet.getRange(first, 1, DASHBOARD_ROWS, 7).setFormulas(formulas);
  sheet.getRange(first, HELPER_COLUMN + 5, DASHBOARD_ROWS, 1).setFormulas(labels);
  sheet.getRange(first, 5, DASHBOARD_ROWS, 1).setNumberFormat('"+"0').setHorizontalAlignment('center');
  sheet.getRange(first, 6, DASHBOARD_ROWS, 1).setHorizontalAlignment('center');
  sheet.getRange(first, 7, DASHBOARD_ROWS, 1).setNumberFormat('dd/mm hh:mm');
  sheet.setColumnWidth(1, 180);
  sheet.setColumnWidth(3, 240);
  sheet.setColumnWidth(4, 110);
  sheet.setColumnWidth(7, 150);

  var progressCells = sheet.getRange(first, 4, DASHBOARD_ROWS, 1);
  var tiers = [[100, '#93d3a2'], [67, '#ceead6'], [34, '#fef7cd'], [0.01, '#e8f0fe']];
  sheet.setConditionalFormatRules(tiers.map(function (tier) {
    return SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND($Z' + first + '<>"",$AD' + first + '>=' + tier[0] + ')')
      .setBackground(tier[1])
      .setRanges([progressCells])
      .build();
  }));

  sheet.insertChart(sheet.newChart()
    .setChartType(Charts.ChartType.BAR)
    .addRange(sheet.getRange(first, HELPER_COLUMN + 5, DASHBOARD_ROWS, 1))
    .addRange(sheet.getRange(first, HELPER_COLUMN + 4, DASHBOARD_ROWS, 1))
    .setHiddenDimensionStrategy(Charts.ChartHiddenDimensionStrategy.SHOW_BOTH)
    .setPosition(2, 1, 0, 0)
    .setOption('title', '% route theo từng người')
    .setOption('legend', {position: 'none'})
    .setOption('hAxis', {minValue: 0, maxValue: 100})
    .setOption('width', 760)
    .setOption('height', 380)
    .build());
  sheet.hideColumns(HELPER_COLUMN, 6);
}

function upsertMember_(book, event, now) {
  var sheet = tab_(book, 'Members');
  var values = sheet.getDataRange().getValues();
  var index = -1;
  for (var i = 1; i < values.length; i++) {
    if (values[i][0] === event.email) {
      index = i;
      break;
    }
  }
  var next = memberRow(index > 0 ? values[index] : null, event, now);
  if (index > 0) sheet.getRange(index + 1, 1, 1, next.length).setValues([next]);
  else sheet.appendRow(next);
  return {name: next[1], track: next[2], share: next[3] === true};
}

function writeProgress_(book, progress) {
  var sheet = tab_(book, 'Progress');
  var values = sheet.getDataRange().getValues();
  var row = progressRow_(progress);
  for (var i = 1; i < values.length; i++) {
    if (values[i][0] === progress.email && values[i][1] === progress.track) {
      sheet.getRange(i + 1, 1, 1, row.length).setValues([row]);
      return;
    }
  }
  sheet.appendRow(row);
}

function progressRow_(progress) {
  return [
    progress.email, progress.track, progress.done, progress.total, progress.pct,
    progress.startedAt ? new Date(progress.startedAt) : '', progress.finishedAt ? new Date(progress.finishedAt) : ''
  ];
}

function readEvents_(book) {
  return dataRows_(tab_(book, 'Events')).map(rowEvent);
}

function readMembers_(book) {
  var members = {};
  dataRows_(tab_(book, 'Members')).forEach(function (row) {
    members[row[0]] = {name: row[1], track: row[2], share: row[3] === true};
  });
  return members;
}

function readProgress_(book) {
  var progress = {};
  dataRows_(tab_(book, 'Progress')).forEach(function (row) { progress[row[0] + '|' + row[1]] = Number(row[4]); });
  return progress;
}

function readTemplates_(book) {
  return dataRows_(tab_(book, 'Templates')).map(function (row) {
    return {situation: String(row[0]).trim(), text: String(row[1])};
  });
}

function awardKeys_(book) {
  var keys = {};
  dataRows_(tab_(book, 'Awards')).forEach(function (row) { keys[row[0]] = true; });
  return keys;
}

function sendChat_(text) {
  var url = PropertiesService.getScriptProperties().getProperty('CHAT_WEBHOOK_URL');
  if (!url || !text) return false;
  try {
    var response = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json; charset=UTF-8',
      payload: JSON.stringify({text: text}),
      muteHttpExceptions: true
    });
    if (response.getResponseCode() < 300) return true;
    console.error('Chat webhook returned ' + response.getResponseCode() + ': ' + response.getContentText());
  } catch (err) {
    console.error(err);
  }
  return false;
}

function book_() {
  var id = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  return id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
}

function tab_(book, name) {
  var sheet = book.getSheetByName(name);
  if (sheet) return sheet;
  sheet = book.insertSheet(name);
  var columns = SHEET_COLUMNS[name];
  sheet.getRange(1, 1, 1, columns.length).setValues([columns]).setFontWeight('bold');
  sheet.setFrozenRows(1);
  // Keep ts as the text the member sent; the Sheet would otherwise guess a date format.
  if (name === 'Events') sheet.getRange('C:C').setNumberFormat('@');
  return sheet;
}

function dataRows_(sheet) {
  var last = sheet.getLastRow();
  if (last < 2) return [];
  return sheet.getRange(2, 1, last - 1, sheet.getLastColumn()).getValues();
}

function hasValue_(sheet, column, value) {
  var last = sheet.getLastRow();
  if (last < 2) return false;
  return sheet.getRange(2, column, last - 1, 1).createTextFinder(value).matchEntireCell(true).findNext() !== null;
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
