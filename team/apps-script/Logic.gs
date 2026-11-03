// Pure rules for the team progress backend: event validation, progress,
// awards, the weekly ranking, message templates and the sync request and
// chunk rules. Nothing here calls a
// Google API, so test_logic.js runs this file under node. Code.gs reads the
// Sheet into plain rows, calls these functions and writes the results back.
// Event schema and award rules: team/README.md.

var SCHEMA_VERSION = 1;
var EVENT_TYPES = ['member_joined', 'share_changed', 'track_started', 'lesson_completed', 'assessment_attempted'];
var TRACK_TYPES = ['track_started', 'lesson_completed', 'assessment_attempted'];
var LESSON_PATTERN = /^certifications\/claude\/lessons\/[a-z0-9-]+$/;
var TRACK_PATTERN = /^[a-z]{4}-[a-z]$/;
var LOCAL_PART_PATTERN = /^[a-z0-9][a-z0-9._%+-]*$/;
var DAY_MS = 24 * 60 * 60 * 1000;
var ZONE_OFFSET_MS = 7 * 60 * 60 * 1000;
var MOCK_PASS = 80;
var DAY_TARGET = 3;
var MEDALS = ['🥇', '🥈', '🥉'];
var STATE_ACTIONS = ['state_claim', 'state_pull', 'state_push', 'state_mail'];
var MAIL_GAP_MS = 10 * 60 * 1000; // one device-code mail per member every 10 minutes
var DEVICE_CODE_MAX = 4000;
var STATE_CHUNK_CHARS = 45000; // a Sheet cell holds at most 50000 characters
var STATE_MAX_CHARS = 1400000; // 1 MB sealed archive, base64url
var CHUNK_PREFIX = 'c:'; // keeps a chunk such as "-12e3" from being read as a number
var HASH_PREFIX = 'sha256:';

var SHEET_COLUMNS = {
  Events: ['received_at', 'id', 'ts', 'email', 'type', 'track', 'lesson', 'score', 'kind', 'payload'],
  Members: ['email', 'name', 'track', 'share', 'joined_at', 'last_activity'],
  Progress: ['email', 'track', 'done', 'total', 'pct', 'started_at', 'finished_at'],
  Awards: ['key', 'email', 'type', 'ts'],
  Templates: ['situation', 'text'],
  State: ['email', 'auth_hash', 'rev', 'updated_at', 'size', 'chunks', 'mailed_at'],
  StateChunks: ['email', 'rev', 'idx', 'data']
};

// Starter messages. Rows in the Templates tab replace the defaults of their situation.
var DEFAULT_TEMPLATES = {
  day3: [
    '🔥 {name} vừa xong {count} bài trong hôm nay! Phong độ quá đỉnh 💪',
    '🚀 Ngày năng suất của {name}: {count} bài Complete chỉ trong một ngày. Nể thật sự! 👏',
    '⚡ {name} đang bật chế độ tăng tốc: {count} bài hôm nay rồi! 🎯'
  ],
  mock80: [
    '🎉 {name} vừa đạt {score}% bài mock {track}! Đỉnh của chóp 🏆',
    '🌟 Mock {track}: {name} cán mốc {score}%. Sẵn sàng chinh chiến rồi! 💯',
    '👏 Tràng pháo tay cho {name}: lần đầu vượt 80% mock {track} với {score}%! 🎯'
  ],
  mock80_anon: [
    '🎉 Một cao thủ giấu tên vừa đạt điểm cao mock {track}! 🥷',
    '🥷 Có người lặng lẽ vượt 80% mock {track}. Team mình nhiều nhân tài quá! ✨'
  ],
  first_finish: [
    '🏁 {name} là người ĐẦU TIÊN về đích track {track}! Người mở đường đây rồi 🏆👑',
    '👑 Ngôi vị tiên phong {track} thuộc về {name}: hoàn thành toàn bộ route! 🎉'
  ],
  finish: [
    '🎓 {name} vừa hoàn thành toàn bộ route {track}! Chúc mừng nhé 🎉',
    '✅ Thêm một chiến binh về đích {track}: {name}! 👏🔥'
  ],
  weekly: [
    '📊 Tổng kết tuần! Những người bứt phá nhất:\n{ranking}\nTuần mới tiếp tục cháy nhé! 🔥',
    '🏆 Bảng vàng tuần này:\n{ranking}\nCảm ơn cả team đã giữ lửa! ✨'
  ]
};

