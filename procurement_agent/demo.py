"""Offline demo and edge-case test harness for the procurement agent.

Calls tools directly — no LLM, no API credentials needed.

Run:
    ``python demo.py``          → offline demo
    ``python demo.py --test``   → edge-case tests
    ``python agent.py --demo``  → same demo (dispatches here)
    ``python agent.py --test``  → same tests (dispatches here)
"""

import json
import sys
from collections import Counter
from typing import Any

try:
    from .memory import clear_audit_log, read_audit_log, write_json, MEMORY_PATH
    from .tools import (
        confirm_order, fetch_vendors, filter_vendors,
        place_order, retrieve_site_rules, store_site_rules,
    )
except ImportError:
    from memory import clear_audit_log, read_audit_log, write_json, MEMORY_PATH
    from tools import (
        confirm_order, fetch_vendors, filter_vendors,
        place_order, retrieve_site_rules, store_site_rules,
    )


# ---------------------------------------------------------------------------
# Audit log summary printer
# ---------------------------------------------------------------------------

def _print_audit_summary() -> None:
    """Read ``audit_log.jsonl`` and print a formatted summary.

    Sections:
    1. Full chronological log (every decision)
    2. Vendors considered — why each was rejected
    3. Counters by event type
    4. Final outcome
    """
    entries: list[dict[str, Any]] = read_audit_log()
    if not entries:
        print("  (audit log is empty)")
        return

    # --- 1. Chronological log ---
    print("-" * 58)
    for e in entries:
        ts: str = e.get("timestamp", "")[:19]
        etype: str = e.get("event_type", "")
        site: str = e.get("site_name", "")
        d: dict[str, Any] = e.get("details", {})

        if etype == "rules_stored":
            body = (
                f"approval_limit=₹{d.get('approval_limit', 0):,}, "
                f"blacklist={d.get('vendor_blacklist', [])}"
            )
        elif etype == "vendor_rejected":
            body = f"{d.get('vendor')}: {d.get('reason')} (₹{d.get('price', 0):,})"
        elif etype == "vendor_selected":
            body = (
                f"{d.get('vendor')} at ₹{d.get('price', 0):,} — "
                f"{d.get('quantity')} bags {d.get('material')}"
            )
        elif etype == "approval_requested":
            body = (
                f"{d.get('vendor')}: ₹{d.get('price', 0):,} exceeds "
                f"limit ₹{d.get('approval_limit', 0):,} "
                f"(overage ₹{d.get('overage', 0):,} / {d.get('overage_pct', 0)}%)"
            )
        elif etype == "order_placed":
            approval_mode = "human-approved" if d.get("approval") == "human" else "auto-approved"
            body = (
                f"{d.get('vendor')}: {d.get('quantity')} bags "
                f"{d.get('material')} at ₹{d.get('price', 0):,} ({approval_mode})"
            )
        else:
            body = json.dumps(d) if d else ""

        print(f"  [{ts}]  {etype:<22} | {site}")
        print(f"    {body}")

    # --- 2. Vendors considered ---
    rejected = [e for e in entries if e["event_type"] == "vendor_rejected"]
    if rejected:
        print()
        print("  Vendors rejected:")
        for r in rejected:
            d = r["details"]
            print(f"    - {d['vendor']:20s}  ₹{d['price']:>7,}  {d['reason']}")

    # --- 3. Counters ---
    counts: Counter = Counter(e["event_type"] for e in entries)
    print()
    print("  Decisions summary:")
    for etype in [
        "rules_stored", "vendor_rejected", "vendor_selected",
        "approval_requested", "order_placed",
    ]:
        if counts[etype]:
            print(f"    {etype:<22} {counts[etype]}")
    print(f"    {'total':<22} {len(entries)}")

    # --- 4. Final outcome ---
    placed = [e for e in entries if e["event_type"] == "order_placed"]
    if placed:
        last = placed[-1]["details"]
        mode = "human-approved" if last.get("approval") == "human" else "auto-approved"
        print()
        print(
            f"  Final outcome: Order placed with {last['vendor']} — "
            f"{last['quantity']} bags {last['material']} "
            f"at ₹{last['price']:,} ({mode})"
        )
    else:
        approvals = [e for e in entries if e["event_type"] == "approval_requested"]
        if approvals:
            print()
            print("  Final outcome: Awaiting human approval")
        else:
            print()
            print("  Final outcome: No order placed")


