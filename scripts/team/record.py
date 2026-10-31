"""Record one certification progress event for the study team.

The claude-certification tutor runs this right after it updates
CLAUDE-CERTIFICATION.md. Wrong arguments exit 2 so the agent can fix the
command. A clone without .team/config.json, or a network failure, prints one
line and exits 0, so a lesson never stops on it. See team/README.md.
"""

import argparse
import sys

import _common as team


def parser():
    root = argparse.ArgumentParser(prog="record.py", description="Record team certification progress.")
    commands = root.add_subparsers(dest="command", required=True)

    start = commands.add_parser("track-start", help="the learner confirmed or changed the active track")
    start.add_argument("--track", required=True)

    lesson = commands.add_parser("lesson-complete", help="a route lesson became Complete")
    lesson.add_argument("--track", required=True)
    lesson.add_argument("--lesson", required=True, help="lesson path from the track JSON, or its folder name")
    lesson.add_argument("--quiz", required=True, help="post-quiz score as N/M")

    attempt = commands.add_parser("assessment", help="a diagnostic or mock attempt was scored")
    attempt.add_argument("--track", required=True)
    attempt.add_argument("--kind", required=True, choices=["diagnostic", "mock"])
    attempt.add_argument("--score", required=True, help="raw percent, 0 to 100")
    attempt.add_argument("--domain", action="append", default=[], help="<domain-id>=<percent>, once per domain")
    attempt.add_argument("--id", help="assessment id, only when the track declares several of this kind")

    commands.add_parser("flush", help="send events still waiting in .team/outbox.jsonl")
    return root


def build_event(args, config, tracks_dir=team.TRACKS_DIR):
    if args.command == "flush":
        return None
    track = team.load_track(args.track, tracks_dir)
    if args.command == "track-start":
        return team.new_event(config, "track_started", track)
    if args.command == "lesson-complete":
        data = {"lesson": team.normalize_lesson(args.lesson, track), "quiz": team.parse_quiz(args.quiz)}
        return team.new_event(config, "lesson_completed", track, data)
    data = {
        "kind": args.kind,
        "assessment_id": team.assessment_id(track, args.kind, args.id),
        "score": team.parse_percent(args.score, "--score"),
        "domains": team.parse_domains(args.domain, track),
    }
    return team.new_event(config, "assessment_attempted", track, data)


def main(argv=None, team_dir=team.TEAM_DIR, tracks_dir=team.TRACKS_DIR, send=team.post_event):
    args = parser().parse_args(argv)
    try:
        config = team.load_config(team_dir)
    except team.TeamError as error:
        print(f"Team progress: bỏ qua. {error}")
        return 0
    if config is None:
        print("Team progress: chưa có .team/config.json nên bỏ qua. Việc học vẫn tiếp tục bình thường.")
        return 0
    try:
        event = build_event(args, config, tracks_dir)
    except team.TeamError as error:
        print(f"Lỗi: {error}", file=sys.stderr)
        return 2
    if event:
        team.append_outbox(event, team_dir)
    sent, left = team.flush(config, team_dir, send)
    if event and not left:
        print(f"Team progress: đã ghi {event['type']} ({event['track']}).")
    elif not event:
        print(f"Team progress: đã gửi {sent} event, còn {left} event chờ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
