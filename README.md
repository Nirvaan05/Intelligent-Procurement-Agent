# Intelligent Procurement Agent

An AI-powered procurement assistant for construction sites, built with the
**Google Agent Development Kit (ADK)** and **Gemini 2.0 Flash**. It stores
site-specific rules, compares vendor catalogs, enforces budget gates, and
routes over-budget orders through human approval  all with a full audit trail.

---

## Architecture

```
 ┌────────────────────────────────────────────────────────────────┐
 │                       User / ADK CLI                           │
 │               (adk run / adk web / cli.py)                     │
 └──────────────────────────┬─────────────────────────────────────┘
                            │  natural-language messages
                            ▼
 ┌────────────────────────────────────────────────────────────────┐
 │              Agent Layer  (agent.py)                           │
 │  ┌──────────────────────────────────────────────────────┐      │
 │  │  Gemini 2.0 Flash  +  SYSTEM_PROMPT                  │      │
 │  │  • Extracts intent from user messages                │      │
 │  │  • Orchestrates tool calls automatically             │      │
 │  │  • Manages multi-turn approval conversations         │      │
 │  └──────────────────────────────────────────────────────┘      │
 └──────────────────────────┬─────────────────────────────────────┘
                            │  automatic function calling
                            ▼
 ┌────────────────────────────────────────────────────────────────┐
 │              Tool Layer  (tools.py)                            │
 │  Pure business logic  no file I/O, no LLM calls               │
 │                                                                │
 │  ┌─────────────────┐   ┌─────────────────┐  ┌───────────────┐  │
 │  │ store_site_rules │  │ fetch_vendors   │  │ place_order   │  │
 │  │ retrieve_site_   │  │ filter_vendors  │  │ confirm_order │  │
 │  │   rules          │  │                 │  │               │  │
 │  └────────┬─────────┘  └────────┬────────┘  └───────┬───────┘  │
 └───────────┼─────────────────────┼────────────────────┼─────────┘
             │  delegates I/O      │                    │
             ▼                     ▼                    ▼
 ┌────────────────────────────────────────────────────────────────┐
 │              Memory Layer  (memory.py)                         │
 │  Persistence  all file I/O lives here                         │
 │                                                                │
 │  ┌────────────────┐  ┌─────────────────┐  ┌────────────────┐   │
 │  │ memory_store   │  │ mock_vendors    │  │ audit_log      │   │
 │  │   .json        │  │   .json         │  │   .jsonl       │   │
 │  │ (site rules +  │  │ (vendor catalog)│  │ (append-only   │   │
 │  │  orders)       │  │                 │  │  decision log) │   │
 │  └────────────────┘  └─────────────────┘  └────────────────┘   │
 │                                                                │
 │  read_json · write_json · log_decision · read_audit_log        │
 └────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
User: "Set rules for Mumbai-Site-1: limit ₹40,000, blacklist BadRock"
  └─► LLM extracts params
        └─► store_site_rules("Mumbai-Site-1", 40000, ["BadRock Cements"])
              ├─► writes to memory_store.json
              └─► appends to audit_log.jsonl

User: "Order 100 bags of cement for Mumbai-Site-1"
  └─► LLM orchestrates:
        ├─► retrieve_site_rules("Mumbai-Site-1")  → {limit, blacklist}
        ├─► fetch_vendors("cement")               → [N vendors from mock_vendors.json]
        ├─► filter_vendors(vendors, blacklist, limit)
        │     ├─► blacklisted vendors    → REJECTED
        │     ├─► over-budget vendors    → OVER_BUDGET
        │     └─► remaining vendors      → ELIGIBLE (sorted by price)
        └─► place_order(cheapest_eligible, price, 100, ...)
              └─► ORDER_CONFIRMED or APPROVAL_REQUIRED
```
Example :
<img width="1881" height="711" alt="image" src="https://github.com/user-attachments/assets/94e17a98-328a-42c3-b1eb-ac6f62acf65c" />
<img width="1877" height="709" alt="image" src="https://github.com/user-attachments/assets/ae42ea3a-3553-480b-bb0b-12dd07c68c74" />
<img width="1869" height="644" alt="image" src="https://github.com/user-attachments/assets/a70135b0-1d1d-4cdb-9d2e-2bd870389b59" />
<img width="1884" height="717" alt="image" src="https://github.com/user-attachments/assets/ef4e0814-5a5d-4b54-92c9-258b0d70812d" />



