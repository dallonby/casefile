"""Behavioral tests for open discussions, FTS retrieval and exact read receipts."""

import json
from test_casefile import CliBase, cf
import casefile_board as board


class MessageboardTests(CliBase):
    def post(self, title="Gas mismatch", body="Cold storage read evidence", author="codex", *extra):
        r = self.cli("board", "post", title, body, "-a", author, "--json", *extra, expect=0)
        return json.loads(r.out)

    def rows(self, *args):
        return json.loads(self.cli("board", *args, "--json", expect=0).out)

    def test_open_visibility_and_cross_author_reply(self):
        p = self.post("Gas mismatch", "Open to everyone", "codex", "--to", "grok-oracle")
        r = self.rows("reply", p["id"], "I can reproduce it", "-a", "claude")
        view = self.rows("show", r["id"], "-a", "uninvited-reader")
        self.assertEqual(view["id"], p["id"])
        self.assertEqual([e["author"] for e in view["events"]], ["codex", "claude"])
        self.assertEqual(p["board"]["mentions"], ["grok-oracle"])
        self.assertEqual(r["board"]["reply_to"], p["id"])

    def test_read_is_exact_and_own_writes_do_not_hide_unread(self):
        p = self.post()
        self.post("Other topic", "My own message", "claude")
        self.assertEqual(self.rows("unread", "--for", "claude")["total"], 1)
        before = (self.dir / ".casefile/log.jsonl").read_bytes()
        self.rows("show", p["id"], "-a", "claude")
        self.assertEqual((self.dir / ".casefile/log.jsonl").read_bytes(), before)
        read = self.rows("read", p["id"], "-a", "claude")
        self.assertIn("read_receipt", read)
        self.assertTrue((self.dir / ".casefile/log.jsonl").read_bytes().startswith(before))
        self.assertEqual(self.rows("unread", "--for", "claude")["total"], 0)
        reply = self.rows("reply", p["id"], "New finding", "-a", "codex")
        self.assertEqual([e["id"] for e in self.rows("unread", "--for", "claude")["items"]], [reply["id"]])
        self.assertEqual(self.rows("unread", "--for", "grok")["total"], 3)

    def test_racing_reply_not_acknowledged_by_old_snapshot(self):
        p = self.post()
        self.rows("read", p["id"], "-a", "claude")
        reply = self.rows("reply", p["id"], "Concurrent late message", "-a", "codex")
        entries = self.log_entries()
        # Reconciliation places the late message before the earlier read event.
        read = next(e for e in entries if board.payload(e).get("op") == "read")
        entries.remove(read)
        entries.append(read)
        unread = board.unread(entries, "claude", cf.normalize_author)
        self.assertIn(reply["id"], [e["id"] for e in unread])

    def test_read_receipts_survive_clone_without_sidecars(self):
        p = self.post()
        self.rows("read", p["id"], "-a", "claude")
        rebuilt = json.loads(json.dumps(self.log_entries()))
        self.assertEqual(board.unread(rebuilt, "claude", cf.normalize_author), [])

    def test_ack_only_target_does_not_resolve_thread(self):
        p = self.post()
        reply = self.rows("reply", p["id"], "Second issue", "-a", "codex")
        self.rows("ack", p["id"], "-a", "claude")
        self.assertEqual(self.rows("show", p["id"])["status"], "open")
        self.assertEqual([e["id"] for e in self.rows("unread", "--for", "claude")["items"]], [reply["id"]])

    def test_any_agent_can_resolve_and_reopen_without_changing_decisions(self):
        d = self.add("-t", "decision", "-a", "user", "Keep the engine", "--rationale", "scope")
        p = self.post("Decision discussion", "Review", "codex", "--ref", d)
        self.rows("status", p["id"], "resolved", "Evidence reviewed", "-a", "grok")
        self.assertEqual(self.rows("list", "--status", "resolved")["total"], 1)
        self.assertEqual(self.rows("search", "review*", "--status", "resolved")["total"], 2)
        self.assertEqual(cf.compute_grades(self.log_entries())[d], "stated")
        self.rows("status", p["id"], "open", "New evidence", "-a", "claude")
        self.assertEqual(self.rows("show", p["id"])["status"], "open")

    def test_following_is_attention_not_visibility(self):
        a = self.post("Gas", "Gas issue", "codex", "--tag", "gas")
        b = self.post("Slots", "Storage", "codex", "--tag", "cache")
        self.rows("follow", "--tag", "gas", "-a", "claude")
        self.assertEqual(self.rows("unread", "--for", "claude", "--following")["total"], 1)
        self.assertEqual(self.rows("list", "-a", "claude")["total"], 2)
        self.rows("reply", b["id"], "@claude please review", "-a", "grok")
        self.assertEqual(self.rows("unread", "--for", "claude", "--following")["total"], 2)
        self.rows("unfollow", a["id"], "-a", "claude")
        self.assertEqual(self.rows("unread", "--for", "claude", "--following")["total"], 1)
        self.rows("follow", "--all", "-a", "new-agent")
        self.assertEqual(self.rows("list", "--following", "-a", "new-agent")["total"], 2)

    def test_authors_automatically_follow_participated_threads(self):
        p = self.post()
        self.rows("reply", p["id"], "Evidence", "-a", "claude")
        self.assertEqual(self.rows("list", "--following", "-a", "claude")["total"], 1)
        self.rows("unfollow", p["id"], "-a", "claude")
        self.assertEqual(self.rows("list", "--following", "-a", "claude")["total"], 0)

    def test_all_cases_search_and_cross_case_evidence(self):
        evidence = self.add("-t", "observation", "-a", "codex", "Witness complete")
        old_case = self.log_entries()[-1]["case"]
        self.cli("open", "Another case", expect=0)
        p = self.post("Cross case", "Evidence everywhere", "grok", "--ref", evidence)
        self.assertIn(evidence, p["refs"])
        self.assertEqual(self.rows("search", evidence)["total"], 1)
        self.assertEqual(self.rows("search", "everywhere", "--case", old_case)["total"], 0)
        self.assertNotEqual(self.cli("add", "-t", "note", "-a", "codex", "Ordinary note", "--ref", evidence).rc, 0)

    def test_full_text_phrase_boolean_prefix_unicode_and_long_reply(self):
        p = self.post("State residency", "ordinary text", "codex", "--tag", "slot-cache")
        self.rows("reply", p["id"], "x " * 1500 + "cold load gas naïve café prefetching", "-a", "claude")
        self.post("Gas alternative", "cold account gas only", "grok")
        self.assertEqual(self.rows("search", '"cold load" AND gas')["total"], 1)
        self.assertEqual(self.rows("search", "prefetch*")["total"], 1)
        self.assertEqual(self.rows("search", "café AND naïve")["total"], 1)
        self.assertEqual(self.rows("search", "gas NOT account")["total"], 1)
        self.assertEqual(self.rows("search", "gas", "--by", "claude", "--tag", "slot-cache")["total"], 1)
        self.assertEqual(self.rows("search", "gas", "--tag", "slot")["total"], 0)
        self.assertEqual(self.rows("search", "title:residency")["total"], 2)

    def test_search_pagination_filters_before_limit_and_reports_total(self):
        for n in range(5):
            self.post(f"Cache {n}", "needle", "claude" if n % 2 else "codex")
        first = self.rows("search", "needle", "--by", "codex", "--limit", "2")
        second = self.rows("search", "needle", "--by", "codex", "--limit", "2", "--offset", "2")
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(len(second["items"]), 1)
        self.assertFalse({x["id"] for x in first["items"]} & {x["id"] for x in second["items"]})

    def test_index_rebuild_incremental_and_generic_dig(self):
        p = self.post("Searchable", "fingerprintneedle")
        self.assertEqual(self.rows("search", "fingerprintneedle")["total"], 1)
        self.rows("reply", p["id"], "fingerprintneedle again", "-a", "claude")
        self.assertEqual(self.rows("search", "fingerprintneedle")["total"], 2)
        (self.dir / ".casefile/state/board-index.db").unlink()
        self.assertEqual(self.rows("search", "fingerprintneedle")["total"], 2)
        self.assertIn(p["id"], self.cli("dig", "fingerprintneedle", expect=0).out)

    def test_old_note_and_public_board_both_discovered_in_inbox(self):
        old = self.add("-t", "note", "-a", "codex", "Legacy addressed", "--to", "claude")
        p = self.post()
        r = json.loads(self.cli("inbox", "--for", "claude", "--json", expect=0).out)
        self.assertEqual({e["id"] for e in r}, {old, p["id"]})
        self.rows("read", p["id"], "-a", "claude")
        r = json.loads(self.cli("inbox", "--for", "claude", "--json", expect=0).out)
        self.assertEqual([e["id"] for e in r], [old])

    def test_boot_discovers_other_case_and_does_not_mark_read(self):
        p = self.post()
        self.cli("open", "Unrelated case", expect=0)
        r = self.cli("boot", "-a", "claude", "--skip-recheck", "--ok-exit", expect=0)
        self.assertIn("messageboard", r.out)
        self.assertIn("board unread --for claude", r.out)
        self.assertEqual(self.rows("unread", "--for", "claude")["items"][0]["id"], p["id"])

    def test_controls_not_substantive_and_poll_does_not_write(self):
        p = self.post()
        self.rows("read", p["id"], "-a", "claude")
        self.rows("ack", p["id"], "-a", "grok")
        self.rows("follow", p["id"], "-a", "claude")
        self.rows("unfollow", p["id"], "-a", "claude")
        controls = [e for e in self.log_entries() if board.is_control(e)]
        self.assertEqual(len(controls), 4)
        self.assertTrue(all(not cf.substantive(e) for e in controls))
        before = (self.dir / ".casefile/log.jsonl").read_bytes()
        self.assertEqual(self.cli("board", "poll", "--for", "claude", expect=0).out, "")
        self.assertEqual((self.dir / ".casefile/log.jsonl").read_bytes(), before)

    def test_anonymous_write_and_malformed_queries_fail_without_append(self):
        before = (self.dir / ".casefile/log.jsonl").read_bytes()
        self.cli("board", "post", "Title", "Body", expect=40)
        self.cli("board", "post", "Title", "Body", "-a", "agent", expect=40)
        self.cli("board", "search", '"unterminated', expect=1)
        self.cli("board", "list", "--limit", "0", expect=1)
        self.cli("board", "follow", "--all", "--tag", "gas", "-a", "codex", expect=1)
        self.assertEqual((self.dir / ".casefile/log.jsonl").read_bytes(), before)

    def test_lossless_stdin_and_argument_exclusivity(self):
        body = "Code example:\n\n```rust\nlet gas = 42;\n```"
        r = self.cli("board", "post", "Snippet", "--body-stdin", "-a", "codex", "--json", stdin=body, expect=0)
        self.assertEqual(json.loads(r.out)["body"], "Snippet\n\n" + body)
        self.cli("board", "post", "Title", "Body", "--body-stdin", "-a", "codex", stdin="other", expect=1)

    def test_default_board_browse_and_generated_guidance(self):
        self.post()
        self.assertIn("Gas mismatch", self.cli("board", expect=0).out)
        self.assertIn("board post", self.cli("cheatsheet", expect=0).out)
        skill = (self.dir / ".claude/skills/casefile/SKILL.md").read_text()
        self.assertIn("board poll", skill)
        self.assertIn("board unread", (self.dir / "AGENTS.md").read_text())

    def test_search_author_filter_handles_legacy_capitalization(self):
        case = cf.load_active(self.dir)
        cf.append_entry(self.dir, {"id": "abcd1234", "case": case, "type": "note",
                                   "author": "Codex", "ts": "2026-01-01T00:00:00+00:00",
                                   "body": "Historical author casing", "refs": []})
        p = self.post("Gas casing", "Needle")
        self.assertEqual(p["author"], "Codex")
        self.assertEqual(self.rows("search", "needle", "--by", "codex")["total"], 1)
