"""Interactive CLI for the Intelligent Procurement Agent.

Two modes:
  python cli.py            → LLM-powered chat via ADK InMemoryRunner
  python cli.py --offline  → offline mode (calls tools directly, no LLM)

The LLM mode requires a configured ``.env`` with ``GOOGLE_API_KEY``.
"""

import json
import sys
import uuid
import warnings
from typing import Any

from colorama import Fore, Style, init as colorama_init

try:
    from .memory import read_json, read_audit_log, MEMORY_PATH
    from .tools import (
        confirm_order, fetch_vendors, filter_vendors,
        place_order, retrieve_site_rules, store_site_rules,
    )
except ImportError:
    from memory import read_json, read_audit_log, MEMORY_PATH
    from tools import (
        confirm_order, fetch_vendors, filter_vendors,
        place_order, retrieve_site_rules, store_site_rules,
    )


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _tool(msg: str) -> None:
    """Tool-call trace — cyan."""
    print(f"  {Fore.CYAN}[Tool] {msg}{Style.RESET_ALL}")


def _ok(msg: str) -> None:
    """Success — green checkmark."""
    print(f"  {Fore.GREEN}\u2713 {msg}{Style.RESET_ALL}")


def _warn(msg: str) -> None:
    """Warning — yellow."""
    print(f"  {Fore.YELLOW}\u26a0 {msg}{Style.RESET_ALL}")


def _err(msg: str) -> None:
    """Error — red cross."""
    print(f"  {Fore.RED}\u2717 {msg}{Style.RESET_ALL}")


def _agent(msg: str) -> None:
    """Agent speech — magenta."""
    print(f"\n  {Fore.MAGENTA}Agent: {msg}{Style.RESET_ALL}")


def _dim(msg: str) -> None:
    """Muted detail line."""
    print(f"  {Style.DIM}{msg}{Style.RESET_ALL}")


# ---------------------------------------------------------------------------
# Prompt helpers (offline mode only)
# ---------------------------------------------------------------------------

def _ask(label: str) -> str:
    return input(f"  {Fore.WHITE}{label}{Style.RESET_ALL}").strip()


def _ask_int(label: str) -> int | None:
    raw = _ask(label).replace("\u20b9", "").replace(",", "").strip()
    try:
        return int(raw)
    except ValueError:
        _err(f"Invalid number: '{raw}'")
        return None


# ---------------------------------------------------------------------------
# Shared commands (available in both modes)
# ---------------------------------------------------------------------------

def cmd_show_rules() -> None:
    """Display all stored site rules from memory_store.json."""
    memory = read_json(MEMORY_PATH)
    sites = {
        k: v for k, v in memory.items()
        if isinstance(v, dict) and "approval_limit" in v
    }
    if not sites:
        _warn("No site rules stored yet.")
        return

    print()
    for name, rules in sites.items():
        limit: int = rules.get("approval_limit", 0)
        bl: list[str] = rules.get("vendor_blacklist", [])
        bl_display = ", ".join(bl) if bl else "(none)"
        print(f"  {Fore.GREEN}{name}{Style.RESET_ALL}")
        print(f"    Approval limit : \u20b9{limit:,}")
        print(f"    Blacklist      : {bl_display}")


def cmd_show_log() -> None:
    """Display the audit log with colour-coded event types."""
    entries = read_audit_log()
    if not entries:
        _warn("Audit log is empty.")
        return

    COLOR_MAP: dict[str, str] = {
        "rules_stored": Fore.CYAN,
        "vendor_rejected": Fore.YELLOW,
        "vendor_selected": Fore.WHITE,
        "approval_requested": Fore.YELLOW,
        "order_placed": Fore.GREEN,
    }

    print()
    for e in entries:
        ts: str = e.get("timestamp", "")[:19]
        etype: str = e.get("event_type", "")
        site: str = e.get("site_name", "")
        d: dict[str, Any] = e.get("details", {})
        color: str = COLOR_MAP.get(etype, Fore.WHITE)

        parts: list[str] = []
        for k, v in d.items():
            if isinstance(v, int) and k in ("price", "approval_limit", "overage"):
                parts.append(f"{k}=\u20b9{v:,}")
            elif isinstance(v, list):
                parts.append(f"{k}={v}")
            else:
                parts.append(f"{k}={v}")
        detail = ", ".join(parts)

        print(f"  {color}[{ts}]  {etype:<22} | {site}{Style.RESET_ALL}")
        print(f"    {detail}")


# ===================================================================
# LLM-POWERED MODE  (uses ADK InMemoryRunner)
# ===================================================================

