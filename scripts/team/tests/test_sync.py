import io
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _common as team
import _vault as vault
import setup
import sync

ENDPOINT = "https://script.google.com/macros/s/abc/exec"
CONFIG = {"endpoint": ENDPOINT, "token": "secret", "domain": "vti.com.vn", "email": "an.nguyen@vti.com.vn", "name": "An", "share": True}
PROGRESS = "CLAUDE-CERTIFICATION.md"
ARTIFACT = "learning-artifacts/claude/ccdv-f/00-certification-strategy/plan.md"


class FakeSyncServer:
    """The state_* rules of Code.gs, in memory."""

    def __init__(self):
        self.members = {}
        self.offline = False
        self.mail_fails = False
        self.calls = []
        self.mails = []

    def __call__(self, endpoint, body):
        self.calls.append(body["action"])
        if self.offline:
            raise OSError("no route to host")
        member = self.members.get(body["email"])
        if body["action"] == "state_claim":
            if member is None:
                self.members[body["email"]] = {"auth": body["auth"], "rev": 0, "blob": ""}
                return {"ok": True, "rev": 0, "created": True}
            return {"ok": True, "rev": member["rev"]} if member["auth"] == body["auth"] else {"ok": False, "error": "claimed"}
        if member is None:
            return {"ok": False, "error": "not_found"}
        if member["auth"] != body["auth"]:
            return {"ok": False, "error": "bad_sync_key"}
        if body["action"] == "state_mail":
            if self.mail_fails:
                return {"ok": False, "error": "mail_failed"}
            self.mails.append((body["email"], body["code"]))
            return {"ok": True, "mailed": True}
        if body["action"] == "state_pull":
            if body.get("rev") == member["rev"]:
                return {"ok": True, "rev": member["rev"], "unchanged": True}
            return {"ok": True, "rev": member["rev"], "blob": member["blob"]}
        if body["base_rev"] != member["rev"]:
            return {"ok": False, "error": "stale", "rev": member["rev"]}
        member.update(rev=member["rev"] + 1, blob=body["blob"])
        return {"ok": True, "rev": member["rev"]}


class Machine:
    def __init__(self, folder, name, server, config=CONFIG):
        self.root = Path(folder) / name
        self.team_dir = self.root / ".team"
        self.server = server
        self.said = []
        if config is not None:
            team.save_config(dict(config), self.team_dir)

    @property
    def config(self):
        return team.load_config(self.team_dir)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def read(self, path):
        return (self.root / path).read_text(encoding="utf-8")

    def pull(self):
        return sync.pull(self.config, self.root, self.team_dir, self.server, self.said.append)

    def push(self, resolved=False):
        return sync.push(self.config, self.root, self.team_dir, self.server, self.said.append, resolved=resolved)


