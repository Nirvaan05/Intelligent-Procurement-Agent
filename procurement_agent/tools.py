"""Deterministic tool functions for the intelligent procurement agent.

Pure business logic — no LLM calls, no direct file I/O.  All persistence
is delegated to the ``memory`` module.

Dependencies: ``memory`` module (project-local) + standard library only.
"""

from typing import Any

try:
    from . import memory as _mem  # package import (adk run / adk web)
except ImportError:
    import memory as _mem  # direct execution (python tools.py)


# ---------------------------------------------------------------------------
# 1. store_site_rules
# ---------------------------------------------------------------------------

def store_site_rules(
    site_name: str,
    approval_limit: int,
    vendor_blacklist: list[str],
) -> str:
    """Save procurement rules for a construction site.

    Structure persisted::

        {
          "<site_name>": {
            "approval_limit": <int>,
            "vendor_blacklist": [<str>, ...]
          }
        }

    Args:
        site_name:        Unique name of the construction site (e.g. "Delhi-Site-7").
        approval_limit:   Maximum order value (INR) that can be approved without
                          escalation.
        vendor_blacklist:  List of vendor names that must NOT be used for this site.

    Returns:
        Human-readable confirmation string summarising what was stored.

    Examples:
        >>> store_site_rules("Delhi-Site-7", 38000, ["BadRock Cements"])
        "Rules stored for site 'Delhi-Site-7': approval_limit=₹38,000, vendor_blacklist=[BadRock Cements]."

        >>> store_site_rules("Site-A", 100000, [])
        "Rules stored for site 'Site-A': approval_limit=₹100,000, vendor_blacklist=[(none)]."

        >>> store_site_rules("", 50000, [])
        'Error: site_name must be a non-empty string.'
    """
    site_key = site_name.strip()
    if not site_key:
        return "Error: site_name must be a non-empty string."

    store = _mem.read_json(_mem.MEMORY_PATH)
    store[site_key] = {
        "approval_limit": approval_limit,
        "vendor_blacklist": [v.strip() for v in vendor_blacklist],
    }
    write_err = _mem.write_json(_mem.MEMORY_PATH, store)
    if write_err:
        return f"Error saving rules: {write_err}"

    _mem.log_decision("rules_stored", site_key, {
        "approval_limit": approval_limit,
        "vendor_blacklist": store[site_key]["vendor_blacklist"],
    })

    blacklist_display = ", ".join(vendor_blacklist) if vendor_blacklist else "(none)"
    return (
        f"Rules stored for site '{site_key}': "
        f"approval_limit=₹{approval_limit:,}, "
        f"vendor_blacklist=[{blacklist_display}]."
    )


# ---------------------------------------------------------------------------
# 2. retrieve_site_rules
# ---------------------------------------------------------------------------

def retrieve_site_rules(site_name: str) -> dict[str, Any]:
    """Retrieve stored procurement rules for a given site.

    Args:
        site_name: Name of the construction site whose rules to look up.

    Returns:
        A dict ``{"approval_limit": int, "vendor_blacklist": list[str]}`` if
        the site exists.  Otherwise a dict with an ``"error"`` key containing
        a human-readable message explaining that rules must be set first.

    Examples:
        >>> retrieve_site_rules("Delhi-Site-7")  # after storing rules
        {'approval_limit': 38000, 'vendor_blacklist': ['BadRock Cements']}

        >>> retrieve_site_rules("NonExistent-Site")
        {'error': "No rules found for 'NonExistent-Site'. Please set rules first using store_site_rules."}

        >>> retrieve_site_rules("")
        {'error': 'site_name must be a non-empty string.'}
    """
    site_key = site_name.strip()
    if not site_key:
        return {"error": "site_name must be a non-empty string."}

    store = _mem.read_json(_mem.MEMORY_PATH)
    rules = store.get(site_key)
    if isinstance(rules, dict) and "approval_limit" in rules:
        return rules
    return {
        "error": (
            f"No rules found for '{site_key}'. "
            "Please set rules first using store_site_rules."
        )
    }


# ---------------------------------------------------------------------------
# 3. fetch_vendors
# ---------------------------------------------------------------------------

def fetch_vendors(material: str) -> list[dict[str, Any]]:
    """Return all vendors that supply a given material from the catalog.

    The look-up is case-insensitive against each vendor's ``category`` field.
    If no vendors match, a warning is logged to the audit log listing the
    available categories so the caller can self-correct.

    Args:
        material: Material type to search for (e.g. "cement").

    Returns:
        A list of vendor dicts.  Returns an empty list when the material is
        not found or the catalog file is missing / malformed.

    Examples:
        >>> fetch_vendors("cement")  # returns all cement vendors from catalog
        [{'id': 'badrock', 'name': 'BadRock Cements', ...}, ...]

        >>> fetch_vendors("glass")  # not in catalog
        []

        >>> fetch_vendors("CEMENT")  # case-insensitive
        [{'id': 'badrock', 'name': 'BadRock Cements', ...}, ...]
    """
    data = _mem.read_json(_mem.VENDORS_PATH)
    all_vendors: list[dict[str, Any]] = data.get("vendors", [])
    material_lower = material.strip().lower()

    matched = [
        v for v in all_vendors
        if v.get("category", "").lower() == material_lower
    ]

    if not matched:
        available = sorted({v.get("category", "unknown") for v in all_vendors})
        _mem.log_decision("vendor_rejected", "", {
            "reason": f"No vendors found for material '{material}'",
            "available_categories": available,
        })

    return matched