---

## Design Decisions

### Why Tools Instead of Prompts?

| Aspect | Tool-based | Prompt-only |
|--------|-----------|-------------|
| **Determinism** | Tools produce identical output for identical input  testable, auditable | LLM may hallucinate prices, invent vendors, or vary formatting |
| **Separation of concerns** | Business logic in Python; reasoning in the LLM | Logic and data mixed into prompt text |
| **Testability** | Unit-test each tool with `pytest`; mock file I/O | No clean way to test "what the LLM would say" |
| **Auditability** | Every decision is logged to `audit_log.jsonl` | LLM reasoning is ephemeral unless logged manually |
| **Security** | Budget gates enforced in code  the LLM cannot bypass `place_order` | LLM could be prompt-injected to skip checks |

### Why Structured + Semantic Memory?

The agent combines two memory patterns:

1. **Structured memory** (`memory_store.json`)  Stores site rules and orders
   as strongly-typed JSON objects. This enables:
   - Exact retrieval by site name (no fuzzy search needed).
   - Deterministic budget comparisons against `approval_limit`.
   - Reliable blacklist enforcement (set membership, not similarity).

2. **Semantic memory** (LLM context)  The system prompt gives the LLM
   domain knowledge (procurement workflows, approval flows) so it can:
   - Extract intent from natural language ("cheapest vendor" → sort by price).
   - Handle multi-turn approval conversations.
   - Explain its reasoning to the user.

Together, structured data provides correctness guarantees while the LLM
provides flexibility and natural-language understanding.

### Why JSONL for Audit Logs?

- **Append-only**  Each tool call appends one line; no read-modify-write race.
- **Streamable**  Each line is self-contained JSON; partial reads are valid.
- **Human-readable**  Easy to `grep`, `jq`, or inspect in any text editor.

---

## Memory Architecture: Long-Term vs. Short-Term

The agent separates information into two distinct lifetimes:

```
┌──────────────────────────────────────────────────────────────┐
│                    SHORT-TERM CONTEXT                        │
│              (lives only during a conversation)              │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  ADK Session (in-memory / session.db)                   │ │
│  │  • Current conversation history (user + agent turns)    │ │
│  │  • Intermediate tool results (vendor lists, filter      │ │
│  │    output) the LLM references within the same session   │ │
│  │  • Approval state  which vendor is awaiting yes/no     │ │
│  └─────────────────────────────────────────────────────────┘ │
│  Lifetime: single session · Lost on restart                  │
└──────────────────────────────────────────────────────────────┘
                            │
              tools read/write persistent state
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                    LONG-TERM MEMORY                          │
│              (persists across all sessions)                  │
│                                                              │
│  ┌──────────────────┐   ┌────────────────┐  ┌──────────────┐ │
│  │ memory_store.json│   │ mock_vendors   │  │ audit_log    │ │
│  │                  │   │   .json        │  │   .jsonl     │ │
│  │ •Site rules      │   │                │  │              │ │
│  │  (approval limit,│   │ • Vendor       │  │ • Every      │ │
│  │   blacklists)    │   │   catalog      │  │   decision   │ │
│  │ •Confirmed orders│   │   (read-only)  │  │   logged     │ │
│  └──────────────────┘   └────────────────┘  └──────────────┘ │
│  Lifetime: permanent · Survives restarts                     │
└──────────────────────────────────────────────────────────────┘
```

**How they interact during a request:**

| Step | Short-term context | Long-term memory |
|------|-------------------|-----------------|
| User says "Order cement for Delhi-Site-7" | LLM parses intent → extracts site name, material |  |
| `retrieve_site_rules("Delhi-Site-7")` | LLM receives rules dict in session | Reads `memory_store.json` |
| `fetch_vendors("cement")` | LLM receives vendor list in session | Reads `mock_vendors.json` |
| `filter_vendors(...)` | LLM holds filtered result for follow-up turns | Logs rejections to `audit_log.jsonl` |
| `place_order(...)` → APPROVAL_REQUIRED | LLM remembers which vendor needs approval |  |
| User says "yes" → `confirm_order(...)` | Session ends | Order saved to `memory_store.json`; logged to `audit_log.jsonl` |

**Key design choice:** The LLM never reads `memory_store.json` or `audit_log.jsonl`
directly. It accesses long-term memory exclusively through tool calls, which means
every read and write is validated, logged, and testable.

---

## Vendor Filtering: Reasoning Chain

