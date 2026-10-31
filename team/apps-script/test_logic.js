const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const logic = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'Logic.gs'), 'utf8'), logic, {filename: 'Logic.gs'});

// Values built inside the vm context carry its prototypes; compare them as plain JSON.
const plain = (value) => JSON.parse(JSON.stringify(value));
const CONFIG = {token: 'secret', domain: 'vti.com.vn'};
const AN = 'an.nguyen@vti.com.vn';
const lessonPath = (n) => `certifications/claude/lessons/${String(n).padStart(2, '0')}-lesson`;

function posted(overrides = {}) {
  return {
    v: 1, id: 'id-1', ts: '2026-11-02T21:15:04+07:00', token: 'secret', email: AN, type: 'lesson_completed',
    track: 'ccdv-f', route_total: 15, data: {lesson: lessonPath(0), quiz: '2/2'}, ...overrides
  };
}

let sequence = 0;
function stored(fields) {
  sequence += 1;
  const event = {
    v: 1, id: `e${sequence}`, ts: '2026-11-02T10:00:00+07:00', email: AN, type: 'lesson_completed',
    track: 'ccdv-f', route_total: 15, data: {}, ...fields
  };
  if (fields.lesson) event.data = {lesson: fields.lesson, quiz: '2/2'};
  if (fields.score !== undefined) event.data = {kind: fields.kind || 'mock', score: fields.score, assessment_id: 'x'};
  delete event.lesson;
  delete event.score;
  delete event.kind;
  return logic.rowEvent(logic.eventRow(event, fields.receivedAt || new Date(Date.parse(event.ts))));
}

function awardsFor(events, ctx = {}) {
  const current = events[events.length - 1];
  return plain(logic.detectAwards(events, current, {keys: {}, now: new Date('2026-11-02T23:00:00+07:00'), share: true, name: 'An', ...ctx}));
}

test('a well-formed event passes and each broken field gets its own error code', () => {
  assert.deepEqual(plain(logic.validateEvent(posted(), CONFIG)), {ok: true});
  const cases = [
    [{token: 'wrong'}, 'bad_token'],
    [{email: 'an@gmail.com'}, 'bad_domain'],
    [{email: 'An.Nguyen@vti.com.vn'}, 'bad_domain'],
    [{v: 2}, 'bad_request'],
    [{type: 'lesson_deleted'}, 'bad_request'],
    [{ts: 'yesterday'}, 'bad_request'],
    [{route_total: 0}, 'bad_request'],
    [{data: {lesson: '../../etc', quiz: '2/2'}}, 'bad_request'],
    [{type: 'assessment_attempted', data: {kind: 'mock', score: 120}}, 'bad_request'],
    [{type: 'member_joined', track: null, route_total: null, data: {name: '=IMPORTXML(1)', share: true}}, 'bad_request']
  ];
  for (const [change, error] of cases) {
    assert.equal(logic.validateEvent(posted(change), CONFIG).error, error, JSON.stringify(change));
  }
  assert.equal(logic.validateEvent(posted(), {token: null, domain: 'vti.com.vn'}).error, 'bad_token');
  assert.equal(logic.validateEvent(posted({type: 'member_joined', track: null, route_total: null, data: {name: 'An', share: false}}), CONFIG).ok, true);
});

test('a stored row keeps everything except the token', () => {
  const row = logic.eventRow(posted(), new Date('2026-11-02T14:20:00Z'));
  assert.equal(row.length, logic.SHEET_COLUMNS.Events.length);
  assert.ok(!row[9].includes('secret'));
  const event = plain(logic.rowEvent(row));
  assert.equal(event.lesson, lessonPath(0));
  assert.equal(event.routeTotal, 15);
  assert.equal(event.score, null);
});

