"""Expected-COP model for an air-source heat pump (reusable by S03 comfort, energy, insights).

COP of an air-source heat pump falls naturally when it is cold outside and when the supply
temperature is high (Carnot). A *degraded* unit (e.g. refrigerant loss) delivers less heat per
kW electric than a healthy unit would at the same outdoor/supply temperature. So we model

    COP_expected = b0 + b1 * outdoor_c + b2 * supply_temp_c

with a robust (MAD-trimmed) least-squares fit on *normal* operation only (status running,
meaningful electric load), refitted every day on all history strictly before that day
(expanding window -> causal). Degradation is a persistent negative relative residual
    heat_output / (COP_expected * electric_kw) - 1
aggregated as a load-weighted rolling ratio so low-load (noisy) intervals count less.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

NORMAL_STATUS = "running"
MIN_ELECTRIC_KW = 1.0       # COP undefined / very noisy when the compressor barely runs
TRIM_MAD = 4.0              # robust fit: drop points > 4 robust sigma, refit
MIN_FIT_POINTS = 96         # >= 1 day of normal 15-min operation before we trust a fit
MIN_OUTDOOR_SPAN_C = 3.0    # need some spread in outdoor temperature to estimate the slope


@dataclass
class ExpectedCOP:
    coef: np.ndarray            # [b0, b_outdoor, b_supply] (b_supply = 0 if supply unknown)
    n: int
    outdoor_range: tuple[float, float]
    resid_sigma: float          # robust sigma of relative residual on the fit data

    def predict(self, outdoor_c, supply_temp_c=None):
        outdoor_c = np.asarray(outdoor_c, dtype=float)
        sup = np.zeros_like(outdoor_c) if supply_temp_c is None else np.asarray(supply_temp_c, dtype=float)
        sup = np.where(np.isnan(sup), 0.0, sup) if self.coef[2] == 0 else sup
        return self.coef[0] + self.coef[1] * outdoor_c + self.coef[2] * sup

    def extrapolating(self, outdoor_c: float, margin_c: float = 2.0) -> bool:
        lo, hi = self.outdoor_range
        return outdoor_c < lo - margin_c or outdoor_c > hi + margin_c

    @classmethod
    def fit(cls, outdoor_c, supply_temp_c, cop) -> "ExpectedCOP | None":
        o = np.asarray(outdoor_c, dtype=float)
        s = np.asarray(supply_temp_c, dtype=float)
        y = np.asarray(cop, dtype=float)
        ok = np.isfinite(o) & np.isfinite(y)
        use_supply = bool(np.isfinite(s[ok]).mean() > 0.9) if ok.any() else False
        if use_supply:
            ok &= np.isfinite(s)
        o, s, y = o[ok], s[ok], y[ok]
        if len(y) < MIN_FIT_POINTS or np.ptp(o) < MIN_OUTDOOR_SPAN_C:
            return None
        cols = [np.ones_like(o), o] + ([s] if use_supply and np.ptp(s) > 0.5 else [])
        X = np.column_stack(cols)
        keep = np.ones(len(y), dtype=bool)
        for _ in range(3):
            b, *_ = np.linalg.lstsq(X[keep], y[keep], rcond=None)
            r = y - X @ b
            mad = np.median(np.abs(r[keep] - np.median(r[keep])))
            sigma = 1.4826 * mad if mad > 0 else np.std(r[keep])
            new_keep = np.abs(r) <= TRIM_MAD * max(sigma, 1e-6)
            if new_keep.sum() < MIN_FIT_POINTS or (new_keep == keep).all():
                break
            keep = new_keep
        coef = np.zeros(3)
        coef[: len(b)] = b
        pred = X @ b
        rel = y[keep] / pred[keep] - 1
        rmad = np.median(np.abs(rel - np.median(rel)))
        return cls(coef=coef, n=int(keep.sum()), outdoor_range=(float(o.min()), float(o.max())),
                   resid_sigma=float(1.4826 * rmad))


def heatpump_frame(data, asset_id: str = "HP1") -> pd.DataFrame:
    """15-min frame: heat/electric/supply/status of the heat pump + measured outdoor temperature + COP + normal flag."""
    hp = data.equipment_wide(asset_id).copy()
    for col in ("electric_kw", "heat_output_kw", "supply_temp_c"):
        if col not in hp:
            hp[col] = np.nan
    if "status" not in hp:
        hp["status"] = np.nan
    w = data.weather.drop_duplicates("timestamp_utc").set_index("timestamp_utc")["outdoor_c"]
    hp["outdoor_c"] = w.reindex(hp.index)
    elec = hp["electric_kw"].where(hp["electric_kw"] > 0)
    cop = hp["heat_output_kw"] / elec
    if "cop" in hp:  # use the reported COP where the unit provides it
        cop = hp["cop"].where(hp["cop"].notna(), cop)
    hp["cop_obs"] = cop
    hp["normal"] = ((hp["status"] == NORMAL_STATUS) & (hp["electric_kw"] >= MIN_ELECTRIC_KW)
                    & hp["cop_obs"].notna() & np.isfinite(hp["cop_obs"]) & hp["outdoor_c"].notna())
    return hp


def causal_expected_cop(hp: pd.DataFrame, refit: str = "D") -> tuple[pd.Series, dict]:
    """Expected COP per interval from a model refitted at each period start on history before it.

    Returns (expected series, {period_start: ExpectedCOP}). Periods without enough history -> NaN.
    """
    expected = pd.Series(np.nan, index=hp.index)
    models: dict[pd.Timestamp, ExpectedCOP] = {}
    if hp.empty:
        return expected, models
    normal = hp[hp["normal"]]
    starts = pd.date_range(hp.index[0].floor(refit), hp.index[-1], freq=refit)
    idx = hp.index
    nidx = normal.index
    o_all, s_all = hp["outdoor_c"].to_numpy(), hp["supply_temp_c"].to_numpy()
    for p0 in starts:
        k = nidx.searchsorted(p0, side="left")
        if k < MIN_FIT_POINTS:
            continue
        hist = normal.iloc[:k]
        m = ExpectedCOP.fit(hist["outdoor_c"], hist["supply_temp_c"], hist["cop_obs"])
        if m is None:
            continue
        models[p0] = m
        a, b = idx.searchsorted(p0), idx.searchsorted(p0 + pd.tseries.frequencies.to_offset(refit))
        expected.iloc[a:b] = m.predict(o_all[a:b], s_all[a:b])
    return expected, models


def performance_residual(hp: pd.DataFrame, expected: pd.Series, window: int = 8, min_periods: int = 6) -> pd.Series:
    """Load-weighted rolling relative residual: sum(heat) / sum(expected_cop * electric) - 1 over normal intervals.

    Right-aligned rolling window (causal). Negative = less heat per kW than a healthy unit at that weather.
    """
    ok = hp["normal"] & expected.notna() & (expected > 0)
    heat = hp["heat_output_kw"].where(ok)
    exp_heat = (expected * hp["electric_kw"]).where(ok)
    num = heat.rolling(window, min_periods=min_periods).sum()
    den = exp_heat.rolling(window, min_periods=min_periods).sum()
    return (num / den - 1).where(den > 0)


def recent_residual_sigma(resid: pd.Series, window: str = "14D", lag: int = 8, min_periods: int = 96) -> pd.Series:
    """Robust sigma (MAD x 1.4826) of the performance residual over the previous ``window``, ending ``lag``
    intervals before t (so the window being scored is excluded). Causal.

    The fit sigma of :func:`causal_expected_cop` mixes all history; if the data starts outside the heating
    season it is inflated by summer/DHW operation, and a winter degradation stays inside -k*sigma. The recent
    sigma reflects the current operating regime."""
    r = resid.shift(lag)
    med = r.rolling(window, min_periods=min_periods).median()
    return (r - med).abs().rolling(window, min_periods=min_periods).median() * 1.4826