The `filter_vendors` function implements a strict, deterministic three-stage
pipeline. Every vendor passes through each gate in order  no gate can be
skipped, and the LLM cannot override the logic.

```
                        ┌─────────────────────┐
                        │   All vendors from   │
                        │  mock_vendors.json   │
                        │  (e.g. 11 vendors)   │
                        └──────────┬──────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │    GATE 1: Blacklist Check   │
                    │                              │
                    │  vendor.name ∈ blacklist?    │
                    │  (case-insensitive match)    │
                    └──────┬──────────────┬────────┘
                           │              │
                      ❌ YES             ✅ NO
                           │              │
                    ┌──────▼──────┐       │
                    │  REJECTED   │       │
                    │  reason:    │       │
                    │  "Blacklist │       │
                    │   for site" │       │
                    │  → logged   │       │
                    └─────────────┘       │
                                          │
                    ┌─────────────────────▼────────┐
                    │    GATE 2: Budget Check       │
                    │                               │
                    │  vendor.price > approval_limit│
                    └──────┬───────────────┬────────┘
                           │               │
                      ❌ YES              ✅ NO
                           │               │
                    ┌──────▼──────┐        │
                    │ OVER_BUDGET │        │
                    │ reason:     │        │
                    │ "Price ₹X   │        │
                    │  exceeds ₹Y"│        │
                    │ → logged    │        │
                    └─────────────┘        │
                                           │
                    ┌──────────────────────▼────────┐
                    │    GATE 3: Sort by Price       │
                    │                                │
                    │  Stable sort ascending by      │
                    │  price_per_100_bags_inr        │
                    └──────────────┬─────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │         ELIGIBLE             │
                    │  [cheapest, ..., priciest]   │
                    │  → LLM picks index 0         │
                    └──────────────────────────────┘
```

**Post-filtering decision logic (handled by the LLM + `place_order`):**

| Eligible? | Over-budget? | Action |
|-----------|-------------|--------|
| Yes | Any | Pick cheapest eligible → `place_order` → auto-approved |
| No | Yes | Pick cheapest over-budget → `place_order` → APPROVAL_REQUIRED |
| No | No (all blacklisted) | Return message: "All vendors blacklisted"  no order placed |

**Example trace** (Delhi-Site-7: limit ₹38,000, blacklist BadRock):

```
Input:  11 vendors from catalog
  │
  ├─ Gate 1 (blacklist): BadRock Cements → REJECTED
  │   Remaining: 10 vendors
  │
  ├─ Gate 2 (budget ₹38,000):
  │   SlowRock  ₹39,000 → OVER_BUDGET
  │   GoodRock  ₹45,000 → OVER_BUDGET
  │   ... (8 more over-budget)
  │   Remaining eligible: 0
  │
  ├─ Gate 3 (sort): n/a (no eligible vendors)
  │
  └─ Decision: All non-blacklisted vendors exceed budget.
     Cheapest over-budget: SlowRock at ₹39,000.
     → place_order triggers APPROVAL_REQUIRED
     → Overage: ₹1,000 (2.6%)  awaiting human approval.
```

Every rejection at each gate is logged to `audit_log.jsonl` with the vendor
name, price, and reason  providing a complete, auditable paper trail of why
each vendor was accepted or rejected.

---

## API Reference

### `store_site_rules(site_name, approval_limit, vendor_blacklist) → str`

Save procurement rules for a construction site.

| Parameter | Type | Description |
|-----------|------|-------------|
| `site_name` | `str` | Unique site identifier (e.g. `"Delhi-Site-7"`) |
| `approval_limit` | `int` | Max auto-approved order value in INR |
| `vendor_blacklist` | `list[str]` | Vendor names to exclude from orders |

**Returns:** Confirmation string or error message.

```python
>>> store_site_rules("Delhi-Site-7", 38000, ["BadRock Cements"])
"Rules stored for site 'Delhi-Site-7': approval_limit=₹38,000, vendor_blacklist=[BadRock Cements]."
```

---

### `retrieve_site_rules(site_name) → dict`

Look up stored rules for a site.

| Parameter | Type | Description |
|-----------|------|-------------|
| `site_name` | `str` | Site name to look up |

**Returns:** `{"approval_limit": int, "vendor_blacklist": list}` or `{"error": "..."}`.