# ---------------------------------------------------------------------------
# Offline demo
# ---------------------------------------------------------------------------

def demo() -> None:
    """Run the full procurement flow offline (no LLM), then print the audit log.

    Scenario — **Delhi-Site-7**:
      * Approval limit: ₹38,000
      * Blacklist: BadRock Cements
      * Order: 500 bags of cement

    The demo reads vendors dynamically from ``mock_vendors.json`` so the
    exact flow depends on the current catalog contents.  The tool chain is:
      1. ``store_site_rules`` — persist rules
      2. ``fetch_vendors("cement")`` — load vendor catalog from file
      3. ``filter_vendors`` — apply blacklist + budget gate
      4. ``place_order`` — auto-approve or request human approval
      5. ``confirm_order`` — finalise if over-budget was approved
    """
    site = "Delhi-Site-7"
    limit = 38_000
    blacklist = ["BadRock Cements"]
    material = "cement"
    qty = 500

    # Reset state
    clear_audit_log()
    write_json(MEMORY_PATH, {})

    print("=" * 60)
    print("  DEMO — Intelligent Procurement Agent (offline)")
    print("=" * 60)

    # Turn 1 — store rules
    print(f"\n[Turn 1] Store rules for {site}")
    print(f"  Approval limit : ₹{limit:,}")
    print(f"  Blacklist      : {blacklist}")
    result = store_site_rules(site, limit, blacklist)
    print(f"  → {result}")

    # Turn 2a — retrieve rules & fetch vendors
    print(f"\n[Turn 2] Order {qty} bags of {material} for {site}")
    rules = retrieve_site_rules(site)
    if "error" in rules:
        print(f"  ERROR: {rules['error']}")
        return
    print(f"  Rules loaded: {rules}")

    vendors = fetch_vendors(material)
    print(f"  Vendors found: {len(vendors)}")
    for v in vendors:
        print(f"    {v['name']:20s}  ₹{v['price_per_100_bags_inr']:>7,}  "
              f"{v['delivery_days']}d  stock={'yes' if v['in_stock'] else 'no'}")

    # Turn 2b — filter
    filtered = filter_vendors(
        vendors, rules["vendor_blacklist"], rules["approval_limit"], site,
    )
    print(f"\n  Filter results:")
    print(f"    Eligible   : {[v['name'] for v in filtered['eligible']] or '(none)'}")
    print(f"    Rejected   : {[r['vendor'] for r in filtered['rejected']] or '(none)'}")
    print(f"    Over budget: {[o['vendor'] for o in filtered['over_budget']] or '(none)'}")

    # Turn 2c — select cheapest available vendor
    if filtered["eligible"]:
        chosen = filtered["eligible"][0]
        vendor_name = chosen["name"]
        price = chosen["price_per_100_bags_inr"]
    elif filtered["over_budget"]:
        cheapest_ob = min(filtered["over_budget"], key=lambda x: x["price"])
        vendor_name = cheapest_ob["vendor"]
        price = cheapest_ob["price"]
    else:
        print("\n  All vendors blacklisted — cannot place order.")
        print("\n" + "=" * 60)
        print("  AUDIT LOG SUMMARY")
        print("=" * 60)
        _print_audit_summary()
        return

    print(f"\n  Selected: {vendor_name} at ₹{price:,}")

    # Turn 2d — place order (may trigger approval)
    order_result = place_order(vendor_name, price, qty, material, site, limit)
    print(f"\n  place_order response:")
    for line in order_result.splitlines():
        print(f"    {line}")

    # Turn 3 — if approval required, simulate human "yes"
    if order_result.startswith("APPROVAL_REQUIRED"):
        print(f"\n[Turn 3] Human approves the over-budget order")
        confirm_result = confirm_order(vendor_name, price, qty, material, site)
        print(f"  → {confirm_result}")

    # --- Audit log summary ---
    print("\n" + "=" * 60)
    print("  AUDIT LOG SUMMARY")
    print("=" * 60)
    _print_audit_summary()
    print("=" * 60)


