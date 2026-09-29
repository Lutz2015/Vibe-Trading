"""Example python_module strategy for Qbit paper trading.

Contract:
    build_targets(quotes, universe, params) -> list[{"symbol": str, "weight": float}]
"""


def _score(quote) -> float:
    prev_close = float(getattr(quote, "prev_close", 0.0) or 0.0)
    price = float(getattr(quote, "price", 0.0) or 0.0)
    if prev_close <= 0 or price <= 0:
        return 0.0
    return (price / prev_close) - 1.0


def build_targets(quotes, universe, params):
    top_n = int(params.get("top_n", 4))
    scored = []
    for symbol in universe:
        quote = quotes.get(symbol)
        if quote is None:
            continue
        scored.append((symbol, _score(quote)))
    if not scored:
        return []
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)
    picked = [symbol for symbol, _ in ranked[:top_n]]
    if not picked:
        return []
    weight = round(1.0 / len(picked), 6)
    return [{"symbol": symbol, "weight": weight} for symbol in picked]
