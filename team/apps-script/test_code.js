const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Runs Code.gs against in-memory stand-ins for the Google services it touches,
// so a wiring mistake shows up here instead of in the first real request.

class FakeRange {
  constructor(sheet, row, column, rows, columns) {
    Object.assign(this, {sheet, row, column, rows, columns});
  }

  getValues() {
    const values = [];
    for (let r = 0; r < this.rows; r += 1) {
      const source = this.sheet.cells[this.row - 1 + r] || [];
      const line = [];
      for (let c = 0; c < this.columns; c += 1) {
        const value = source[this.column - 1 + c];
        line.push(value === undefined ? '' : value);
      }
      values.push(line);
    }
    return values;
  }

  setValues(values) {
    values.forEach((line, r) => {
      const target = (this.sheet.cells[this.row - 1 + r] ||= []);
      line.forEach((value, c) => { target[this.column - 1 + c] = value; });
    });
    return this;
  }

  clearContent() {
    return this.setValues(Array.from({length: this.rows}, () => Array(this.columns).fill('')));
  }

  createTextFinder(text) {
    const values = this.getValues();
    return {
      matchEntireCell: () => ({findNext: () => (values.some((line) => line.some((value) => String(value) === text)) ? {} : null)})
    };
  }

  setFontWeight() { return this; }
  setNumberFormat() { return this; }
}

class FakeSheet {
  constructor(name) {
    this.name = name;
    this.cells = [];
  }

  getRange(row, column, rows = 1, columns = 1) {
    if (typeof row === 'string') return new FakeRange(this, 1, 1, 0, 0);
    return new FakeRange(this, row, column, rows, columns);
  }

  getLastRow() {
    let last = 0;
    this.cells.forEach((line, index) => { if (line && line.some((value) => value !== '' && value !== undefined)) last = index + 1; });
    return last;
  }

  getLastColumn() {
    return Math.max(0, ...this.cells.map((line) => (line ? line.length : 0)));
  }

  getDataRange() {
    return this.getRange(1, 1, Math.max(1, this.getLastRow()), Math.max(1, this.getLastColumn()));
  }

  appendRow(row) {
    this.cells[this.getLastRow()] = row.slice();
  }

  records() {
    return this.getDataRange().getValues().slice(1);
  }

  setFrozenRows() {}
  setColumnWidth() {}
}

function load({chatStatus = 200} = {}) {
  const sheets = {};
  const properties = {TEAM_TOKEN: 'secret', ALLOWED_DOMAIN: 'VTI.com.vn', CHAT_WEBHOOK_URL: 'https://chat.example/hook'};
  const chat = [];
  const book = {
    getSheetByName: (name) => sheets[name] || null,
    insertSheet: (name) => (sheets[name] = new FakeSheet(name))
  };
  const context = vm.createContext({
    console: {error() {}, log() {}},
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: (key) => (key in properties ? properties[key] : null),
        setProperty: (key, value) => { properties[key] = value; }
      })
    },
    LockService: {getScriptLock: () => ({tryLock: () => true, waitLock() {}, releaseLock() {}})},
    SpreadsheetApp: {getActiveSpreadsheet: () => book, openById: () => book},
    ContentService: {
      MimeType: {JSON: 'json'},
      createTextOutput: (text) => ({text, setMimeType() { return this; }})
    },
    UrlFetchApp: {
      fetch: (url, options) => {
        chat.push(JSON.parse(options.payload).text);
        return {getResponseCode: () => chatStatus, getContentText: () => ''};
      }
    }
  });
  for (const file of ['Logic.gs', 'Code.gs']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, file), 'utf8'), context, {filename: file});
  }
  const post = (event) => JSON.parse(context.doPost({postData: {contents: JSON.stringify(event)}}).text);
  return {context, sheets, properties, chat, post};
}

let sequence = 0;
function event(type, fields = {}) {
  sequence += 1;
  return {
    v: 1, id: `id-${sequence}`, ts: new Date().toISOString(), token: 'secret', email: 'an.nguyen@vti.com.vn', type,
    track: 'ccdv-f', route_total: 3, data: {}, ...fields
  };
}

