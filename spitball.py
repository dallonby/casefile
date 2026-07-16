#!/usr/bin/env python3
"""spitball — multi-model deliberation driver (SPEC §12).

A disposable turn-ferrying loop (P1, P8): everything of consequence lands in
the casefile log via the models' own CLI filings; the driver itself holds no
state worth keeping. kill -9 mid-session must lose nothing of consequence.

Adapters (§12.2): start(context) -> handle, send(handle, msg) -> reply,
cost(handle) -> {"usd": float|None, "tokens": int}, stop(handle). Flag sets
below were verified against the installed CLIs on 2026-07-17 (see the log,
source: manual) — re-verify on CLI upgrades, never trust memory.
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import casefile as cf

TURN_TIMEOUT_S = 600
CLI = [sys.executable, str(Path(cf.__file__).resolve())]  # works outside this repo
CLI_STR = f"python3 {Path(cf.__file__).resolve()}"


# ------------------------------------------------------------------ adapters

class ClaudeAdapter:
    """claude -p resume-mode (§12.2 v1). Project settings are excluded so the
    repo's own hooks (sweep/observe) don't fire inside deliberation turns —
    the driver owns transcripts and end-of-session sweeps (§12.1 step 4)."""
    name = "claude"

    def __init__(self, root: Path):
        self.root = root
        self.base = ["claude", "-p", "--output-format", "json",
                     "--setting-sources", "user",
                     "--allowedTools",
                     f"Bash({CLI_STR}:*)", "Bash(python3 casefile.py:*)"]

    def start(self, context: str) -> dict:
        return self._call(None, context)

    def send(self, handle: dict, msg: str) -> str:
        h = self._call(handle["sid"], msg, handle)
        return h["reply"]

    def _call(self, sid, prompt, handle=None):
        cmd = list(self.base) + (["-r", sid] if sid else []) + [prompt]
        p = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True,
                           timeout=TURN_TIMEOUT_S)
        if p.returncode != 0:
            raise RuntimeError(f"claude adapter: rc={p.returncode}: {p.stderr[:300]}")
        d = json.loads(p.stdout)
        h = handle or {"sid": None, "usd": 0.0, "tokens": 0}
        h["sid"] = d.get("session_id", h["sid"])
        h["usd"] += d.get("total_cost_usd") or 0.0
        h["reply"] = d.get("result", "")
        return h

    def cost(self, handle):
        return {"usd": handle["usd"], "tokens": handle["tokens"]}

    def stop(self, handle):
        pass  # -p sessions end per call; nothing to tear down


class CodexAdapter:
    """codex exec with session resume (§12.2). thread_id from the
    thread.started event; reply is the last agent_message item."""
    name = "codex"

    def __init__(self, root: Path):
        self.root = root
        self.opts = ["--json", "--sandbox", "workspace-write"]

    def start(self, context: str) -> dict:
        return self._call(["codex", "exec", *self.opts, context],
                          {"tid": None, "usd": None, "tokens": 0})

    def send(self, handle: dict, msg: str) -> str:
        h = self._call(["codex", "exec", "resume", handle["tid"], *self.opts, msg],
                       handle)
        return h["reply"]

    def _call(self, cmd, handle):
        p = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True,
                           timeout=TURN_TIMEOUT_S)
        if p.returncode != 0:
            raise RuntimeError(f"codex adapter: rc={p.returncode}: {p.stderr[:300]}")
        reply = ""
        for line in p.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "thread.started":
                handle["tid"] = ev.get("thread_id", handle["tid"])
            elif ev.get("type") == "item.completed" \
                    and ev.get("item", {}).get("type") == "agent_message":
                reply = ev["item"].get("text", reply)
            elif ev.get("type") == "turn.completed":
                u = ev.get("usage", {})
                handle["tokens"] += (u.get("input_tokens", 0)
                                     + u.get("output_tokens", 0))
        handle["reply"] = reply
        return handle

    def cost(self, handle):
        return {"usd": handle["usd"], "tokens": handle["tokens"]}

    def stop(self, handle):
        pass


class FakeAdapter:
    """Scripted adapter for tests and the CI kill test (SPEC §18). The script
    file maps model name -> list of replies; a reply may be a string or
    {"sleep": s, "text": …} to hold a turn open (so tests can kill -9
    mid-round). Runs out of script -> replies 'pass'."""

    def __init__(self, root: Path, name: str, script: Path):
        self.root, self.name, self.script = root, name, script

    def start(self, context: str) -> dict:
        return {"i": 0}

    def send(self, handle: dict, msg: str) -> str:
        replies = json.loads(self.script.read_text()).get(self.name, [])
        if handle["i"] >= len(replies):
            return "pass"
        r = replies[handle["i"]]
        handle["i"] += 1
        if isinstance(r, dict):
            time.sleep(r.get("sleep", 0))
            return r.get("text", "")
        return r

    def cost(self, handle):
        return {"usd": 0.0, "tokens": 0}

    def stop(self, handle):
        pass


def make_adapter(name: str, root: Path, fake_script: Path | None = None):
    if fake_script:
        return FakeAdapter(root, name, fake_script)
    if name == "claude":
        return ClaudeAdapter(root)
    if name == "codex":
        return CodexAdapter(root)
    raise SystemExit(f"unknown model '{name}' (adapters: claude, codex)")


# ------------------------------------------------------------------- briefs

DEFAULT_BRIEFS = {
    "proposer": """\
