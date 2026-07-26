# casefile demo cast

Duration target: **15s**. Generated from real CLI output.

Frontpage embed: `demo/casefile-continuity.gif` (rendered with
[agg](https://github.com/asciinema/agg) from the cast).

## Play

```bash
asciinema play demo/casefile-continuity.cast
# or upload:
asciinema upload demo/casefile-continuity.cast
```

Regenerate GIF after rebuilding the cast:

```bash
python3 demo/build_cast.py
agg --cols 100 --rows 28 --font-size 14 \
  demo/casefile-continuity.cast demo/casefile-continuity.gif
```

## Storyboard

1. **codex** whoami + boot (case state from log)
2. **codex** packet → grok (handoff without shared chat)
3. **CONTEXT RESET** banner (empty agent chat)
4. **grok** boot + inbox + next (survives reset)

Regenerate: `python3 demo/build_cast.py`
