"""Open, append-only discussion threads for casefile (stdlib only).

All authoritative state is ordinary note entries with a versioned ``board``
payload. SQLite is a disposable full-text index, never the message store.
Recipients and follows select attention; they never restrict visibility.
"""

import argparse
import hashlib
import json
import re
import sqlite3

VERSION = 1
ACTIVITY = {"post", "reply", "status"}
CONTROLS = {"read", "ack", "follow", "unfollow"}
STATES = ("open", "in-progress", "blocked", "resolved")


def payload(entry):
    value = entry.get("board")
    return value if (entry.get("type") == "note" and isinstance(value, dict)
                     and value.get("version") == VERSION) else {}


def is_control(entry):
    return payload(entry).get("op") in CONTROLS


def threads(entries):
    """Materialize all cases, including resolved and compacted discussions."""
    result = {}
    for e in entries:
        b = payload(e)
        if b.get("op") == "post":
            result[e["id"]] = {
                "id": e["id"], "case": e["case"], "title": b["title"],
                "author": e["author"], "tags": b.get("tags", []),
                "status": "open", "messages": [e], "events": [e],
                "updated": e["ts"], "position": 0,
            }
    # Two passes allow merged logs to put a reply before its root.
    for i, e in enumerate(entries):
        b = payload(e)
        t = result.get(b.get("thread", e["id"]))
        if not t:
            continue
        if b.get("op") in ACTIVITY:
            if b["op"] != "post":
                t["messages"].append(e)
                t["events"].append(e)
            if b["op"] == "status":
                t["status"] = b["status"]
            t["updated"], t["position"] = e["ts"], i
        elif b.get("op") == "ack":
            t["events"].append(e)
    return result


def read_ids(entries, author, normalize):
    author = normalize(author)
    return {target for e in entries
            if normalize(e["author"]) == author
            and payload(e).get("op") in {"read", "ack"}
            for target in payload(e).get("targets", [])}


def following(entries, thread, author, normalize):
    author = normalize(author)
    implicit = any(normalize(e["author"]) == author
                   for e in thread["messages"])
    settings = {}
    for e in entries:
        b = payload(e)
        if normalize(e["author"]) == author and b.get("op") in {"follow", "unfollow"}:
            settings[b["scope"]] = b["op"] == "follow"
    exact = "thread:" + thread["id"]
    if exact in settings:
        return settings[exact]
    return implicit or settings.get("all", False) or any(
        settings.get("tag:" + tag, False) for tag in thread["tags"])


def unread(entries, author, normalize, *, followed=False):
    """Exact seen-ID sets: writes and later replies never advance a cursor."""
    author = normalize(author)
    seen = read_ids(entries, author, normalize)
    result = []
    for t in threads(entries).values():
        is_following = following(entries, t, author, normalize) if followed else False
        for e in t["messages"]:
            if e["id"] in seen or normalize(e["author"]) == author:
                continue
            if followed and not is_following and author not in payload(e).get("mentions", []):
                continue
            result.append(e)
    positions = {e["id"]: i for i, e in enumerate(entries)}
    return sorted(result, key=lambda e: positions[e["id"]], reverse=True)


def notice(entries, author, normalize, limit=3):
    rows = unread(entries, author, normalize)
    if not rows:
        return []
    titles = threads(entries)
    lines = [f"Open messageboard: {len(rows)} unread message(s) across all cases."]
    for e in rows[:limit]:
        t = titles[payload(e).get("thread", e["id"])]
        lines.append(f"  {e['id']} from {e['author']} in {t['id']}: {t['title'][:100]}")
    lines.append(f"casefile board unread --for {author}; board read <thread-id>; "
                 "board ack <message-id> (read is not completion)")
    return lines


def _resolve(cf, entries, value):
    exact = next((e for e in entries if e["id"] == value), None)
    hits = [exact] if exact else [e for e in entries if e["id"].startswith(value)]
    if len(hits) != 1:
        cf.die(f"unknown or ambiguous entry: {value}")
    return hits[0]


def _thread(cf, entries, value, catalog):
    e = _resolve(cf, entries, value)
    tid = payload(e).get("thread", e["id"])
    if tid not in catalog:
        cf.die(f"{value} is not a board thread/message; link it with board post --ref")
    return catalog[tid], e


def _author(cf, args, writing=False):
    author, source = cf.resolve_author(getattr(args, "author", None))
    if writing and (source == "default" or author in {"agent", "system"}):
        cf.die("board writes require a named author: export CASEFILE_AUTHOR or use -a",
               cf.EXIT_IDENTITY)
    return cf.normalize_author(author)