LIVE_HELP = (
    f"  {Fore.WHITE}Type any request{Style.RESET_ALL} — the LLM agent calls tools automatically\n"
    f"  {Fore.WHITE}show rules{Style.RESET_ALL}      — display stored site rules\n"
    f"  {Fore.WHITE}show log{Style.RESET_ALL}        — display the audit trail\n"
    f"  {Fore.WHITE}help{Style.RESET_ALL}            — show this message\n"
    f"  {Fore.WHITE}exit{Style.RESET_ALL}            — quit"
)


def _format_args(args: dict[str, Any]) -> str:
    """Format function-call args for compact display, truncating long values."""
    pieces: list[str] = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 50:
            v_str = repr(v[:47] + "...")
        elif isinstance(v, list) and len(str(v)) > 60:
            v_str = f"[{len(v)} items]"
        else:
            v_str = repr(v)
        pieces.append(f"{k}={v_str}")
    return ", ".join(pieces)


def _display_event(event: Any) -> None:
    """Render a single ADK event with coloured output.

    Handles three event types:
      * **function_call** — tool invocation (shown in cyan)
      * **function_response** — tool result summary (shown dim)
      * **text** — agent response (shown in magenta)
    """
    content = getattr(event, "content", None)
    if not content:
        return
    event_parts = getattr(content, "parts", None)
    if not event_parts:
        return

    # Skip echoed user messages
    author = getattr(event, "author", "")
    if author == "user":
        return

    for part in event_parts:
        fc = getattr(part, "function_call", None)
        fr = getattr(part, "function_response", None)
        text = getattr(part, "text", None)

        if fc:
            name = getattr(fc, "name", "?")
            args = getattr(fc, "args", None) or {}
            _tool(f"{name}({_format_args(args)})")

        elif fr:
            resp = getattr(fr, "response", None) or {}
            try:
                preview = json.dumps(resp, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                preview = str(resp)
            if len(preview) > 150:
                preview = preview[:147] + "..."
            _dim(f"  \u2192 {preview}")

        elif text:
            stripped = text.strip()
            if stripped:
                _agent(stripped)


def run_live() -> None:
    """LLM-powered interactive chat using ADK InMemoryRunner.

    Each user message is sent to the live Gemini model via the ADK Runner.
    Tool calls (fetch_vendors, filter_vendors, etc.) happen automatically
    and are displayed in real time.
    """
    # Suppress ADK experimental warnings
    warnings.filterwarnings("ignore", category=UserWarning, module=r"google\.adk")

    try:
        from google.adk.runners import InMemoryRunner  # type: ignore[import-untyped]
        from google.genai.types import Content, Part    # type: ignore[import-untyped]
        try:
            from .agent import root_agent
        except ImportError:
            from agent import root_agent
    except ImportError as exc:
        _err(f"Cannot start live mode: {exc}")
        _warn("Install dependencies: pip install google-adk")
        _warn("Or use offline mode: python cli.py --offline")
        return

    runner = InMemoryRunner(agent=root_agent, app_name="procurement_cli")
    session_id = f"cli-{uuid.uuid4().hex[:8]}"
    user_id = "cli-user"

    print(f"\n{Fore.CYAN}{'=' * 60}")
    print("  Intelligent Procurement Agent \u2014 Live Chat")
    print(f"{'=' * 60}{Style.RESET_ALL}")
    print(f"\n{LIVE_HELP}\n")

    while True:
        try:
            raw = input(f"{Fore.GREEN}> {Style.RESET_ALL}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Fore.CYAN}Goodbye.{Style.RESET_ALL}")
            break

        if not raw:
            continue

        cmd = raw.lower()
        if cmd in ("exit", "quit", "q"):
            print(f"{Fore.CYAN}Goodbye.{Style.RESET_ALL}")
            break
        if cmd == "show rules":
            cmd_show_rules()
            print()
            continue
        if cmd == "show log":
            cmd_show_log()
            print()
            continue
        if cmd in ("help", "?"):
            print(f"\n{LIVE_HELP}\n")
            continue

        # Send to ADK agent
        msg = Content(parts=[Part(text=raw)], role="user")

        print()
        try:
            for event in runner.run(
                user_id=user_id,
                session_id=session_id,
                new_message=msg,
            ):
                _display_event(event)
        except Exception as exc:
            _err(f"Agent error: {exc}")

        print()


# ===================================================================
# OFFLINE MODE  (calls tools directly — no LLM needed)
# ===================================================================

OFFLINE_HELP = (
    f"  {Fore.WHITE}set rules{Style.RESET_ALL}  \u2014 store site approval limit & vendor blacklist\n"
    f"  {Fore.WHITE}order{Style.RESET_ALL}      \u2014 run the full procurement pipeline\n"
    f"  {Fore.WHITE}show rules{Style.RESET_ALL} \u2014 display all stored site rules\n"
    f"  {Fore.WHITE}show log{Style.RESET_ALL}   \u2014 display the audit log\n"
    f"  {Fore.WHITE}help{Style.RESET_ALL}       \u2014 show this message\n"
    f"  {Fore.WHITE}exit{Style.RESET_ALL}       \u2014 quit"
)


def cmd_set_rules() -> None:
    """Prompt for site name, approval limit, blacklist, then store."""
    print()
    site = _ask("Site name: ")
    if not site:
        _err("Site name cannot be empty.")
        return

    limit = _ask_int("Approval limit (INR): ")
    if limit is None:
        return

    bl_raw = _ask("Blacklisted vendors (comma-separated): ")
    blacklist = [v.strip() for v in bl_raw.split(",") if v.strip()] if bl_raw else []

    print()
    _tool(f"store_site_rules({site!r}, {limit}, {blacklist})")
    result = store_site_rules(site, limit, blacklist)

    if result.startswith("Error"):
        _err(result)
    else:
        _ok(result)


def cmd_order() -> None:
    """Run the full procurement pipeline with real-time tool tracing."""
    print()
    site = _ask("Site: ")
    if not site:
        _err("Site name cannot be empty.")
        return

    material = _ask("Material: ")
    if not material:
        _err("Material cannot be empty.")
        return

    qty = _ask_int("Quantity (bags): ")
    if qty is None:
        return

    # ---- agent thinking ----
    print(f"\n  {Fore.CYAN}[Agent thinking...]{Style.RESET_ALL}")

    # Step 1 — rules
    _tool(f"retrieve_site_rules({site!r})")
    rules = retrieve_site_rules(site)
    if "error" in rules:
        _err(rules["error"])
        return
    _dim(f"  limit=\u20b9{rules['approval_limit']:,}  blacklist={rules['vendor_blacklist']}")

    # Step 2 — vendors (read dynamically from mock_vendors.json)
    _tool(f"fetch_vendors({material!r})")
    vendors = fetch_vendors(material)
    if not vendors:
        _warn(f"No vendors found for material '{material}'.")
        return
    _dim(f"  {len(vendors)} vendor(s) loaded from catalog")

    # Step 3 — filter
    bl: list[str] = rules["vendor_blacklist"]
    budget: int = rules["approval_limit"]
    _tool(f"filter_vendors(vendors, {bl}, {budget})")
    filtered = filter_vendors(vendors, bl, budget, site)

    print()
    for v in filtered["eligible"]:
        _ok(f"{v['name']:28s}  \u20b9{v['price_per_100_bags_inr']:>7,}  "
            f"{v['delivery_days']}d  ELIGIBLE")
    for r in filtered["rejected"]:
        _err(f"{r['vendor']:28s}  \u20b9{r['price']:>7,}  {r['reason']}")
    for o in filtered["over_budget"]:
        _warn(f"{o['vendor']:28s}  \u20b9{o['price']:>7,}  {o['reason']}")

    if "message" in filtered:
        _agent(filtered["message"])
        if not filtered["eligible"] and not filtered["over_budget"]:
            return  # all blacklisted — nothing to order

    # Step 4 — pick cheapest available
    if filtered["eligible"]:
        vendor_name: str = filtered["eligible"][0]["name"]
        price: int = filtered["eligible"][0]["price_per_100_bags_inr"]
    elif filtered["over_budget"]:
        cheapest_ob = min(filtered["over_budget"], key=lambda x: x["price"])
        vendor_name = cheapest_ob["vendor"]
        price = cheapest_ob["price"]
    else:
        return

    _agent(f"Selected {vendor_name} at \u20b9{price:,} for {qty} bags of {material}.")

    # Step 5 — place order (may require approval)
    _tool(f"place_order({vendor_name!r}, {price}, {qty}, "
          f"{material!r}, {site!r}, {budget})")
    order_result = place_order(vendor_name, price, qty, material, site, budget)

    if order_result.startswith("ORDER_CONFIRMED"):
        _ok(order_result.replace("ORDER_CONFIRMED: ", ""))
        return

    if order_result.startswith("APPROVAL_REQUIRED"):
        _handle_approval(
            order_result, vendor_name, price, qty, material, site, filtered,
        )
        return

    # Unexpected response
    _err(order_result)


def _handle_approval(
    approval_text: str,
    vendor_name: str,
    price: int,
    qty: int,
    material: str,
    site: str,
    filtered: dict[str, Any],
) -> None:
    """Interactive Tool Confirmation: show details, pause for yes/no.

    If rejected, walk through remaining over-budget vendors cheapest-first
    and re-offer until the user accepts one or runs out of options.
    """
    # --- show the approval block ---
    print()
    horiz = "\u2500" * 50
    bar = f"{Fore.YELLOW}{horiz}{Style.RESET_ALL}"
    print(f"  {bar}")
    for line in approval_text.splitlines():
        print(f"  {Fore.YELLOW}{line}{Style.RESET_ALL}")
    print(f"  {bar}")

    # Build a queue of remaining over-budget vendors (cheapest first),
    # excluding the one we just offered.
    remaining: list[dict[str, Any]] = sorted(
        [o for o in filtered["over_budget"] if o["vendor"] != vendor_name],
        key=lambda x: x["price"],
    )

    # --- approval loop ---
    current_vendor = vendor_name
    current_price = price

    while True:
        print()
        choice = _ask("Approve this order? (yes/no): ").lower()

        if choice in ("yes", "y", "approve", "go ahead"):
            _tool(f"confirm_order({current_vendor!r}, {current_price}, "
                  f"{qty}, {material!r}, {site!r})")
            result = confirm_order(current_vendor, current_price, qty, material, site)
            _ok(result.replace("ORDER_CONFIRMED: ", ""))
            return

        # Rejected
        _warn("Order rejected by user.")

        if not remaining:
            _agent("No other vendors available for this material and site.")
            return

        # Offer the next cheapest
        nxt = remaining.pop(0)
        current_vendor = nxt["vendor"]
        current_price = nxt["price"]
        _agent(
            f"Next cheapest option: {current_vendor} "
            f"at \u20b9{current_price:,}. "
            f"This also exceeds the budget."
        )
        # Recalculate overage from the site's actual budget
        rules = retrieve_site_rules(site)
        budget_val: int = current_price
        if "error" not in rules:
            budget_val = rules["approval_limit"]
        overage = current_price - budget_val
        pct = round((overage / budget_val) * 100, 1) if budget_val else 0
        print()
        print(f"  {bar}")
        print(f"  {Fore.YELLOW}Order Details:")
        print(f"  {Fore.YELLOW}  Vendor: {current_vendor}")
        print(f"  {Fore.YELLOW}  Cost: \u20b9{current_price:,}")
        print(f"  {Fore.YELLOW}  Limit: \u20b9{budget_val:,}")
        print(f"  {Fore.YELLOW}  Overage: \u20b9{overage:,} ({pct}%)")
        print(f"  {bar}{Style.RESET_ALL}")


def run_offline() -> None:
    """Offline command-based interaction (calls tools directly, no LLM)."""
    COMMANDS: dict[str, Any] = {
        "set rules": cmd_set_rules,
        "order":     cmd_order,
        "show rules": cmd_show_rules,
        "show log":  cmd_show_log,
    }

    print(f"\n{Fore.CYAN}{'=' * 60}")
    print("  Intelligent Procurement Agent \u2014 Offline CLI")
    print(f"{'=' * 60}{Style.RESET_ALL}")
    print(f"\n{OFFLINE_HELP}\n")

    while True:
        try:
            raw = input(f"{Fore.GREEN}> {Style.RESET_ALL}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Fore.CYAN}Goodbye.{Style.RESET_ALL}")
            break

        if not raw:
            continue

        cmd = raw.lower()
        if cmd in ("exit", "quit", "q"):
            print(f"{Fore.CYAN}Goodbye.{Style.RESET_ALL}")
            break
        elif cmd in ("help", "?"):
            print(f"\n{OFFLINE_HELP}")
        elif cmd in COMMANDS:
            try:
                COMMANDS[cmd]()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{Fore.CYAN}(cancelled){Style.RESET_ALL}")
            except Exception as exc:
                _err(f"Unexpected error: {exc}")
        else:
            _warn(f"Unknown command: '{raw}'. Type 'help' for options.")

        print()


# ===================================================================
# Entry point
# ===================================================================

def main() -> None:
    """Launch the CLI in live (default) or offline mode."""
    colorama_init()

    # Windows UTF-8 fix for ₹ symbol
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    if "--offline" in sys.argv:
        run_offline()
    else:
        run_live()


if __name__ == "__main__":
    main()
