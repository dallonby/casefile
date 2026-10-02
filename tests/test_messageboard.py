"""Behavioral tests for open discussions, FTS retrieval and exact read receipts."""

import json
import os
import select
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from test_casefile import CASEFILE, CliBase, cf
import casefile_board as board


class MessageboardTests(CliBase):
    def post(self, title="Gas mismatch", body="Cold storage read evidence", author="codex", *extra):
        r = self.cli("board", "post", title, body, "-a", author, "--json", *extra, expect=0)
        return json.loads(r.out)

    def rows(self, *args):
        return json.loads(self.cli("board", *args, "--json", expect=0).out)

    def reader(self, *args, author="claude"):
        out = self.cli("board", "watch", "--once", "--for", author,
                       "--json", *args, expect=0).out
        return [json.loads(line) for line in out.splitlines()]

    def test_reader_restart_has_exact_cursor_but_does_not_mark_read(self):
        p = self.post()
        before = (self.dir / ".casefile/log.jsonl").read_bytes()
        self.assertEqual([r["id"] for r in self.reader()], [p["id"]])
        self.assertEqual(self.reader(), [])
        r = self.rows("reply", p["id"], "follow-up", "-a", "codex")
        self.assertEqual([m["id"] for m in self.reader()], [r["id"]])
        self.assertEqual(self.rows("unread", "--for", "claude")["total"], 2)
        self.assertTrue((self.dir / ".casefile/log.jsonl").read_bytes().startswith(before))
        self.assertFalse(any(board.is_control(e) for e in self.log_entries()))
        # Another session/consumer can rediscover unprocessed instructions.
        self.assertEqual([m["id"] for m in self.reader("--reader", "other")], [p["id"], r["id"]])

    def test_reader_drains_burst_oldest_first_and_preserves_filtered_messages(self):
        p = self.post()
        expected = [p["id"]]
        expected += [self.rows("reply", p["id"], str(i), "-a", "codex")["id"] for i in range(6)]
        observed = []
        for _ in range(4):
            observed += [e["id"] for e in self.reader("--limit", "2")]
        self.assertEqual(observed, expected)
        self.assertEqual(self.reader(), [])
        # Consumer identity includes the filter scope, so one view cannot
        # discard undelivered instructions belonging to another view.
        other = self.post("Different thread", "other", "codex", "--tag", "slots")
        self.assertEqual(self.reader("--tag", "gas"), [])
        self.assertEqual([e["id"] for e in self.reader("--tag", "slots")], [other["id"]])

    def test_reader_follows_mentions_and_normalizes_author_filter(self):
        p = self.post("Follow", "interesting", "codex", "--tag", "gas")
        self.rows("follow", p["id"], "-a", "claude")
        other = self.post("Unfollowed", "other", "codex")
        mention = self.rows("reply", other["id"], "@claude please look", "-a", "Codex")
        self.assertEqual([r["id"] for r in self.reader("--following", "--by", "codex")],
                         [p["id"], mention["id"]])
        self.assertEqual([r["id"] for r in self.reader("--thread", mention["id"], "--following")],
                         [mention["id"]])
        self.assertEqual(self.reader(author="codex"), [])

    def test_reader_preview_full_read_and_status_filters_are_independent(self):
        p = self.post(body="x" * 1000)
        clipped = self.reader()[0]
        self.assertTrue(clipped["body_truncated"])
        full = self.reader("--full")[0]
        self.assertEqual(full["body"], p["body"])
        self.assertFalse(full["body_truncated"])
        self.rows("read", p["id"], "--message", "--full", "-a", "claude")
        self.assertEqual(self.reader("--reader", "fresh"), [])
        s = self.rows("status", p["id"], "blocked", "needs help", "-a", "codex")
        self.assertEqual(self.reader("--status", "open"), [])
        self.assertEqual([r["id"] for r in self.reader("--status", "blocked")], [s["id"]])

    def test_reader_temporary_unfollow_does_not_erase_delivery(self):
        p = self.post()
        self.rows("follow", p["id"], "-a", "claude")
        self.assertEqual([r["id"] for r in self.reader("--following")], [p["id"]])
        self.rows("unfollow", p["id"], "-a", "claude")
        self.assertEqual(self.reader("--following"), [])
        self.rows("follow", p["id"], "-a", "claude")
        self.assertEqual(self.reader("--following"), [])

    def test_reader_fresh_priority_preserves_backlog_and_cursor(self):
        p = self.post()
        replies = [self.rows("reply", p["id"], str(i), "-a", "codex")["id"] for i in range(4)]
        self.assertEqual([r["id"] for r in self.reader("--newest-first", "--limit", "2")],
                         list(reversed(replies[-2:])))
        self.assertEqual([r["id"] for r in self.reader()], [p["id"], *replies[:2]])
        self.assertEqual(self.reader("--newest-first"), [])

    def test_reader_reconciled_older_message_is_not_lost(self):
        p = self.post()
        self.reader()
        later = self.rows("reply", p["id"], "merged older finding", "-a", "codex")
        entries = self.log_entries()
        entries.insert(0, entries.pop())  # a merge can place activity before its root
        (self.dir / ".casefile/log.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in entries))
        self.assertEqual([r["id"] for r in self.reader()], [later["id"]])

    def test_reader_partial_append_cooldown_and_corrupt_cursor(self):
        p = self.post()
        self.assertEqual([r["id"] for r in self.reader("--cooldown", "30")], [p["id"]])
        r = self.rows("reply", p["id"], "new", "-a", "codex")
        self.assertEqual(self.reader("--cooldown", "30"), [])
        self.assertEqual([m["id"] for m in self.reader()], [r["id"]])
        with (self.dir / ".casefile/log.jsonl").open("ab") as f:
            f.write(b'{"id":')
        self.assertEqual(self.reader(), [])
        cursor = next((self.dir / ".casefile/state").glob("board-reader-*.json"))
        cursor.write_text("broken JSON")
        failure = self.cli("board", "watch", "--once", "--for", "claude", expect=1)
        self.assertIn("invalid board reader cursor", failure.err)

    def test_reader_failed_output_does_not_advance_and_busy_reader_is_quiet(self):
        p = self.post()
        args = SimpleNamespace(for_author="claude", author=None, reader="watch", thread=None,
                               case=None, tag=[], by=None, status=None, following=False,
                               full=False, json=True, cooldown=0, limit=20)
        def broken(_):
            raise BrokenPipeError()
        with self.assertRaises(BrokenPipeError):
            board.reader_once(cf, self.dir, args, emit=broken)
        self.assertFalse(list((self.dir / ".casefile/state").glob("board-reader-*.json")))
        lock = next((self.dir / ".casefile/state").glob("board-reader-*.lock"))
        with board._reader_lock(lock) as held:
            self.assertTrue(held)
            self.assertEqual(self.reader(), [])
        self.assertEqual([r["id"] for r in self.reader()], [p["id"]])

    def test_reader_refreshes_postgres_mirror_each_cycle(self):
        p = self.post()
        with patch.object(cf, "persistence_mode", return_value="postgres"), \
                patch.object(cf, "read_entries", return_value=self.log_entries()) as read:
            key = str(self.dir.resolve())
            cf._PG_RECONCILED.add(key)
            cf._PG_LOCAL_CACHE[key] = []
            self.assertEqual(board._tail_entries(cf, self.dir)[-1]["id"], p["id"])
            self.assertNotIn(key, cf._PG_RECONCILED)
            self.assertNotIn(key, cf._PG_LOCAL_CACHE)
            self.assertEqual(read.call_count, 1)

    def test_reader_follows_live_arrivals_and_deadline_is_finite(self):
        p = self.post()
        env = {k: v for k, v in os.environ.items() if not k.startswith("CASEFILE_")}
        env["CASEFILE_SKIP_PIP"] = "1"
        proc = subprocess.Popen([sys.executable, str(CASEFILE), "board", "watch", "--for", "claude",
                                 "--interval", "0.05", "--timeout", "2", "--json"],
                                cwd=self.dir, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 5)[0])
            self.assertEqual(json.loads(proc.stdout.readline())["id"], p["id"])
            r = self.rows("reply", p["id"], "live", "-a", "codex")
            self.assertTrue(select.select([proc.stdout], [], [], 5)[0])
            self.assertEqual(json.loads(proc.stdout.readline())["id"], r["id"])
            out, err = proc.communicate(timeout=4)
            self.assertEqual((proc.returncode, out, err), (0, "", ""))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=3)
        for flag, value in [("--interval", "0"), ("--timeout", "nan"), ("--cooldown", "-1"), ("--limit", "0")]:
            self.assertEqual(self.cli("board", "watch", "--once", "--for", "claude", flag, value).rc, 1)
        self.assertEqual(self.cli("board", "watch", "--once").rc, cf.EXIT_IDENTITY)

    def test_tail_is_bounded_by_default_and_full_is_explicit(self):
        p = self.post()
        r = self.rows("reply", p["id"], "Full body\n" + "x" * 1200, "-a", "claude")
        self.rows("ack", r["id"], "-a", "codex")
        before = (self.dir / ".casefile/log.jsonl").read_bytes()
        out = self.cli("board", "tail", "--json", expect=0).out
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([row["id"] for row in rows], [p["id"], r["id"]])
        self.assertTrue(rows[1]["body_truncated"])
        self.assertLessEqual(len(rows[1]["body"]), 600)
        full = [json.loads(line) for line in self.cli(
            "board", "tail", "--json", "--full", expect=0).out.splitlines()]
        self.assertEqual(full[1]["body"], r["body"])
        self.assertFalse(full[1]["body_truncated"])
        plain = self.cli("board", "tail", "-n", "1", expect=0).out
        self.assertIn("truncated; use --full", plain)
        full_plain = self.cli("board", "tail", "-n", "1", "--full", expect=0).out
        self.assertIn(r["body"], full_plain)
        self.assertNotIn("[ack]", plain)
        self.assertEqual((self.dir / ".casefile/log.jsonl").read_bytes(), before)

    def test_tail_filters_before_taking_last_messages(self):
        p = self.post("Gas", "first", "codex", "--tag", "gas")
        r = self.rows("reply", p["id"], "second", "-a", "claude")
        self.post("Unrelated", "third", "claude")
        out = self.cli("board", "tail", "--thread", r["id"], "--tag", "gas",
                       "--by", "claude", "-n", "1", "--json", expect=0).out
        self.assertEqual(json.loads(out)["id"], r["id"])
        self.assertEqual(self.cli("board", "tail", "-n", "0", expect=0).out, "")
        self.assertIn("nonnegative", self.cli("board", "tail", "-n", "-1", expect=1).err)

    def test_tail_status_changes_are_visible(self):
        p = self.post()
        s = self.rows("status", p["id"], "blocked", "Needs data", "-a", "codex")
        out = self.cli("board", "tail", "--status", "blocked", "-n", "1",
                       "--json", expect=0).out
        self.assertEqual(json.loads(out)["id"], s["id"])

    def test_tail_defers_partial_append_but_reports_corrupt_complete_line(self):
        p = self.post()
        path = self.dir / ".casefile/log.jsonl"
        with path.open("ab") as f:
            f.write(b'{"id":')
        out = self.cli("board", "tail", "--json", expect=0).out
        self.assertEqual(json.loads(out)["id"], p["id"])
        with path.open("ab") as f:
            f.write(b'\n')
        self.assertIn("corrupt log line", self.cli("board", "tail", expect=1).err)

    def test_tail_follow_prints_new_message_once_and_ctrl_c_is_clean(self):
        p = self.post()
        env = {k: v for k, v in os.environ.items() if not k.startswith("CASEFILE_")}
        env["CASEFILE_SKIP_PIP"] = "1"
        proc = subprocess.Popen([sys.executable, str(CASEFILE), "board", "tail", "-f", "--json"],
                                cwd=self.dir, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([proc.stdout], [], [], 5)[0], "initial output timed out")
            self.assertEqual(json.loads(proc.stdout.readline())["id"], p["id"])
            r = self.rows("reply", p["id"], "New live message", "-a", "claude")
            self.assertTrue(select.select([proc.stdout], [], [], 5)[0], "live output timed out")
            self.assertEqual(json.loads(proc.stdout.readline())["id"], r["id"])
            proc.send_signal(signal.SIGINT)
            out, err = proc.communicate(timeout=3)
            self.assertEqual((proc.returncode, out, err), (0, "", ""))
            self.assertFalse(any(board.payload(e).get("op") == "read" for e in self.log_entries()))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=3)

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

    def test_thread_page_and_receipt_only_cover_exposed_messages(self):
        p = self.post("Long thread", "root body", "codex")
        replies = [self.rows("reply", p["id"], f"reply {n}",
                             "-a", "codex") for n in range(25)]

        anchored = self.rows("show", replies[-1]["id"])
        self.assertIn(replies[-1]["id"], [e["id"] for e in anchored["events"]])
        self.assertGreater(anchored["offset"], 0)
        explicit_first = self.rows("show", replies[-1]["id"], "--offset", "0")
        self.assertEqual(explicit_first["offset"], 0)
        self.assertNotIn(replies[-1]["id"], [e["id"] for e in explicit_first["events"]])
        plain = self.cli("board", "show", p["id"], expect=0).out
        self.assertIn("messages: showing 20 of 26 from offset 0", plain)
        self.assertIn(f"more: board show {p['id']} --offset 20", plain)

        page = self.rows("read", p["id"], "-a", "reader")
        self.assertEqual(page["total_messages"], 26)
        self.assertEqual(page["shown_messages"], 20)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_offset"], 20)
        self.assertTrue(page["read_complete"])
        self.assertEqual(len(page["read_message_ids"]), 20)
        self.assertEqual(self.rows("unread", "--for", "reader")["total"], 6)

        rest = self.rows("read", p["id"], "-a", "reader", "--offset", "20")
        self.assertEqual(rest["shown_messages"], 6)
        self.assertFalse(rest["has_more"])
        self.assertEqual(len(rest["read_message_ids"]), 6)
        self.assertEqual(self.rows("unread", "--for", "reader")["total"], 0)

        receipt = [e for e in self.log_entries()
                   if board.payload(e).get("op") == "read"]
        self.assertEqual(len(receipt), 2)
        self.assertTrue(all(board.payload(e).get("complete") is True for e in receipt))
        self.assertEqual(set(board.payload(receipt[0])["targets"]),
                         set(page["read_message_ids"]))

    def test_clipped_read_stays_unread_until_full_message_read(self):
        p = self.post("Clipped", "root", "codex")
        reply = self.rows("reply", p["id"], "oversized " + "z" * 1400, "-a", "codex")
        self.rows("read", p["id"], "--message", "-a", "reader")
        preview = self.rows("read", reply["id"], "--message", "-a", "reader")
        self.assertEqual(preview["read_message_ids"], [])
        self.assertEqual(preview["preview_message_ids"], [reply["id"]])
        self.assertFalse(preview["read_complete"])
        self.assertEqual(self.rows("unread", "--for", "reader")["total"], 1)
        full = self.rows("read", reply["id"], "--message", "--full", "-a", "reader")
        self.assertEqual(full["read_message_ids"], [reply["id"]])
        self.assertEqual(full["preview_message_ids"], [])
        self.assertTrue(full["read_complete"])
        self.assertEqual(self.rows("unread", "--for", "reader")["total"], 0)

    def test_message_specific_read_handles_old_reply_without_thread_dump(self):
        p = self.post("Focused", "root", "codex")
        reply = self.rows("reply", p["id"], "target " + "y" * 1400, "-a", "codex")
        view = self.rows("show", reply["id"], "--message")
        self.assertTrue(view["message_view"])
        self.assertEqual(view["shown_messages"], 1)
        self.assertEqual([e["id"] for e in view["events"]], [reply["id"]])
        self.assertTrue(view["events"][0]["body_truncated"])
        full = self.rows("show", reply["id"], "--message", "--full")
        self.assertEqual(full["events"][0]["body"], reply["body"])
        read = self.rows("read", reply["id"], "--message", "--full", "-a", "reader")
        self.assertEqual(read["read_message_ids"], [reply["id"]])
        receipt = next(e for e in self.log_entries()
                       if board.payload(e).get("op") == "read")
        self.assertEqual(board.payload(receipt)["targets"], [reply["id"]])
        self.assertTrue(board.payload(receipt)["complete"])

    def test_natural_language_search_is_lexical_ranked_and_reaches_old_messages(self):
        old = self.post("Replay proof", "The proof explains replay residency", "codex")
        for n in range(25):
            self.post(f"Unrelated {n}", "routine maintenance", "claude")
        result = self.rows("search", "Which messages mention replay proofs?!", "--limit", "5")
        self.assertEqual(result["mode"], "lexical")
        self.assertIn(old["id"], [item["id"] for item in result["items"]])
        self.assertTrue(all(len(item["body"]) <= 600 for item in result["items"]))
        plain = self.cli("board", "search", "Which messages mention replay proofs?!",
                         "--limit", "1", expect=0).out
        self.assertIn("lexical search (natural-language terms; no semantic embeddings)", plain)
        exact = self.rows("search", '"Replay proof"', "--fts")
        self.assertEqual(exact["mode"], "fts")
        self.assertEqual(exact["total"], 1)

    def test_default_views_bound_title_and_reference_metadata(self):
        refs = [self.add("-t", "observation", "-a", "codex", f"reference {n}")
                for n in range(30)]
        p = self.post("T" * 1000, "short", "codex",
                      *sum((["--ref", ref] for ref in refs), []))
        view = self.rows("show", p["id"])
        event = view["events"][0]
        self.assertTrue(view["title_truncated"])
        self.assertTrue(event["title_truncated"])
        self.assertLessEqual(len(event["board"]["title"]), 240)
        self.assertTrue(event["refs_truncated"])
        self.assertEqual(event["refs_count"], 30)
        self.assertLessEqual(len(event["refs"]), 24)
        full = self.rows("show", p["id"], "--full")
        self.assertEqual(full["events"][0]["board"]["title"], p["body"].split("\n", 1)[0])
        self.assertEqual(len(full["events"][0]["refs"]), 30)

    def test_search_author_filter_handles_legacy_capitalization(self):
        case = cf.load_active(self.dir)
        cf.append_entry(self.dir, {"id": "abcd1234", "case": case, "type": "note",
                                   "author": "Codex", "ts": "2026-01-01T00:00:00+00:00",
                                   "body": "Historical author casing", "refs": []})
        p = self.post("Gas casing", "Needle")
        self.assertEqual(p["author"], "Codex")
        self.assertEqual(self.rows("search", "needle", "--by", "codex")["total"], 1)