const lesson = (n) => event('lesson_completed', {data: {lesson: `certifications/claude/lessons/0${n}-x`, quiz: '2/2'}});

test('doPost records a member, their progress and the awards a run of lessons earns', () => {
  const app = load();
  assert.equal(app.post(event('member_joined', {track: null, route_total: null, data: {name: 'An Nguyễn', share: true}})).ok, true);
  app.post(event('track_started'));
  [0, 1].forEach((n) => app.post(lesson(n)));
  assert.deepEqual(app.chat, []);
  assert.deepEqual(app.post(lesson(2)), {ok: true, awards: 3});

  assert.equal(app.chat.length, 2, 'a productive day and the first finish');
  assert.match(app.chat[0], /An Nguyễn/);
  assert.match(app.chat[1], /CCDV-F/);
  const member = app.sheets.Members.records()[0];
  assert.deepEqual(member.slice(0, 4), ['an.nguyen@vti.com.vn', 'An Nguyễn', 'ccdv-f', true]);
  const progress = app.sheets.Progress.records()[0];
  assert.deepEqual(progress.slice(0, 5), ['an.nguyen@vti.com.vn', 'ccdv-f', 3, 3, 100]);
  assert.equal(Object.prototype.toString.call(progress[6]), '[object Date]', 'finished_at is set');
  assert.equal(app.sheets.Events.records().length, 5);
  assert.ok(app.sheets.Events.records().every((row) => !row[9].includes('secret')));
});

test('doPost answers duplicates and rejections without writing twice', () => {
  const app = load();
  const once = lesson(0);
  assert.equal(app.post(once).ok, true);
  assert.deepEqual(app.post(once), {ok: true, duplicate: true});
  assert.equal(app.post({...lesson(1), token: 'old'}).error, 'bad_token');
  assert.equal(app.post({...lesson(1), email: 'an@gmail.com'}).error, 'bad_domain');
  assert.equal(JSON.parse(app.context.doPost({postData: {contents: '{oops'}}).text).error, 'bad_json');
  assert.equal(app.sheets.Events.records().length, 1);
});

test('a hidden score still earns an anonymous mock message', () => {
  const app = load();
  app.post(event('member_joined', {track: null, route_total: null, data: {name: 'An', share: false}}));
  app.post(event('assessment_attempted', {data: {kind: 'mock', assessment_id: 'claude-ccdv-f-mock-01', score: 90}}));
  assert.equal(app.chat.length, 1);
  assert.doesNotMatch(app.chat[0], /An|90/);
});

test('weeklySummary posts the podium once and moves the window forward', () => {
  const app = load();
  app.post(event('member_joined', {track: null, route_total: null, data: {name: 'An', share: true}}));
  app.post(event('track_started', {route_total: 10}));
  app.post({...lesson(0), route_total: 10});
  app.chat.length = 0;
  app.context.weeklySummary();
  assert.equal(app.chat.length, 1);
  assert.match(app.chat[0], /🥇 An: \+10% route \(CCDV-F đang ở 10%\)/);
  const marker = app.properties.LAST_WEEKLY_AT;
  assert.ok(Number(marker) > 0);
  app.context.weeklySummary();
  assert.equal(app.chat.length, 1, 'nothing new, so nothing is sent');
});

test('a failed weekly delivery keeps the window open for a rerun', () => {
  const app = load({chatStatus: 500});
  app.post({...lesson(0), route_total: 10});
  assert.throws(() => app.context.weeklySummary(), /not delivered/);
  assert.equal(app.properties.LAST_WEEKLY_AT, undefined);
});

test('rebuildProgress recomputes the Progress tab after rows are deleted by hand', () => {
  const app = load();
  [0, 1].forEach((n) => app.post(lesson(n)));
  app.sheets.Events.cells.splice(2, 1);
  app.context.rebuildProgress();
  assert.deepEqual(app.sheets.Progress.records()[0].slice(0, 5), ['an.nguyen@vti.com.vn', 'ccdv-f', 1, 3, 33.3]);
});
