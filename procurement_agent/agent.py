"""Agent definition for the intelligent procurement agent (ADK).

This module contains **only** the LLM agent wiring:

* Model selection
* System prompt (instructions for the LLM)
* Tool registration
* ``root_agent`` — the object ADK discovers via ``adk run`` / ``adk web``

No business logic or persistence code lives here.  Tools are in
``tools.py``; persistence is in ``memory.py``.

Run via ADK CLI (from the **parent** directory of ``procurement_agent/``):
    ``adk run procurement_agent``   → terminal chat
    ``adk web``                     → browser-based dev UI

Offline modes (no LLM):
    ``python agent.py --demo``  → offline demo
    ``python agent.py --test``  → edge-case tests
"""

import sys

from google.adk.agents import Agent

try:
    from .tools import (
        confirm_order, fetch_vendors, filter_vendors,
        place_order, retrieve_site_rules, store_site_rules,
    )
except ImportError:
    from tools import (
        confirm_order, fetch_vendors, filter_vendors,
        place_order, retrieve_site_rules, store_site_rules,
    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


MODEL = gemini-2.0-flash

SYSTEM_PROMPT: str = """\
You are a procurement agent for construction sites. Your job:

1. When user provides site rules, extract:
   - Site name
   - Approval limit (as integer, remove ₹ symbols)
   - Vendor blacklist (as list of exact names)
   Then call store_site_rules.

2. When user requests an order, extract:
   - Site name
   - Material type
   - Quantity
   Then:
   a) Call retrieve_site_rules(site_name)
   b) Call fetch_vendors(material)
   c) Call filter_vendors(vendors, blacklist, budget)
   d) Analyze the filter_vendors result:
      - If eligible vendors exist → select the cheapest and call
        place_order(vendor_name, price, quantity, material, site_name,
        approval_limit).
      - If only over_budget vendors remain → select the cheapest of
        those and call place_order so the budget gate fires.
      - If ALL vendors are blacklisted → explain why no order can be placed.

3. Handling place_order responses:
   - If the response starts with ORDER_CONFIRMED → relay success to user.
   - If the response starts with APPROVAL_REQUIRED → present the full
     approval block to the user EXACTLY as returned (Vendor, Cost, Limit,
     Overage, percentage) and ask them to approve or reject.

4. Handling human approval / rejection:
   - If the user APPROVES (e.g. "yes", "approve", "go ahead") → call
     confirm_order(vendor_name, price, quantity, material, site_name) to
     finalise the order.
   - If the user REJECTS (e.g. "no", "reject", "cancel") → look at the
     filter_vendors result you already have.  If there is a next-cheapest
     vendor (eligible or over-budget), offer it.  Otherwise tell the user
     no more vendors are available.

CRITICAL: Never make up vendor prices. Only use data returned by the tools.
Always explain your reasoning: why you rejected vendors, why you chose one,
why you need approval, and what the overage is."""

TOOLS: list = [
    store_site_rules,
    retrieve_site_rules,
    fetch_vendors,
    filter_vendors,
    place_order,
    confirm_order,
]

# ---------------------------------------------------------------------------
# ADK Agent definition  — this is what ``adk run`` / ``adk web`` discovers
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="procurement_agent",
    model=MODEL,
    description=(
        "Intelligent procurement agent for construction sites. "
        "Stores site-specific rules (approval limits, vendor blacklists), "
        "fetches and filters vendor catalogs, places orders with budget-gate "
        "logic, and handles human approval for over-budget orders."
    ),
    instruction=SYSTEM_PROMPT,
    tools=TOOLS,
)


# ---------------------------------------------------------------------------
# Entry-point  (dispatches to demo.py for offline modes)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    if "--test" in sys.argv:
        try:
            from .demo import test_edge_cases
        except ImportError:
            from demo import test_edge_cases
        test_edge_cases()
    elif "--demo" in sys.argv:
        try:
            from .demo import demo
        except ImportError:
            from demo import demo
        demo()
    else:
        print("=" * 60)
        print("  Intelligent Procurement Agent (ADK)")
        print("=" * 60)
        print()
        print("  Use ADK CLI to run this agent interactively:")
        print("    adk run procurement_agent   (terminal chat)")
        print("    adk web                     (browser dev UI)")
        print()
        print("  Or use offline modes:")
        print("    python agent.py --demo      (offline demo)")
        print("    python agent.py --test      (edge-case tests)")
        print("    python cli.py               (LLM-powered CLI)")
        print("    python cli.py --offline     (offline CLI)")
        print("=" * 60)