function validateEvent(event, config) {
  if (!event || typeof event !== 'object' || Array.isArray(event)) return fail_('bad_request', 'body must be a JSON object');
  if (!config.token || event.token !== config.token) return fail_('bad_token', 'token rejected');
  if (event.v !== SCHEMA_VERSION) return fail_('bad_request', 'unsupported schema version');
  if (!isText_(event.id) || event.id.length > 64) return fail_('bad_request', 'id is missing');
  if (!isText_(event.ts) || isNaN(Date.parse(event.ts))) return fail_('bad_request', 'ts is not a timestamp');
  if (!isMemberEmail(event.email, config.domain)) return fail_('bad_domain', 'email must end with @' + config.domain);
  if (EVENT_TYPES.indexOf(event.type) < 0) return fail_('bad_request', 'unknown type ' + event.type);
  var data = event.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return fail_('bad_request', 'data must be an object');
  if (TRACK_TYPES.indexOf(event.type) >= 0) {
    if (!TRACK_PATTERN.test(event.track)) return fail_('bad_request', 'track is invalid');
    if (!Number.isInteger(event.route_total) || event.route_total < 1) return fail_('bad_request', 'route_total is invalid');
  }
  if (event.type === 'member_joined') {
    if (!isDisplayName_(data.name)) return fail_('bad_request', 'name is invalid');
    if (typeof data.share !== 'boolean') return fail_('bad_request', 'share must be true or false');
  }
  if (event.type === 'share_changed' && typeof data.share !== 'boolean') return fail_('bad_request', 'share must be true or false');
  if (event.type === 'lesson_completed') {
    if (!LESSON_PATTERN.test(data.lesson)) return fail_('bad_request', 'lesson path is invalid');
    if (!/^\d+\/\d+$/.test(data.quiz)) return fail_('bad_request', 'quiz must look like N/M');
  }
  if (event.type === 'assessment_attempted') {
    if (data.kind !== 'diagnostic' && data.kind !== 'mock') return fail_('bad_request', 'kind must be diagnostic or mock');
    if (!isPercent_(data.score)) return fail_('bad_request', 'score must be between 0 and 100');
  }
  return {ok: true};
}

// Sync requests carry {action, token, email, auth} plus rev (pull), base_rev and blob (push) or code (mail).
// The blob is sealed on the member's machine; the server only stores and returns it.
function validateStateRequest(req, config) {
  if (!req || typeof req !== 'object' || Array.isArray(req)) return fail_('bad_request', 'body must be a JSON object');
  if (!config.token || req.token !== config.token) return fail_('bad_token', 'token rejected');
  if (!isMemberEmail(req.email, config.domain)) return fail_('bad_domain', 'email must end with @' + config.domain);
  if (STATE_ACTIONS.indexOf(req.action) < 0) return fail_('bad_request', 'unknown action ' + req.action);
  if (typeof req.auth !== 'string' || !/^[0-9a-f]{64}$/.test(req.auth)) return fail_('bad_request', 'auth is invalid');
  if (req.action === 'state_pull' && req.rev !== null && req.rev !== undefined && !isRevision_(req.rev)) {
    return fail_('bad_request', 'rev is invalid');
  }
  if (req.action === 'state_push') {
    if (!isRevision_(req.base_rev)) return fail_('bad_request', 'base_rev is invalid');
    if (typeof req.blob !== 'string' || !/^[A-Za-z0-9_-]+$/.test(req.blob)) return fail_('bad_request', 'blob is invalid');
    if (req.blob.length > STATE_MAX_CHARS) return fail_('bad_request', 'blob is larger than ' + STATE_MAX_CHARS + ' characters');
  }
  if (req.action === 'state_mail') {
    if (typeof req.code !== 'string' || !/^[A-Za-z0-9_-]+$/.test(req.code) || req.code.length > DEVICE_CODE_MAX) {
      return fail_('bad_request', 'code is invalid');
    }
  }
  return {ok: true};
}

