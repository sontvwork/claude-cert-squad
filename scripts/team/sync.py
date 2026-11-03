"""Sync one member's learner state across their machines.

    python3 scripts/team/sync.py pull              session start, before reading CLAUDE-CERTIFICATION.md
    python3 scripts/team/sync.py push              after a write to CLAUDE-CERTIFICATION.md or learning-artifacts/
    python3 scripts/team/sync.py push --resolved   after merging the copies in .team/remote/
    python3 scripts/team/sync.py status

The first pull or push of a clone that already joined the team turns sync on:
it makes a sync_key, claims the email on the web app, mails the member the
command that adds another machine, and uploads the local state. Snapshots are sealed with that key (see _vault.py). Like record.py,
every network or key problem prints one line and exits 0. See team/README.md.
"""

import argparse
import http.client
import json
import shutil
import sys
from datetime import datetime

import _common as team
import _vault as vault

BACKUPS_KEPT = 5


def state_path(team_dir):
    return team_dir / "sync.json"


def load_state(team_dir):
    path = state_path(team_dir)
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        state = {}
    return {"rev": state.get("rev"), "fingerprint": state.get("fingerprint"), "merge": bool(state.get("merge"))}


def save_state(team_dir, rev, fingerprint, merge=False):
    team.write_text(state_path(team_dir), json.dumps({"rev": rev, "fingerprint": fingerprint, "merge": merge}) + "\n")


def forget(team_dir):
    """Drop what this clone knows about the server copy, e.g. after the key changes."""
    state_path(team_dir).unlink(missing_ok=True)
    shutil.rmtree(team_dir / "remote", ignore_errors=True)


def post_slow(endpoint, body):
    return team.post_json(endpoint, body, team.SYNC_TIMEOUT_S)


def request(config, action, send, **fields):
    body = {"action": action, "token": config["token"], "email": config["email"], "auth": vault.auth_token(config["sync_key"])}
    try:
        reply = send(config["endpoint"], {**body, **fields})
    except (OSError, ValueError, http.client.HTTPException) as error:
        return {"ok": False, "error": "network", "detail": str(error) or type(error).__name__}
    return reply if isinstance(reply, dict) else {"ok": False, "error": "bad_reply"}


def warn(say, text):
    say(team.paint(f"⚠️ Team sync: {text}", "warn"))


def explain(reply, say):
    error = reply.get("error")
    if error == "network":
        warn(say, f"chưa kết nối được ({reply.get('detail')}). Học tiếp bằng bản trên máy này, lần sau sẽ đồng bộ.")
    elif error == "bad_token":
        warn(say, "token team bị từ chối. Xin admin mã tham gia mới rồi chạy setup.py --join <mã>.")
    elif error == "bad_sync_key":
        warn(say, "sync_key của máy này không khớp với server. Import mã từ máy đang học bằng setup.py --export-device, hoặc nhờ admin xoá dòng của bạn trong tab State.")
    elif error == "mail_limited":
        warn(say, "vừa gửi mail mã thêm máy, đợi 10 phút rồi thử lại.")
    elif error == "mail_failed":
        warn(say, "web app chưa gửi được mail. Dùng setup.py --export-device để lấy mã, và báo admin kiểm tra quyền gửi mail.")
    elif error == "bad_request":
        warn(say, "web app chưa hỗ trợ đồng bộ hoặc từ chối yêu cầu. Nhắc admin deploy bản Apps Script mới nhất.")
    else:
        warn(say, f"máy chủ trả lỗi {error}. Lần sau sẽ thử lại.")


def device_code(config):
    """The --join code that adds another machine as this member. It carries the sync_key."""
    device = {key: config[key] for key in ("email", "name", "share", "sync_key")}
    return team.encode_join_code(config["endpoint"], config["token"], config["domain"], device)


def mail_device(config, team_dir, send, say):
    """Ask the web app to mail the device code to the member's own inbox. Return True when sent."""
    reply = request(config, "state_mail", send, code=device_code(config))
    if not reply.get("ok"):
        explain(reply, say)
        return False
    config["device_mailed"] = True
    team.save_config(config, team_dir)
    say(team.paint(f"📧 Team sync: đã gửi lệnh thêm máy vào {config['email']}. Về máy khác, mở mail và chạy lệnh trong đó.", "ok"))
    return True


