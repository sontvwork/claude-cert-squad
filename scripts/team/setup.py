"""Join the study team so the certification tutor can report progress.

    python3 scripts/team/setup.py --join <code>   first run, or after the admin rotates the token
    python3 scripts/team/setup.py --join          ask for the code, safe for a code split across lines
    python3 scripts/team/setup.py --share off     hide quiz and mock scores, progress stays public
    python3 scripts/team/setup.py --email-device  mail yourself the command that joins another machine as you
    python3 scripts/team/setup.py --export-device print that command here instead
    python3 scripts/team/setup.py --make-code     admin only: build the join code to pin in Chat

The config lands in .team/config.json, which git ignores. See team/README.md.
"""

import argparse
import getpass
import sys

import _common as team
import sync


def ask(prompt, check, read=input, say=print):
    while True:
        try:
            return check(read(prompt))
        except team.TeamError as error:
            say(team.paint(f"❌ {error}", "error"))


def read_code(read=input, say=print):
    """Collect pasted lines until they form a whole code. A blank line gives up."""
    say("📋 Dán mã vào đây rồi bấm Enter:")
    code = ""
    while True:
        line = "".join(read("").split())
        if not line:
            raise team.TeamError("Chưa nhận đủ mã. Copy lại toàn bộ mã rồi chạy lại lệnh.")
        code += line
        try:
            team.decode_join_code(code)
            return code
        except team.TeamError:
            continue


def summarize(config, left, say=print):
    """Close the join with what was stored and whether the team heard about it."""
    say(team.paint("💾 Đã lưu .team/config.json", "ok"))
    say(f"👤 Tên: {config['name']}")
    say(f"📧 Email: {config['email']}")
    say(f"📊 Chia sẻ điểm: {'bật' if config['share'] else 'tắt'}")
    if left:
        say(team.paint(f"⏳ Còn {left} event chờ gửi, lần ghi sau sẽ gửi tiếp.", "warn"))
    else:
        say(team.paint("🎉 Từ giờ tiến độ sẽ tự động được ghi sau mỗi bài.", "ok"))


def join(code, team_dir=team.TEAM_DIR, email=None, name=None, share=None, read=input, say=print, send=team.post_event,
         root=team.ROOT, sync_send=None):
    link = team.decode_join_code(code)
    device = team.decode_device(code)
    domain = link["domain"]
    try:
        old = team.load_config(team_dir) or {}
    except team.TeamError:
        old = {}
    known = old.get("email") if str(old.get("email", "")).endswith("@" + domain) else None
    if device:
        email = device["email"] if email is None else email
        name = device["name"] if name is None else name
        share = device["share"] if share is None else share

    if email is not None:
        email = team.normalize_email(email, domain)
    elif known:
        email = ask(f"[1/3] 📧 Email công ty [{known}]: ", lambda value: team.normalize_email(value or known, domain), read, say)
    else:
        email = ask(f"[1/3] 📧 Email công ty (gõ phần trước @{domain}): ", lambda value: team.normalize_email(value, domain), read, say)

    default_name = old.get("name") if known == email and old.get("name") else email.split("@")[0]
    if name is not None:
        name = team.normalize_name(name)
    else:
        name = ask(f"[2/3] 👤 Tên hiển thị trên Chat [{default_name}]: ", lambda value: team.normalize_name(value or default_name), read, say)

    if share is None:
        default_share = old.get("share", True)
        hint = "Y/n" if default_share else "y/N"
        share = ask(
            f"[3/3] 📊 Chia sẻ điểm quiz và mock với team? Tiến độ luôn công khai. [{hint}]: ",
            lambda value: team.parse_yes(value, default_share),
            read,
            say,
        )

    config = {**link, "email": email, "name": name, "share": share}
    if device and device["email"] == email:
        config.update(sync_key=device["sync_key"], sync_claimed=True, device_mailed=True)
    elif old.get("email") == email and old.get("sync_key"):
        config.update({key: old[key] for key in ("sync_key", "sync_claimed", "device_mailed") if key in old})
    if config.get("sync_key") != old.get("sync_key") or old.get("email") != email:
        sync.forget(team_dir)
    team.save_config(config, team_dir)
    team.append_outbox(team.new_event(config, "member_joined", data={"name": name, "share": share}), team_dir)
    sent, left = team.flush(config, team_dir, send, say)
    summarize(config, left, say)
    if sync_send:
        sync.pull(config, root, team_dir, sync_send, say)
    return config