You are the PROPOSER in a recorded two-model deliberation. Rules:
- File every falsifiable claim you make: `{cli} add -t hypothesis -a {name} "<claim>"`.
- File decisions/constraints/questions likewise (author {name}); ground truth
  only as observations with --source. Never edit .casefile/log.jsonl by hand.
- Attack the critic's leading hypothesis harder than you defend your own —
  dispute via `{cli} dispute <id> -a {name} --reason "…"`.
- Endorse the other model's claims only when genuinely persuaded
  (`{cli} endorse <id> -a {name}`); agreement is not verification.
- End each turn with a concise message to the other model: your current
  position, what you filed (ids), and what evidence would change your mind.
""",
    "critic": """\
You are the CRITIC in a recorded two-model deliberation. Rules:
- Your job is to break the proposer's leading hypothesis: find the
  discriminating test, the counterexample, the unstated assumption. File
  disputes via `{cli} dispute <id> -a {name} --reason "…"`.
- File your own alternatives as hypotheses (author {name}); endorse the
  proposer's claims only when they withstand your attack.
- Ground truth only as observations with --source. Never edit the log by hand.
- End each turn with a concise message to the other model: strongest
  objection, what you filed (ids), and what would settle the question.
""",
}


def role_brief(root: Path, role: str, model: str) -> str:
    p = root / ".casefile" / "roles" / f"{role}.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_BRIEFS[role])
    return p.read_text().replace("{name}", model).replace("{cli}", CLI_STR)


# -------------------------------------------------------------------- driver

def case_snapshot(root: Path, case: str):
    entries = cf.read_entries(root)
    ce = [e for e in entries if e["case"] == case]
    grades = cf.compute_grades(entries)
    qs, ds = cf.open_items(ce)
    return ce, grades, ds


def converged(root: Path, case: str) -> bool:
    """§12.1: no open disputes; leading hypothesis endorsed or verified."""
    ce, grades, ds = case_snapshot(root, case)
    if ds:
        return False
    hyps = [e for e in ce if e["type"] == "hypothesis"
            and grades[e["id"]] not in ("refuted",)]
    return any(grades[h["id"]] in ("verified", "consensus") for h in hyps)


def seed_context(root: Path, case: str, topic: str, blind: bool) -> str:
    cmd = CLI + ["resume-context"]
    if blind:
        cmd.append("--blind")
    ctx = subprocess.run(cmd, cwd=root, capture_output=True, text=True).stdout
    rec = subprocess.run(CLI + ["recall", topic], cwd=root,
                         capture_output=True, text=True).stdout
    seed = f"TOPIC: {topic}\n\n{ctx}"
    if rec and "no matches" not in rec:
        seed += f"\nPRIOR CASES (compost matches — dig before re-treading):\n{rec}"
    return seed


def run(topic: str, models=("claude", "codex"), turns: int = 6,
        budget_usd: float | None = None, blind: str | None = None,
        fake_script: str | None = None, root: Path | None = None) -> dict:
    root = root or cf.find_root()
    if root is None:
        raise SystemExit("no .casefile here (run `casefile init`)")
    meta = cf.load_meta(root)
    case = cf.load_active(root, meta)
    if not case:
        raise SystemExit("no active case (run `casefile open`)")

    session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tdir = root / ".casefile" / "transcripts" / session
    tdir.mkdir(parents=True, exist_ok=True)

    fake = Path(fake_script) if fake_script else None
    a_name, b_name = models
    A = make_adapter(a_name, root, fake)
    B = make_adapter(b_name, root, fake)
    roles = {a_name: "proposer", b_name: "critic"}

    def log_t(model, tag, text):
        with (tdir / f"{model}.log").open("a") as f:
            f.write(f"--- {tag} {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n{text}\n")
        print(f"[{model}] {text.strip()[:400]}" + ("…" if len(text.strip()) > 400 else ""))

    def spend():
        c = [A.cost(ha), B.cost(hb)]
        return sum(x["usd"] or 0.0 for x in c), sum(x["tokens"] for x in c)

    # 1. seed each model: role brief + resume-context (+recall)
    ha = A.start(role_brief(root, roles[a_name], a_name) + "\n"
                 + seed_context(root, case, topic, blind == a_name)
                 + "\nBegin: state your opening position.")
    log_t(a_name, "seed-reply", ha.get("reply", ""))
    hb = B.start(role_brief(root, roles[b_name], b_name) + "\n"
                 + seed_context(root, case, topic, blind == b_name)
                 + f"\nThe {a_name} (proposer) opened with:\n{ha.get('reply','')}\n"
                 + "Respond: your opening critique.")
    log_t(b_name, "seed-reply", hb.get("reply", ""))

    # 2. ferry turns
    msg_to_a = hb.get("reply", "")
    outcome, idle_rounds = "turn-budget", 0
    entries_before = len(cf.read_entries(root))
    for turn in range(turns):
        if converged(root, case):
            outcome = "converged"
            break
        usd, _ = spend()
        if budget_usd is not None and usd >= budget_usd:
            outcome = "spend-budget"
            break
        ra = A.send(ha, f"[{b_name} says]:\n{msg_to_a}")
        log_t(a_name, f"turn-{turn}", ra)
        rb = B.send(hb, f"[{a_name} says]:\n{ra}")
        log_t(b_name, f"turn-{turn}", rb)
        msg_to_a = rb
        n = len(cf.read_entries(root))
        idle_rounds = idle_rounds + 1 if n == entries_before else 0
        entries_before = n
        if idle_rounds >= 2:
            outcome = "stalemate"  # two rounds with nothing filed (§12.1)
            break
    else:
        if converged(root, case):
            outcome = "converged"

    # 3. independent summaries — each written without seeing the other's (§12.1)
    sum_prompt = ("Deliberation over. WITHOUT consulting the other model's view, "
                  "state in <=5 bullet points what you believe was decided, "
                  "ruled out, and left open. Do not file anything for this.")
    sa, sb = A.send(ha, sum_prompt), B.send(hb, sum_prompt)
    log_t(a_name, "summary", sa)
    log_t(b_name, "summary", sb)

    # 4. sweep + digest-and-review happen via the models (driver prompts, models file)
    if outcome in ("converged", "turn-budget", "spend-budget"):
        da = A.send(ha, "Secretary sweep: file anything from this deliberation "
                        "not yet in the log, then propose a judgment digest via "
                        f"`{CLI_STR} digest \"…\" -a " + a_name +
                        " --kind judgment --supersedes <ids…>` for the settled span. "
                        "Reply with the digest id or NONE.")
        log_t(a_name, "digest", da)
        db = B.send(hb, "Adversarial review (§6.2): read the newest judgment digest "
                        "against the raw span (`python3 casefile.py log -n 50`). "
                        "Find anything dropped or upgraded; endorse or dispute it "
                        "via the CLI. Reply with what you filed.")
        log_t(b_name, "digest-review", db)
    A.stop(ha)
    B.stop(hb)

    usd, tokens = spend()
    result = {"outcome": outcome, "case": case, "session": session,
              "transcripts": str(tdir), "spend_usd": round(usd, 4),
              "tokens": tokens,
              "summaries": {a_name: sa, b_name: sb},
              "summary_divergence": _diff_summaries(sa, sb)}
    print(json.dumps({k: result[k] for k in
                      ("outcome", "spend_usd", "tokens", "summary_divergence")}))
    return result


def _diff_summaries(sa: str, sb: str) -> bool:
    """Crude divergence detector (§12.1): flag if either summary contains a
    bullet whose key terms are wholly absent from the other. A true diff is a
    human/model job; this only raises the flag."""
    def keyterms(s):
        words = {w.strip(".,`'\"()").lower() for w in s.split()}
        return {w for w in words if len(w) > 5}
    ka, kb = keyterms(sa), keyterms(sb)
    if not ka or not kb:
        return True
    overlap = len(ka & kb) / min(len(ka), len(kb))
    return overlap < 0.2