test('progress counts distinct lessons and dates the finish at the last new lesson', () => {
  const events = [
    stored({type: 'track_started', ts: '2026-11-01T09:00:00+07:00'}),
    stored({lesson: lessonPath(0), route_total: 2}),
    stored({lesson: lessonPath(0), route_total: 2}),
    stored({lesson: lessonPath(1), route_total: 2, ts: '2026-11-03T08:00:00+07:00'}),
    stored({lesson: lessonPath(5), track: 'ccar-f', route_total: 21})
  ];
  assert.deepEqual(plain(logic.computeProgress(events, AN, 'ccdv-f', 2)), {
    email: AN, track: 'ccdv-f', done: 2, total: 2, pct: 100,
    startedAt: '2026-11-01T09:00:00+07:00', finishedAt: '2026-11-03T08:00:00+07:00'
  });
  assert.equal(logic.computeProgress(events, AN, 'ccdv-f', 15).pct, 13.3);
  assert.deepEqual(plain(logic.progressPairs(events)).map((pair) => pair.track), ['ccdv-f', 'ccar-f']);
});

test('the productive-day award fires on the third new lesson of a GMT+7 day, once', () => {
  const day = [0, 1, 2].map((n) => stored({lesson: lessonPath(n), ts: `2026-11-02T0${n + 7}:00:00+07:00`}));
  assert.deepEqual(awardsFor(day.slice(0, 2)), []);
  const [award] = awardsFor(day);
  assert.equal(award.key, `day3:${AN}:2026-11-02`);
  assert.deepEqual(award.values, {name: 'An', count: 3});
  assert.deepEqual(awardsFor(day, {keys: {[award.key]: true}}), []);
  assert.deepEqual(awardsFor([...day.slice(0, 2), stored({lesson: lessonPath(0)})]), [], 'a repeated lesson is not new');
  const lateNight = [...day.slice(0, 2), stored({lesson: lessonPath(3), ts: '2026-11-01T23:30:00+07:00'})];
  assert.deepEqual(awardsFor(lateNight), [], '23:30 belongs to the previous day');
});

test('a lesson that arrives more than a day late earns no productive-day award', () => {
  const day = [0, 1, 2].map((n) => stored({lesson: lessonPath(n), ts: '2026-10-30T10:00:00+07:00'}));
  assert.deepEqual(awardsFor(day), []);
});

test('the first mock at 80 or more is praised by name, or anonymously when sharing is off', () => {
  const mock = [stored({type: 'assessment_attempted', score: 82.5})];
  const [named] = awardsFor(mock);
  assert.equal(named.situation, 'mock80');
  assert.deepEqual(named.values, {name: 'An', track: 'CCDV-F', score: '82.5'});
  assert.equal(awardsFor(mock, {share: false})[0].situation, 'mock80_anon');
  assert.deepEqual(awardsFor(mock, {keys: {[`mock80:${AN}:ccdv-f`]: true}}), []);
  assert.deepEqual(awardsFor([stored({type: 'assessment_attempted', score: 95, kind: 'diagnostic'})]), []);
  assert.deepEqual(awardsFor([stored({type: 'assessment_attempted', score: 79.9})]), []);
});

test('the first finisher of a track gets the pioneer message and later finishers get their own', () => {
  const finish = [stored({lesson: lessonPath(0), route_total: 2}), stored({lesson: lessonPath(1), route_total: 2})];
  const pioneer = awardsFor(finish);
  assert.deepEqual(pioneer.map((a) => [a.key, a.situation]), [['first:ccdv-f', 'first_finish'], [`finish:${AN}:ccdv-f`, '']]);
  const later = awardsFor(finish, {keys: {'first:ccdv-f': true}});
  assert.deepEqual(later.map((a) => [a.key, a.situation]), [[`finish:${AN}:ccdv-f`, 'finish']]);
  assert.deepEqual(awardsFor(finish, {keys: {'first:ccdv-f': true, [`finish:${AN}:ccdv-f`]: true}}), []);
});

