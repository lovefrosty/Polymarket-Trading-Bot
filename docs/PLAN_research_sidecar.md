# AutoResearchClaw Integration — Research-Driven Alpha Sidecar

> Saved for later implementation (after Kalshi exchange integration).

## Context
The bot currently trades 15-minute binary markets (BTC/ETH/SOL/XRP up/down) using microstructure signals only (book imbalance, fill asymmetry, vol regime, complement arb, depth ratio, spot momentum). The user wants to add LLM-powered research as a directional alpha source — research a market's question, distill a probability estimate, and bias quotes toward the researched edge across many markets.

AutoResearchClaw is a 23-stage autonomous research pipeline (topic -> literature -> hypotheses -> experiments -> synthesis -> paper). We adapt it into a **research sidecar** that produces probability estimates the bot consumes as alpha.

## Architecture: Two-Process Design

```
+-----------------------------------+     +------------------------------------+
| RESEARCH SIDECAR (batch)          |     | TRADING BOT (real-time)            |
|                                   |     |                                    |
| 1. Poll active Polymarket         |     | BookManager -> FlowFilter ->       |
|    markets (Gamma API)            |     |   AlphaOverlayManager ->           |
| 2. For each market question:      |     |     (6 existing signals +          |
|    run AutoResearchClaw ->        |     |      NEW: ResearchAlpha) ->        |
|    extract probability (0-1)      |  -->|   QuoteEngine -> Sizing ->         |
| 3. Write results to              |     |   RiskManager -> Execution          |
|    research_signals.json          |     |                                    |
|                                   |     | ResearchAlpha reads                |
| Runs every N minutes per market   |     | research_signals.json each cycle   |
+-----------------------------------+     +------------------------------------+
```

**Why two processes?** Research is slow (30-120s per market via LLM API) and expensive. The bot cycles every ~500ms. Decoupling means the bot never blocks on research, and research can run on its own schedule.

## Signal Flow: Research -> Quote Bias

```
Research probability: P_research = 0.70  (e.g., "70% chance BTC goes up")
Market mid-price:     P_market   = 0.52  (YES token at 0.52)
Divergence:           delta      = P_research - P_market = +0.18

-> If delta > threshold (e.g., 0.05):
    extra_skew_ticks = +N  (lean toward buying YES / selling NO)
    spread_multiplier stays at 1.0 (don't widen -- we have conviction)
-> If delta < -threshold:
    extra_skew_ticks = -N  (lean toward selling YES / buying NO)
-> If |delta| < threshold:
    no signal (research agrees with market)
```

## Files to Create/Modify

### 1. NEW: `core_mm/research_alpha.py` -- Signal consumer

Follows the `SpotMomentum` pattern (`core_mm/spot_momentum.py`): a lightweight class that reads external data and outputs skew ticks.

```python
class ResearchAlpha:
    """Reads LLM research probability estimates and outputs directional skew.

    Parameters:
        signal_file: Path to research_signals.json (written by sidecar)
        max_skew_ticks: Maximum skew from research signal (default: 2)
        activation_delta: Minimum |P_research - P_market| to activate (default: 0.05)
        full_scale_delta: Delta at which max skew is reached (default: 0.20)
        max_age_secs: Ignore research older than this (default: 600)
        confidence_threshold: Minimum confidence to use signal (default: 0.5)

    Methods:
        get_skew(token_id, market_mid) -> ResearchSignal
            Reads cached signals, computes divergence, returns skew ticks

    Signal file format (research_signals.json):
        {
            "condition_id_123": {
                "question": "Will BTC be above $85,000 at 3:00 PM?",
                "probability": 0.70,
                "confidence": 0.8,
                "reasoning_summary": "Strong support at $84,500...",
                "updated_at": "2026-03-21T14:30:00Z",
                "token_id_yes": "abc123",
                "token_id_no": "def456"
            }
        }
```

### 2. MODIFY: `core_mm/alpha_overlay.py`
- Add `research_alpha_bps` diagnostic field to `AlphaSignal`
- Add `ResearchAlpha` as 7th signal in `AlphaOverlayManager.__init__()`
- Wire `research_alpha.get_skew()` into `get_signal()` -> adds to `extra_skew_ticks`
- New constructor params: `research_signal_file`, `research_max_skew_ticks`, `research_activation_delta`