def change_share(value, team_dir=team.TEAM_DIR, say=print, send=team.post_event):
    config = team.load_config(team_dir)
    if config is None:
        raise team.TeamError("Chưa tham gia team. Chạy setup.py --join <mã> trước.")
    config["share"] = team.parse_yes(value, None)
    team.save_config(config, team_dir)
    team.append_outbox(team.new_event(config, "share_changed", data={"share": config["share"]}), team_dir)
    team.flush(config, team_dir, send, say)
    say(team.paint(f"📊 Chia sẻ điểm: {'bật' if config['share'] else 'tắt'}. Tiến độ vẫn công khai.", "ok"))
    return config


def synced_config(team_dir, send, say):
    config = team.load_config(team_dir)
    if config is None:
        raise team.TeamError("Chưa tham gia team. Chạy setup.py --join <mã> trước.")
    config["device_mailed"] = True  # this command handles the mail itself
    if not sync.ensure_claimed(config, team_dir, send, say):
        raise team.TeamError("Chưa bật được đồng bộ, xem cảnh báo ở trên.")
    return config


def export_device(team_dir=team.TEAM_DIR, say=print, send=sync.post_slow):
    config = synced_config(team_dir, send, say)
    code = sync.device_code(config)
    say(team.paint("🔑 Mã này chứa khoá đồng bộ riêng của bạn. Đừng dán vào Chat chung.", "warn"))
    say("💻 Chạy lệnh sau ở thư mục gốc repo trên máy mới:")
    say(f"python3 scripts/team/setup.py --join {code}")
    return code


def email_device(team_dir=team.TEAM_DIR, say=print, send=sync.post_slow):
    config = synced_config(team_dir, send, say)
    if not sync.mail_device(config, team_dir, send, say):
        raise team.TeamError("Chưa gửi được mail. Dùng setup.py --export-device để lấy lệnh ngay tại đây.")
    return config


def make_code(read=input, secret=getpass.getpass, say=print):
    endpoint = ask("[1/3] 🔗 URL web app (kết thúc bằng /exec): ", team.check_endpoint, read, say)
    token = ask("[2/3] 🔑 TEAM_TOKEN (không hiện khi gõ): ", team.check_token, secret, say)
    domain = ask(f"[3/3] 🌐 Domain email [{team.DEFAULT_DOMAIN}]: ", lambda value: team.check_domain(value or team.DEFAULT_DOMAIN), read, say)
    code = team.encode_join_code(endpoint, token, domain)
    say(team.paint("📌 Ghim lệnh này trong space Chat của team:", "ok"))
    say(f"python3 scripts/team/setup.py --join {code}")
    return code


def parser():
    root = argparse.ArgumentParser(prog="setup.py", description="Join the certification study team.")
    mode = root.add_mutually_exclusive_group()
    mode.add_argument("--join", metavar="CODE", nargs="?", const="", help="join code; leave it out to paste it at a prompt")
    mode.add_argument("--make-code", action="store_true", help="admin: build a join code")
    mode.add_argument("--email-device", action="store_true", help="mail yourself the command that joins another machine as you")
    mode.add_argument("--export-device", action="store_true", help="print the command that joins another machine as you")
    root.add_argument("--share", choices=["on", "off"], help="share quiz and mock scores")
    root.add_argument("--email", help="skip the email prompt")
    root.add_argument("--name", help="skip the display name prompt")
    return root


def main(argv=None, team_dir=team.TEAM_DIR):
    args = parser().parse_args(argv)
    try:
        if args.make_code:
            make_code()
        elif args.email_device:
            email_device(team_dir)
        elif args.export_device:
            export_device(team_dir)
        elif args.join is not None:
            share = None if args.share is None else args.share == "on"
            join(args.join or read_code(), team_dir, args.email, args.name, share, sync_send=sync.post_slow)
        elif args.share:
            change_share(args.share, team_dir)
        else:
            parser().print_help()
            return 2
    except team.TeamError as error:
        print(team.paint(f"❌ Lỗi: {error}", "error", sys.stderr), file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\n👋 Đã huỷ, chưa thay đổi gì.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
