"""Driver tests (SPEC §12, §18) with the scripted FakeAdapter — deterministic,
no model calls. Includes the kill test: kill -9 the driver mid-session and
assert no consequential loss (P1)."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASEFILE = ROOT / "casefile.py"

sys.path.insert(0, str(ROOT))
import spitball  # noqa: E402
import casefile as cf  # noqa: E402


class SpitballBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.cli("init")
        self.cli("open", "Deliberation case", "--goal", "settle it")

    def cli(self, *args):
        p = subprocess.run([sys.executable, str(CASEFILE), *args],
                           cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def script(self, mapping):
        p = self.dir / "fake.json"
        p.write_text(json.dumps(mapping))
        return str(p)

    def run_driver(self, **kw):
        kw.setdefault("topic", "the bug")
        kw.setdefault("models", ("claude", "codex"))
        kw.setdefault("root", self.dir)
        return spitball.run(**kw)


class DriverTests(SpitballBase):
    def test_stalemate_when_nothing_filed(self):
        s = self.script({"claude": ["I think A"] * 8, "codex": ["I doubt A"] * 8})
        r = self.run_driver(fake_script=s, turns=8)
        self.assertEqual(r["outcome"], "stalemate")

    def test_turn_budget_halts(self):
        s = self.script({"claude": ["A"] * 4, "codex": ["B"] * 4})
        r = self.run_driver(fake_script=s, turns=1)
        self.assertEqual(r["outcome"], "turn-budget")

    def test_convergence_halts(self):
        # converged state pre-exists: endorsed hypothesis, no open disputes.
        h = self.cli("add", "-t", "hypothesis", "-a", "claude", "it is the cache")
        self.cli("endorse", h, "-a", "codex")
        s = self.script({"claude": ["x"] * 4, "codex": ["y"] * 4})
        r = self.run_driver(fake_script=s, turns=4)
        self.assertEqual(r["outcome"], "converged")

    def test_transcripts_written_per_model(self):
        s = self.script({"claude": ["A"], "codex": ["B"]})
        r = self.run_driver(fake_script=s, turns=1)
        tdir = Path(r["transcripts"])
        self.assertTrue((tdir / "claude.log").exists())
        self.assertTrue((tdir / "codex.log").exists())
        self.assertIn("summary", (tdir / "claude.log").read_text())

    def test_role_briefs_created_and_user_editable(self):
        s = self.script({})
        self.run_driver(fake_script=s, turns=1)
        pb = self.dir / ".casefile" / "roles" / "proposer.md"
        self.assertTrue(pb.exists())
        pb.write_text("CUSTOM BRIEF for {name}")
        self.assertEqual(spitball.role_brief(self.dir, "proposer", "codex"),
                         "CUSTOM BRIEF for codex")

    def test_divergence_detector(self):
        self.assertFalse(spitball._diff_summaries(
            "decided the importer encoding theory holds",
            "importer encoding theory accepted; decided"))
        self.assertTrue(spitball._diff_summaries(
            "decided the importer encoding theory holds",
            "concluded nothing whatsoever relevant today"))


class StreamAdapterTests(unittest.TestCase):
    """Protocol-folding logic only — no live process (probed live 2026-07-17)."""

    def test_apply_event_folds_init_and_result(self):
        h = {"sid": None, "usd": 0.0, "tokens": 0, "reply": ""}
        A = spitball.StreamClaudeAdapter._apply_event
        self.assertFalse(A(h, {"type": "system", "subtype": "init",
                               "session_id": "s-1"}))
        self.assertFalse(A(h, {"type": "assistant", "message": {}}))
        self.assertTrue(A(h, {"type": "result", "result": "pong",
                              "session_id": "s-1", "total_cost_usd": 0.01}))
        self.assertEqual(h["reply"], "pong")
        self.assertEqual(h["sid"], "s-1")
        self.assertAlmostEqual(h["usd"], 0.01)

    def test_umsg_shape(self):
        m = json.loads(spitball.StreamClaudeAdapter._umsg("hi"))
        self.assertEqual(m["type"], "user")
        self.assertEqual(m["message"]["content"][0]["text"], "hi")

    def test_adapter_registry(self):
        root = Path(".")
        self.assertIsInstance(spitball.make_adapter("claude", root),
                              spitball.StreamClaudeAdapter)
        self.assertIsInstance(spitball.make_adapter("claude-resume", root),
                              spitball.ClaudeAdapter)
        self.assertIsInstance(spitball.make_adapter("codex", root),
                              spitball.CodexAdapter)


class KillTest(SpitballBase):
    """SPEC §18: kill -9 the driver mid-session; restart; no consequential
    loss. Everything of consequence is already in the log (P1)."""

    def test_kill9_mid_session_loses_nothing(self):
        before = self.cli("add", "-t", "hypothesis", "-a", "claude",
                          "pre-existing claim")
        s = self.script({"claude": [{"sleep": 30, "text": "slow"}],
                         "codex": ["quick"]})
        p = subprocess.Popen(
            [sys.executable, str(CASEFILE), "spitball", "--topic", "t",
             "--fake-script", s, "--turns", "3"],
            cwd=self.dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)  # driver is inside the slow fake turn
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        # the log is intact, parseable, and the pre-existing claim survives
        entries = [json.loads(l) for l in
                   (self.dir / ".casefile" / "log.jsonl").read_text().splitlines()]
        self.assertIn(before, {e["id"] for e in entries})
        # no stuck lock; the CLI works immediately after the kill
        self.assertFalse((self.dir / ".casefile" / "log.lock").exists())
        self.cli("status")
        self.cli("add", "-t", "note", "-a", "claude", "post-kill append works")


if __name__ == "__main__":
    unittest.main()