### 3. MODIFY: `core_mm/runner.py` -- Pass token->market mapping
- In `_run_single_market_cycle()`, after getting alpha overlays, call `alpha_mgr.update_research(token_id, mid_price)` so the research alpha can compare its probability to the live market price

### 4. NEW: `scripts/run_research_sidecar.py` -- Standalone research process

```python
"""Research sidecar: polls Polymarket markets and runs LLM research.

Usage:
    python scripts/run_research_sidecar.py \
        --output-file tmp/research_signals.json \
        --interval-secs 300 \
        --symbols BTC,ETH,SOL,XRP \
        --model claude-sonnet-4-6
"""
```

Loop:
1. Fetch active markets from Gamma API (reuse `MarketSelector._fetch_gamma_events()`)
2. For each market, extract the question (e.g., "Will BTC be above $85,000?")
3. Call Claude API with a structured prompt:
   - System: "You are a prediction market analyst. Given the question and current market data, estimate the probability of YES outcome."
   - Include: current price, recent price action, time to expiry, market context
   - Output: `{"probability": 0.70, "confidence": 0.8, "reasoning": "..."}`
4. Write all results to `research_signals.json` (atomic write)
5. Sleep `interval_secs`, repeat

### 5. MODIFY: `scripts/run_core_mm.py` -- Add `--research-signals` flag
- New CLI arg: `--research-signals <path>` (default: None = no research alpha)
- Pass path through to `CoreMMRunner` -> `AlphaOverlayManager` -> `ResearchAlpha`

### 6. NEW: `tests/core_mm/test_research_alpha.py` -- Unit tests
- Test divergence -> skew mapping
- Test stale signal expiry
- Test confidence threshold gating
- Test missing/empty signal file gracefully returns 0 skew

## Research Prompt Design (for sidecar)

```
You are analyzing a Polymarket prediction market.

MARKET QUESTION: {question}
CURRENT YES PRICE: {yes_mid} (market-implied probability)
TIME TO EXPIRY: {minutes_remaining} minutes
UNDERLYING ASSET: {symbol}
CURRENT SPOT PRICE: {spot_price} (from reference feed)

Based on:
1. Current market conditions and price levels
2. Recent price momentum and volatility
3. Time remaining until resolution
4. Historical patterns for similar markets

Estimate the TRUE probability of the YES outcome.

Respond with ONLY valid JSON:
{
    "probability": <float 0.0-1.0>,
    "confidence": <float 0.0-1.0>,
    "reasoning": "<one sentence>"
}
```

## Scope Boundaries

- Does NOT replace the market maker -- adds a directional bias on top of existing MM logic
- Does NOT auto-size based on research conviction (sizing stays in `sizing.py`)
- Does NOT run the full 23-stage AutoResearchClaw pipeline -- uses a simplified LLM prompt for speed (full pipeline can be swapped in later)
- Does NOT affect markets where no research signal exists -- ResearchAlpha returns 0 skew

## Implementation Steps

1. Create `core_mm/research_alpha.py` -- `ResearchAlpha` class + `ResearchSignal` dataclass
2. Modify `core_mm/alpha_overlay.py` -- add `research_alpha_bps` to `AlphaSignal`, wire `ResearchAlpha` into manager
3. Modify `core_mm/runner.py` -- pass market mid to research alpha update
4. Create `scripts/run_research_sidecar.py` -- standalone LLM research loop
5. Modify `scripts/run_core_mm.py` -- add `--research-signals` CLI arg
6. Create `tests/core_mm/test_research_alpha.py` -- unit tests
7. Run full test suite: `python3 -m pytest tests/core_mm/ -q`

## Verification

1. **Unit tests**: `python3 -m pytest tests/core_mm/test_research_alpha.py -v`
2. **Sidecar standalone**: `python3 scripts/run_research_sidecar.py --symbols BTC --interval-secs 60 --output-file tmp/research_signals.json`
3. **Bot with research**: `python3 scripts/run_core_mm.py --mode PAPER --research-signals tmp/research_signals.json --symbols BTC --duration-secs 300`
4. **All tests pass**: `python3 -m pytest tests/core_mm/ -q`
