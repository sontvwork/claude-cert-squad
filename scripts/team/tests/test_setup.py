import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _common as team
import setup

ENDPOINT = "https://script.google.com/macros/s/abc/exec"
CODE = team.encode_join_code(ENDPOINT, "secret", "vti.com.vn")


def answers(*values):
    queue = list(values)
    return lambda prompt: queue.pop(0)


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, endpoint, event, token):
        self.events.append(event)
        return {"ok": True}


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.team_dir = Path(self.folder.name) / ".team"
        self.said = []

    def tearDown(self):
        self.folder.cleanup()

    def test_join_code_round_trips_without_padding(self):
        self.assertNotIn("=", CODE)
        self.assertEqual(team.decode_join_code(f"  {CODE}\n"), {"endpoint": ENDPOINT, "token": "secret", "domain": "vti.com.vn"})

    def test_broken_or_unsafe_join_codes_are_rejected(self):
        for code in ("not-a-code", CODE[:-6], team.encode_join_code("http://example.com/exec", "secret", "vti.com.vn"),
                     team.encode_join_code(ENDPOINT, "", "vti.com.vn"), team.encode_join_code(ENDPOINT, "secret", "nodot")):
            with self.subTest(code=code), self.assertRaises(team.TeamError):
                team.decode_join_code(code)

    def test_email_accepts_the_local_part_or_the_full_company_address(self):
        self.assertEqual(team.normalize_email(" An.Nguyen ", "vti.com.vn"), "an.nguyen@vti.com.vn")
        self.assertEqual(team.normalize_email("an.nguyen@VTI.com.vn", "vti.com.vn"), "an.nguyen@vti.com.vn")
        for raw in ("an@gmail.com", "an@vti.com.vn.evil", "an@", "", ".an", "an nguyen", "a@b@vti.com.vn"):
            with self.subTest(raw=raw), self.assertRaises(team.TeamError):
                team.normalize_email(raw, "vti.com.vn")

    def test_join_asks_again_until_the_email_matches_the_domain(self):
        server = Recorder()
        read = answers("an@gmail.com", "an.nguyen", "", "")
        config = setup.join(CODE, self.team_dir, read=read, say=self.said.append, send=server)
        self.assertEqual(config["email"], "an.nguyen@vti.com.vn")
        self.assertEqual(config["name"], "an.nguyen")
        self.assertTrue(config["share"])
        self.assertTrue(any("@vti.com.vn" in line for line in self.said))
        self.assertEqual(team.load_config(self.team_dir)["token"], "secret")
        self.assertEqual(stat.S_IMODE(os.stat(self.team_dir / "config.json").st_mode), 0o600)
        self.assertEqual([(e["type"], e["data"]) for e in server.events], [("member_joined", {"name": "an.nguyen", "share": True})])

    def test_join_takes_a_display_name_and_a_share_choice(self):
        server = Recorder()
        config = setup.join(CODE, self.team_dir, read=answers("an.nguyen", "  Nguyễn  Văn An ", "n"), say=self.said.append, send=server)
        self.assertEqual(config["name"], "Nguyễn Văn An")
        self.assertFalse(config["share"])
        self.assertEqual(server.events[0]["data"], {"name": "Nguyễn Văn An", "share": False})

    def test_rejoin_keeps_the_previous_answers_as_defaults(self):
        setup.join(CODE, self.team_dir, email="an.nguyen", name="An", share=False, say=self.said.append, send=Recorder())
        rotated = team.encode_join_code(ENDPOINT, "rotated", "vti.com.vn")
        config = setup.join(rotated, self.team_dir, read=answers("", "", ""), say=self.said.append, send=Recorder())
        self.assertEqual((config["email"], config["name"], config["share"], config["token"]), ("an.nguyen@vti.com.vn", "An", False, "rotated"))

    def test_display_name_rules(self):
        self.assertEqual(team.normalize_name("  An   Nguyễn "), "An Nguyễn")
        for raw in ("", "   ", "=HYPERLINK(1)", "+1", "x" * 61):
            with self.subTest(raw=raw), self.assertRaises(team.TeamError):
                team.normalize_name(raw)

    def test_share_change_updates_config_and_sends_share_changed(self):
        setup.join(CODE, self.team_dir, email="an.nguyen", name="An", share=True, say=self.said.append, send=Recorder())
        server = Recorder()
        config = setup.change_share("off", self.team_dir, say=self.said.append, send=server)
        self.assertFalse(config["share"])
        self.assertFalse(team.load_config(self.team_dir)["share"])
        self.assertEqual([(e["type"], e["data"]) for e in server.events], [("share_changed", {"share": False})])

    def test_share_change_before_joining_is_an_error(self):
        with self.assertRaises(team.TeamError):
            setup.change_share("on", self.team_dir, say=self.said.append, send=Recorder())

    def test_make_code_builds_a_code_that_join_accepts(self):
        code = setup.make_code(read=answers(ENDPOINT, ""), secret=answers("secret"), say=self.said.append)
        self.assertEqual(team.decode_join_code(code)["domain"], "vti.com.vn")
        self.assertIn(f"setup.py --join {code}", self.said[-1])

    def test_yes_no_answers(self):
        self.assertTrue(team.parse_yes("", True))
        self.assertTrue(team.parse_yes("Có", False))
        self.assertFalse(team.parse_yes("off", True))
        with self.assertRaises(team.TeamError):
            team.parse_yes("maybe", True)


if __name__ == "__main__":
    unittest.main()
