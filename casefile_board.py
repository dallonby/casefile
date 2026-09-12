"""Open, append-only discussion threads for casefile (stdlib only).

All authoritative state is ordinary note entries with a versioned ``board``
payload. SQLite is a disposable full-text index, never the message store.
Recipients and follows select attention; they never restrict visibility.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

VERSION = 1
ACTIVITY = {"post", "reply", "status"}
CONTROLS = {"read", "ack", "follow", "unfollow"}
STATES = ("open", "in-progress", "blocked", "resolved")

# Board reads are commonly injected into another model's context.  Keep the
# ordinary view deliberately small and make expansion an explicit choice.
DEFAULT_LIMIT = 20
DEFAULT_POLL_LIMIT = 5
BODY_PREVIEW_CHARS = 600
SEARCH_SNIPPET_CHARS = 320
TITLE_PREVIEW_CHARS = 240
REF_PREVIEW_COUNT = 24
TRUNCATION_MARKER = " … [truncated; use --full]"

_NATURAL_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
# These words are useful for prose but carry little lexical signal in a
# board-wide search.  This is intentionally only a lexical convenience;
# there is no embedding or LLM semantic retrieval here.
_NATURAL_STOPWORDS = frozenset({
    "a", "about", "after", "again", "all", "also", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being",
    "but", "by", "can", "could", "did", "do", "does", "for", "from",
    "had", "has", "have", "how", "i", "if", "in", "into", "is", "it",
    "its", "just", "may", "me", "more", "most", "my", "no", "nor", "of",
    "on", "or", "our", "please", "same", "should", "some", "such", "tell",
    "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "those", "to", "under", "up", "us", "very", "was", "we", "were",
    "what", "when", "where", "which", "who", "why", "will", "with", "would",
    "you", "your",
})


def payload(entry):
    value = entry.get("board")
    return value if (entry.get("type") == "note" and isinstance(value, dict)
                     and value.get("version") == VERSION) else {}


def is_control(entry):
    return payload(entry).get("op") in CONTROLS


def _bounded_body(body, *, full=False, limit=BODY_PREVIEW_CHARS):
    """Return a context-safe body preview and whether it was shortened."""
    body = str(body or "")
    if full or len(body) <= limit:
        return body, False
    keep = max(0, limit - len(TRUNCATION_MARKER))
    return body[:keep].rstrip() + TRUNCATION_MARKER, True


def _bounded_title(title, *, full=False):
    return _bounded_body(title, full=full, limit=TITLE_PREVIEW_CHARS)


def _bounded_refs(refs, *, full=False):
    refs = list(refs or [])
    if full or len(refs) <= REF_PREVIEW_COUNT:
        return refs, False
    return refs[:REF_PREVIEW_COUNT], True


def _message_view(entry, args):
    """Copy one activity entry with a bounded body for default views."""
    row = dict(entry)
    row["body"], row["body_truncated"] = _bounded_body(
        entry.get("body", ""), full=getattr(args, "full", False))
    row["refs"], row["refs_truncated"] = _bounded_refs(
        entry.get("refs", []), full=getattr(args, "full", False))
    row["refs_count"] = len(entry.get("refs", []))
    row["title"], row["title_truncated"] = _bounded_title(
        payload(entry).get("title", ""), full=getattr(args, "full", False))
    board_data = row.get("board")
    if isinstance(board_data, dict) and not getattr(args, "full", False):
        board_data = dict(board_data)
        if "title" in board_data:
            board_data["title"], board_data["title_truncated"] = _bounded_title(
                board_data["title"])
        for key in ("tags", "mentions"):
            values = list(board_data.get(key, []) or [])
            if len(values) > REF_PREVIEW_COUNT:
                board_data[key] = values[:REF_PREVIEW_COUNT]
                board_data[key + "_truncated"] = True
                board_data[key + "_count"] = len(values)
        row["board"] = board_data
    return row


def _view_is_complete(row):
    if row.get("body_truncated") or row.get("title_truncated") \
            or row.get("refs_truncated"):
        return False
    board_data = row.get("board")
    return not isinstance(board_data, dict) or not any(
        board_data.get(key + "_truncated") for key in ("tags", "mentions"))


def _natural_terms(query):
    tokens = [token.casefold() for token in _NATURAL_TOKEN_RE.findall(query)]
    tokens = list(dict.fromkeys(tokens))
    useful = [token for token in tokens if token not in _NATURAL_STOPWORDS]
    return useful or tokens


def _natural_variants(term):
    variants = [term]
    # Prefix matching handles many forms, and this small plural companion
    # covers the useful reverse direction ("proofs" -> "proof") without
    # pretending to be a stemmer.
    if len(term) > 4 and term.endswith("ies"):
        variants.append(term[:-3] + "y")
    elif len(term) > 4 and term.endswith("s") \
            and not term.endswith(("ss", "us", "is")):
        variants.append(term[:-1])
    return list(dict.fromkeys(variants))


def _natural_fts_query(query):
    """Build safe OR-term FTS for ordinary prose.

    FTS5 is still doing lexical matching. Prefixes make common plurals and
    inflections a little friendlier, but this function does not infer meaning.
    """
    terms = _natural_terms(query)
    if not terms:
        return ""
    rendered = []
    for term in terms:
        for variant in _natural_variants(term):
            # _NATURAL_TOKEN_RE only yields word characters, so these are safe
            # FTS tokens. Exact short terms avoid turning one-letter words
            # into broad prefix scans; longer words tolerate simple inflections.
            rendered.append(variant + "*" if len(variant) >= 4 else variant)
    return " OR ".join(rendered)


def _looks_like_fts(query, args):
    if getattr(args, "fts", False):
        return True
    if getattr(args, "natural", False):
        return False
    # Existing FTS callers use phrases, prefixes, Boolean operators or column
    # selectors. Ordinary punctuation/questions are routed through lexical
    # term extraction instead of being handed to the FTS parser.
    return bool(re.search(
        r'"|\*|\b(?:AND|OR|NOT)\b|\b(?:id|thread|case_id|author|title|tags?|body|refs?|status):',
        query,
    ))


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


def preview_ids(entries, author, normalize):
    """IDs for which this author recorded only a clipped preview.

    Preview receipts are retained for auditability but deliberately do not
    advance the unread cursor.  A later ``read --full`` can promote the same
    message into the exact seen-ID set.
    """
    author = normalize(author)
    return {target for e in entries
            if normalize(e["author"]) == author
            and payload(e).get("op") == "read"
            for target in payload(e).get("previews", [])}


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


def _summary(t, args=None):
    full = bool(getattr(args, "full", False))
    title, title_truncated = _bounded_title(t["title"], full=full)
    tags = list(t.get("tags", []))
    if not full and len(tags) > REF_PREVIEW_COUNT:
        tags = tags[:REF_PREVIEW_COUNT]
    row = {k: t[k] for k in ("id", "case", "author", "status", "updated")}
    row.update({"title": title, "tags": tags,
                "title_truncated": title_truncated,
                "tags_count": len(t.get("tags", [])),
                "tags_truncated": len(tags) < len(t.get("tags", []))})
    return row | {
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
    fts_mode = _looks_like_fts(args.query, args)
    query = args.query if fts_mode else _natural_fts_query(args.query)
    if not query:
        cf.die("search query must contain at least one searchable word")
    args._search_mode = "fts" if fts_mode else "lexical"
    db = None
    try:
        db = _index(cf, root, catalog)
        where, params = ["board_search MATCH ?"], [query]
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
        result = []
        for row in rows:
            item = dict(zip(("id", "thread", "case", "author", "title",
                             "body", "snippet"), row))
            item["title"], item["title_truncated"] = _bounded_title(
                item["title"], full=getattr(args, "full", False))
            item["body"], item["body_truncated"] = _bounded_body(
                item["body"], full=getattr(args, "full", False))
            item["snippet"], _ = _bounded_body(item["snippet"], limit=SEARCH_SNIPPET_CHARS)
            result.append(item)
        return total, result
    except sqlite3.Error as exc:
        cf.die(f"board FTS5 search failed: {exc}. Use --natural for ordinary prose, "
               "or --fts with quoted phrases, AND/OR/NOT, and prefix*.")
    finally:
        if db is not None:
            db.close()


def _tail_entries(cf, root):
    if cf.persistence_mode() == "postgres":
        # Ordinary CLI reads cache reconciliation for one invocation. A follower
        # must refresh that snapshot to see messages from other machines.
        key = str(root.resolve())
        cf._PG_RECONCILED.discard(key)
        cf._PG_LOCAL_CACHE.pop(key, None)
        return cf.read_entries(root)
    path = root / cf.DIR / cf.LOG
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    entries = []
    for n, line in enumerate(data.splitlines(keepends=True), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except (ValueError, UnicodeError):
            if not line.endswith(b"\n"):
                break  # a concurrent append has not finished its last record
            cf.die(f"corrupt log line {n} in {path}")
    return entries


def tail(cf, args):
    """Plain chronological messages, optionally followed; never write receipts."""
    if args.lines < 0:
        cf.die("--lines must be nonnegative")
    root = cf.find_root()
    if root is None:
        cf.die("no .casefile found here or in any parent")
    seen, first, thread_id = set(), True, None
    try:
        while True:
            entries = _tail_entries(cf, root)
            catalog = threads(entries)
            if first and args.thread:
                thread_id = _thread(cf, entries, args.thread, catalog)[0]["id"]
            rows = []
            for e in entries:
                b = payload(e)
                t = catalog.get(b.get("thread", e["id"]))
                if (b.get("op") not in ACTIVITY or not t or e["id"] in seen
                        or (thread_id and t["id"] != thread_id)
                        or not _selected(t, args, cf)
                        or (args.by and cf.normalize_author(e["author"]) != cf.normalize_author(args.by))):
                    continue
                body, body_truncated = _bounded_body(
                    e["body"], full=getattr(args, "full", False))
                title, title_truncated = _bounded_title(
                    t["title"], full=getattr(args, "full", False))
                refs, refs_truncated = _bounded_refs(
                    e.get("refs", []), full=getattr(args, "full", False))
                rows.append({"id": e["id"], "thread": t["id"],
                             "case": t["case"], "ts": e["ts"], "author": e["author"],
                             "op": b["op"], "body": body,
                             "body_truncated": body_truncated,
                             "refs": refs, "refs_count": len(e.get("refs", [])),
                             "refs_truncated": refs_truncated,
                             "title": title, "title_truncated": title_truncated})
            if first:
                rows = rows[-args.lines:] if args.lines else []
            for row in rows:
                if args.json:
                    print(json.dumps(row, ensure_ascii=False), flush=True)
                else:
                    print(f"{row['ts']}  {row['author']} [{row['op']}]  "
                          f"thread={row['thread']} message={row['id']}\n"
                          f"{row['title']}\n{row['body']}\n", flush=True)
            seen.update(e["id"] for e in entries)
            first = False
            if not args.follow:
                return
            time.sleep(1)
    except KeyboardInterrupt:
        return
    except BrokenPipeError:
        # Avoid a second exception while Python flushes stdout at shutdown.
        sys.stdout = open(os.devnull, "w")


def run(cf, args):
    if args.board_command == "tail":
        return tail(cf, args)
    root, entries, meta = cf.require_root()
    op = args.board_command or "list"
    if getattr(args, "limit", 1) < 1 or getattr(args, "limit", 1) > 500:
        cf.die("--limit must be between 1 and 500; use --offset for further pages")
    offset = getattr(args, "offset", 0)
    if offset is not None and offset < 0:
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
            messages = t["messages"]
            if getattr(args, "message", False):
                if payload(target).get("op") not in ACTIVITY:
                    cf.die("--message requires a post, reply or status message ID")
                selected = [target]
                page_offset = next((i for i, e in enumerate(messages)
                                    if e["id"] == target["id"]), 0)
            elif getattr(args, "all_messages", False):
                selected = messages
                page_offset = 0
            else:
                page_offset = args.offset if args.offset is not None else 0
                # When a caller supplies a reply/status ID, keep that
                # requested message on the bounded page even if it is deep in
                # a long thread. Explicit --offset (including --offset 0)
                # always wins for deliberate archive paging; --message remains
                # the exact one-message view.
                target_index = next((i for i, e in enumerate(messages)
                                     if e["id"] == target["id"]), None)
                if args.offset is None and target_index is not None \
                        and target_index >= args.limit:
                    page_offset = target_index - args.limit + 1
                selected = messages[page_offset:page_offset + args.limit]
            viewed = [_message_view(e, args) for e in selected]
            result = {
                **_summary(t, args),
                "total_messages": len(messages),
                "shown_messages": len(viewed),
                "total_events": len(t["events"]),
                "shown_events": len(viewed),
                "offset": page_offset,
                "limit": args.limit,
                "has_more": not getattr(args, "message", False)
                and page_offset + len(viewed) < len(messages),
                "next_offset": (page_offset + len(viewed)
                                 if not getattr(args, "message", False)
                                 and page_offset + len(viewed) < len(messages) else None),
                "expanded": bool(getattr(args, "all_messages", False)),
                "message_view": bool(getattr(args, "message", False)),
                "requested_message": target["id"] if target["id"] != t["id"] else None,
                "events": viewed,
                "read_message_ids": [],
                "preview_message_ids": [],
            }
            if op == "read":
                result["read_complete"] = False
                # Mark only the selected snapshot, never a future reply or a
                # whole log offset. A bounded page must leave later messages
                # unread, and --message marks exactly its target.
                seen = read_ids(entries, author, cf.normalize_author)
                previews_seen = preview_ids(entries, author, cf.normalize_author)
                exposed = [_message_view(e, args) for e in selected
                           if e["id"] not in seen
                           and cf.normalize_author(e["author"]) != author]
                targets = [e["id"] for e in exposed
                           if _view_is_complete(e)]
                previews = [e["id"] for e in exposed
                            if not _view_is_complete(e) and e["id"] not in previews_seen]
                if targets or previews:
                    receipt_title, _ = _bounded_title(t["title"])
                    receipt = _append(cf, root, entries, t["case"], author,
                                      f"Viewed {len(targets)} complete and {len(previews)} "
                                      f"preview message(s) in {receipt_title}", "read",
                                      refs=[t["id"], *targets, *previews], thread=t["id"],
                                      targets=targets, previews=previews,
                                      complete=not previews,
                                      preview_chars=BODY_PREVIEW_CHARS)
                    result["read_receipt"] = receipt["id"]
                    result["read_message_ids"] = targets
                    result["preview_message_ids"] = previews
                    result["read_complete"] = bool(targets) and not previews
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                display_title, _ = _bounded_title(
                    t["title"], full=getattr(args, "full", False))
                display_tags = list(t["tags"])
                if not getattr(args, "full", False):
                    display_tags = display_tags[:REF_PREVIEW_COUNT]
                print(f"{t['id']} [{t['status']}] {display_title} (case {t['case']})")
                print("tags: " + ", ".join(display_tags))
                print(f"messages: showing {len(viewed)} of {len(messages)} "
                      f"from offset {page_offset}")
                for e in viewed:
                    b = payload(e)
                    print(f"\n{e['id']} {e['ts']} {e['author']} [{b['op']}]")
                    if b.get("mentions"):
                        print("attention: " + ", ".join(b["mentions"]))
                    if e.get("refs"):
                        print("refs: " + ", ".join(e["refs"]))
                    print(e["body"])
                if "read_receipt" in result:
                    detail = "complete bodies" if result["read_complete"] else "preview bodies"
                    print("\nread receipt: " + result["read_receipt"] + f" ({detail})")
                if result["has_more"]:
                    print(f"more: board {op} {t['id']} --offset {result['next_offset']} "
                          "(or --all for every message; --full for complete bodies)")
                elif not viewed and messages:
                    print("no messages on this page; use --offset within "
                          f"0..{len(messages) - 1} or --message MESSAGE-ID")
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
        _print_rows(rows, total, args, mode=getattr(args, "_search_mode", None))
    elif op in {"unread", "poll"}:
        peer = cf.normalize_author(args.for_author or author)
        all_rows = unread(entries, peer, cf.normalize_author, followed=args.following)
        rows = []
        for e in all_rows:
            t = catalog[payload(e).get("thread", e["id"])]
            if _selected(t, args, cf) and (not args.by or cf.normalize_author(e["author"]) == cf.normalize_author(args.by)):
                body, body_truncated = _bounded_body(
                    e["body"], full=getattr(args, "full", False))
                title, title_truncated = _bounded_title(
                    t["title"], full=getattr(args, "full", False))
                rows.append({"id": e["id"], "thread": t["id"], "case": e["case"],
                             "author": e["author"], "title": title, "body": body,
                             "body_truncated": body_truncated,
                             "title_truncated": title_truncated,
                             "mentioned": peer in payload(e).get("mentions", [])})
        if op == "poll" and not rows and not args.json:
            return
        _print_rows(rows[args.offset:args.offset + args.limit], len(rows), args)
    elif op == "list":
        rows = [_summary(t) for t in sorted(catalog.values(), key=lambda t: t["position"], reverse=True)
                if _selected(t, args, cf) and (not args.by or cf.normalize_author(t["author"]) == cf.normalize_author(args.by))
                and (not args.following or following(entries, t, author, cf.normalize_author))]
        _print_rows(rows[args.offset:args.offset + args.limit], len(rows), args)


def _print_rows(rows, total, args, *, mode=None):
    offset = getattr(args, "offset", 0)
    limit = getattr(args, "limit", len(rows))
    has_more = offset + len(rows) < total
    next_offset = offset + len(rows) if has_more else None
    if args.json:
        result = {"total": total, "offset": offset, "limit": limit,
                  "shown": len(rows), "has_more": has_more,
                  "next_offset": next_offset, "items": rows}
        if mode:
            result["mode"] = mode
        print(json.dumps(result, ensure_ascii=False))
        return
    if mode == "lexical":
        print("lexical search (natural-language terms; no semantic embeddings)")
    elif mode == "fts":
        print("FTS search (explicit query syntax)")
    print(f"{total} result(s); showing {len(rows)} from offset {offset}")
    for row in rows:
        suffix = f" [{row['status']}]" if "status" in row else ""
        print(f"{row['id']} thread={row.get('thread', row['id'])}{suffix} "
              f"{row['author']} case={row['case']}: {row['title']}")
        if "snippet" in row or "body" in row:
            text = row.get("body", "") if getattr(args, "full", False) \
                else row.get("snippet", row.get("body", ""))
            if not getattr(args, "full", False):
                if row.get("body_truncated") and TRUNCATION_MARKER in text:
                    text = text.split(TRUNCATION_MARKER, 1)[0]
                    text, _ = _bounded_body(text, limit=240)
                else:
                    text = text[:240]
            print("  " + " ".join(text.split()))
    if has_more:
        print(f"more: --offset {next_offset} (pages are bounded; add --full for complete bodies)")


def register(subparsers, cf):
    board = subparsers.add_parser("board", help="open searchable messageboard across all project cases")
    actions = board.add_subparsers(dest="board_command")
    board.set_defaults(fn=lambda a: run(cf, a), board_command="list", author=None,
                       json=False, limit=DEFAULT_LIMIT, offset=0, case=None, status=None,
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

    def filters(p, default=DEFAULT_LIMIT):
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
    for name in ("show", "read"):
        p = command(name, {"show": "display a bounded thread page without marking read",
                           "read": "display a bounded page and record exact exposed messages"}[name])
        p.add_argument("entry")
        p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                       help=f"messages per page (default {DEFAULT_LIMIT})")
        p.add_argument("--offset", type=int, default=None,
                       help="chronological message offset; omitted reply views are anchored to the target")
        p.add_argument("--all", "--expand", dest="all_messages", action="store_true",
                       help="expand to every thread message (bodies remain previews)")
        p.add_argument("--full", action="store_true",
                       help="include complete bodies, titles and references")
        p.add_argument("--message", action="store_true",
                       help="show/read only the supplied post, reply or status message")
    p = command("ack", "acknowledge one message; does not complete a task")
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
    p = command("search", "ranked lexical prose search or explicit FTS5 over board messages")
    p.add_argument("query", help="ordinary question/phrase, or explicit FTS5 query")
    filters(p)
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--fts", action="store_true", help="treat query as raw FTS5 syntax")
    modes.add_argument("--natural", action="store_true", help="treat query as ordinary lexical terms")
    p.add_argument("--full", action="store_true",
                   help="include complete matching bodies (default is bounded previews)")
    p = command("tail", "print recent bounded messages; -f follows new arrivals without marking read")
    p.add_argument("-n", "--lines", type=int, default=20, help="initial message count (default 20; 0 for new only)")
    p.add_argument("-f", "--follow", action="store_true", help="poll every second; Ctrl-C stops")
    p.add_argument("--full", action="store_true", help="include complete message bodies and metadata")
    p.add_argument("--thread", help="restrict to a thread or message ID/prefix")
    p.add_argument("--case")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--by", help="message author")
    p.add_argument("--status", choices=STATES)
    for name in ("unread", "poll"):
        p = command(name, "unread public messages; poll is quiet when empty")
        filters(p, default=DEFAULT_POLL_LIMIT if name == "poll" else DEFAULT_LIMIT)
        p.add_argument("--for", dest="for_author")
        p.add_argument("--following", action="store_true", help="followed topics/threads and explicit mentions only")
        p.add_argument("--full", action="store_true",
                       help="include complete message bodies (default is bounded previews)")