```python
>>> retrieve_site_rules("Delhi-Site-7")
{"approval_limit": 38000, "vendor_blacklist": ["BadRock Cements"]}

>>> retrieve_site_rules("NonExistent")
{"error": "No rules found for 'NonExistent'. Please set rules first using store_site_rules."}
```

---

### `fetch_vendors(material) → list[dict]`

Return all vendors supplying a given material from the catalog.

| Parameter | Type | Description |
|-----------|------|-------------|
| `material` | `str` | Material category (e.g. `"cement"`)  case-insensitive |

**Returns:** List of vendor dicts, or `[]` if no match.

```python
>>> fetch_vendors("cement")
[{"id": "badrock", "name": "BadRock Cements", "price_per_100_bags_inr": 35000, ...}, ...]

>>> fetch_vendors("glass")
[]  # also logs a warning to audit_log.jsonl
```

---

### `filter_vendors(vendors, blacklist, budget, site_name="") → dict`

Filter vendors by blacklist and budget, sort eligible by price.

| Parameter | Type | Description |
|-----------|------|-------------|
| `vendors` | `list[dict]` | Vendor list from `fetch_vendors` |
| `blacklist` | `list[str]` | Names to exclude |
| `budget` | `int` | Max price per 100 bags (INR) |
| `site_name` | `str` | Optional  for audit log entries |

**Returns:**

```python
{
    "eligible":    [...],  # sorted cheapest-first
    "rejected":    [{"vendor": str, "reason": str, "price": int}, ...],
    "over_budget": [{"vendor": str, "reason": str, "price": int}, ...],
    "message":     "..."   # only present when eligible is empty
}
```

---

### `place_order(vendor_name, price, quantity, material, site_name, approval_limit) → str`

Place an order with budget-gate logic.

| Parameter | Type | Description |
|-----------|------|-------------|
| `vendor_name` | `str` | Selected vendor |
| `price` | `int` | Total cost in INR |
| `quantity` | `int` | Number of bags |
| `material` | `str` | Material type |
| `site_name` | `str` | Construction site |
| `approval_limit` | `int` | Auto-approval ceiling (INR) |

**Returns:**
- `"ORDER_CONFIRMED: ..."` if `price <= approval_limit`
- `"APPROVAL_REQUIRED\n..."` if `price > approval_limit`

---

### `confirm_order(vendor_name, price, quantity, material, site_name) → str`

Finalise a previously-flagged over-budget order after human approval.

| Parameter | Type | Description |
|-----------|------|-------------|
| `vendor_name` | `str` | Vendor from the original `place_order` |
| `price` | `int` | Total cost in INR |
| `quantity` | `int` | Number of bags |
| `material` | `str` | Material type |
| `site_name` | `str` | Construction site |

**Returns:** `"ORDER_CONFIRMED: ... (Human-approved over-budget order.)"`

---

### Audit Utilities  (`memory.py`)

| Function | Description |
|----------|-------------|
| `log_decision(event_type, site_name, details)` | Append entry to `audit_log.jsonl` |
| `read_audit_log() → list[dict]` | Read all entries chronologically |
| `clear_audit_log()` | Delete the audit log file |

---

## Example Usage Scenarios

### Scenario 1: Simple Order (Within Budget)

```
User: Set rules for Chennai-Site-3: approval limit ₹50,000, no blacklist.
Agent: ✓ Rules stored for site 'Chennai-Site-3': approval_limit=₹50,000

User: Order 100 bags of cement for Chennai-Site-3.
Agent: [calls retrieve_site_rules → fetch_vendors → filter_vendors]
       BadRock Cements is cheapest at ₹35,000 (within ₹50,000 limit).
       ORDER_CONFIRMED: 100 bags cement from BadRock Cements at ₹35,000.
```

### Scenario 2: Over-Budget with Approval

```
User: Set rules for Delhi-Site-7: limit ₹38,000, blacklist BadRock Cements.
Agent: ✓ Rules stored.

User: Order 500 bags of cement for Delhi-Site-7.
Agent: [filters vendors]
       BadRock → rejected (blacklisted)
       SlowRock (₹39,000) → over budget
       GoodRock (₹45,000) → over budget
       Cheapest non-blacklisted: SlowRock at ₹39,000.

       APPROVAL REQUIRED:
         Vendor: SlowRock Cements
         Cost: ₹39,000
         Limit: ₹38,000
         Overage: ₹1,000 (2.6%)
       Approve this order?

User: Yes, approve it.
Agent: ORDER_CONFIRMED: 500 bags cement from SlowRock at ₹39,000 (human-approved).
```