def _tags(cf, values):
    tags = [s.strip().lstrip("#").casefold() for s in values]
    if any(not s or len(s) > 80 or any(c.isspace() for c in s) for s in tags):
        cf.die("tags must be nonempty words of at most 80 characters")
    return list(dict.fromkeys(tags))


def _mentions(cf, args, body):
    names = list(getattr(args, "to", []) or [])
    names += re.findall(r"(?<![\w./])@([\w][\w.-]*)", body)
    return list(dict.fromkeys(cf.normalize_author(s) for s in names))


def _append(cf, root, entries, case, author, body, op, refs=(), **metadata):
    entry = cf.make_entry(entries, case, "note", author, body,
                          refs=list(dict.fromkeys(refs)),
                          board={"version": VERSION, "op": op, **metadata})
    cf.append_entry(root, entry)
    return entry


def _receipt(entry, args):
    print(json.dumps(entry, ensure_ascii=False) if args.json else entry["id"])


def _summary(t):
    return {k: t[k] for k in ("id", "case", "title", "author", "tags", "status", "updated")} | {
        "messages": len(t["messages"]), "replies": sum(
            payload(e).get("op") == "reply" for e in t["messages"])}


def _selected(t, args, cf):
    return (not args.case or t["case"] == args.case) and (
        not args.status or t["status"] == args.status) and all(
        tag in t["tags"] for tag in _tags(cf, args.tag))