function mailAllowed(lastMailedAt, now) {
  var last = lastMailedAt instanceof Date ? lastMailedAt.getTime() : Date.parse(lastMailedAt);
  return isNaN(last) || now.getTime() - last >= MAIL_GAP_MS;
}

// The member's own add-a-machine command. The code holds their sync key, so it goes only to their inbox.
function deviceMail(code) {
  return {
    subject: 'Mã thêm máy học chứng chỉ Claude',
    body: [
      'Chào bạn,',
      '',
      'Đồng bộ nhiều máy đã được bật cho tiến độ học của bạn.',
      'Trên máy mới, clone repo rồi chạy lệnh sau ở thư mục gốc repo:',
      '',
      'python3 scripts/team/setup.py --join ' + code,
      '',
      'Lệnh này chứa khoá đồng bộ riêng của bạn. Đừng chuyển tiếp mail này và đừng dán lệnh vào Chat chung.',
      'Muốn nhận lại mail này: chạy python3 scripts/team/setup.py --email-device trên máy đang học.'
    ].join('\n')
  };
}

function splitChunks(text, size) {
  var parts = [];
  for (var i = 0; i < text.length; i += size) parts.push(CHUNK_PREFIX + text.slice(i, i + size));
  return parts;
}

// rows: [[idx, data]] in any order. Returns null when a chunk is missing or damaged.
function joinChunks(rows, count) {
  var parts = [];
  rows.forEach(function (row) { parts[Number(row[0])] = String(row[1]); });
  if (parts.length !== count) return null;
  for (var i = 0; i < count; i++) {
    if (typeof parts[i] !== 'string' || parts[i].indexOf(CHUNK_PREFIX) !== 0) return null;
    parts[i] = parts[i].slice(CHUNK_PREFIX.length);
  }
  return parts.join('');
}

// Sorted row numbers to [start, length] runs, so contiguous rows are read or deleted in one call.
function rowRuns(rows) {
  var runs = [];
  rows.slice().sort(function (a, b) { return a - b; }).forEach(function (row) {
    var last = runs[runs.length - 1];
    if (last && last[0] + last[1] === row) last[1] += 1;
    else runs.push([row, 1]);
  });
  return runs;
}

function hashCell(bytes) {
  return HASH_PREFIX + bytes.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join('');
}

function isMemberEmail(email, domain) {
  if (typeof email !== 'string' || !domain) return false;
  var at = email.indexOf('@');
  return email === email.toLowerCase() && LOCAL_PART_PATTERN.test(email.slice(0, at)) && email.slice(at + 1) === domain;
}

function eventRow(event, receivedAt) {
  var data = event.data || {};
  var payload = {};
  Object.keys(event).forEach(function (key) {
    if (key !== 'token') payload[key] = event[key];
  });
  return [
    receivedAt, event.id, event.ts, event.email, event.type, event.track || '', data.lesson || '',
    isPercent_(data.score) ? data.score : '', data.kind || '', JSON.stringify(payload)
  ];
}

function rowEvent(row) {
  var payload = {};
  try {
    payload = JSON.parse(row[9] || '{}');
  } catch (err) {
    payload = {};
  }
  return {
    receivedAt: row[0] instanceof Date ? row[0] : new Date(row[0]),
    id: String(row[1]),
    ts: row[2] instanceof Date ? row[2].toISOString() : String(row[2]),
    email: String(row[3]),
    type: String(row[4]),
    track: String(row[5]),
    lesson: String(row[6]),
    score: row[7] === '' || row[7] === null ? null : Number(row[7]),
    kind: String(row[8]),
    routeTotal: Number(payload.route_total) || 0,
    data: payload.data || {}
  };
}

