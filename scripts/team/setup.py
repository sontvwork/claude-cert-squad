"""Join the study team so the certification tutor can report progress.

    python3 scripts/team/setup.py --join <code>   first run, or after the admin rotates the token
    python3 scripts/team/setup.py --share off     hide quiz and mock scores, progress stays public
    python3 scripts/team/setup.py --make-code     admin only: build the join code to pin in Chat

The config lands in .team/config.json, which git ignores. See team/README.md.
"""

import argparse
import getpass
import sys

import _common as team


def ask(prompt, check, read=input, say=print):
    while True:
        try:
            return check(read(prompt))
        except team.TeamError as error:
            say(team.paint(f"❌ {error}", "error"))


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


def join(code, team_dir=team.TEAM_DIR, email=None, name=None, share=None, read=input, say=print, send=team.post_event):
    link = team.decode_join_code(code)
    domain = link["domain"]
    try:
        old = team.load_config(team_dir) or {}
    except team.TeamError:
        old = {}
    known = old.get("email") if str(old.get("email", "")).endswith("@" + domain) else None

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
    team.save_config(config, team_dir)
    team.append_outbox(team.new_event(config, "member_joined", data={"name": name, "share": share}), team_dir)
    sent, left = team.flush(config, team_dir, send, say)
    summarize(config, left, say)
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
    mode.add_argument("--join", metavar="CODE", help="join code pinned in the team Chat space")
    mode.add_argument("--make-code", action="store_true", help="admin: build a join code")
    root.add_argument("--share", choices=["on", "off"], help="share quiz and mock scores")
    root.add_argument("--email", help="skip the email prompt")
    root.add_argument("--name", help="skip the display name prompt")
    return root


def main(argv=None, team_dir=team.TEAM_DIR):
    args = parser().parse_args(argv)
    try:
        if args.make_code:
            make_code()
        elif args.join:
            share = None if args.share is None else args.share == "on"
            join(args.join, team_dir, args.email, args.name, share)
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
