"""Demo storefront: free shipping over $500.

BUG (for casefile continuity demo): free-shipping threshold compares the
tax-inclusive total, so a $480 cart + 8% tax ($518.40) gets free shipping
while a $500 tax-exclusive order is intended. Support keeps seeing
"orders over $500" reports that don't match the marketing copy.
"""

TAX_RATE = 0.08
FREE_SHIP_THRESHOLD = 500.00
FLAT_SHIP = 12.00


def cart_total(items: list[float]) -> float:
    return round(sum(items), 2)


def tax_on(subtotal: float) -> float:
    return round(subtotal * TAX_RATE, 2)


def shipping_for(subtotal: float) -> float:
    # Intent (marketing): free shipping when merchandise subtotal >= $500.
    # Actual: compares tax-inclusive total — wrong for support / marketing.
    taxed = subtotal + tax_on(subtotal)
    if taxed >= FREE_SHIP_THRESHOLD:
        return 0.0
    return FLAT_SHIP


def checkout(items: list[float]) -> dict:
    sub = cart_total(items)
    tax = tax_on(sub)
    ship = shipping_for(sub)
    return {
        "subtotal": sub,
        "tax": tax,
        "shipping": ship,
        "total": round(sub + tax + ship, 2),
    }


if __name__ == "__main__":
    # Support repro: customer cart is $480 merch — they expect $12 ship,
    # but free shipping kicks in because tax pushes them over $500.
    print(checkout([120.0, 180.0, 180.0]))