### Scenario 3: All Vendors Blacklisted

```
User: Set rules for Pune-Site-2: limit ₹100,000,
      blacklist BadRock, GoodRock, SlowRock.

User: Order cement for Pune-Site-2.
Agent: All 3 vendors are blacklisted for this site.
       No order can be placed. Update the blacklist or add new vendors.
```

---

## Running the Agent

### Prerequisites

- **Python 3.10+**
- **Google ADK** and **Gemini API key**

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/<your-username>/intelligent-procurement-agent.git
cd intelligent-procurement-agent

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 3. Install dependencies
pip install -r procurement_agent/requirements.txt

# 4. Configure API key
cp procurement_agent/.env.example procurement_agent/.env
# Edit .env and add your Gemini API key
# Get a free key at: https://aistudio.google.com/apikey
```

### Run Modes

| Command | Description |
|---------|-------------|
| `adk run procurement_agent` | Interactive terminal chat (LLM-powered) |
| `adk web` | Browser-based dev UI |
| `python cli.py` | LLM-powered CLI with colored tool tracing |
| `python cli.py --offline` | Offline CLI (calls tools directly, no LLM) |
| `python agent.py --demo` | Offline demo scenario (no LLM) |
| `python agent.py --test` | Edge-case tests (no LLM) |
| `pytest test_tools.py -v` | Run unit tests |

---

## Project Layout

```
procurement_agent/
├── __init__.py          # Package init; ADK entry point
├── .env                 # API key config (git-ignored  see .env.example)
├── .env.example         # Template showing required env vars
│
│  ── Layered Architecture ─────────────────────────────
├── agent.py             # AGENT LAYER  ADK Agent definition only
├── tools.py             # TOOL LAYER   pure business logic (no I/O)
├── memory.py            # MEMORY LAYER  all file I/O & persistence
│
│  ── Offline / Testing ─────────────────────────────────
├── demo.py              # Offline demo + edge-case test harness
├── cli.py               # Interactive CLI (LLM chat + offline mode)
├── test_tools.py        # Pytest unit tests
│
│  ── Data Files ────────────────────────────────────────
├── mock_vendors.json    # Vendor catalog (read-only)
├── memory_store.json    # Runtime state: site rules + orders (git-ignored)
├── audit_log.jsonl      # Append-only decision audit trail (git-ignored)
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

> **Note:** `memory_store.json` and `audit_log.jsonl` are generated at
> runtime and excluded from version control via `.gitignore`.

### Import Dependency Graph

```
memory.py          ← (no project imports  leaf module)
    ↑
tools.py           ← imports memory
    ↑
agent.py           ← imports tools
demo.py            ← imports tools + memory
cli.py             ← imports tools + memory + agent
test_tools.py      ← imports tools + memory (patches memory)
```

---

## Vendor Catalog (`mock_vendors.json`)

The vendor catalog is loaded dynamically from `mock_vendors.json` at runtime.
Tools never use hardcoded vendor data. Current catalog (sorted by price):

| Vendor | Price (per 100 bags) | Delivery | In Stock | Notes |
|--------|---------------------|----------|----------|-------|
| BadRock Cements | ₹35,000 | 5 days | Yes | Budget option; standard quality |
| SlowRock Cements | ₹39,000 | 7 days | Yes | Mid-range price; slower delivery |
| ValueCrest Cements | ₹41,000 | 6 days | No | Budget-friendly but currently out of stock |
| MonsoonMart Cement Supply | ₹44,500 | 9 days | No | Seasonal supplier; long lead times |
| GoodRock Cements | ₹45,000 | 3 days | Yes | Premium quality; faster delivery |
| EcoCem Builders Cement | ₹46,500 | 4 days | Yes | Low-carbon option; consistent setting |
| PrimeMix Cement Co. | ₹47,000 | 2 days | Yes | Premium blend; very fast delivery |
| DuraBuild Cement | ₹48,000 | 5 days | Yes | High strength; good for structural work |
| SitePrime Cement | ₹49,500 | 3 days | Yes | Trusted contractor brand; stable supply |
| RapidSet Cement | ₹52,000 | 1 day | Yes | Rush orders; highest cost |
| UltraRock Cementworks | ₹56,000 | 2 days | Yes | Top-tier; priced for critical projects |

> **Note:** To add or modify vendors, edit `mock_vendors.json` directly. All
> tools read from this file at runtime  no code changes required.

---