def ensure_claimed(config, team_dir, send, say):
    """Make and register this member's sync_key once. Return True when the clone may sync."""
    if config.get("sync_key") and config.get("sync_claimed"):
        if not config.get("device_mailed"):
            mail_device(config, team_dir, send, say)
        return True
    if not config.get("sync_key"):
        config["sync_key"] = vault.new_key()
        config["sync_claimed"] = False
        config["device_mailed"] = False
        team.save_config(config, team_dir)
        forget(team_dir)
    reply = request(config, "state_claim", send)
    if reply.get("ok"):
        config["sync_claimed"] = True
        team.save_config(config, team_dir)
        say(team.paint("🔐 Team sync: đã bật đồng bộ nhiều máy.", "ok"))
        if not config.get("device_mailed"):
            mail_device(config, team_dir, send, say)
        return True
    if reply.get("error") == "claimed":
        warn(say, "email này đã bật đồng bộ ở máy khác. Mở mail \"Mã thêm máy học chứng chỉ Claude\" trong hộp thư công ty "
                  "và chạy lệnh trong đó. Không thấy mail thì chạy setup.py --email-device trên máy kia.")
        return False
    explain(reply, say)
    return False


def local_files(root):
    return vault.fit(vault.collect(root))


def backup(root, team_dir, files):
    if not files:
        return None
    folder = team_dir / "backup" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    write_files(folder, files)
    olds = sorted(path for path in (team_dir / "backup").iterdir() if path.is_dir())
    for old in olds[:-BACKUPS_KEPT]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def write_files(root, files):
    for path, content in files.items():
        if not vault.is_synced_path(path):
            raise vault.VaultError(f"Đường dẫn không hợp lệ: {path!r}.")
        target = root.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def replace(root, old, new):
    for path in old:
        if path not in new:
            root.joinpath(*path.split("/")).unlink(missing_ok=True)
    write_files(root, new)
    artifacts = root / vault.ARTIFACTS_DIR
    if artifacts.is_dir():
        for folder in sorted((p for p in artifacts.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            if not any(folder.iterdir()):
                folder.rmdir()


def pull(config, root=team.ROOT, team_dir=team.TEAM_DIR, send=post_slow, say=print, force=False, depth=0):
    """Bring the server copy here. Return a short status word for tests and callers."""
    if not ensure_claimed(config, team_dir, send, say):
        return "skipped"
    state = load_state(team_dir)
    if state["merge"] and not force:
        warn(say, "đang chờ gộp bản từ máy khác trong .team/remote/. Gộp xong chạy sync.py push --resolved.")
        return "merge"
    reply = request(config, "state_pull", send, rev=None if force else state["rev"])
    if not reply.get("ok"):
        if reply.get("error") == "not_found" and depth == 0:
            config["sync_claimed"] = False
            forget(team_dir)
            return pull(config, root, team_dir, send, say, depth=1)
        explain(reply, say)
        return "offline"
    if reply.get("unchanged"):
        return "unchanged"
    try:
        remote = vault.unpack(vault.open_sealed(config["sync_key"], config["email"], reply["blob"])) if reply.get("blob") else {}
        kept, _, _ = local_files(root)
    except vault.VaultError as error:
        warn(say, str(error))
        return "error"
    rev, here, there = reply["rev"], vault.fingerprint(kept), vault.fingerprint(remote)
    dirty = state["fingerprint"] != here if state["rev"] is not None else bool(kept)
    if here == there:
        save_state(team_dir, rev, here)
        shutil.rmtree(team_dir / "remote", ignore_errors=True)
        return "same"
    if not dirty:
        saved = backup(root, team_dir, kept)
        replace(root, kept, remote)
        save_state(team_dir, rev, there)
        note = f", bản cũ ở {saved.relative_to(team_dir.parent)}" if saved else ""
        say(team.paint(f"☁️ Team sync: đã tải tiến độ mới nhất (rev {rev}{note}).", "ok"))
        return "updated"
    return merge(config, root, team_dir, send, say, rev, kept, remote, there, depth)


def merge(config, root, team_dir, send, say, rev, kept, remote, there, depth):
    """Both sides changed. Take files only the server has; park differing ones in .team/remote/."""
    added = {path: content for path, content in remote.items() if path not in kept}
    differing = {path: content for path, content in remote.items() if path in kept and kept[path] != content}
    write_files(root, added)
    shutil.rmtree(team_dir / "remote", ignore_errors=True)
    if not differing:
        save_state(team_dir, rev, there)
        if added:
            say(team.paint(f"☁️ Team sync: đã thêm {len(added)} file từ máy khác.", "ok"))
        return push(config, root, team_dir, send, say, depth=depth + 1)
    write_files(team_dir / "remote", differing)
    save_state(team_dir, rev, there, merge=True)
    warn(say, f"máy này và máy khác cùng sửa {len(differing)} file. Bản của máy khác nằm trong .team/remote/: "
              + ", ".join(sorted(differing)) + ". Gộp vào file ở thư mục gốc, rồi chạy sync.py push --resolved.")
    return "conflict"


def push(config, root=team.ROOT, team_dir=team.TEAM_DIR, send=post_slow, say=print, resolved=False, depth=0):
    if not ensure_claimed(config, team_dir, send, say):
        return "skipped"
    state = load_state(team_dir)
    if state["merge"] and not resolved:
        warn(say, "chưa đẩy lên vì còn file chờ gộp trong .team/remote/. Gộp xong chạy sync.py push --resolved.")
        return "merge"
    if state["rev"] is None:
        return pull(config, root, team_dir, send, say, depth=depth)
    try:
        kept, dropped, packed = local_files(root)
    except vault.VaultError as error:
        warn(say, str(error))
        return "error"
    here = vault.fingerprint(kept)
    if here == state["fingerprint"] and not state["merge"]:
        return "unchanged"
    if dropped:
        warn(say, f"bản đồng bộ vượt {vault.MAX_PACKED_BYTES // 1000}KB nên bỏ qua: {', '.join(dropped)}.")
    blob = vault.seal(config["sync_key"], config["email"], packed)
    reply = request(config, "state_push", send, base_rev=state["rev"], blob=blob)
    if reply.get("ok"):
        save_state(team_dir, reply["rev"], here)
        shutil.rmtree(team_dir / "remote", ignore_errors=True)
        say(team.paint(f"☁️ Team sync: đã đồng bộ (rev {reply['rev']}).", "ok"))
        return "pushed"
    if reply.get("error") in ("stale", "not_found") and depth < 2:
        if reply.get("error") == "not_found":
            config["sync_claimed"] = False
            forget(team_dir)
            say("Team sync: server chưa có bản của bạn, đăng ký lại.")
        else:
            say("Team sync: máy khác vừa đồng bộ, tải về để gộp.")
        return pull(config, root, team_dir, send, say, force=True, depth=depth + 1)
    explain(reply, say)
    return "offline"


def status(config, team_dir, say=print):
    state = load_state(team_dir)
    if not config.get("sync_claimed"):
        say("Team sync: chưa bật. Lần pull hoặc push đầu tiên sẽ tự bật.")
    elif state["merge"]:
        say(f"Team sync: rev {state['rev']}, đang chờ gộp file trong .team/remote/.")
    else:
        say(f"Team sync: rev {state['rev']}.")


def parser():
    root = argparse.ArgumentParser(prog="sync.py", description="Sync learner state across a member's machines.")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("pull", help="download the newest state, before reading CLAUDE-CERTIFICATION.md")
    upload = commands.add_parser("push", help="upload the local state")
    upload.add_argument("--resolved", action="store_true", help="the copies in .team/remote/ are merged")
    commands.add_parser("status", help="print the synced revision")
    return root


def main(argv=None, root=team.ROOT, team_dir=team.TEAM_DIR, send=post_slow):
    args = parser().parse_args(argv)
    try:
        config = team.load_config(team_dir)
    except team.TeamError as error:
        print(f"Team sync: bỏ qua. {error}")
        return 0
    if config is None:
        print("Team sync: chưa có .team/config.json nên bỏ qua. Việc học vẫn tiếp tục bình thường.")
        return 0
    if args.command == "pull":
        pull(config, root, team_dir, send)
    elif args.command == "push":
        push(config, root, team_dir, send, resolved=args.resolved)
    else:
        status(config, team_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
