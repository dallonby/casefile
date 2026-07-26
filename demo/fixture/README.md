# checkout-demo

Tiny storefront used by the casefile continuity demo (free-shipping bug).

The committed `.casefile/log.jsonl` is from a real Codex investigation:
hypothesis `5fbd68af` verified against observation `11188444`, packet to grok.

```bash
python3 test_shipping.py   # shows the bug
python3 shipping.py

# resume as a fresh agent (after casefile is on PATH)
export CASEFILE_AUTHOR=grok
casefile boot
```

If you re-init casefile here, preserve `log.jsonl` — that is the demo ground truth.
