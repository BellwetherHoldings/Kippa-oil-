# Why the signal log produced one trade in nine days

**Research note · 2026-08-19 · governed by docs/010_Backtesting.md · research only, never feeds production**

## The question

Nine days of live signal logging (144 rows, Aug 10 → Aug 19) produced **one**
simulated trade: long Aug 11 @ $82.15, out Aug 13 @ $81.45 on
`score_crossed_zero`, **−0.85%, −$700/lot, 0W/1L**.

The obvious hypothesis is that the |0.20| entry gate is too tight. It is not.

## 1. Gate sweep — measured on the panel, not on the log

`oil backtest gates` sweeps seven gates across **680 out-of-sample weeks**
with walk-forward expanding weights. Gate *performance* is measured only
here; the live log contributes sample counts and nothing else.

| gate | trades | hit | mean | PF | p | **late-regime hit** | live n |
|---|---|---|---|---|---|---|---|
| 0.05 | 632 | 55.5% | 0.9% | 1.495 | 0.0115 | **47.7%** | 2 |
| 0.10 | 568 | 55.6% | 0.9% | 1.483 | 0.027 | **47.3%** | 1 |
| 0.15 | 528 | 55.1% | 0.9% | 1.482 | 0.038 | **47.7%** | 1 |
| **0.20** | 481 | 53.2% | 0.8% | 1.389 | 0.117 | **45.5%** | 1 |
| 0.25 | 417 | 53.7% | 1.0% | 1.504 | 0.073 | **45.9%** | 1 |
| 0.30 | 361 | 54.0% | 1.1% | 1.561 | 0.092 | **46.5%** | 1 |
| 0.40 | 256 | 50.8% | 1.2% | 1.571 | 0.183 | **43.1%** | 0 |

**Two findings, both negative.**

**(a) No gate survives multiple-testing correction.** Seven gates is seven
shots at a 5% false positive, so the threshold is Bonferroni p < 0.0071.
The best gate (0.05) prints p = 0.0115 and misses. Picking 0.05 off this
table because it has the lowest p-value is exactly how backtests get fitted.

**(b) Every gate is below a coin flip in the regime we actually trade.**
The late split (2022-03-04 → present, 228 weeks) runs **43.1% to 47.7%**
hit rate. Not one gate clears 50%. The full-sample edge lives entirely in
the pre-2022 era. This corroborates the standing replay finding
(late IC −0.0047) rather than adding to it.

**Lowering the gate buys neither edge nor throughput: 0.05 would have
produced two live trades instead of one.**

## 2. The actual constraint: a saturated component

Splitting the live log at the Aug 12 invalidation:

| window | n | min | max | **range** | sd |
|---|---|---|---|---|---|
| pre Aug 13 | 49 | +0.079 | +0.453 | 0.374 | 0.0957 |
| **post Aug 13** | 95 | **−0.066** | **+0.078** | **0.144** | **0.0363** |

**Post-Aug-13 the composite cannot reach ±0.20 at any point — its entire
range is 0.144 wide.** Rows clearing each gate, post-Aug-13:

- gate 0.05 → 37 of 95
- gate 0.10 → **0 of 95**
- gate 0.15 → **0 of 95**
- gate 0.20 → **0 of 95**

### Why

`composite_signal.py:157`:

```python
inv_signal = _clip(-float(latest["surprise_z"]) / 3.0)
```

Any |z| ≥ 3.0 saturates. The Aug 7 print was **z = +3.41**, so
`inventory_surprise` has read **exactly −1.000 in all 95 rows** while
carrying a **31% effective weight**.

Remove that one component and recompute the same 95 rows:

| | min | max | rows clearing 0.20 |
|---|---|---|---|
| composite as published | −0.066 | +0.078 | 0 of 95 |
| **composite ex-inventory** | **+0.351** | **+0.684** | **95 of 95** |

**One clipped component is holding the entire board at zero.** The other
five are collectively and persistently bullish; the pin is arithmetic.

### What it takes to unstick

Solving the latest row for the inventory signal that would put the
composite at +0.20:

- required signal **−0.181** (currently −1.000)
- implied **surprise_z ≈ +0.54** (currently +3.41)

A short position is **arithmetically unreachable**: composite = −0.20 would
need z ≈ +4.45, beyond the clip.

## 3. What I am NOT concluding

**I am not proposing to drop or down-weight inventory to manufacture trades.**
Inventory is the *only* component with a defensible IC (+0.123 against
momentum's +0.013), and the replay's own weight recommendation is
**91% inventory / 9% momentum**. Removing the best-evidenced input to
generate activity would be the worst possible response to a low trade count.

The defect is **scaling, not weighting**. A z of 3.41 and a z of 8.0 both
map to −1.000, so the signal carries no information above z = 3 and cannot
respond to partial mean-reversion. A saturating transform (tanh) would keep
the same ordering, keep inventory dominant, and let the board move again as
the surprise decays — but changing the transform changes every historical
composite, so it is a reviewed change under the human-oversight invariant,
not something to slip in.

## 4. Standing decision

- **Gate stays at |0.20|.** No evidence supports moving it; a change would
  be a throughput decision dressed as an edge decision.
- **`oil backtest gates` and `oil backtest sim` run daily** and their
  results go in the morning report, including when the answer is "still one
  trade" — that silence is itself the finding, and I sat on it for nine days.
- **Trade generation is blocked until the WPSR mean-reverts** below roughly
  z = +0.5. The Wednesday print is the unlock, not a gate parameter.

## 5. Honest limits

- The panel replays a **reduced composite** — only `inventory_surprise`,
  `price_momentum`, `vol_regime`. Geopolitical risk, supply-chain stress,
  chokepoint capacity and CFTC positioning are not historically
  reconstructible. Gate conclusions describe the reduced signal.
- One live trade is a correlated draw inside a single regime. It is not
  evidence of anything and is not treated as such here.
- Slippage of $0.03/bbl each way is an assumption. Real fills at 61%
  annualised vol will be worse.
