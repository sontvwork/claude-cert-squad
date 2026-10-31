"""Shared helpers for the team progress recorder.

setup.py joins a member to the team once. record.py turns each progress change
from the claude-certification tutor into an event, queues it in
.team/outbox.jsonl, and sends the queue to the team's Apps Script web app.
See team/README.md.
"""

import base64
import http.client
import json
import math
import os
import re
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKS_DIR = ROOT / "certifications" / "claude" / "tracks"
TEAM_DIR = ROOT / ".team"
LESSON_PREFIX = "certifications/claude/lessons/"
DEFAULT_DOMAIN = "vti.com.vn"
SCHEMA_VERSION = 1
TIMEOUT_S = 10
PASS_RATIO = 0.7

# Server errors that mean the event itself is wrong, so sending it again cannot help.
REJECTED = {"bad_json", "bad_request", "bad_domain"}

LOCAL_PART = re.compile(r"^[a-z0-9][a-z0-9._%+-]*$")
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
FORMULA_START = ("=", "+", "-", "@")


class TeamError(ValueError):
    """A value the member or the agent must correct before the event is valid."""


def encode_join_code(endpoint, token, domain):
    payload = json.dumps({"endpoint": endpoint, "token": token, "domain": domain}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_join_code(code):
    text = code.strip()
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise TeamError("Mã tham gia không hợp lệ. Copy lại toàn bộ mã đang ghim trong Chat.") from error
    if not isinstance(data, dict):
        raise TeamError("Mã tham gia không hợp lệ. Copy lại toàn bộ mã đang ghim trong Chat.")
    return {
        "endpoint": check_endpoint(str(data.get("endpoint", ""))),
        "token": check_token(str(data.get("token", ""))),
        "domain": check_domain(str(data.get("domain", ""))),
    }


def check_endpoint(value):
    endpoint = value.strip()
    if not endpoint.startswith("https://"):
        raise TeamError("URL web app phải bắt đầu bằng https://.")
    if endpoint.endswith("/dev"):
        raise TeamError("Dùng URL /exec của deployment, không dùng URL /dev.")
    return endpoint


def check_token(value):
    token = value.strip()
    if not token:
        raise TeamError("Token team không được để trống.")
    return token


def check_domain(value):
    domain = value.strip().lower()
    if not DOMAIN.match(domain):
        raise TeamError(f"Domain email {value!r} không hợp lệ.")
    return domain


def normalize_email(raw, domain):
    email = raw.strip().lower()
    if "@" not in email:
        email = f"{email}@{domain}"
    local, _, host = email.partition("@")
    if host != domain:
        raise TeamError(f"Email phải có đuôi @{domain}.")
    if not LOCAL_PART.match(local):
        raise TeamError(f"Phần trước @{domain} chỉ gồm chữ thường, số và . _ % + -.")
    return email


def normalize_name(raw):
    name = " ".join(raw.split())
    if not name:
        raise TeamError("Tên hiển thị không được để trống.")
    if len(name) > 60:
        raise TeamError("Tên hiển thị dài tối đa 60 ký tự.")
    if name.startswith(FORMULA_START):
        raise TeamError("Tên hiển thị không được bắt đầu bằng = + - @.")
    return name


def parse_yes(raw, default):
    value = raw.strip().lower()
    if not value:
        return default
    if value in {"y", "yes", "c", "co", "có", "on", "true", "1"}:
        return True
    if value in {"n", "no", "k", "khong", "không", "off", "false", "0"}:
        return False
    raise TeamError("Gõ y (có) hoặc n (không).")


def track_ids(tracks_dir=TRACKS_DIR):
    return sorted(path.stem for path in tracks_dir.glob("*.json"))


def load_track(track_id, tracks_dir=TRACKS_DIR):
    slug = track_id.strip().lower()
    known = track_ids(tracks_dir)
    if slug not in known:
        raise TeamError(f"Track {track_id!r} không tồn tại. Chọn một trong: {', '.join(known)}.")
    return json.loads((tracks_dir / f"{slug}.json").read_text(encoding="utf-8"))


def route(track):
    return [lesson["path"] for lesson in track["lessons"]]


def normalize_lesson(value, track):
    path = value.strip().rstrip("/")
    if "/" not in path:
        path = LESSON_PREFIX + path
    if path not in route(track):
        raise TeamError(
            f"{path} không nằm trong route {track['slug']}. "
            f"Dùng đúng đường dẫn bài trong certifications/claude/tracks/{track['slug']}.json."
        )
    return path


def parse_quiz(value):
    match = re.fullmatch(r"(\d+)/(\d+)", value.strip())
    if not match:
        raise TeamError("--quiz phải có dạng N/M, ví dụ 2/2.")
    right, total = int(match.group(1)), int(match.group(2))
    if total == 0 or right > total:
        raise TeamError("--quiz phải có 0 <= N <= M và M > 0.")
    if right / total < PASS_RATIO:
        raise TeamError("Bài chỉ Complete khi post-quiz đạt từ 70% trở lên. Chỉ ghi bài đã Complete.")
    return f"{right}/{total}"


def parse_percent(value, label):
    try:
        score = float(value)
    except (TypeError, ValueError) as error:
        raise TeamError(f"{label} phải là số từ 0 đến 100.") from error
    if math.isnan(score) or not 0 <= score <= 100:
        raise TeamError(f"{label} phải là số từ 0 đến 100.")
    return int(score) if score.is_integer() else round(score, 1)


def parse_domains(pairs, track):
    known = [domain["id"] for domain in track["domains"]]
    result = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        name = name.strip()
        if not sep:
            raise TeamError(f"--domain phải có dạng <domain-id>=<percent>, nhận được {pair!r}.")
        if name not in known:
            raise TeamError(f"Domain {name!r} không thuộc {track['slug']}. Domain hợp lệ: {', '.join(known)}.")
        result[name] = parse_percent(value, f"--domain {name}")
    return result


def assessment_id(track, kind, wanted=None):
    ids = [item["id"] for item in track["assessments"] if item["kind"] == kind]
    if wanted:
        if wanted not in ids:
            raise TeamError(f"Assessment {wanted!r} không phải {kind} của {track['slug']}. Chọn: {', '.join(ids)}.")
        return wanted
    if len(ids) != 1:
        raise TeamError(f"{track['slug']} có {len(ids)} bài {kind}. Chọn bằng --id: {', '.join(ids)}.")
    return ids[0]


def load_config(team_dir=TEAM_DIR):
    path = team_dir / "config.json"
    if not path.is_file():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise TeamError(f"{path} bị hỏng. Chạy lại setup.py --join <mã>.") from error
    if not isinstance(config, dict) or not all(config.get(key) for key in ("endpoint", "token", "email")):
        raise TeamError(f"{path} thiếu endpoint, token hoặc email. Chạy lại setup.py --join <mã>.")
    return config


def save_config(config, team_dir=TEAM_DIR):
    team_dir.mkdir(parents=True, exist_ok=True)
    path = team_dir / "config.json"
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(staged, 0o600)
    staged.replace(path)


def new_event(config, kind, track=None, data=None, now=None):
    moment = now or datetime.now().astimezone()
    return {
        "v": SCHEMA_VERSION,
        "id": str(uuid.uuid4()),
        "ts": moment.isoformat(timespec="seconds"),
        "email": config["email"],
        "type": kind,
        "track": track["slug"] if track else None,
        "route_total": len(track["lessons"]) if track else None,
        "data": data or {},
    }


def read_lines(path):
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("id"):
            events.append(event)
    return events


def write_lines(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".tmp")
    staged.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    staged.replace(path)


def append_outbox(event, team_dir=TEAM_DIR):
    path = team_dir / "outbox.jsonl"
    events = read_lines(path)
    if any(queued["id"] == event["id"] for queued in events):
        return False
    write_lines(path, events + [event])
    return True


def post_event(endpoint, event, token, timeout=TIMEOUT_S):
    # Apps Script answers a POST with a 302 to the result; urllib follows it with a GET.
    body = json.dumps({**event, "token": token}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def flush(config, team_dir=TEAM_DIR, send=post_event, say=print):
    """Send the outbox in order and return (sent, left). Never raises on network errors."""
    outbox = team_dir / "outbox.jsonl"
    events = read_lines(outbox)
    sent = 0
    for index, event in enumerate(events):
        try:
            reply = send(config["endpoint"], event, config["token"])
        except (OSError, ValueError, http.client.HTTPException) as error:
            reply = {"ok": False, "error": "network", "detail": str(error) or type(error).__name__}
        if not isinstance(reply, dict):
            reply = {"ok": False, "error": "bad_reply"}
        if reply.get("ok"):
            sent += 1
            continue
        error = reply.get("error") or "unknown"
        if error in REJECTED:
            write_lines(team_dir / "rejected.jsonl", read_lines(team_dir / "rejected.jsonl") + [event])
            say(f"Cảnh báo: máy chủ từ chối event {event.get('type')} ({error}), đã chuyển sang .team/rejected.jsonl.")
            continue
        left = events[index:]
        write_lines(outbox, left)
        say(stop_message(reply, len(left)))
        return sent, len(left)
    if events:
        write_lines(outbox, [])
    return sent, 0


def stop_message(reply, left):
    waiting = f"{left} event đang chờ trong .team/outbox.jsonl, lần ghi sau sẽ gửi tiếp."
    if reply["error"] == "bad_token":
        return f"Cảnh báo: token team bị từ chối. Xin admin mã tham gia mới rồi chạy setup.py --join <mã>. {waiting}"
    if reply["error"] == "network":
        return f"Cảnh báo: chưa gửi được tiến độ ({reply['detail']}). {waiting}"
    return f"Cảnh báo: máy chủ trả lỗi {reply['error']}. {waiting}"
