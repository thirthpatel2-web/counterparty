from engine.simulator import VENDOR_BY_SLUG, VENDORS, Negotiation, lost_deal_price


def believes_final(n):
    """Naive buyer: pushes hard, but accepts the first 'final' it hears."""
    r = n.opening()
    bid = r.price * 0.7
    while True:
        r = n.counter(bid)
        if r.deal or r.walked:
            return r
        if r.says_final:
            return n.accept()


def never_believes(n):
    """Keeps pushing until the deal closes or the vendor leaves."""
    r = n.opening()
    bid = r.price * 0.7
    while True:
        r = n.counter(bid)
        if r.deal or r.walked:
            return r


def knows_vendor(n):
    """Knows the hidden traits: pushes to the last safe round. The ceiling of what memory can teach."""
    n.opening()
    for _ in range(n.vendor.walks_after_rounds - 1):
        r = n.counter(n.floor)
        if r.price <= n.floor + 1:
            break
    return n.accept()


def average(policy, vendor):
    prices = []
    lost = 0
    for s in range(300):
        r = policy(Negotiation(vendor, month=1 + s % 12, seed=s))
        lost += r.walked
        prices.append(lost_deal_price() if r.walked else r.price)
    return sum(prices) / len(prices), lost


def test_same_seed_is_deterministic():
    a, b = Negotiation(VENDORS[0], 3, 7), Negotiation(VENDORS[0], 3, 7)
    assert a.floor == b.floor and a.counter(70_000) == b.counter(70_000)


def test_price_never_goes_below_floor():
    for v in VENDORS:
        for s in range(100):
            n = Negotiation(v, 3, s)
            n.opening()
            for _ in range(v.walks_after_rounds - 1):
                assert n.counter(1).price >= round(n.floor)


def test_honest_vendor_says_final_truthfully_then_walks():
    n = Negotiation(VENDOR_BY_SLUG["bharat"], 1, 1)
    n.opening()
    n.counter(1)
    second = n.counter(1)
    assert second.says_final and second.price <= n.floor + 1001
    assert n.counter(1).walked


def test_no_single_fixed_strategy_wins_everywhere():
    """The point of the design: whether to believe "final" must depend on which vendor it is."""
    results = {}
    for v in VENDORS:
        naive, naive_lost = average(believes_final, v)
        pusher, pusher_lost = average(never_believes, v)
        informed, informed_lost = average(knows_vendor, v)
        results[v.slug] = (naive, pusher, informed)
        print(f"{v.name}: believes 'final' {naive:,.0f} (lost {naive_lost}) | never believes {pusher:,.0f} "
              f"(lost {pusher_lost}) | knows vendor {informed:,.0f} (lost {informed_lost})")
        assert informed_lost == 0
    assert results["apex"][2] < results["apex"][0] and results["nova"][2] < results["nova"][0]
    assert results["bharat"][1] > results["bharat"][0]  # pushing an honest vendor loses the deal