# ---------------------------------------------------------------------------
# 4. filter_vendors
# ---------------------------------------------------------------------------

def filter_vendors(
    vendors: list[dict[str, Any]],
    blacklist: list[str],
    budget: int,
    site_name: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Filter a vendor list by blacklist and budget, then sort eligible by price.

    Processing order:
      1. Remove vendors whose ``name`` appears in *blacklist* (case-insensitive)
         → added to ``rejected`` and logged as ``vendor_rejected``.
      2. Of the remaining, separate those whose ``price_per_100_bags_inr`` exceeds
         *budget* → added to ``over_budget`` and logged as ``vendor_rejected``.
      3. Eligible vendors are sorted **ascending** by ``price_per_100_bags_inr``.

    Args:
        vendors:   List of vendor dicts (as returned by :func:`fetch_vendors`).
        blacklist: Vendor names to exclude.
        budget:    Maximum acceptable price per 100 bags (INR).
        site_name: Optional site name for audit-log entries (default ``""``).

    Returns:
        A dict with three keys::

            {
              "eligible":    [<vendor_dict>, ...],   # sorted cheapest-first
              "rejected":    [{"vendor": str, "reason": str, "price": int}, ...],
              "over_budget": [{"vendor": str, "reason": str, "price": int}, ...]
            }

    Examples:
        >>> filter_vendors([{"name": "BadRock", "price_per_100_bags_inr": 35000}], ["BadRock"], 40000)
        {'eligible': [], 'rejected': [{'vendor': 'BadRock', 'reason': 'Blacklisted for this site', 'price': 35000}], 'over_budget': []}

        >>> filter_vendors([{"name": "V1", "price_per_100_bags_inr": 50000}], [], 40000)
        {'eligible': [], 'rejected': [], 'over_budget': [{'vendor': 'V1', 'reason': 'Price ...exceeds budget...', 'price': 50000}], 'message': '...'}

        >>> filter_vendors([], [], 100000)
        {'eligible': [], 'rejected': [], 'over_budget': []}
    """
    blacklist_lower: set[str] = {name.strip().lower() for name in blacklist}

    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    over_budget: list[dict[str, Any]] = []

    for v in vendors:
        name: str = v.get("name", "Unknown")
        price: int = v.get("price_per_100_bags_inr", 0)

        # Step 1 — blacklist check
        if name.strip().lower() in blacklist_lower:
            reason = "Blacklisted for this site"
            rejected.append({"vendor": name, "reason": reason, "price": price})
            _mem.log_decision("vendor_rejected", site_name, {
                "vendor": name, "price": price, "reason": reason,
            })
            continue

        # Step 2 — budget check
        if price > budget:
            reason = f"Price ₹{price:,} exceeds budget ₹{budget:,}"
            over_budget.append({"vendor": name, "reason": reason, "price": price})
            _mem.log_decision("vendor_rejected", site_name, {
                "vendor": name, "price": price, "reason": reason,
            })
            continue

        # Step 3 — eligible
        eligible.append(v)

    # Sort eligible by price ascending (deterministic: stable sort)
    eligible.sort(key=lambda v: v.get("price_per_100_bags_inr", 0))

    result: dict[str, Any] = {
        "eligible": eligible,
        "rejected": rejected,
        "over_budget": over_budget,
    }

    # Diagnostic message for edge cases
    if not eligible and not over_budget and rejected:
        result["message"] = (
            f"All {len(rejected)} vendor(s) are blacklisted for this site. "
            "No order can be placed. Update the blacklist or add new vendors."
        )
    elif not eligible and over_budget:
        cheapest = min(over_budget, key=lambda x: x["price"])
        result["message"] = (
            f"All non-blacklisted vendors exceed the budget of ₹{budget:,}. "
            f"Cheapest option: {cheapest['vendor']} at ₹{cheapest['price']:,}. "
            "Request a budget increase or approve the over-budget order."
        )

    return result


# ---------------------------------------------------------------------------
# 5. place_order
# ---------------------------------------------------------------------------

def place_order(
    vendor_name: str,
    price: int,
    quantity: int,
    material: str,
    site_name: str,
    approval_limit: int,
) -> str:
    """Place an order for construction materials with budget-gate logic.

    If ``price <= approval_limit`` the order is auto-approved and saved.
    If ``price > approval_limit`` an approval-request block is returned
    instead (the order is **not** saved).

    Args:
        vendor_name:    Display name of the selected vendor.
        price:          Total order cost in INR.
        quantity:       Number of bags to order.
        material:       Material type (e.g. ``"cement"``).
        site_name:      Construction site name.
        approval_limit: Max auto-approved amount in INR for this site.

    Returns:
        * On auto-approve — confirmation string starting with
          ``ORDER_CONFIRMED``.
        * On over-budget — approval-request string starting with
          ``APPROVAL_REQUIRED``.

    Examples:
        >>> place_order("BadRock Cements", 35000, 100, "cement", "Site-1", 50000)
        'ORDER_CONFIRMED: Order placed: 100 bags cement from BadRock Cements at ₹35,000. Within approval limit of ₹50,000.'

        >>> place_order("GoodRock Cements", 45000, 100, "cement", "Site-1", 40000)
        'APPROVAL_REQUIRED\\nOrder Details:\\n  Vendor: GoodRock Cements\\n  Cost: ₹45,000\\n  Limit: ₹40,000\\n  Overage: ₹5,000 (12.5%)\\n\\nApprove this order?'
    """
    _mem.log_decision("vendor_selected", site_name, {
        "vendor": vendor_name, "price": price,
        "quantity": quantity, "material": material,
    })

    # --- within budget: auto-approve ---
    if price <= approval_limit:
        order: dict[str, Any] = {
            "site_name": site_name,
            "vendor_name": vendor_name,
            "material": material,
            "quantity": quantity,
            "price_inr": price,
            "status": "confirmed",
        }
        store = _mem.read_json(_mem.MEMORY_PATH)
        orders: list[dict[str, Any]] = store.get("orders", [])
        orders.append(order)
        store["orders"] = orders
        write_err = _mem.write_json(_mem.MEMORY_PATH, store)
        if write_err:
            return f"Error saving order: {write_err}"

        _mem.log_decision("order_placed", site_name, {
            "vendor": vendor_name, "price": price,
            "quantity": quantity, "material": material,
            "approval": "auto",
        })
        return (
            f"ORDER_CONFIRMED: Order placed: {quantity} bags {material} "
            f"from {vendor_name} at ₹{price:,}. "
            f"Within approval limit of ₹{approval_limit:,}."
        )

    # --- over budget: request human approval ---
    overage: int = price - approval_limit
    percentage: float = round((overage / approval_limit) * 100, 1)

    _mem.log_decision("approval_requested", site_name, {
        "vendor": vendor_name, "price": price,
        "approval_limit": approval_limit,
        "overage": overage, "overage_pct": percentage,
    })
    return (
        "APPROVAL_REQUIRED\n"
        "Order Details:\n"
        f"  Vendor: {vendor_name}\n"
        f"  Cost: ₹{price:,}\n"
        f"  Limit: ₹{approval_limit:,}\n"
        f"  Overage: ₹{overage:,} ({percentage}%)\n"
        "\n"
        "Approve this order?"
    )


# ---------------------------------------------------------------------------
# 6. confirm_order
# ---------------------------------------------------------------------------

def confirm_order(
    vendor_name: str,
    price: int,
    quantity: int,
    material: str,
    site_name: str,
) -> str:
    """Finalise a previously-flagged over-budget order after human approval.

    This tool MUST only be called after the human has explicitly approved
    the over-budget order surfaced by :func:`place_order`.

    Args:
        vendor_name: Vendor name (from the original ``place_order`` call).
        price:       Total order cost in INR.
        quantity:    Number of bags to order.
        material:    Material type.
        site_name:   Construction site name.

    Returns:
        Confirmation string starting with ``ORDER_CONFIRMED``.

    Examples:
        >>> confirm_order("SlowRock Cements", 39000, 500, "cement", "Delhi-Site-7")
        'ORDER_CONFIRMED: Order placed: 500 bags cement from SlowRock Cements at ₹39,000. (Human-approved over-budget order.)'
    """
    order: dict[str, Any] = {
        "site_name": site_name,
        "vendor_name": vendor_name,
        "material": material,
        "quantity": quantity,
        "price_inr": price,
        "status": "confirmed_with_approval",
    }
    store = _mem.read_json(_mem.MEMORY_PATH)
    orders: list[dict[str, Any]] = store.get("orders", [])
    orders.append(order)
    store["orders"] = orders
    write_err = _mem.write_json(_mem.MEMORY_PATH, store)
    if write_err:
        return f"Error saving order: {write_err}"

    _mem.log_decision("order_placed", site_name, {
        "vendor": vendor_name, "price": price,
        "quantity": quantity, "material": material,
        "approval": "human",
    })
    return (
        f"ORDER_CONFIRMED: Order placed: {quantity} bags {material} "
        f"from {vendor_name} at ₹{price:,}. (Human-approved over-budget order.)"
    )
