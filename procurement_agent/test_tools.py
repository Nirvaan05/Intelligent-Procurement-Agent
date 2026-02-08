"""Unit tests for tools.py — deterministic procurement tool implementations.

Uses pytest with monkeypatching to mock file I/O so tests have no side effects
on disk.  All path constants are patched on the ``memory`` module, which is the
single source of truth for file locations.

Run with:  ``pytest test_tools.py -v``
"""

import json
from pathlib import Path
from typing import Any

import pytest

import memory as mem
import tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_VENDORS: dict[str, Any] = {
    "vendors": [
        {
            "id": "badrock",
            "name": "BadRock Cements",
            "category": "cement",
            "price_per_100_bags_inr": 35000,
            "currency": "INR",
            "delivery_days": 5,
            "in_stock": True,
            "notes": "Budget option; standard quality",
        },
        {
            "id": "goodrock",
            "name": "GoodRock Cements",
            "category": "cement",
            "price_per_100_bags_inr": 45000,
            "currency": "INR",
            "delivery_days": 3,
            "in_stock": True,
            "notes": "Premium quality; faster delivery",
        },
        {
            "id": "slowrock",
            "name": "SlowRock Cements",
            "category": "cement",
            "price_per_100_bags_inr": 39000,
            "currency": "INR",
            "delivery_days": 7,
            "in_stock": True,
            "notes": "Mid-range price; slower delivery",
        },
    ]
}