class VaultTest(unittest.TestCase):
    def test_a_sealed_snapshot_opens_only_with_the_same_key_and_email(self):
        key = vault.new_key()
        packed = vault.pack({PROGRESS: "| 00 | Complete | 2/2 |".encode("utf-8")})
        sealed = vault.seal(key, CONFIG["email"], packed)
        self.assertEqual(vault.open_sealed(key, CONFIG["email"], sealed), packed)
        self.assertNotIn(b"Complete", sealed.encode("ascii"))
        tampered = sealed[:-5] + ("A" if sealed[-5] != "A" else "B") + sealed[-4:]
        for args in ((vault.new_key(), CONFIG["email"], sealed), (key, "binh@vti.com.vn", sealed), (key, CONFIG["email"], tampered)):
            with self.subTest(args=args[1:2]), self.assertRaises(vault.VaultError):
                vault.open_sealed(*args)

    def test_the_auth_value_is_derived_and_differs_per_key(self):
        key = vault.new_key()
        self.assertRegex(vault.auth_token(key), r"^[0-9a-f]{64}$")
        self.assertEqual(vault.auth_token(key), vault.auth_token(key))
        self.assertNotEqual(vault.auth_token(key), vault.auth_token(vault.new_key()))
        with self.assertRaises(vault.VaultError):
            vault.auth_token("short")

    def test_collect_takes_progress_files_and_artifacts_but_skips_noise(self):
        with tempfile.TemporaryDirectory() as folder:
            machine = Machine(folder, "a", None, config=None)
            for path in (PROGRESS, "CLAUDE-CERTIFICATION-CCDV-F-2026-11-01.md", ARTIFACT, "README.md",
                         "learning-artifacts/x/__pycache__/m.cpython-312.pyc", "learning-artifacts/x/node_modules/a.js",
                         "learning-artifacts/x/.DS_Store", ".team/secret.txt"):
                machine.write(path, "x")
            self.assertEqual(sorted(vault.collect(machine.root)), sorted([PROGRESS, "CLAUDE-CERTIFICATION-CCDV-F-2026-11-01.md", ARTIFACT]))

    def test_an_oversized_snapshot_drops_the_largest_artifacts_first(self):
        noise = bytes(range(256)) * 3000
        files = {PROGRESS: b"route", "learning-artifacts/big.bin": noise * 2, "learning-artifacts/mid.bin": noise, ARTIFACT: b"plan"}
        kept, dropped, packed = vault.fit(files, limit=len(vault.pack({PROGRESS: b"route", ARTIFACT: b"plan"})) + 64)
        self.assertEqual(dropped, ["learning-artifacts/big.bin", "learning-artifacts/mid.bin"])
        self.assertEqual(sorted(kept), [PROGRESS, ARTIFACT])
        self.assertEqual(vault.unpack(packed), kept)

    def test_unpack_refuses_paths_outside_the_synced_set(self):
        for name in ("../evil.md", "/etc/passwd", ".team/config.json", "scripts/team/record.py"):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                info = tarfile.TarInfo(name)
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            with self.subTest(name=name), self.assertRaises(vault.VaultError):
                vault.unpack(buffer.getvalue())


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.server = FakeSyncServer()
        self.home = Machine(self.folder.name, "office", self.server)

    def tearDown(self):
        self.folder.cleanup()

    def second_machine(self):
        """Join a fresh clone with the command the first machine mailed when sync turned on."""
        self.home.push()
        code = self.server.mails[-1][1]
        laptop = Machine(self.folder.name, "laptop", self.server, config=None)
        setup.join(code, laptop.team_dir, say=laptop.said.append, send=lambda *args: {"ok": True},
                   root=laptop.root, sync_send=self.server)
        return laptop

    def test_first_pull_of_a_joined_clone_migrates_its_local_state(self):
        self.home.write(PROGRESS, "route: lesson 00 Complete")
        self.home.write(ARTIFACT, "my plan")
        self.assertEqual(self.home.pull(), "pushed")
        config = self.home.config
        self.assertTrue(config["sync_claimed"])
        member = self.server.members[CONFIG["email"]]
        self.assertEqual(member["rev"], 1)
        self.assertEqual(member["auth"], vault.auth_token(config["sync_key"]))
        files = vault.unpack(vault.open_sealed(config["sync_key"], CONFIG["email"], member["blob"]))
        self.assertEqual(files, {PROGRESS: b"route: lesson 00 Complete", ARTIFACT: b"my plan"})
        self.assertEqual(self.home.push(), "unchanged")

    def test_turning_sync_on_mails_the_add_a_machine_command_once(self):
        self.home.pull()
        self.home.pull()
        self.assertEqual(len(self.server.mails), 1)
        email, code = self.server.mails[0]
        self.assertEqual(email, CONFIG["email"])
        device = team.decode_device(code)
        self.assertEqual(device["sync_key"], self.home.config["sync_key"])
        self.assertEqual(device["email"], CONFIG["email"])
        self.assertTrue(any("📧" in line and CONFIG["email"] in line for line in self.home.said))

    def test_a_failed_mail_is_retried_next_session_and_can_be_resent_by_hand(self):
        self.server.mail_fails = True
        self.assertEqual(self.home.pull(), "same")
        self.assertFalse(self.home.config["device_mailed"])
        self.assertTrue(any("export-device" in line for line in self.home.said))
        self.server.mail_fails = False
        self.home.pull()
        self.assertTrue(self.home.config["device_mailed"])
        setup.email_device(self.home.team_dir, say=self.home.said.append, send=self.server)
        self.assertEqual(len(self.server.mails), 2)

    def test_a_machine_joined_by_device_code_does_not_mail_again(self):
        self.home.write(PROGRESS, "route v1")
        laptop = self.second_machine()
        laptop.pull()
        self.assertEqual(len(self.server.mails), 1)
        self.assertTrue(laptop.config["device_mailed"])

    def test_a_second_machine_joins_with_the_exported_code_and_gets_the_state(self):
        self.home.write(PROGRESS, "route v1")
        self.home.push()
        laptop = self.second_machine()
        self.assertEqual(laptop.config["email"], CONFIG["email"])
        self.assertEqual(laptop.config["sync_key"], self.home.config["sync_key"])
        self.assertEqual(laptop.read(PROGRESS), "route v1")

    def test_a_clean_machine_takes_the_newer_state_and_keeps_a_backup(self):
        self.home.write(PROGRESS, "route v1")
        self.home.write(ARTIFACT, "draft")
        self.home.push()
        laptop = self.second_machine()
        laptop.write(PROGRESS, "route v2")
        (laptop.root / ARTIFACT).unlink()
        self.assertEqual(laptop.push(), "pushed")

        self.assertEqual(self.home.pull(), "updated")
        self.assertEqual(self.home.read(PROGRESS), "route v2")
        self.assertFalse((self.home.root / ARTIFACT).exists(), "a deletion travels too")
        backups = list((self.home.team_dir / "backup").iterdir())
        self.assertEqual((backups[0] / ARTIFACT).read_text(encoding="utf-8"), "draft")
        self.assertEqual(self.home.pull(), "unchanged")

    def test_edits_to_different_files_merge_without_asking(self):
        self.home.write(PROGRESS, "route v1")
        self.home.push()
        laptop = self.second_machine()
        laptop.write(ARTIFACT, "from laptop")
        laptop.push()
        self.home.write("learning-artifacts/claude/ccdv-f/01/notes.md", "from office")
        self.assertEqual(self.home.push(), "pushed")
        self.assertEqual(self.home.read(ARTIFACT), "from laptop")
        self.assertEqual(laptop.pull(), "updated")
        self.assertEqual(laptop.read("learning-artifacts/claude/ccdv-f/01/notes.md"), "from office")

    def test_edits_to_the_same_file_wait_for_a_merge(self):
        self.home.write(PROGRESS, "route v1")
        self.home.push()
        laptop = self.second_machine()
        laptop.write(PROGRESS, "route v1 + lesson 01 on laptop")
        laptop.push()
        self.home.write(PROGRESS, "route v1 + lesson 02 at the office")

        self.assertEqual(self.home.push(), "conflict")
        self.assertEqual((self.home.team_dir / "remote" / PROGRESS).read_text(encoding="utf-8"), "route v1 + lesson 01 on laptop")
        self.assertEqual(self.home.read(PROGRESS), "route v1 + lesson 02 at the office", "the local file is never overwritten")
        self.assertEqual(self.home.push(), "merge")
        self.assertEqual(self.home.pull(), "merge")

        self.home.write(PROGRESS, "route v1 + lessons 01 and 02")
        self.assertEqual(self.home.push(resolved=True), "pushed")
        self.assertFalse((self.home.team_dir / "remote").exists())
        laptop.pull()
        self.assertEqual(laptop.read(PROGRESS), "route v1 + lessons 01 and 02")

    def test_two_clones_that_studied_before_sync_existed(self):
        self.home.write(PROGRESS, "office route")
        laptop = Machine(self.folder.name, "laptop", self.server)
        laptop.write(PROGRESS, "laptop route")
        self.home.pull()
        self.assertEqual(laptop.pull(), "skipped")
        self.assertTrue(any("Mã thêm máy" in line for line in laptop.said))
        self.assertFalse(laptop.config["sync_claimed"])

        code = setup.export_device(self.home.team_dir, say=lambda text: None, send=self.server)
        self.assertEqual(code, self.server.mails[0][1], "the printed and the mailed command match")
        setup.join(code, laptop.team_dir, say=laptop.said.append, send=lambda *args: {"ok": True},
                   root=laptop.root, sync_send=self.server)
        self.assertEqual((laptop.team_dir / "remote" / PROGRESS).read_text(encoding="utf-8"), "office route")
        self.assertEqual(laptop.read(PROGRESS), "laptop route")

    def test_offline_keeps_the_key_and_claims_it_later(self):
        self.home.write(PROGRESS, "route")
        self.server.offline = True
        self.assertEqual(self.home.pull(), "skipped")
        key = self.home.config["sync_key"]
        self.assertFalse(self.home.config["sync_claimed"])
        self.assertTrue(any("chưa kết nối" in line for line in self.home.said))
        self.server.offline = False
        self.assertEqual(self.home.push(), "pushed")
        self.assertEqual(self.home.config["sync_key"], key)

    def test_an_admin_reset_reclaims_the_email_and_uploads_again(self):
        self.home.write(PROGRESS, "route")
        self.home.push()
        del self.server.members[CONFIG["email"]]
        self.home.write(PROGRESS, "route v2")
        self.assertEqual(self.home.push(), "pushed")
        self.assertEqual(self.server.members[CONFIG["email"]]["rev"], 1)

    def test_main_without_a_team_config_exits_zero(self):
        empty = Path(self.folder.name) / "solo"
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.main(["pull"], empty, empty / ".team", send=self.server)
        self.assertEqual(code, 0)
        self.assertIn("bỏ qua", out.getvalue())
        self.assertEqual(self.server.calls, [])


if __name__ == "__main__":
    unittest.main()
