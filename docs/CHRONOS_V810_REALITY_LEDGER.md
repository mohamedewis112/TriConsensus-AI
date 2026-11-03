# CHRONOS V8.10 — Reality Ledger

The Reality Ledger pre-registers every research forecast before its horizon resolves.

Each forecast receipt hashes:
- timestamp and horizon;
- model/world version;
- direction probabilities and quantiles;
- future diversity;
- the exact input snapshot hash;
- the previous receipt hash.

The hash chain makes hindsight edits detectable. A receipt cannot be resolved before
its horizon end and can only be resolved once.

Resolved forecasts produce directional accuracy, interval coverage, Brier score, MAE,
RMSE and expected calibration error. This is the basis for deciding whether CHRONOS
actually predicts reality rather than merely producing compelling simulations.

The ledger is research-only and carries no execution authority.
