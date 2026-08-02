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
from unittest import mock
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
        # CODEX_HOME sandboxed: init/hooks-install must never touch ~/.codex
        env = {**os.environ, "CODEX_HOME": str(self.dir / ".codex-home")}
        p = subprocess.run([sys.executable, str(CASEFILE), *args],
                           cwd=self.dir, capture_output=True, text=True,
                           env=env)
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


class AdapterRegistryTests(unittest.TestCase):
    def test_make_adapter_knows_grok_family(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        for name in ("grok", "grok45", "xai", "GROK47"):
            a = spitball.make_adapter(name, root)
            self.assertIsInstance(a, spitball.GrokAdapter)
            self.assertEqual(a.name, "grok")

    def test_make_adapter_rejects_unknown(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        with self.assertRaises(SystemExit) as cm:
            spitball.make_adapter("gpt-nope", root)
        self.assertIn("grok", str(cm.exception))

    def test_driver_rejects_unsafe_channel_name_before_file_creation(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        env = {**os.environ, "CODEX_HOME": str(d / ".codex-home")}
        for args in (["init"], ["open", "safe"]):
            p = subprocess.run([sys.executable, str(CASEFILE), *args],
                               cwd=d, capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
        with self.assertRaisesRegex(SystemExit, "safe channel"):
            spitball.run(topic="x", models=("../escape", "codex"), root=d)
        self.assertFalse((d.parent / "escape.log").exists())

    def test_driver_rejects_two_transports_for_same_author(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        env = {**os.environ, "CODEX_HOME": str(d / ".codex-home")}
        for args in (["init"], ["open", "safe"]):
            p = subprocess.run(
                [sys.executable, str(CASEFILE), *args],
                cwd=d, capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
        with self.assertRaisesRegex(SystemExit, "different author identities"):
            spitball.run(
                topic="x", models=("claude", "claude-resume"), root=d)

    def test_role_brief_normalizes_grok45_author(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        (root / ".casefile" / "roles").mkdir(parents=True)
        text = spitball.role_brief(root, "proposer", "grok45")
        self.assertIn('-a grok', text)
        self.assertNotIn('-a grok45', text)

    def test_grok_adapter_uses_private_prompt_file_and_removes_it(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        reply = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({"text": "ok", "sessionId": "sid", "usage": {}}),
            stderr="")
        seen = {}

        def capture(cmd, **kwargs):
            path = Path(cmd[cmd.index("--prompt-file") + 1])
            seen["path"] = path
            seen["text"] = path.read_text()
            return reply

        with mock.patch.object(spitball.subprocess, "run",
                               side_effect=capture) as run:
            spitball.GrokAdapter(root).start("hello")
        cmd = run.call_args.args[0]
        self.assertIn("--prompt-file", cmd)
        self.assertEqual(seen["text"], "hello")
        self.assertFalse(seen["path"].exists())

    def test_codex_adapter_allows_non_git_no_namespace_casefile_roots(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        adapter = spitball.CodexAdapter(root)
        self.assertIn("--skip-git-repo-check", adapter.opts)
        self.assertIn("--skip-git-repo-check", adapter.resume_opts)
        self.assertIn("danger-full-access", adapter.opts)
        self.assertNotIn("--search", adapter.opts)
        self.assertIn('sandbox_mode="danger-full-access"', adapter.resume_opts)
        self.assertIn("--ignore-user-config", adapter.opts)
        self.assertIn("--ignore-user-config", adapter.resume_opts)

    def test_codex_prefers_substantive_message_over_late_hook_receipt(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "item.completed", "item": {
                "type": "agent_message",
                "text": "Substantive analysis with mechanisms, evidence, "
                        "counterarguments, and a concrete conclusion.\n"
                        "CASEFILE_TURN_JSON: "
                        '{"coverage":[],"filed":[],"position":"A",'
                        '"falsifiers":[]}' }},
            {"type": "item.completed", "item": {
                "type": "agent_message",
                "text": 'recorded: note "secretary sweep: nothing unrecorded"'}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 10, "cached_input_tokens": 4,
                "output_tokens": 5}},
        ]
        reply = subprocess.CompletedProcess(
            [], 0, stdout="\n".join(json.dumps(e) for e in events), stderr="")
        with mock.patch.object(
                spitball.subprocess, "run", return_value=reply) as run:
            h = spitball.CodexAdapter(root).start("hello")
        self.assertIn("Substantive analysis", h["reply"])
        self.assertNotIn("secretary sweep", h["reply"])
        self.assertEqual(h["tokens"], 11)
        self.assertEqual(h["cache_read_tokens"], 4)
        self.assertEqual(run.call_args.args[0][-1], "-")
        self.assertEqual(run.call_args.kwargs["input"], "hello")

    def test_grok_primary_token_counter_excludes_cache_reads(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        replies = [
            {"text": "one", "sessionId": "sid", "total_cost_usd": 0.01,
             "usage": {"input_tokens": 100, "cache_read_input_tokens": 9000,
                       "output_tokens": 20, "total_tokens": 9120}},
            {"text": "two", "sessionId": "sid", "total_cost_usd": 0.02,
             "usage": {"input_tokens": 50, "cache_read_input_tokens": 12000,
                       "output_tokens": 30, "total_tokens": 12080}},
        ]
        completed = [
            subprocess.CompletedProcess([], 0, stdout=json.dumps(d), stderr="")
            for d in replies]
        with mock.patch.object(spitball.subprocess, "run",
                               side_effect=completed):
            ad = spitball.GrokAdapter(root)
            h = ad.start("hello")
            ad.send(h, "again")
        self.assertEqual(ad.cost(h)["tokens"], 200)
        self.assertEqual(h["cache_read_tokens"], 21000)
        self.assertAlmostEqual(ad.cost(h)["usd"], 0.03)

    def test_grok_noisy_json_parser_keeps_top_level_result(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        raw = ('debug preamble\n'
               '{"text":"answer","sessionId":"sid",'
               '"usage":{"input_tokens":3,"output_tokens":2}}\ntrailer')
        reply = subprocess.CompletedProcess([], 0, stdout=raw, stderr="")
        with mock.patch.object(spitball.subprocess, "run", return_value=reply):
            h = spitball.GrokAdapter(root).start("hello")
        self.assertEqual(h["reply"], "answer")
        self.assertEqual(h["sid"], "sid")

    def test_fake_driver_with_codex_and_grok_names(self):
        # FakeAdapter path must accept grok as a model name in spitball.run
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        env = {**os.environ, "CODEX_HOME": str(d / ".codex-home")}
        for args in (["init"], ["open", "Grok pair", "--goal", "pair"]):
            p = subprocess.run([sys.executable, str(CASEFILE), *args],
                               cwd=d, capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
        script = d / "fake.json"
        script.write_text(json.dumps({
            "codex": ["claim"] * 4,
            "grok": ["critique"] * 4,
        }))
        r = spitball.run(topic="pair", models=("codex", "grok"), turns=1,
                         fake_script=str(script), root=d)
        self.assertEqual(r["outcome"], "turn-budget")


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
        headings = (
            "Round 1\nArgument evidence assumptions concessions falsifiers "
            "unresolved points.\nManifest coverage matrix\nFinal decided "
            "ruled-out open.\n")
        self.assertTrue(spitball._diff_summaries(
            headings + "Choose design A for lower latency and reversibility.",
            headings + "Choose design B for stronger safety and correctness."))

    def test_candidate_validator_rejects_entries_outside_exact_span(self):
        case = cf.load_active(self.dir, cf.load_meta(self.dir))
        entries = cf.read_entries(self.dir)
        h = cf.make_entry(
            entries, case, "hypothesis", "claude", "new session claim")
        cf.append_entry(self.dir, h)
        entries = cf.read_entries(self.dir)
        old = cf.make_entry(
            entries, case, "hypothesis", "claude", "unrelated prior claim")
        cf.append_entry(self.dir, old)
        entries = cf.read_entries(self.dir)
        candidate = cf.make_entry(
            entries, case, "digest", "claude", "recommendation",
            kind="candidate", supersedes=[h["id"], old["id"]])
        cf.append_entry(self.dir, candidate)
        found, error = spitball._candidate_digest(
            cf.read_entries(self.dir), len(entries), case, "claude", {h["id"]})
        self.assertIsNone(found)
        self.assertIn("outside the exact session span", error)
        self.assertIn(old["id"], error)

    def test_recovery_manifest_cannot_escape_session_directory(self):
        session = "safe-session"
        tdir = self.dir / ".casefile" / "transcripts" / session
        tdir.mkdir(parents=True)
        case = cf.load_active(self.dir, cf.load_meta(self.dir))
        (self.dir / ".casefile" / "outside.json").write_text("{}")
        (tdir / "run.json").write_text(json.dumps({
            "status": "aborted",
            "case": case,
            "topic": "choose",
            "models": ["claude", "codex"],
            "turn_budget": 1,
            "rounds_completed": 0,
            "manifest": "../../outside.json",
            "calls": [],
        }))
        with self.assertRaisesRegex(SystemExit, "inside its session directory"):
            spitball.recover(session, root=self.dir)

    def test_resolving_logged_manifest_question_clears_that_gate(self):
        q = self.cli("add", "-t", "question", "-a", "user", "which design?")
        case = cf.load_active(self.dir, cf.load_meta(self.dir))
        manifest = spitball.build_manifest(
            self.dir, case, "choose",
            requirements=("preserve correctness",),
            criteria=("failure rate",),
            alternatives=("A", "B"),
            evidence_domains=("tests",),
            weighting="equal",
            mode="enforce")
        self.cli(
            "resolve", q, "-a", "user", "--outcome", "answered",
            "--reason", "choose A")
        covered = set(manifest["required_coverage"])
        blockers = spitball._finalization_blockers(
            self.dir, case, len(cf.read_entries(self.dir)), manifest,
            {"claude": covered, "codex": covered},
            {"claude": covered, "codex": covered},
            False, "converged")
        self.assertFalse(any(
            blocker.startswith("manifest questions unresolved")
            for blocker in blockers))

    def test_manifest_snapshots_case_requirements_and_symmetry_grid(self):
        constraint = self.cli(
            "add", "-t", "constraint", "-a", "user", "latency under 200ms")
        self.cli("add", "-t", "hypothesis", "-a", "claude", "use design A")
        m = spitball.build_manifest(
            self.dir, cf.load_active(self.dir, cf.load_meta(self.dir)),
            "choose a design", requirements=("preserve correctness",),
            criteria=("latency", "failure rate"),
            alternatives=("design A", "design B"),
            evidence_domains=("benchmarks",), weighting="equal",
            mode="enforce")
        req_ids = {x["id"] for x in m["requirements"]}
        self.assertIn(f"entry-{constraint}", req_ids)
        matrix = [r for r in m["coverage_rows"]
                  if r["kind"] == "alternative_x_criterion"]
        self.assertGreaterEqual(len(matrix), 4)
        self.assertTrue(any(r["kind"] == "criterion_weighting"
                            for r in m["coverage_rows"]))
        self.assertEqual(m["warnings"], [])

    def test_enforced_manifest_refuses_missing_criteria_before_calls(self):
        with self.assertRaisesRegex(ValueError, "criteria"):
            spitball.build_manifest(
                self.dir, cf.load_active(self.dir, cf.load_meta(self.dir)),
                "choose", mode="enforce")

    def test_inferred_weighting_blocks_enforced_manifest(self):
        case = cf.load_active(self.dir, cf.load_meta(self.dir))
        path = self.dir / "inferred-manifest.json"
        path.write_text(json.dumps({
            "topic": "choose",
            "case": case,
            "requirements": [{"text": "preserve correctness",
                              "status": "confirmed"}],
            "criteria": [{"text": "failure rate", "status": "confirmed"}],
            "weighting": {"scheme": "equal", "status": "inferred"},
            "alternatives": ["A", "B"],
            "evidence_domains": ["tests"],
        }))
        warned = spitball.build_manifest(
            self.dir, case, "choose", manifest_path=str(path), mode="warn")
        self.assertTrue(any("weighting is inferred" in warning
                            for warning in warned["warnings"]))
        with self.assertRaisesRegex(ValueError, "weighting is inferred"):
            spitball.build_manifest(
                self.dir, case, "choose", manifest_path=str(path),
                mode="enforce")

    def test_manifest_rejects_unknown_fields_and_case_mismatch(self):
        bad = self.dir / "bad-manifest.json"
        bad.write_text(json.dumps({"topic": "choose", "criterai": ["safety"]}))
        with self.assertRaisesRegex(ValueError, "unknown manifest"):
            spitball.build_manifest(
                self.dir, cf.load_active(self.dir, cf.load_meta(self.dir)),
                "choose", manifest_path=str(bad))
        bad.write_text(json.dumps(
            {"topic": "choose", "case": "some-other-case"}))
        with self.assertRaisesRegex(ValueError, "active case"):
            spitball.build_manifest(
                self.dir, cf.load_active(self.dir, cf.load_meta(self.dir)),
                "choose", manifest_path=str(bad))

    def test_run_persists_manifest_and_atomic_call_journal(self):
        s = self.script({"claude": ["A"] * 8, "codex": ["A"] * 8})
        r = self.run_driver(
            fake_script=s, turns=1, requirements=("must be safe",),
            criteria=("failure rate",), alternatives=("A", "B"),
            evidence_domains=("tests",), weighting="equal",
            manifest_mode="enforce")
        journal = json.loads(Path(r["journal"]).read_text())
        manifest = json.loads(Path(r["manifest"]).read_text())
        self.assertEqual(journal["status"], "complete")
        self.assertEqual(journal["manifest_warnings"], [])
        self.assertTrue(journal["calls"])
        self.assertTrue(all(c["status"] == "completed"
                            for c in journal["calls"]))
        self.assertTrue(all("prompt" in c and "reply" in c
                            for c in journal["calls"]))
        self.assertTrue(any(row["kind"] == "alternative_x_criterion"
                            for row in manifest["coverage_rows"]))
        self.assertEqual(Path(r["journal"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(r["manifest"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(r["finalization"]["status"], "blocked")

    def test_driver_finalizes_only_exact_reviewed_candidate(self):
        state = {}
        case = cf.load_active(self.dir, cf.load_meta(self.dir))

        class FilingAdapter:
            strict_output = False
            executable = None
            context_transport = "test"
            hook_isolation = "test"

            def __init__(self, name):
                self.name = name

            def start(self, ctx):
                return {"reply": ""}

            def send(self, handle, prompt):
                entries = cf.read_entries(self_outer.dir)
                if self.name == "claude" and prompt.startswith("[codex says]"):
                    e = cf.make_entry(
                        entries, case, "hypothesis", "claude",
                        "design A reduces failure rate",
                        claim_mode="causal-inference",
                        mechanism="it performs fewer state writes",
                        comparator="design B",
                        analysis_layer="execution",
                        falsifier="A has no lower measured failure rate",
                        counterfactual="equal failures without write reduction",
                        horizon="30 days",
                        testability="within-session")
                    cf.append_entry(self_outer.dir, e)
                    state["hypothesis"] = e["id"]
                    return "A argues for design A with a testable mechanism."
                if self.name == "codex" and prompt.startswith("[claude says]"):
                    e = cf.make_entry(
                        entries, case, "endorsement", "codex",
                        "mechanism and comparator survive critique",
                        refs=[state["hypothesis"]])
                    cf.append_entry(self_outer.dir, e)
                    return "Codex independently endorses the exact claim."
                if self.name == "claude" and "Propose a CANDIDATE" in prompt:
                    span = spitball._eligible_digest_span(
                        entries, case, 0)
                    e = cf.make_entry(
                        entries, case, "digest", "claude",
                        "Recommend design A with a reversible rollout.",
                        supersedes=span, kind="candidate",
                        conclusion_class="model-recommendation")
                    cf.append_entry(self_outer.dir, e)
                    state["candidate"] = e["id"]
                    return f"Candidate {e['id']} preserves the exact span."
                if self.name == "codex" and \
                        "Adversarially review the exact candidate" in prompt:
                    e = cf.make_entry(
                        entries, case, "endorsement", "codex",
                        "exact candidate preserves evidence and caveats",
                        refs=[state["candidate"]])
                    cf.append_entry(self_outer.dir, e)
                    return f"Reviewed and endorsed via {e['id']}."
                return (
                    "Both models agree design A has a testable lower-write "
                    "mechanism, design B remains the comparator, and rollout "
                    "must remain reversible with measured failure rates.")

            def cost(self, handle):
                return {"usd": 0.0, "tokens": 0}

            def stop(self, handle):
                pass

        self_outer = self
        original = spitball.make_adapter
        spitball.make_adapter = lambda name, root, fake=None: FilingAdapter(name)
        self.addCleanup(lambda: setattr(spitball, "make_adapter", original))
        r = self.run_driver(turns=1, manifest_mode="off")
        self.assertEqual(r["outcome"], "converged")
        self.assertEqual(r["finalization"]["status"], "finalized")
        self.assertEqual(
            r["finalization"]["candidate_digest_id"], state["candidate"])
        final = next(
            e for e in cf.read_entries(self.dir)
            if e["id"] == r["finalization"]["judgment_digest_id"])
        self.assertEqual(final["conclusion_class"], "cross-model-consensus")
        self.assertEqual(final["author"], "system")


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

    def test_ferry_payload_keeps_argument_but_compacts_receipts(self):
        text = (
            "The comparator fails under the measured load profile.\n"
            'recorded: hypothesis "A fails" (codex)\n'
            'CASEFILE_TURN_JSON: {"coverage":["req-1"],"filed":["deadbeef"],'
            '"position":"prefer B","falsifiers":[]}')
        payload = spitball._ferry_payload(text, {
            "coverage": ["req-1"],
            "filed": ["deadbeef"],
            "position": "prefer B",
            "falsifiers": [],
        })
        self.assertIn("comparator fails", payload)
        self.assertNotIn("recorded:", payload)
        self.assertNotIn("CASEFILE_TURN_JSON", payload)
        self.assertIn("CASEFILE TURN DELTA", payload)
        self.assertIn("deadbeef", payload)

    def test_adapter_registry(self):
        root = Path(".")
        self.assertIsInstance(spitball.make_adapter("claude", root),
                              spitball.StreamClaudeAdapter)
        self.assertIsInstance(spitball.make_adapter("claude-resume", root),
                              spitball.ClaudeAdapter)
        self.assertIsInstance(spitball.make_adapter("codex", root),
                              spitball.CodexAdapter)

    def test_claude_adapter_isolates_hooks_but_keeps_analysis_tools(self):
        for ad in (spitball.ClaudeAdapter(Path(".")),
                   spitball.StreamClaudeAdapter(Path("."))):
            cmd = ad.base if hasattr(ad, "base") else ad.cmd
            self.assertIn("--safe-mode", cmd)
            tools = cmd[cmd.index("--allowedTools") + 1]
            self.assertIn("Read", tools)
            self.assertIn("WebSearch", tools)
            self.assertIn("casefile.py", tools)
            denied = cmd[cmd.index("--disallowedTools") + 1]
            self.assertIn("Edit", denied)

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

    def test_live_output_contract_rejects_progress_and_unknown_rows(self):
        ok, reason, _ = spitball._validate_model_reply(
            "I'll inspect the repository first.", "turn", {"req-1"})
        self.assertFalse(ok)
        self.assertIn("progress", reason)
        text = (
            "This is a substantive analysis with a mechanism, comparator, "
            "counterargument, and falsifier explained over multiple lines.\n"
            'CASEFILE_TURN_JSON: {"coverage":["made-up"],"filed":[],'
            '"position":"A","falsifiers":[]}')
        ok, reason, _ = spitball._validate_model_reply(
            text, "turn", {"req-1"})
        self.assertFalse(ok)
        self.assertIn("unknown manifest", reason)

    def test_live_output_contract_accepts_structured_summary(self):
        text = (
            "Round one compared both alternatives against the same evidence "
            "and surfaced a reversible implementation path.\n"
            "The remaining uncertainty is explicitly preserved for testing.\n"
            'CASEFILE_SUMMARY_JSON: {"coverage":["req-1"],'
            '"rounds":["opening","round-1"],'
            '"decided":["A"],"ruled_out":["C"],"open":["latency"],'
            '"conclusion_class":"model-recommendation"}')
        ok, reason, envelope = spitball._validate_model_reply(
            text, "summary", {"req-1"}, ["opening", "round-1"])
        self.assertTrue(ok, reason)
        self.assertEqual(envelope["coverage"], ["req-1"])

    def test_live_output_contract_rejects_missing_round_synopsis(self):
        text = (
            "Round one compared both alternatives against the same evidence "
            "and explained their mechanisms and falsifiers.\n"
            "The conclusion preserves the remaining uncertainty.\n"
            'CASEFILE_SUMMARY_JSON: {"coverage":["req-1"],'
            '"rounds":["opening"],"decided":["A"],"ruled_out":[],'
            '"open":[],"conclusion_class":"model-recommendation"}')
        ok, reason, _ = spitball._validate_model_reply(
            text, "summary", {"req-1"}, ["opening", "round-1"])
        self.assertFalse(ok)
        self.assertIn("rounds must exactly equal", reason)

    def test_live_output_contract_rejects_placeholder_summary(self):
        text = (
            "The alternatives were assessed in detail across evidence, "
            "mechanisms, implementation risks, and falsifiers.\n"
            "A substantive conclusion should appear here.\n"
            'CASEFILE_SUMMARY_JSON: {"coverage":["req-1"],'
            '"rounds":["opening"],"decided":["..."],"ruled_out":[],"open":[],'
            '"conclusion_class":"model-recommendation"}')
        ok, reason, _ = spitball._validate_model_reply(
            text, "summary", {"req-1"})
        self.assertFalse(ok)
        self.assertIn("non-placeholder", reason)


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
        sessions = sorted(
            p for p in (self.dir / ".casefile" / "transcripts").iterdir()
            if (p / "run.json").exists())
        self.assertEqual(len(sessions), 1)
        run_journal = json.loads((sessions[0] / "run.json").read_text())
        self.assertEqual(run_journal["status"], "running")
        self.assertEqual(run_journal["calls"][-1]["status"], "pending")
        self.assertIn("[codex says]", run_journal["calls"][-1]["prompt"])
        self.assertTrue((sessions[0] / "manifest.json").exists())
        # the log is intact, parseable, and the pre-existing claim survives
        entries = [json.loads(l) for l in
                   (self.dir / ".casefile" / "log.jsonl").read_text().splitlines()]
        self.assertIn(before, {e["id"] for e in entries})
        # no stuck lock; the CLI works immediately after the kill
        self.assertFalse((self.dir / ".casefile" / "log.lock").exists())
        self.cli("status")
        self.cli("add", "-t", "note", "-a", "claude", "post-kill append works")

        # Recovery replays each model's private journal into fresh vendor
        # sessions, including the explicit not-durably-recorded pending call.
        quick = self.script({"claude": ["same summary"] * 8,
                             "codex": ["same summary"] * 8})
        recovered = spitball.recover(
            sessions[0].name, turns=0, fake_script=quick, root=self.dir)
        recovered_journal = json.loads(Path(recovered["journal"]).read_text())
        self.assertEqual(recovered_journal["recovery_from"], sessions[0].name)
        self.assertEqual(recovered_journal["recovery_mode"], "journal-replay")
        seed_prompt = recovered_journal["calls"][0]["prompt"]
        self.assertIn("RECOVERY CONTEXT FROM DURABLE", seed_prompt)
        self.assertIn("NOT DURABLY RECORDED", seed_prompt)

    def test_recovery_rejects_path_traversal_session(self):
        with self.assertRaisesRegex(SystemExit, "invalid"):
            spitball.recover("../../etc", root=self.dir)


if __name__ == "__main__":
    unittest.main()