def _index(cf, root, catalog):
    """Persist FTS5 over full messages, titles, tags, authors and linked IDs.

    Snapshot identity covers searchable content, not just a row count. A
    transaction rebuilds the disposable cache atomically across CLI writers.
    """
    records = []
    for t in catalog.values():
        for e in t["messages"]:
            records.append((e["id"], t["id"], e["case"], cf.normalize_author(e["author"]),
                            t["title"], " ".join(t["tags"]), e["body"],
                            " ".join(e.get("refs", [])), t["status"]))
    fingerprint = hashlib.sha256(json.dumps(records, ensure_ascii=False).encode()).hexdigest()
    cache = root / cf.DIR / "state"
    cache.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(cache / "board-index.db", timeout=10)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS board_meta (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS board_search USING fts5("
                       "id, thread, case_id, author, title, tags, body, refs, status, "
                       "tokenize='unicode61')")
            old = db.execute("SELECT value FROM board_meta WHERE key='snapshot'").fetchone()
            if not old or old[0] != fingerprint:
                db.execute("DELETE FROM board_search")
                db.executemany("INSERT INTO board_search VALUES (?,?,?,?,?,?,?,?,?)", records)
                db.execute("INSERT OR REPLACE INTO board_meta VALUES ('snapshot', ?)", (fingerprint,))
        return db
    except Exception:
        db.close()
        raise


def search(cf, root, catalog, args):
    if not args.query.strip():
        cf.die("search query must not be empty")
    db = None
    try:
        db = _index(cf, root, catalog)
        where, params = ["board_search MATCH ?"], [args.query]
        for column, value in (("case_id", args.case), ("author", args.by),
                              ("status", args.status)):
            if value:
                where.append(column + " = ?")
                params.append(cf.normalize_author(value) if column == "author" else value)
        # Tag equality is applied before pagination; MATCH tags alone would
        # split hyphenated tags and could match a different topic.
        allowed = [t["id"] for t in catalog.values() if _selected(t, args, cf)]
        db.execute("CREATE TEMP TABLE allowed_threads (id TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO allowed_threads VALUES (?)", [(tid,) for tid in allowed])
        where.append("thread IN (SELECT id FROM allowed_threads)")
        query = " WHERE " + " AND ".join(where)
        total = db.execute("SELECT count(*) FROM board_search" + query, params).fetchone()[0]
        rows = db.execute(
            "SELECT id,thread,case_id,author,title,body,"
            "snippet(board_search,6,'[',']',' … ',36) FROM board_search" + query +
            " ORDER BY bm25(board_search,0,0,0,1,5,3,1,1,0), rowid DESC LIMIT ? OFFSET ?",
            [*params, args.limit, args.offset]).fetchall()
        return total, [dict(zip(("id", "thread", "case", "author", "title", "body", "snippet"), row))
                       for row in rows]
    except sqlite3.Error as exc:
        cf.die(f"board FTS5 search failed: {exc}. Use quoted phrases, AND/OR/NOT, or prefix*.")
    finally:
        if db is not None:
            db.close()


def run(cf, args):
    root, entries, meta = cf.require_root()
    op = args.board_command or "list"
    if getattr(args, "limit", 1) < 1 or getattr(args, "limit", 1) > 500:
        cf.die("--limit must be between 1 and 500; use --offset for further pages")
    if getattr(args, "offset", 0) < 0:
        cf.die("--offset must be nonnegative")
    writing = op in {"post", "reply", "read", "ack", "status", "follow", "unfollow"}
    author = _author(cf, args, writing)
    catalog = threads(entries)

    if op == "post":
        case = cf.resolve_case(root, meta, args.case)
        title = " ".join(args.title.split())
        if not title:
            cf.die("thread title must not be empty")
        body = cf._body_arg(args)
        refs = [_resolve(cf, entries, r)["id"] for r in args.ref]
        e = _append(cf, root, entries, case, author, title + "\n\n" + body, "post",
                    refs=refs, title=title, tags=_tags(cf, args.tag),
                    mentions=_mentions(cf, args, body))
        _receipt(e, args)
    elif op in {"show", "read", "reply", "status", "ack"}:
        t, target = _thread(cf, entries, args.entry, catalog)
        if op in {"show", "read"}:
            result = {**_summary(t), "events": t["events"]}
            if op == "read":
                # Mark only this snapshot, never a future reply or a whole log offset.
                seen = read_ids(entries, author, cf.normalize_author)
                targets = [e["id"] for e in t["messages"]
                           if e["id"] not in seen and cf.normalize_author(e["author"]) != author]
                if targets:
                    receipt = _append(cf, root, entries, t["case"], author,
                                      f"Read {len(targets)} message(s) in {t['title']}", "read",
                                      refs=[t["id"], *targets], thread=t["id"], targets=targets)
                    result["read_receipt"] = receipt["id"]
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(f"{t['id']} [{t['status']}] {t['title']} (case {t['case']})")
                print("tags: " + ", ".join(t["tags"]))
                for e in t["events"]:
                    b = payload(e)
                    print(f"\n{e['id']} {e['ts']} {e['author']} [{b['op']}]")
                    if b.get("mentions"):
                        print("attention: " + ", ".join(b["mentions"]))
                    if e.get("refs"):
                        print("refs: " + ", ".join(e["refs"]))
                    print(e["body"])
                if "read_receipt" in result:
                    print("\nread receipt: " + result["read_receipt"])
        elif op == "reply":
            body = cf._body_arg(args)
            refs = [t["id"], target["id"], *[_resolve(cf, entries, r)["id"] for r in args.ref]]
            _receipt(_append(cf, root, entries, t["case"], author, body, "reply",
                             refs=refs, thread=t["id"], reply_to=target["id"],
                             mentions=_mentions(cf, args, body)), args)
        elif op == "status":
            reason = cf._body_arg(args)
            _receipt(_append(cf, root, entries, t["case"], author, reason, "status",
                             refs=[t["id"]], thread=t["id"], status=args.state), args)
        else:
            if payload(target).get("op") not in ACTIVITY:
                cf.die("ack requires a post, reply or status message ID")
            _receipt(_append(cf, root, entries, t["case"], author,
                             f"Acknowledged {target['id']} (receipt, not task completion)", "ack",
                             refs=[t["id"], target["id"]], thread=t["id"], targets=[target["id"]]), args)
    elif op in {"follow", "unfollow"}:
        choices = sum((bool(args.entry), bool(args.tag), bool(args.all)))
        if choices != 1:
            cf.die("choose one thread/message ID, --tag TAG, or --all")
        refs = []
        case = cf.resolve_case(root, meta, None)
        if args.entry:
            t, _ = _thread(cf, entries, args.entry, catalog)
            scope, refs, case = "thread:" + t["id"], [t["id"]], t["case"]
        elif args.tag:
            scope = "tag:" + _tags(cf, [args.tag])[0]
        else:
            scope = "all"
        _receipt(_append(cf, root, entries, case, author, f"{op} {scope}", op,
                         refs=refs, scope=scope), args)
    elif op == "search":
        total, rows = search(cf, root, catalog, args)
        _print_rows(rows, total, args)
    elif op in {"unread", "poll"}:
        peer = cf.normalize_author(args.for_author or author)
        all_rows = unread(entries, peer, cf.normalize_author, followed=args.following)
        rows = []
        for e in all_rows:
            t = catalog[payload(e).get("thread", e["id"])]
            if _selected(t, args, cf) and (not args.by or cf.normalize_author(e["author"]) == cf.normalize_author(args.by)):
                rows.append({"id": e["id"], "thread": t["id"], "case": e["case"],
                             "author": e["author"], "title": t["title"], "body": e["body"],
                             "mentioned": peer in payload(e).get("mentions", [])})
        if op == "poll" and not rows and not args.json:
            return
        _print_rows(rows[args.offset:args.offset + args.limit], len(rows), args)
    elif op == "list":
        rows = [_summary(t) for t in sorted(catalog.values(), key=lambda t: t["position"], reverse=True)
                if _selected(t, args, cf) and (not args.by or cf.normalize_author(t["author"]) == cf.normalize_author(args.by))
                and (not args.following or following(entries, t, author, cf.normalize_author))]
        _print_rows(rows[args.offset:args.offset + args.limit], len(rows), args)


def _print_rows(rows, total, args):
    if args.json:
        print(json.dumps({"total": total, "offset": args.offset, "limit": args.limit,
                          "items": rows}, ensure_ascii=False))
        return
    print(f"{total} result(s); showing {len(rows)} from offset {args.offset}")
    for row in rows:
        suffix = f" [{row['status']}]" if "status" in row else ""
        print(f"{row['id']} thread={row.get('thread', row['id'])}{suffix} "
              f"{row['author']} case={row['case']}: {row['title']}")
        if "snippet" in row or "body" in row:
            print("  " + " ".join(row.get("snippet", row.get("body", "")).split())[:240])
    if args.offset + len(rows) < total:
        print(f"more: --offset {args.offset + len(rows)}; board read <thread-id> for full discussion")


def register(subparsers, cf):
    board = subparsers.add_parser("board", help="open searchable messageboard across all project cases")
    actions = board.add_subparsers(dest="board_command")
    board.set_defaults(fn=lambda a: run(cf, a), board_command="list", author=None,
                       json=False, limit=30, offset=0, case=None, status=None,
                       tag=[], by=None, following=False)

    def command(name, help_text):
        p = actions.add_parser(name, help=help_text)
        p.add_argument("-a", "--author", help="your stable author ID")
        p.add_argument("--json", action="store_true")
        p.set_defaults(fn=lambda a: run(cf, a))
        return p

    def body(p):
        p.add_argument("body", nargs="?")
        p.add_argument("--body-stdin", action="store_true")

    def filters(p, default=30):
        p.add_argument("--case", help="exact case ID; default all cases")
        p.add_argument("--tag", action="append", default=[], help="exact tag; repeat for intersection")
        p.add_argument("--by", help="message author (thread starter for list)")
        p.add_argument("--status", choices=STATES)
        p.add_argument("--limit", type=int, default=default)
        p.add_argument("--offset", type=int, default=0)

    p = command("post", "start a public thread; recipients are attention hints")
    p.add_argument("title")
    body(p)
    p.add_argument("--case")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--to", action="append", default=[])
    p.add_argument("--ref", action="append", default=[])
    p = command("reply", "reply to any thread/message, with evidence references")
    p.add_argument("entry")
    body(p)
    p.add_argument("--to", action="append", default=[])
    p.add_argument("--ref", action="append", default=[])
    for name in ("show", "read", "ack"):
        p = command(name, {"show": "display full thread without marking read",
                           "read": "display full thread and record exact seen messages",
                           "ack": "acknowledge one message; does not complete a task"}[name])
        p.add_argument("entry")
    p = command("status", "append discussion status; leaves epistemic decisions untouched")
    p.add_argument("entry")
    p.add_argument("state", choices=STATES)
    body(p)
    for name in ("follow", "unfollow"):
        p = command(name, "select attention for a thread, tag, or whole board")
        p.add_argument("entry", nargs="?")
        p.add_argument("--tag")
        p.add_argument("--all", action="store_true")
    p = command("list", "browse every thread, including resolved threads")
    filters(p)
    p.add_argument("--following", action="store_true")
    p = command("search", "FTS5 over full posts/replies, titles, tags, authors and refs")
    p.add_argument("query", help='FTS5 query, e.g. \'"cold load" AND gas\' or prefetch*')
    filters(p)
    for name in ("unread", "poll"):
        p = command(name, "unread public messages; poll is quiet when empty")
        filters(p, default=5 if name == "poll" else 30)
        p.add_argument("--for", dest="for_author")
        p.add_argument("--following", action="store_true", help="followed topics/threads and explicit mentions only")