function memberRow(row, event, now) {
  var next = row ? row.slice(0, 6) : [event.email, event.email.split('@')[0], '', false, now, now];
  var data = event.data || {};
  if (event.type === 'member_joined') {
    next[1] = data.name;
    next[3] = data.share;
  }
  if (event.type === 'share_changed') next[3] = data.share;
  if (event.type === 'track_started' || (TRACK_TYPES.indexOf(event.type) >= 0 && !next[2])) next[2] = event.track;
  var at = new Date(event.ts);
  if (!(next[5] instanceof Date) || at > next[5]) next[5] = at;
  return next;
}

function computeProgress(events, email, track, total) {
  var seen = {};
  var done = 0;
  var startedAt = '';
  var firstAt = '';
  var finishedAt = '';
  events.forEach(function (event) {
    if (event.email !== email || event.track !== track) return;
    if (!firstAt) firstAt = event.ts;
    if (event.type === 'track_started' && !startedAt) startedAt = event.ts;
    if (event.type !== 'lesson_completed' || !event.lesson || seen[event.lesson]) return;
    seen[event.lesson] = true;
    done += 1;
    if (done >= total && !finishedAt) finishedAt = event.ts;
  });
  return {
    email: email, track: track, done: done, total: total, pct: percent_(done, total),
    startedAt: startedAt || firstAt, finishedAt: finishedAt
  };
}

// Every member x track pair with the route length its newest event reported.
function progressPairs(events) {
  var pairs = {};
  events.forEach(function (event) {
    if (!event.track || !event.routeTotal) return;
    pairs[event.email + '|' + event.track] = {email: event.email, track: event.track, total: event.routeTotal};
  });
  return Object.keys(pairs).map(function (key) { return pairs[key]; });
}

// awards: [{key, type, situation, values}]. An empty situation records the key without a message.
function detectAwards(events, current, ctx) {
  var awards = [];
  var name = ctx.name || current.email.split('@')[0];
  var label = trackLabel(current.track);
  var upto = eventsThrough_(events, current);

  if (current.type === 'lesson_completed') {
    var firsts = firstCompletions_(upto, current.email);
    var isNew = firsts[current.lesson] && firsts[current.lesson].id === current.id;
    if (isNew && ctx.now.getTime() - Date.parse(current.ts) <= DAY_MS) {
      var day = localDay(current.ts);
      var count = Object.keys(firsts).filter(function (lesson) { return localDay(firsts[lesson].ts) === day; }).length;
      var dayKey = 'day3:' + current.email + ':' + day;
      if (count >= DAY_TARGET && !ctx.keys[dayKey]) {
        awards.push({key: dayKey, type: 'day3', situation: 'day3', values: {name: name, count: count}});
      }
    }
    var progress = computeProgress(upto, current.email, current.track, current.routeTotal);
    var finishKey = 'finish:' + current.email + ':' + current.track;
    var firstKey = 'first:' + current.track;
    var values = {name: name, track: label, pct: '100'};
    if (progress.done >= progress.total && !ctx.keys[finishKey]) {
      if (!ctx.keys[firstKey]) {
        awards.push({key: firstKey, type: 'first_finish', situation: 'first_finish', values: values});
        awards.push({key: finishKey, type: 'finish', situation: '', values: values});
      } else {
        awards.push({key: finishKey, type: 'finish', situation: 'finish', values: values});
      }
    }
  }

  if (current.type === 'assessment_attempted' && current.kind === 'mock' && current.score >= MOCK_PASS) {
    var mockKey = 'mock80:' + current.email + ':' + current.track;
    if (!ctx.keys[mockKey]) {
      awards.push({
        key: mockKey, type: 'mock80', situation: ctx.share ? 'mock80' : 'mock80_anon',
        values: {name: name, track: label, score: formatNumber(current.score)}
      });
    }
  }
  return awards;
}

