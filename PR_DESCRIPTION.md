# Strategy Persistence & Live Timing Sync

## Summary
This PR implements strategy persistence to localStorage and ensures the Live Timing page automatically syncs with loaded strategies. When a strategy is loaded on the Strategy page, it is now persisted to localStorage and automatically reflected on the Live Timing page.

## Changes Made

### 1. Strategy Persistence (Already Implemented)
- **File**: `static/js/live_timing_strategies.js`
- **Function**: `exposeStints` (lines 81-104)
- **What it does**:
  - Sets `window.currentStrategyStints` to the loaded stints array
  - Persists strategy to localStorage key `lt_current_strategy` with shape:
    ```javascript
    {
      meta: {
        name: strategyDetail.name || strategyDetail.title || null,
        id: strategyDetail.id || null,
        ts: Date.now()
      },
      stints: stints
    }
    ```
  - Dispatches CustomEvent `lt:strategy:updated` for same-tab synchronization
  - Wrapped in try/catch for browser compatibility and safety

### 2. Script Includes
- **File**: `templates/race_strategy.html`
  - Added `live_timing_strategies.js` include (line 854)
  - Already had `live_timing_sync.js` include (line 855)

- **File**: `templates/live_timing.html`
  - Added `live_timing_strategies.js` include (line 159)
  - Added `live_timing_sync.js` include (line 160)

### 3. Sync Mechanism (Already Implemented)
- **File**: `static/js/live_timing_sync.js`
- Automatically listens for localStorage changes and custom events
- Populates `#tblRelay` table on Live Timing page with strategy stints
- Recalculates totals to match strategy page

## How to Test

### Prerequisites
- Disable browser cache and perform a full reload (Ctrl+F5 or Cmd+Shift+R)

### Test Steps

1. **Load a Strategy**
   - Open the Race Strategy page (`/race_strategy` or equivalent)
   - Select a saved strategy from the dropdown
   - Click "Cargar" (Load) button

2. **Verify localStorage Persistence**
   - Open DevTools (F12)
   - Navigate to Application → Local Storage
   - Verify key `lt_current_strategy` exists
   - Verify it contains:
     - `meta` object with `name`, `id`, and `ts` fields
     - `stints` array with strategy data (start, end, laps, fuel, pit, notes)

3. **Verify Live Timing Sync**
   - Open/Reload the Live Timing page in the same browser
   - The `#tblRelay` table should automatically populate with:
     - All stints from the loaded strategy
     - Correct start/end times
     - Correct laps and fuel values
     - Pit times and notes
   - Footer totals should match the strategy page totals

4. **Test Real-time Updates**
   - With both Strategy and Live Timing pages open (separate tabs)
   - Load a different strategy on the Strategy page
   - The Live Timing page should automatically update within ~200ms

5. **Test Cross-tab Sync**
   - Open Live Timing page in a new tab
   - In the original tab, load a new strategy
   - The new tab should automatically update via storage event listener

## How to Revert

To revert this change:
```bash
git revert <this-commit-sha>
```

Or manually:
1. Remove the script includes from `templates/race_strategy.html` (line 854)
2. Remove the script includes from `templates/live_timing.html` (lines 159-160)
3. The localStorage persistence code in `exposeStints` is idempotent and safe to leave in place

## Notes

- All changes are minimal and reversible
- Existing functionality is preserved
- localStorage writes are wrapped in try/catch for safety
- The sync script uses defensive programming for older browsers
- No changes to existing telemetry or business logic
- Scripts are loaded in correct order (strategies before sync)

## Files Changed
- `templates/race_strategy.html` (+1 line)
- `templates/live_timing.html` (+3 lines)
- `static/js/live_timing_strategies.js` (no changes - already implemented)
- `static/js/live_timing_sync.js` (no changes - already implemented)

## Target Branch
- **main** (or default branch)