test('the weekly gain sums first-time lessons per track inside the window only', () => {
  const window = {from: Date.parse('2026-11-01T22:00:00+07:00'), to: Date.parse('2026-11-08T22:00:00+07:00')};
  const inside = new Date('2026-11-04T10:00:00+07:00');
  const events = [
    stored({lesson: lessonPath(0), route_total: 10, receivedAt: new Date('2026-10-30T10:00:00+07:00')}),
    stored({lesson: lessonPath(0), route_total: 10, receivedAt: inside}),
    stored({lesson: lessonPath(0), track: 'ccar-p', route_total: 25, receivedAt: inside}),
    stored({lesson: lessonPath(1), route_total: 10, receivedAt: inside}),
    stored({lesson: lessonPath(2), track: 'ccar-p', route_total: 25, receivedAt: inside}),
    stored({lesson: lessonPath(3), route_total: 10, receivedAt: new Date('2026-11-08T22:00:01+07:00')})
  ];
  assert.deepEqual(plain(logic.weeklyRanking(events, window)), [{email: AN, gain: 14, rank: 1}]);
  assert.deepEqual(plain(logic.weeklyRanking(events.slice(0, 3), window)), [], 'repeats and carried-over lessons add nothing');
});

test('ties share a rank, so the podium can hold more than three people', () => {
  const window = {from: 0, to: Date.parse('2027-01-01T00:00:00Z')};
  const gains = {a: 3, b: 3, c: 2, d: 1, e: 1, f: 0};
  const events = [];
  for (const [who, count] of Object.entries(gains)) {
    for (let n = 0; n < count; n += 1) events.push(stored({email: `${who}@vti.com.vn`, lesson: lessonPath(n), route_total: 10}));
  }
  const rows = plain(logic.weeklyRanking(events, window));
  assert.deepEqual(rows.map((row) => [row.email[0], row.rank]), [['a', 1], ['b', 1], ['c', 2], ['d', 3], ['e', 3]]);
  const text = logic.renderRanking(rows.slice(0, 1), {[rows[0].email]: {name: 'Anh', track: 'ccdv-f'}}, {[`${rows[0].email}|ccdv-f`]: 46.7});
  assert.equal(text, '🥇 Anh: +30% route (CCDV-F đang ở 46.7%)');
  assert.deepEqual(plain(logic.weeklyRanking([], window)), []);
});

test('templates come from the sheet when present, else from the defaults, and keep unknown placeholders', () => {
  const sheet = [{situation: 'day3', text: 'A {name}'}, {situation: 'day3', text: 'B {name} {missing}'}, {situation: 'finish', text: ''}];
  assert.equal(logic.pickTemplate(sheet, 'day3', () => 0), 'A {name}');
  assert.equal(logic.renderTemplate(logic.pickTemplate(sheet, 'day3', () => 0.99), {name: 'An'}), 'B An {missing}');
  assert.equal(logic.pickTemplate(sheet, 'finish', () => 0), logic.DEFAULT_TEMPLATES.finish[0]);
  for (const [situation, texts] of Object.entries(logic.DEFAULT_TEMPLATES)) {
    assert.ok(texts.length >= 2, situation);
  }
  assert.ok(logic.DEFAULT_TEMPLATES.mock80_anon.every((text) => !text.includes('{name}') && !text.includes('{score}')));
});

test('member rows follow joins, share changes and track starts', () => {
  const now = new Date('2026-11-02T12:00:00Z');
  let row = logic.memberRow(null, posted({type: 'track_started', data: {}}), now);
  assert.deepEqual(plain(row).slice(0, 4), [AN, 'an.nguyen', 'ccdv-f', false]);
  row = logic.memberRow(row, posted({type: 'member_joined', track: null, data: {name: 'An Nguyễn', share: true}}), now);
  row = logic.memberRow(row, posted({type: 'lesson_completed', track: 'ccar-f'}), now);
  assert.deepEqual(plain(row).slice(0, 4), [AN, 'An Nguyễn', 'ccdv-f', true]);
  row = logic.memberRow(row, posted({type: 'share_changed', track: null, data: {share: false}}), now);
  assert.equal(row[3], false);
  assert.equal(logic.localDay('2026-11-02T17:00:00Z'), '2026-11-03');
});
