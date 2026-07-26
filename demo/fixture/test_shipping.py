"""Minimal checks for the demo storefront. One assertion encodes the bug."""

from shipping import checkout


def test_under_threshold_pays_shipping():
    # $400 merch + tax = $432 — always pays shipping
    r = checkout([200.0, 200.0])
    assert r["shipping"] == 12.0


def test_exactly_500_free_shipping_intended():
    # Marketing: free shipping at $500 merchandise subtotal.
    # Current implementation may disagree — this test documents intent.
    r = checkout([250.0, 250.0])
    assert r["subtotal"] == 500.0
    # Intent: free. If this fails, the bug is still live.
    assert r["shipping"] == 0.0, f"expected free ship at $500 subtotal, got {r}"


def test_480_should_still_pay_shipping_per_marketing():
    # Marketing says free only over $500 *merchandise*. $480 must pay ship.
    r = checkout([120.0, 180.0, 180.0])
    assert r["subtotal"] == 480.0
    assert r["shipping"] == 12.0, (
        f"BUG: $480 merch got shipping={r['shipping']} "
        f"(tax-inclusive compare?). full={r}"
    )


if __name__ == "__main__":
    import traceback
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
            except Exception:
                print(f"ERROR {name}")
                traceback.print_exc()