// Gain = sum over tracks of first-ever lesson completions received in (from, to] / route length.
// A lesson carried over to a new track was already counted, so it adds nothing.
function weeklyRanking(events, window) {
  var seen = {};
  var gains = {};
  events.forEach(function (event) {
    if (event.type !== 'lesson_completed' || !event.lesson) return;
    var key = event.email + '|' + event.lesson;
    if (seen[key]) return;
    seen[key] = true;
    var at = event.receivedAt.getTime();
    if (!(at > window.from && at <= window.to) || !(event.routeTotal > 0)) return;
    gains[event.email] = (gains[event.email] || 0) + 100 / event.routeTotal;
  });
  var rows = Object.keys(gains)
    .map(function (email) { return {email: email, gain: round1_(gains[email])}; })
    .filter(function (row) { return row.gain > 0; })
    .sort(function (a, b) { return b.gain - a.gain || (a.email < b.email ? -1 : 1); });
  var rank = 0;
  var previous = null;
  rows.forEach(function (row) {
    if (row.gain !== previous) rank += 1;
    row.rank = rank;
    previous = row.gain;
  });
  return rows.filter(function (row) { return row.rank <= MEDALS.length; });
}

// members: {email: {name, track}}; progress: {'email|track': pct}
function renderRanking(rows, members, progress) {
  return rows.map(function (row) {
    var member = members[row.email] || {};
    var name = member.name || row.email.split('@')[0];
    var line = MEDALS[row.rank - 1] + ' ' + name + ': +' + formatNumber(row.gain) + '% route';
    var pct = member.track ? progress[row.email + '|' + member.track] : undefined;
    if (pct !== undefined) line += ' (' + trackLabel(member.track) + ' đang ở ' + formatNumber(pct) + '%)';
    return line;
  }).join('\n');
}

function pickTemplate(templates, situation, random) {
  var texts = templates
    .filter(function (row) { return row.situation === situation && row.text; })
    .map(function (row) { return row.text; });
  if (!texts.length) texts = DEFAULT_TEMPLATES[situation] || [];
  if (!texts.length) return '';
  return texts[Math.min(texts.length - 1, Math.floor(random() * texts.length))];
}

function renderTemplate(text, values) {
  return String(text).replace(/\{(\w+)\}/g, function (match, key) {
    return values[key] === undefined ? match : String(values[key]);
  });
}

function localDay(ts) {
  return new Date(Date.parse(ts) + ZONE_OFFSET_MS).toISOString().slice(0, 10);
}

function trackLabel(track) {
  return String(track || '').toUpperCase();
}

function formatNumber(value) {
  return String(round1_(Number(value)));
}

function eventsThrough_(events, current) {
  for (var i = 0; i < events.length; i++) {
    if (events[i].id === current.id) return events.slice(0, i + 1);
  }
  return events.concat([current]);
}

function firstCompletions_(events, email) {
  var firsts = {};
  events.forEach(function (event) {
    if (event.email === email && event.type === 'lesson_completed' && event.lesson && !firsts[event.lesson]) {
      firsts[event.lesson] = event;
    }
  });
  return firsts;
}

function percent_(done, total) {
  return total > 0 ? Math.min(100, round1_(done / total * 100)) : 0;
}

function round1_(value) {
  return Math.round(value * 10) / 10;
}

function isRevision_(value) {
  return Number.isInteger(value) && value >= 0;
}

function isText_(value) {
  return typeof value === 'string' && value.length > 0;
}

function isPercent_(value) {
  return typeof value === 'number' && isFinite(value) && value >= 0 && value <= 100;
}

function isDisplayName_(value) {
  return typeof value === 'string' && value === value.trim() && value.length >= 1 && value.length <= 60 &&
    !/^[=+\-@]/.test(value) && !/[\u0000-\u001f]/.test(value);
}

function fail_(error, detail) {
  return {ok: false, error: error, detail: detail};
}
