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

    def test_preexisting_consensus_does_not_converge(self):
        # a claim settled BEFORE the run must not converge it (485f4fbc):
        # nothing new gets filed here, so the run stalls out instead.
        h = self.cli("add", "-t", "hypothesis", "-a", "claude", "it is the cache")
        self.cli("endorse", h, "-a", "codex")
        s = self.script({"claude": ["x"] * 4, "codex": ["y"] * 4})
        r = self.run_driver(fake_script=s, turns=4)
        self.assertEqual(r["outcome"], "stalemate")

    def test_convergence_scoped_to_since_position(self):
        case = cf.load_active(self.dir, cf.load_meta(self.dir))
        h = self.cli("add", "-t", "hypothesis", "-a", "claude", "old claim")
        self.cli("endorse", h, "-a", "codex")
        n = len(cf.read_entries(self.dir))
        # the settled claim converges from position 0, not from after it
        self.assertTrue(spitball.converged(self.dir, case, 0))
        self.assertFalse(spitball.converged(self.dir, case, n))
        # a hypothesis endorsed after the position converges the scoped view
        h2 = self.cli("add", "-t", "hypothesis", "-a", "claude", "fresh claim")
        self.cli("endorse", h2, "-a", "codex")
        self.assertTrue(spitball.converged(self.dir, case, n))

    def test_adapters_stopped_when_second_start_fails(self):
        stopped = []

        class Good:
            def start(self, ctx):
                return {"reply": "ok"}

            def send(self, h, m):
                return "ok"

            def cost(self, h):
                return {"usd": 0.0, "tokens": 0}

            def stop(self, h):
                stopped.append("A")

        class Bad(Good):
            def start(self, ctx):
                raise RuntimeError("boom")

        orig = spitball.make_adapter
        spitball.make_adapter = (
            lambda name, root, fake=None: Good() if name == "claude" else Bad())
        self.addCleanup(lambda: setattr(spitball, "make_adapter", orig))
        with self.assertRaises(RuntimeError):
            self.run_driver(turns=1)
        self.assertEqual(stopped, ["A"])  # A was started, so A gets stopped

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

    def test_send_times_out_on_silent_child(self):
        # a child that hangs without emitting a newline must still hit the
        # turn deadline (78b17208) and be reclaimed
        ad = spitball.StreamClaudeAdapter(Path("."))
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.addCleanup(proc.kill)
        h = ad._attach(proc)
        orig = spitball.TURN_TIMEOUT_S
        spitball.TURN_TIMEOUT_S = 1
        self.addCleanup(lambda: setattr(spitball, "TURN_TIMEOUT_S", orig))
        t0 = time.time()
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            ad.send(h, "ping")
        self.assertLess(time.time() - t0, 10)
        proc.wait(timeout=5)  # send() killed the wedged child

    def test_codex_adapter_carries_high_effort(self):
        # recorded user constraint: codex always runs at high reasoning effort
        ad = spitball.CodexAdapter(Path("."))
        self.assertIn("model_reasoning_effort=high", ad.opts)
        self.assertIn("model_reasoning_effort=high", ad.resume_opts)


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
