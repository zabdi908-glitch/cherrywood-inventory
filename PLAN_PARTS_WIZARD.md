# Cherrywood Parts Wizard & Bug Fix Plan

**Project:** cherrywood-inventory
**Date:** 2026-08-04
**Stock ID format:** `CH-00222` (prefix + zero-padded numeric suffix)
**Goal:** Fix database-lock bugs first, then rebuild the parts-add experience

---

## Phase A — Bug Fixes (DO THESE FIRST)

### A1. Disable opportunistic backup trigger (highest impact)
- [x] `app.py`: comment out `@app.before_request` hook `run_opportunistic_maintenance()` (lines ~815-842)
- [x] `app.py`: comment out `data_retention.maybe_purge()` call inside same hook
- **WHY:** these run mid-request, hold DB locks, collide with uploads
- **TEST:** start local dev server, verify no errors, confirm `/admin` loads

### A2. Harden backup.py for safe 2 AM cron-only execution
- [x] `backup.py`: add `timeout=20` + retry loop to `create_backup_copy()`
- [x] `backup.py`: add lock file so two backups can't run simultaneously
- **WHY:** prevents the 2 AM cron from colliding with uploads if it overlaps
- **TEST:** `python test_a2.py` — both tests pass (backup copy with timeout, lock prevents concurrent runs)

### A3. Duplicate stock_id = friendly error, never a raw crash
- [x] `parts_agent.py`: add pre-check in `add_part()`: `SELECT id FROM parts WHERE stock_id = ?`
- [x] Return `{"success": False, "error": "Stock ID CH-00223 is already in use by another part"}`
- **WHY:** fixes the user's 223/223 scenario with a clear message

### A4. Harden DB layer
- [x] `app.py`: add `timeout=20` to `get_db()`
- [x] `app.py`: enable WAL mode in `init_db()`
- **WHY:** readers never block writers; requests wait 20s instead of crashing

---

## Phase B — eBay-Style Parts Add Wizard (after Phase A is verified)

### B1. Wizard UI scaffold
- [x] New template: `templates/parts_add_wizard.html` (Tailwind, big fields, big buttons)
- [x] New route: `/parts/add-wizard` in `app.py`
- [x] Button on `parts_index.html`: **"➕ Add Part (Simple Wizard)"** (keep old `/parts/add` as "Advanced")
- **WHY:** older staff need fewer fields, clearer flow, no typing

### B2. 4-step flow
- [x] **Step 1 — "What is it?"**: Stock ID field + Part Name + Category + drag-drop photo upload
- [x] **Step 2 — "What car is it from?"**: Make, Model, Year, Registration inputs
- [x] **Step 3 — "Price & stock"**: big price input + big Available/Reserved/Sold buttons + location
- [x] **Step 4 — Review & Publish**: one big orange "Publish Part" button
- **WHY:** guided flow for fast data entry

### B3. Duplicate-proof stock ID built into wizard
- [x] `parts_agent.py`: `next_stock_id()` → parses last `CH-XXXXX` suffix
- [x] Step 1 pre-fills stock ID with next free ID
- [x] Live "✓ Available" / "✗ Already used" hint via `/api/check-stock-id` as they type
- **WHY:** staff literally can't accidentally reuse a number

### B4. "Add another part from same car" speed button
- [x] After successful publish, redirect to `/parts` with `?make=X&model=X&year=X&registration=X`
- [x] `parts_index.html` shows orange "Add another part from same car" button when prefill data present
- [x] Button links back to `/parts/add-wizard` with those query params
- [x] Wizard GET handler reads `request.args` and passes `prefill` dict to pre-populate make/model/year/reg
- **WHY:** common workflow: stripping one car, adding 10+ parts fast

---

## How to resume this plan in a new chat

1. Open `PLAN_PARTS_WIZARD.md`
2. Check which checkboxes are `[x]` done
3. Start with the first `[ ]` item
4. Update checkboxes + `CHANGED:` notes after each step

---

## Completed steps log

### Phase A1–A4 — Bug Fixes
**Status:** `[x]` done  
**Commits:** `94f8224`, `d92fcb5`, `02d278d`  
**Changed files:** `app.py`, `backup.py`, `parts_agent.py`  
**What changed:** Disabled opportunistic backup trigger, hardened DB layer with WAL + timeout, duplicate stock_id returns friendly error  
**Test result:** `test_a3.py` passes

### Phase B1–B3 — Wizard scaffold, 4-step flow, duplicate-proof stock ID
**Status:** `[x]` done  
**Commits:** `94f8224` (merge) and earlier  
**Changed files:** `templates/parts_add_wizard.html`, `app.py`, `parts_agent.py`, `templates/parts_index.html`  
**What changed:** Wizard template with 4-step flow, `/parts/add-wizard` route, `next_stock_id()` auto-generator, live stock ID availability API  
**Test result:** Manual — wizard loads and stock ID pre-fills correctly

### Phase B4 — "Add another part from same car" speed button
**Status:** `[x]` done  
**Commit:** `6fe29bf`  
**Changed files:** `app.py`, `templates/parts_add_wizard.html`, `templates/parts_index.html`  
**What changed:** Success redirect carries car query params; parts_index shows orange speed button; wizard pre-fills fields from URL  
**Test result:** `test_a3.py` passes
