# casefile demo cast

Duration target: **15s**. Generated from real CLI output.

## Play

```bash
asciinema play demo/casefile-continuity.cast
# or upload:
asciinema upload demo/casefile-continuity.cast
```

## Storyboard

1. **codex** whoami + boot (case state from log)
2. **codex** packet → grok (handoff without shared chat)
3. **CONTEXT RESET** banner (empty agent chat)
4. **grok** boot + inbox + next (survives reset)

Regenerate: `python3 demo/build_cast.py`
