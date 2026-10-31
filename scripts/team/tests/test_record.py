import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _common as team
import record

CONFIG = {
    "endpoint": "https://script.google.com/macros/s/abc/exec",
    "token": "secret",
    "domain": "vti.com.vn",
    "email": "an.nguyen@vti.com.vn",
    "name": "An Nguyễn",
    "share": True,
}
FIRST_LESSON = "certifications/claude/lessons/00-certification-strategy"


class FakeServer:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.received = []

    def __call__(self, endpoint, event, token):
        self.received.append({**event, "token": token})
        reply = self.replies.pop(0) if self.replies else {"ok": True}
        if isinstance(reply, Exception):
            raise reply
        return reply


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.team_dir = Path(self.folder.name) / ".team"
        team.save_config(CONFIG, self.team_dir)

    def tearDown(self):
        self.folder.cleanup()

    def run_record(self, argv, server):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = record.main(argv, self.team_dir, send=server)
        return code, out.getvalue(), err.getvalue()

    def outbox(self):
        return team.read_lines(self.team_dir / "outbox.jsonl")

    def test_lesson_complete_sends_the_event_with_route_total_and_token(self):
        server = FakeServer()
        code, out, _ = self.run_record(
            ["lesson-complete", "--track", "ccdv-f", "--lesson", FIRST_LESSON + "/", "--quiz", "2/2"], server
        )
        self.assertEqual(code, 0)
        event = server.received[0]
        self.assertEqual(event["type"], "lesson_completed")
        self.assertEqual(event["route_total"], 15)
        self.assertEqual(event["token"], "secret")
        self.assertEqual(event["data"], {"lesson": FIRST_LESSON, "quiz": "2/2"})
        self.assertEqual(self.outbox(), [])
        self.assertIn("lesson_completed", out)

    def test_folder_name_resolves_to_the_route_path(self):
        track = team.load_track("CCDV-F")
        self.assertEqual(team.normalize_lesson("00-certification-strategy", track), FIRST_LESSON)

    def test_unknown_track_exits_non_zero_and_queues_nothing(self):
        server = FakeServer()
        code, _, err = self.run_record(["track-start", "--track", "ccxx-f"], server)
        self.assertEqual(code, 2)
        self.assertIn("ccdv-f", err)
        self.assertEqual(server.received, [])
        self.assertEqual(self.outbox(), [])

    def test_lesson_outside_the_route_is_rejected(self):
        track = team.load_track("ccao-f")
        outside = [path for path in team.route(team.load_track("ccar-p")) if path not in team.route(track)][0]
        with self.assertRaises(team.TeamError):
            team.normalize_lesson(outside, track)

    def test_quiz_below_the_complete_threshold_is_rejected(self):
        self.assertEqual(team.parse_quiz("5/6"), "5/6")
        for value in ("1/2", "3/2", "2/0", "two"):
            with self.subTest(value=value), self.assertRaises(team.TeamError):
                team.parse_quiz(value)

    def test_assessment_checks_kind_score_and_domains(self):
        server = FakeServer()
        argv = ["assessment", "--track", "ccdv-f", "--kind", "mock", "--score", "82.5", "--domain", "tools-mcps=70"]
        code, _, _ = self.run_record(argv, server)
        self.assertEqual(code, 0)
        self.assertEqual(
            server.received[0]["data"],
            {"kind": "mock", "assessment_id": "claude-ccdv-f-mock-01", "score": 82.5, "domains": {"tools-mcps": 70}},
        )
        for bad in (["--score", "101"], ["--score", "nan"], ["--score", "80", "--domain", "made-up=50"]):
            with self.subTest(bad=bad):
                argv = ["assessment", "--track", "ccdv-f", "--kind", "mock", *bad]
                self.assertEqual(self.run_record(argv, server)[0], 2)

    def test_network_failure_keeps_the_event_and_exits_zero(self):
        down = FakeServer([OSError("timed out")])
        code, out, _ = self.run_record(["track-start", "--track", "ccar-f"], down)
        self.assertEqual(code, 0)
        self.assertIn("outbox", out)
        queued = self.outbox()
        self.assertEqual([event["type"] for event in queued], ["track_started"])
        self.assertNotIn("token", queued[0])

        up = FakeServer()
        self.assertEqual(self.run_record(["flush"], up)[0], 0)
        self.assertEqual([event["id"] for event in up.received], [queued[0]["id"]])
        self.assertEqual(self.outbox(), [])

    def test_outbox_sends_in_order_and_stops_at_the_first_failure(self):
        server = FakeServer([OSError("offline")] * 3)
        for track in ("ccao-f", "ccdv-f", "ccar-f"):
            self.run_record(["track-start", "--track", track], server)
        later = FakeServer([{"ok": True}, OSError("offline again")])
        self.run_record(["flush"], later)
        self.assertEqual([event["track"] for event in later.received], ["ccao-f", "ccdv-f"])
        self.assertEqual([event["track"] for event in self.outbox()], ["ccdv-f", "ccar-f"])

    def test_same_event_id_is_queued_once_and_duplicates_count_as_sent(self):
        event = team.new_event(CONFIG, "track_started", team.load_track("ccdv-f"))
        self.assertTrue(team.append_outbox(event, self.team_dir))
        self.assertFalse(team.append_outbox(event, self.team_dir))
        self.assertEqual(len(self.outbox()), 1)
        sent, left = team.flush(CONFIG, self.team_dir, FakeServer([{"ok": True, "duplicate": True}]), say=lambda _: None)
        self.assertEqual((sent, left), (1, 0))

    def test_rejected_event_moves_aside_but_a_bad_token_waits(self):
        server = FakeServer([{"ok": False, "error": "bad_request"}, {"ok": False, "error": "bad_token"}])
        messages = []
        team.append_outbox(team.new_event(CONFIG, "track_started", team.load_track("ccdv-f")), self.team_dir)
        team.append_outbox(team.new_event(CONFIG, "track_started", team.load_track("ccar-f")), self.team_dir)
        sent, left = team.flush(CONFIG, self.team_dir, server, say=messages.append)
        self.assertEqual((sent, left), (0, 1))
        self.assertEqual(self.outbox()[0]["track"], "ccar-f")
        self.assertEqual(team.read_lines(self.team_dir / "rejected.jsonl")[0]["track"], "ccdv-f")
        self.assertIn("setup.py --join", messages[-1])

    def test_missing_config_prints_one_line_and_exits_zero(self):
        (self.team_dir / "config.json").unlink()
        server = FakeServer()
        code, out, _ = self.run_record(["lesson-complete", "--track", "nope", "--lesson", "x", "--quiz", "0/2"], server)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertEqual(server.received, [])

    def test_event_timestamp_carries_the_local_offset(self):
        event = team.new_event(CONFIG, "track_started", team.load_track("ccdv-f"))
        self.assertRegex(event["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual(json.loads(json.dumps(event))["v"], 1)


if __name__ == "__main__":
    unittest.main()
