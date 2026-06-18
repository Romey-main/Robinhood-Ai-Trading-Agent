"""Render an equity-curve + drawdown comparison chart from backtest results.

The drawdown math is pure std-lib (and unit-tested); only ``render`` pulls in
matplotlib, lazily, so the rest of the package stays dependency-free.
"""

from __future__ import annotations


def drawdown_series(equity: list) -> list:
    """Underwater curve: (date, drawdown_from_running_peak) for an equity path."""
    peak = float("-inf")
    out = []
    for d, v in equity:
        peak = max(peak, v)
        out.append((d, (v / peak - 1.0) if peak > 0 else 0.0))
    return out


def render(results: dict, out_path: str, title: str = "Backtest tearsheet",
           subtitle: str = "") -> str:
    """Overlay each result's equity curve (top) and drawdown (bottom) into a PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ImportError("tearsheet needs matplotlib: pip install matplotlib") from exc
    from datetime import date as _date

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for name, res in results.items():
        if not res.equity:
            continue
        xs = [_date.fromisoformat(d) for d, _ in res.equity]
        s = res.stats
        ax1.plot(xs, [v for _, v in res.equity], linewidth=1.6,
                 label=f"{name}  (Sharpe {s.get('sharpe')}, "
                       f"vol {s.get('ann_vol', 0)*100:.0f}%, "
                       f"DD {s.get('max_drawdown', 0)*100:.0f}%)")
        dd = drawdown_series(res.equity)
        ax2.fill_between([_date.fromisoformat(d) for d, _ in dd],
                         [v * 100 for _, v in dd], 0, alpha=0.25)

    fig.suptitle(title, fontsize=13, fontweight="bold", y=0.98)
    if subtitle:
        ax1.set_title(subtitle, fontsize=8.5, color="crimson", pad=6)
    ax1.set_ylabel("growth of $1")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.grid(alpha=0.3)
    ax2.set_ylabel("drawdown %")
    ax2.axhline(0, color="black", linewidth=0.6)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path
