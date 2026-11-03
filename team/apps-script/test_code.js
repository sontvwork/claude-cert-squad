const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

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

  deleteRow(row) {
    this.cells.splice(row - 1, 1);
  }

  deleteRows(row, count) {
    this.cells.splice(row - 1, count);
  }

  setFrozenRows() {}
  setColumnWidth() {}
}

function load({chatStatus = 200, mailFails = false} = {}) {
  const sheets = {};
  const properties = {TEAM_TOKEN: 'secret', ALLOWED_DOMAIN: 'VTI.com.vn', CHAT_WEBHOOK_URL: 'https://chat.example/hook'};
  const chat = [];
  const mails = [];
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
    Utilities: {
      DigestAlgorithm: {SHA_256: 'sha256'},
      Charset: {UTF_8: 'utf8'},
      // Apps Script returns signed bytes.
      computeDigest: (algorithm, text) => Array.from(crypto.createHash(algorithm).update(text, 'utf8').digest(), (b) => (b > 127 ? b - 256 : b))
    },
    MailApp: {
      sendEmail: (message) => {
        if (mailFails) throw new Error('Service invoked too many times: email');
        mails.push(message);
      }
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
  return {context, sheets, properties, chat, mails, post};
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

function scratchCopy(app) {
  delete app.properties.TEAM_TOKEN;
  delete app.properties.CHAT_WEBHOOK_URL;
}

test('seedDemo refuses to run where the live secrets are set', () => {
  const app = load();
  assert.throws(() => app.context.seedDemo(), /scratch copy/);
  assert.equal(app.sheets.Events, undefined, 'nothing was written');
});

test('seedDemoRows_ fills Events, Members and Progress with every Dashboard state', () => {
  const app = load();
  scratchCopy(app);
  app.context.seedDemoRows_(app.context.book_(), new Date());
  app.context.rebuildProgress();

  const members = app.sheets.Members.records();
  assert.equal(members.length, 6);
  assert.equal(members.find((row) => row[0] === 'demo.chi@vti.com.vn')[3], false, 'one member hides the score');
  const pct = Object.fromEntries(app.sheets.Progress.records().map((row) => [`${row[0].split('@')[0].slice(5)}|${row[1]}`, row[4]]));
  assert.deepEqual(pct, {'an|ccdv-f': 100, 'binh|ccdv-f': 70, 'chi|ccar-f': 40, 'dung|ccao-f': 15, 'em|ccdv-f': 25, 'em|ccao-f': 50, 'giang|ccar-p': 0});
  const finished = app.sheets.Progress.records().find((row) => row[0] === 'demo.an@vti.com.vn');
  assert.equal(Object.prototype.toString.call(finished[6]), '[object Date]', 'finished_at is set');
  assert.ok(app.sheets.Events.records().some((row) => row[8] === 'mock' && row[7] === 90));
});

test('seedDemoRows_ twice leaves one copy, and clearing keeps real rows', () => {
  const app = load();
  app.post(lesson(0));
  scratchCopy(app);
  const book = app.context.book_();
  app.context.seedDemoRows_(book, new Date());
  const seeded = app.sheets.Events.records().length;
  app.context.seedDemoRows_(book, new Date());
  assert.equal(app.sheets.Events.records().length, seeded);
  assert.equal(app.sheets.Members.records().length, 7);

  app.context.removeDemoRows_(book);
  app.context.rebuildProgress();
  assert.equal(app.sheets.Events.records().length, 1);
  assert.deepEqual(app.sheets.Members.records().map((row) => row[0]), ['an.nguyen@vti.com.vn']);
  assert.deepEqual(app.sheets.Progress.records().map((row) => row[0]), ['an.nguyen@vti.com.vn']);
});

test('rebuildDashboard refuses a locale whose formula separators are not commas', () => {
  const app = load();
  assert.throws(() => app.context.assertFormulaLocale_({getSpreadsheetLocale: () => 'vi_VN'}), /vi_VN.*United States/);
  assert.doesNotThrow(() => app.context.assertFormulaLocale_({getSpreadsheetLocale: () => 'en_US'}));
  assert.throws(() => app.context.rebuildDashboard(), /getSpreadsheetLocale|locale/i, 'the check runs before any Dashboard write');
});

const AUTH = 'a'.repeat(64);
const state = (action, fields = {}) => ({action, token: 'secret', email: 'an.nguyen@vti.com.vn', auth: AUTH, ...fields});

test('a member claims sync, pushes revisions and pulls the newest one back', () => {
  const app = load();
  assert.deepEqual(app.post(state('state_claim')), {ok: true, rev: 0, created: true});
  assert.deepEqual(app.post(state('state_claim')), {ok: true, rev: 0}, 'claiming again with the same key is harmless');
  assert.deepEqual(app.post(state('state_pull', {rev: null})), {ok: true, rev: 0, blob: ''});

  assert.deepEqual(app.post(state('state_push', {base_rev: 0, blob: 'first'})), {ok: true, rev: 1});
  const big = 'x'.repeat(100000) + '-12';
  assert.deepEqual(app.post(state('state_push', {base_rev: 1, blob: big})), {ok: true, rev: 2});
  assert.deepEqual(app.post(state('state_pull', {rev: 1})), {ok: true, rev: 2, blob: big});
  assert.deepEqual(app.post(state('state_pull', {rev: 2})), {ok: true, rev: 2, unchanged: true});

  const chunks = app.sheets.StateChunks.records();
  assert.equal(chunks.length, 3, 'only revision 2 is kept, in three pieces');
  assert.ok(chunks.every((row) => row[1] === 2 && row[3].startsWith('c:') && row[3].length <= 45002));
  const row = app.sheets.State.records()[0];
  assert.deepEqual(row.slice(2, 3).concat(row.slice(4, 6)), [2, big.length, 3]);
  assert.match(row[1], /^sha256:[0-9a-f]{64}$/);
  assert.ok(!JSON.stringify(app.sheets.State.records()).includes(AUTH), 'the auth value itself is not stored');
});

test('sync refuses another key, a stale base and an unknown email', () => {
  const app = load();
  assert.equal(app.post(state('state_pull')).error, 'not_found');
  app.post(state('state_claim'));
  const other = {auth: 'b'.repeat(64)};
  assert.equal(app.post(state('state_claim', other)).error, 'claimed');
  assert.equal(app.post(state('state_pull', other)).error, 'bad_sync_key');
  assert.equal(app.post(state('state_push', {...other, base_rev: 0, blob: 'x'})).error, 'bad_sync_key');
  app.post(state('state_push', {base_rev: 0, blob: 'one'}));
  assert.deepEqual(app.post(state('state_push', {base_rev: 0, blob: 'two'})), {ok: false, error: 'stale', rev: 1});
  assert.equal(app.post(state('state_pull', {token: 'old'})).error, 'bad_token');
  assert.deepEqual(app.post(state('state_pull', {rev: null})).blob, 'one');
});

test('two members keep separate snapshots and a reclaim drops leftover chunks', () => {
  const app = load();
  const binh = {email: 'binh@vti.com.vn', auth: 'c'.repeat(64)};
  app.post(state('state_claim'));
  app.post(state('state_claim', binh));
  app.post(state('state_push', {base_rev: 0, blob: 'an-1'}));
  app.post(state('state_push', {...binh, base_rev: 0, blob: 'binh-1'}));
  app.post(state('state_push', {base_rev: 1, blob: 'an-2'}));
  assert.equal(app.post(state('state_pull', binh)).blob, 'binh-1');
  assert.equal(app.post(state('state_pull')).blob, 'an-2');

  app.sheets.State.deleteRow(2); // the admin resets An by deleting the State row
  assert.equal(app.post(state('state_claim', {auth: 'd'.repeat(64)})).rev, 0);
  assert.deepEqual(app.sheets.StateChunks.records().map((row) => row[0]), ['binh@vti.com.vn']);
});

test('a member mails themselves the add-a-machine command, at most once per 10 minutes', () => {
  const app = load();
  assert.equal(app.post(state('state_mail', {code: 'abc'})).error, 'not_found');
  app.post(state('state_claim'));
  assert.equal(app.post(state('state_mail', {code: 'abc', auth: 'b'.repeat(64)})).error, 'bad_sync_key');
  assert.equal(app.post(state('state_mail', {code: 'a b'})).error, 'bad_request');
  assert.deepEqual(app.post(state('state_mail', {code: 'eyJjb2RlIjoxfQ'})), {ok: true, mailed: true});
  assert.equal(app.mails.length, 1);
  assert.equal(app.mails[0].to, 'an.nguyen@vti.com.vn');
  assert.equal(app.mails[0].noReply, true);
  assert.match(app.mails[0].body, /python3 scripts\/team\/setup.py --join eyJjb2RlIjoxfQ/);
  assert.equal(app.post(state('state_mail', {code: 'eyJjb2RlIjoxfQ'})).error, 'mail_limited');

  app.post(state('state_push', {base_rev: 0, blob: 'one'}));
  assert.equal(Object.prototype.toString.call(app.sheets.State.records()[0][6]), '[object Date]', 'a push keeps mailed_at');
  assert.ok(!JSON.stringify(app.sheets).includes('eyJjb2RlIjoxfQ'), 'the code is never stored');
});

test('a mail the service refuses is reported and does not start the 10 minute wait', () => {
  const app = load({mailFails: true});
  app.post(state('state_claim'));
  assert.equal(app.post(state('state_mail', {code: 'abc'})).error, 'mail_failed');
  assert.equal(app.sheets.State.records()[0][6], '');
});