@pytest.fixture(autouse=True)
def _isolate_file_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect all file paths in the ``memory`` module to a temporary directory.

    Since tools.py accesses paths via ``import memory as _mem; _mem.MEMORY_PATH``,
    patching the ``memory`` module is sufficient to redirect all I/O.
    """
    tmp_memory = tmp_path / "memory_store.json"
    tmp_vendors = tmp_path / "mock_vendors.json"
    tmp_audit = tmp_path / "audit_log.jsonl"

    # Seed the vendor catalog so fetch_vendors works
    tmp_vendors.write_text(json.dumps(SAMPLE_VENDORS), encoding="utf-8")

    # Seed empty memory
    tmp_memory.write_text("{}", encoding="utf-8")

    # Patch the single source of truth — the memory module
    monkeypatch.setattr(mem, "MEMORY_PATH", tmp_memory)
    monkeypatch.setattr(mem, "VENDORS_PATH", tmp_vendors)
    monkeypatch.setattr(mem, "AUDIT_LOG_PATH", tmp_audit)


# ===================================================================
# store_site_rules
# ===================================================================


class TestStoreSiteRules:
    """Tests for store_site_rules."""

    def test_store_basic_rules(self) -> None:
        """Storing rules returns a confirmation and persists to JSON."""
        result = tools.store_site_rules("Mumbai-Site-1", 50000, ["BadRock Cements"])

        assert "Rules stored" in result
        assert "Mumbai-Site-1" in result
        assert "50,000" in result  # formatted approval limit

        # Verify persistence
        data = json.loads(mem.MEMORY_PATH.read_text(encoding="utf-8"))
        assert "Mumbai-Site-1" in data
        assert data["Mumbai-Site-1"]["approval_limit"] == 50000
        assert data["Mumbai-Site-1"]["vendor_blacklist"] == ["BadRock Cements"]

    def test_store_empty_blacklist(self) -> None:
        """Storing rules with an empty blacklist is valid."""
        result = tools.store_site_rules("Site-A", 100000, [])

        assert "Rules stored" in result
        assert "(none)" in result  # blacklist display

    def test_store_overwrites_existing(self) -> None:
        """Storing rules for the same site overwrites previous rules."""
        tools.store_site_rules("Site-X", 10000, ["VendorA"])
        tools.store_site_rules("Site-X", 20000, ["VendorB", "VendorC"])

        data = json.loads(mem.MEMORY_PATH.read_text(encoding="utf-8"))
        assert data["Site-X"]["approval_limit"] == 20000
        assert data["Site-X"]["vendor_blacklist"] == ["VendorB", "VendorC"]

    def test_store_empty_site_name_returns_error(self) -> None:
        """An empty site name should return an error string."""
        result = tools.store_site_rules("", 50000, [])

        assert "Error" in result

    def test_store_whitespace_site_name_returns_error(self) -> None:
        """A whitespace-only site name should return an error string."""
        result = tools.store_site_rules("   ", 50000, [])

        assert "Error" in result

    def test_store_strips_vendor_names(self) -> None:
        """Vendor names in the blacklist should be stripped of whitespace."""
        tools.store_site_rules("Site-S", 10000, ["  BadRock Cements  ", " GoodRock Cements"])

        data = json.loads(mem.MEMORY_PATH.read_text(encoding="utf-8"))
        assert data["Site-S"]["vendor_blacklist"] == [
            "BadRock Cements",
            "GoodRock Cements",
        ]

    def test_store_logs_to_audit(self) -> None:
        """Storing rules should append a 'rules_stored' audit entry."""
        tools.store_site_rules("AuditSite", 25000, ["X"])

        entries = mem.read_audit_log()
        assert len(entries) >= 1
        assert entries[-1]["event_type"] == "rules_stored"
        assert entries[-1]["site_name"] == "AuditSite"


# ===================================================================
# retrieve_site_rules
# ===================================================================


class TestRetrieveSiteRules:
    """Tests for retrieve_site_rules."""

    def test_retrieve_existing_rules(self) -> None:
        """Retrieving rules for a stored site returns the correct dict."""
        tools.store_site_rules("Site-R", 40000, ["BadRock Cements"])

        rules = tools.retrieve_site_rules("Site-R")

        assert rules["approval_limit"] == 40000
        assert rules["vendor_blacklist"] == ["BadRock Cements"]

    def test_retrieve_missing_site_returns_error(self) -> None:
        """Retrieving rules for a non-existent site returns an error dict."""
        rules = tools.retrieve_site_rules("Ghost-Site-99")

        assert "error" in rules
        assert "Ghost-Site-99" in rules["error"]

    def test_retrieve_empty_site_name_returns_error(self) -> None:
        """An empty site name returns an error dict."""
        rules = tools.retrieve_site_rules("")

        assert "error" in rules

    def test_retrieve_whitespace_site_name_returns_error(self) -> None:
        """A whitespace-only site name returns an error dict."""
        rules = tools.retrieve_site_rules("   ")

        assert "error" in rules

    def test_store_then_retrieve_roundtrip(self) -> None:
        """Store-then-retrieve should produce identical data."""
        tools.store_site_rules("RT-Site", 75000, ["A", "B"])

        rules = tools.retrieve_site_rules("RT-Site")

        assert rules == {"approval_limit": 75000, "vendor_blacklist": ["A", "B"]}


# ===================================================================
# fetch_vendors
# ===================================================================


class TestFetchVendors:
    """Tests for fetch_vendors."""

    def test_fetch_cement_returns_three(self) -> None:
        """Fetching 'cement' should return all three mock vendors."""
        vendors = tools.fetch_vendors("cement")

        assert len(vendors) == 3
        names = {v["name"] for v in vendors}
        assert names == {"BadRock Cements", "GoodRock Cements", "SlowRock Cements"}

    def test_fetch_is_case_insensitive(self) -> None:
        """Material matching should be case-insensitive."""
        assert len(tools.fetch_vendors("Cement")) == 3
        assert len(tools.fetch_vendors("CEMENT")) == 3
        assert len(tools.fetch_vendors("CeMeNt")) == 3

    def test_fetch_missing_material_returns_empty(self) -> None:
        """A material not in the catalog returns an empty list."""
        vendors = tools.fetch_vendors("glass")

        assert vendors == []

    def test_fetch_missing_material_logs_warning(self) -> None:
        """Fetching a missing material should log a vendor_rejected audit entry."""
        tools.fetch_vendors("titanium")

        entries = mem.read_audit_log()
        warning = [e for e in entries if e["event_type"] == "vendor_rejected"]
        assert len(warning) >= 1
        assert "titanium" in warning[-1]["details"]["reason"]

    def test_fetch_strips_whitespace(self) -> None:
        """Leading/trailing whitespace in material name is ignored."""
        vendors = tools.fetch_vendors("  cement  ")

        assert len(vendors) == 3

    def test_fetch_with_empty_catalog(self) -> None:
        """An empty vendor catalog file returns an empty list."""
        mem.VENDORS_PATH.write_text("{}", encoding="utf-8")

        vendors = tools.fetch_vendors("cement")

        assert vendors == []

    def test_fetch_with_corrupt_json(self) -> None:
        """A corrupt vendor file returns an empty list (graceful fallback)."""
        mem.VENDORS_PATH.write_text("NOT VALID JSON!!!", encoding="utf-8")

        vendors = tools.fetch_vendors("cement")

        assert vendors == []


# ===================================================================
# filter_vendors
# ===================================================================


class TestFilterVendors:
    """Tests for filter_vendors."""

    def _get_vendors(self) -> list[dict[str, Any]]:
        """Helper: fetch all cement vendors from the mock catalog."""
        return tools.fetch_vendors("cement")

    def test_filter_with_blacklist(self) -> None:
        """Blacklisted vendors appear in 'rejected', not 'eligible'."""
        vendors = self._get_vendors()
        result = tools.filter_vendors(vendors, ["BadRock Cements"], 100000)

        rejected_names = [r["vendor"] for r in result["rejected"]]
        eligible_names = [v["name"] for v in result["eligible"]]

        assert "BadRock Cements" in rejected_names
        assert "BadRock Cements" not in eligible_names

    def test_filter_blacklist_is_case_insensitive(self) -> None:
        """Blacklist matching should be case-insensitive."""
        vendors = self._get_vendors()
        result = tools.filter_vendors(vendors, ["badrock cements"], 100000)

        rejected_names = [r["vendor"] for r in result["rejected"]]
        assert "BadRock Cements" in rejected_names

    def test_filter_with_budget(self) -> None:
        """Vendors exceeding the budget appear in 'over_budget'."""
        vendors = self._get_vendors()
        result = tools.filter_vendors(vendors, [], 40000)

        eligible_names = [v["name"] for v in result["eligible"]]
        over_budget_names = [o["vendor"] for o in result["over_budget"]]

        # BadRock (35K) and SlowRock (39K) are within 40K budget
        assert "BadRock Cements" in eligible_names
        assert "SlowRock Cements" in eligible_names
        # GoodRock (45K) exceeds 40K
        assert "GoodRock Cements" in over_budget_names

    def test_filter_eligible_sorted_by_price(self) -> None:
        """Eligible vendors should be sorted cheapest-first."""
        vendors = self._get_vendors()
        result = tools.filter_vendors(vendors, [], 100000)

        prices = [v["price_per_100_bags_inr"] for v in result["eligible"]]
        assert prices == sorted(prices)

    def test_filter_all_blacklisted_gives_message(self) -> None:
        """When all vendors are blacklisted, a diagnostic message is present."""
        vendors = self._get_vendors()
        all_names = [v["name"] for v in vendors]
        result = tools.filter_vendors(vendors, all_names, 100000, "BL-Site")

        assert len(result["eligible"]) == 0
        assert len(result["rejected"]) == 3
        assert "message" in result
        assert "blacklisted" in result["message"].lower()

    def test_filter_all_over_budget_gives_message(self) -> None:
        """When all vendors exceed budget, a diagnostic message with cheapest is present."""
        vendors = self._get_vendors()
        result = tools.filter_vendors(vendors, [], 1000, "OB-Site")

        assert len(result["eligible"]) == 0
        assert len(result["over_budget"]) == 3
        assert "message" in result
        assert "budget" in result["message"].lower()
        assert "BadRock" in result["message"]  # cheapest option

    def test_filter_blacklist_and_budget_combined(self) -> None:
        """Blacklist is applied first; then budget on remaining vendors."""
        vendors = self._get_vendors()
        result = tools.filter_vendors(
            vendors, ["BadRock Cements"], 40000, "Combo-Site"
        )

        rejected_names = [r["vendor"] for r in result["rejected"]]
        eligible_names = [v["name"] for v in result["eligible"]]
        over_budget_names = [o["vendor"] for o in result["over_budget"]]

        assert "BadRock Cements" in rejected_names         # blacklisted
        assert "SlowRock Cements" in eligible_names        # 39K <= 40K
        assert "GoodRock Cements" in over_budget_names     # 45K > 40K

    def test_filter_empty_vendors_list(self) -> None:
        """An empty vendor list returns empty results."""
        result = tools.filter_vendors([], [], 100000)

        assert result["eligible"] == []
        assert result["rejected"] == []
        assert result["over_budget"] == []

    def test_filter_logs_rejected_vendors(self) -> None:
        """Each rejected vendor should produce a vendor_rejected audit entry."""
        vendors = self._get_vendors()
        mem.clear_audit_log()
        tools.filter_vendors(vendors, ["BadRock Cements"], 40000, "Log-Site")

        entries = mem.read_audit_log()
        rejected_events = [
            e for e in entries if e["event_type"] == "vendor_rejected"
        ]
        # BadRock (blacklisted) + GoodRock (over budget) = 2 rejections
        assert len(rejected_events) == 2


# ===================================================================
# place_order
# ===================================================================


class TestPlaceOrder:
    """Tests for place_order."""

    def test_order_within_budget_auto_approves(self) -> None:
        """An order within the approval limit returns ORDER_CONFIRMED."""
        result = tools.place_order(
            "BadRock Cements", 35000, 100, "cement", "Site-1", 50000
        )

        assert result.startswith("ORDER_CONFIRMED")
        assert "35,000" in result

    def test_order_within_budget_saves_to_memory(self) -> None:
        """An auto-approved order is persisted in memory_store.json."""
        tools.place_order("BadRock Cements", 35000, 100, "cement", "Site-1", 50000)

        data = json.loads(mem.MEMORY_PATH.read_text(encoding="utf-8"))
        assert "orders" in data
        assert len(data["orders"]) == 1
        assert data["orders"][0]["vendor_name"] == "BadRock Cements"
        assert data["orders"][0]["status"] == "confirmed"

    def test_order_over_budget_requests_approval(self) -> None:
        """An order exceeding the limit returns APPROVAL_REQUIRED."""
        result = tools.place_order(
            "GoodRock Cements", 45000, 100, "cement", "Site-1", 40000
        )

        assert result.startswith("APPROVAL_REQUIRED")
        assert "45,000" in result
        assert "40,000" in result

    def test_order_over_budget_not_saved(self) -> None:
        """An over-budget order should NOT be saved to memory_store.json."""
        tools.place_order(
            "GoodRock Cements", 45000, 100, "cement", "Site-1", 40000
        )

        data = json.loads(mem.MEMORY_PATH.read_text(encoding="utf-8"))
        assert data.get("orders", []) == []

    def test_order_at_exact_limit_auto_approves(self) -> None:
        """An order at exactly the approval limit should auto-approve."""
        result = tools.place_order(
            "SlowRock Cements", 39000, 100, "cement", "Site-1", 39000
        )

        assert result.startswith("ORDER_CONFIRMED")

    def test_order_logs_vendor_selected(self) -> None:
        """place_order should log a vendor_selected audit entry."""
        mem.clear_audit_log()
        tools.place_order("BadRock Cements", 35000, 100, "cement", "Site-1", 50000)

        entries = mem.read_audit_log()
        selected = [e for e in entries if e["event_type"] == "vendor_selected"]
        assert len(selected) == 1
        assert selected[0]["details"]["vendor"] == "BadRock Cements"

    def test_order_over_budget_logs_approval_requested(self) -> None:
        """An over-budget order should log an approval_requested audit entry."""
        mem.clear_audit_log()
        tools.place_order(
            "GoodRock Cements", 45000, 100, "cement", "Site-1", 40000
        )

        entries = mem.read_audit_log()
        approvals = [e for e in entries if e["event_type"] == "approval_requested"]
        assert len(approvals) == 1
        assert approvals[0]["details"]["overage"] == 5000


# ===================================================================
# confirm_order
# ===================================================================


class TestConfirmOrder:
    """Tests for confirm_order."""

    def test_confirm_saves_with_approval_status(self) -> None:
        """Confirming an order saves it with 'confirmed_with_approval' status."""
        result = tools.confirm_order(
            "SlowRock Cements", 39000, 500, "cement", "Site-1"
        )

        assert result.startswith("ORDER_CONFIRMED")
        assert "Human-approved" in result

        data = json.loads(mem.MEMORY_PATH.read_text(encoding="utf-8"))
        assert len(data["orders"]) == 1
        assert data["orders"][0]["status"] == "confirmed_with_approval"

    def test_confirm_logs_order_placed_human(self) -> None:
        """confirm_order should log an order_placed entry with approval=human."""
        mem.clear_audit_log()
        tools.confirm_order("SlowRock Cements", 39000, 500, "cement", "Site-1")

        entries = mem.read_audit_log()
        placed = [e for e in entries if e["event_type"] == "order_placed"]
        assert len(placed) == 1
        assert placed[0]["details"]["approval"] == "human"


# ===================================================================
# Audit log utilities (tested via memory module)
# ===================================================================


class TestAuditLog:
    """Tests for log_decision, read_audit_log, and clear_audit_log."""

    def test_log_and_read_roundtrip(self) -> None:
        """Logged entries can be read back."""
        mem.log_decision("rules_stored", "Site-A", {"key": "value"})
        mem.log_decision("order_placed", "Site-B", {"qty": 100})

        entries = mem.read_audit_log()

        assert len(entries) == 2
        assert entries[0]["event_type"] == "rules_stored"
        assert entries[1]["event_type"] == "order_placed"

    def test_clear_removes_all_entries(self) -> None:
        """clear_audit_log should result in an empty log."""
        mem.log_decision("rules_stored", "Site-A", {})
        mem.clear_audit_log()

        entries = mem.read_audit_log()
        assert entries == []

    def test_read_empty_log_returns_empty_list(self) -> None:
        """Reading a non-existent log file returns []."""
        entries = mem.read_audit_log()
        assert entries == []

    def test_log_entry_has_timestamp(self) -> None:
        """Each audit entry should contain an ISO-8601 timestamp."""
        mem.log_decision("rules_stored", "Site-T", {"x": 1})

        entries = mem.read_audit_log()
        assert "timestamp" in entries[0]
        assert "T" in entries[0]["timestamp"]  # ISO-8601 format check
