# casefile continuity demo

Frontpage GIF: multi-model handoff where **agents** run casefile (the user
never types it).

## Story

1. **Codex** opens on `demo/fixture` (buggy free-shipping threshold).
2. User: *Support says free shipping is wrong — find root cause, don't fix, use the casefile.*
3. Codex reproduces, files hypothesis + observation, verifies, packets → grok.
4. **Context reset** — close Codex, open Grok (empty chat, same repo).
5. User: *where are we on the free shipping bug?*
6. Grok `casefile boot`s and already knows the verified root cause.

Scene text is condensed from a real `codex exec` + `grok -p` run. Casefile
entry ids in the GIF match the committed `demo/fixture/.casefile/log.jsonl`.

## Play / regenerate

```bash
# rebuild cast from scenes.json (fast user prompts + ≥10s end hold)
python3 demo/build_cast.py

# render frontpage GIF — idle-time-limit must exceed the 10s loop hold
# (agg defaults to 5s and would clamp the pause otherwise)
agg --cols 100 --rows 30 --font-size 14 --idle-time-limit 15 \
  demo/casefile-continuity.cast demo/casefile-continuity.gif

# optional: play the cast
asciinema play demo/casefile-continuity.cast
```

## Re-run the live agents (optional)

```bash
cd demo/fixture
export PATH="$HOME/.local/bin:$PATH"

# codex investigates + files
export CASEFILE_AUTHOR=codex
codex exec --dangerously-bypass-approvals-and-sandbox \
  --dangerously-bypass-hook-trust -C "$PWD" \
  -c 'model_reasoning_effort="low"' \
  "Support reports free shipping is wrong. Investigate only (don't fix).
   Use casefile open/add/verify/checkpoint/packet --to grok. Prefer
   --body-stdin for any body with dollar amounts."

# cold grok resume (no shared chat)
export CASEFILE_AUTHOR=grok
grok -p 'where are we on the free shipping bug? casefile boot first. terse.' \
  --cwd "$PWD" --always-approve --max-turns 6 --no-memory
```

Then refresh `scenes.json` from the transcripts and rebuild the cast.