# ---------------------------------------------------------------------------
# Edge-case test harness
# ---------------------------------------------------------------------------

def test_edge_cases() -> None:
    """Exercise four failure modes and print whether each is handled gracefully.

    Tests:
      1. Ordering for a non-existent site  (retrieve_site_rules error dict)
      2. Ordering a material with no vendors (fetch_vendors empty + warning log)
      3. Ordering when ALL vendors are blacklisted (filter_vendors message)
      4. Ordering when ALL vendors are over budget (filter_vendors message)
    """
    SEP = "-" * 58
    passed = 0
    failed = 0

    def _check(label: str, ok: bool, detail: str) -> None:
        nonlocal passed, failed
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}]  {label}")
        print(f"         {detail}")

    # Reset state
    clear_audit_log()
    write_json(MEMORY_PATH, {})

    print("=" * 60)
    print("  EDGE-CASE TESTS")
    print("=" * 60)

    # Test 1 — Non-existent site
    print(f"\n{SEP}")
    print("  Test 1: Retrieve rules for a site that was never created")
    print(SEP)
    result = retrieve_site_rules("Ghost-Site-99")
    has_error = isinstance(result, dict) and "error" in result
    _check("retrieve_site_rules returns error dict", has_error, f"Got: {result}")

    # Test 2 — Material with no vendors
    print(f"\n{SEP}")
    print("  Test 2: Fetch vendors for a material not in catalog")
    print(SEP)
    vendors = fetch_vendors("glass")
    is_empty = isinstance(vendors, list) and len(vendors) == 0
    _check("fetch_vendors returns empty list", is_empty, f"Got {len(vendors)} vendor(s) for 'glass'")
    log = read_audit_log()
    warning_logged = any(
        e["event_type"] == "vendor_rejected"
        and "glass" in e.get("details", {}).get("reason", "")
        for e in log
    )
    _check("Warning logged to audit_log.jsonl", warning_logged, "Audit entry: material 'glass' not found")

    # Test 3 — All vendors blacklisted
    print(f"\n{SEP}")
    print("  Test 3: Filter vendors when every vendor is blacklisted")
    print(SEP)
    cement_vendors = fetch_vendors("cement")
    all_names = [v["name"] for v in cement_vendors]
    filtered = filter_vendors(cement_vendors, all_names, 100_000, "Test-Site-BL")
    all_rejected = (
        len(filtered["eligible"]) == 0
        and len(filtered["over_budget"]) == 0
        and len(filtered["rejected"]) == len(cement_vendors)
    )
    _check(
        "All vendors end up in 'rejected'", all_rejected,
        f"eligible={len(filtered['eligible'])}, rejected={len(filtered['rejected'])}, over_budget={len(filtered['over_budget'])}",
    )
    has_msg = "message" in filtered and "blacklisted" in filtered["message"].lower()
    _check("Response contains 'message' about blacklist", has_msg, f"message: {filtered.get('message', '(none)')}")

    # Test 4 — All vendors over budget
    print(f"\n{SEP}")
    print("  Test 4: Filter vendors when budget is below every vendor's price")
    print(SEP)
    filtered_ob = filter_vendors(cement_vendors, [], 1_000, "Test-Site-OB")
    none_eligible = len(filtered_ob["eligible"]) == 0 and len(filtered_ob["over_budget"]) > 0
    _check(
        "Zero eligible, all in 'over_budget'", none_eligible,
        f"eligible={len(filtered_ob['eligible'])}, over_budget={len(filtered_ob['over_budget'])}",
    )
    has_budget_msg = "message" in filtered_ob and "budget" in filtered_ob["message"].lower()
    _check(
        "Response contains 'message' about budget with cheapest option", has_budget_msg,
        f"message: {filtered_ob.get('message', '(none)')}",
    )

    # Summary
    print(f"\n{'=' * 60}")
    total = passed + failed
    print(f"  Results: {passed}/{total} passed, {failed}/{total} failed")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    if "--test" in sys.argv:
        test_edge_cases()
    else:
        demo()
