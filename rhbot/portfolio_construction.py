"""Shared portfolio construction: weighting scheme + concentration caps.

Turns a strategy's *selected* names into final weights, applying, in order:
  1. a weighting scheme  — equal, or inverse-volatility (risk-balanced);
  2. optional volatility targeting — scale gross exposure down in high-vol
     regimes so the basket's (conservatively estimated) vol <= a target;
  3. concentration caps   — per-name and per-sector, via iterative water-filling.

Anything that cannot be placed under the caps stays in CASH rather than
concentrating — same fail-closed philosophy as the data-quality layer.
"""

from __future__ import annotations

import math

ANNUALIZE = math.sqrt(252.0)


def _sector_totals(weights: dict, sectors: dict) -> dict:
    tot: dict = {}
    for s, w in weights.items():
        sec = sectors.get(s, "Unknown")
        tot[sec] = tot.get(sec, 0.0) + w
    return tot


def _base_weights(selected, target_n, panel, asof, cfg) -> dict:
    """Pre-cap weights summing to ``min(1, len/target_n)`` (cash if under-filled)."""
    if not selected or not target_n:
        return {}
    gross = min(1.0, len(selected) / target_n)
    if getattr(cfg, "weight_scheme", "equal") == "inverse_vol":
        inv = {s: panel.trailing_vol(s, asof, cfg.vol_lookback_days) for s in selected}
        inv = {s: (1.0 / v if v and v > 0 else None) for s, v in inv.items()}
        known = sorted(x for x in inv.values() if x is not None)
        fill = known[len(known) // 2] if known else 1.0           # median, don't drop
        inv = {s: (x if x is not None else fill) for s, x in inv.items()}
        tot = sum(inv.values()) or 1.0
        return {s: gross * inv[s] / tot for s in selected}
    w = gross / len(selected)
    return {s: w for s in selected}


def _apply_caps(weights: dict, target_gross: float, cfg, sectors: dict | None) -> dict:
    """Water-fill to honor per-name and per-sector caps; overflow -> cash."""
    name_cap = cfg.max_name_weight
    sec_cap = getattr(cfg, "max_sector_weight", 1.0) if sectors else 1.0
    w = dict(weights)
    syms = list(w)
    for _ in range(500):
        changed = False
        for s in syms:                                   # clamp names
            if w[s] > name_cap + 1e-12:
                w[s] = name_cap
                changed = True
        if sectors:                                      # clamp sectors
            for sec, tot in _sector_totals(w, sectors).items():
                if tot > sec_cap + 1e-12:
                    scale = sec_cap / tot
                    for s in syms:
                        if sectors.get(s, "Unknown") == sec:
                            w[s] *= scale
                    changed = True
        deficit = target_gross - sum(w.values())         # redistribute to names with room
        if deficit > 1e-9:
            sec_tot = _sector_totals(w, sectors) if sectors else {}
            room = {}
            for s in syms:
                sec = sectors.get(s, "Unknown") if sectors else "Unknown"
                sec_room = (sec_cap - sec_tot.get(sec, 0.0)) if sectors else 1.0
                r = min(name_cap - w[s], max(0.0, sec_room))
                if r > 1e-12:
                    room[s] = r
            tot_room = sum(room.values())
            if tot_room <= 1e-12:
                break                                    # nowhere to place -> cash
            add = min(deficit, tot_room)
            for s, r in room.items():
                w[s] += add * (r / tot_room)
            changed = True
        elif not changed:
            break
    return {s: round(v, 6) for s, v in w.items() if v > 1e-9}


def construct(selected, target_n, panel, asof, cfg, sectors=None):
    """Return ``(weights, notes)`` for ``selected`` under the cfg's scheme + caps."""
    notes: list = []
    w = _base_weights(selected, target_n, panel, asof, cfg)
    if not w:
        return {}, notes
    target_gross = sum(w.values())
    if len(selected) < target_n:
        notes.append(f"only {len(selected)}/{target_n} qualified -> "
                     f"{(1 - target_gross) * 100:.0f}% cash")

    target_vol = getattr(cfg, "target_annual_vol", 0.0)
    if target_vol and target_vol > 0:
        vols = {s: panel.trailing_vol(s, asof, cfg.vol_lookback_days) for s in w}
        port = sum(w[s] * vols[s] * ANNUALIZE for s in w if vols[s])  # conservative (rho=1)
        if port > target_vol:
            scale = target_vol / port
            w = {s: v * scale for s, v in w.items()}
            target_gross *= scale
            notes.append(f"vol-target: basket ~{port*100:.0f}% > "
                         f"{target_vol*100:.0f}% -> {scale*100:.0f}% invested")

    w = _apply_caps(w, target_gross, cfg, sectors)
    if sum(w.values()) < target_gross - 1e-6:
        cap_txt = f"name<={cfg.max_name_weight*100:.0f}%"
        if sectors:
            cap_txt += f", sector<={cfg.max_sector_weight*100:.0f}%"
        notes.append(f"caps ({cap_txt}) left {(1 - sum(w.values()))*100:.0f}% in cash")
    if sectors and w:
        sec = max(_sector_totals(w, sectors).items(), key=lambda x: x[1])
        notes.append(f"top sector: {sec[0]} {sec[1]*100:.0f}%")
    return w, notes
