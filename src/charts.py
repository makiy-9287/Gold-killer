"""Clean candlestick charts (indicators නැහැ — body/wick පැහැදිලිව).

TradingView screenshot ගන්න GitHub Actions වල බැහැ (login/bot-detection/ToS).
ඒ නිසා OHLCV data වලින්ම matplotlib candlestick image හදනවා — AI එකට ඕනේ ඒකම.
X axis එකේ weekend gaps නැහැ (candle index වලින් ඇඳීම)."""
from __future__ import annotations

import os
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

UP = "#089981"
DOWN = "#f23645"
BG = "#ffffff"
GRID = "#e6e9ee"
TXT = "#1f2937"

TF_LABEL = {"D1": "1D", "H4": "4H", "H1": "1H", "M15": "15m", "M5": "5m"}


def _tick_fmt(tf: str) -> str:
    return "%d %b" if tf == "D1" else ("%d %b %H:%M" if tf in ("H4", "H1") else "%d %H:%M")


def render_chart(
    df: pd.DataFrame,
    tf: str,
    path: str,
    tz: str,
    n: int = 120,
    levels: Iterable[tuple[float, str, str]] | None = None,
    zone: tuple[float, float] | None = None,
    note: str = "",
) -> str:
    d = df.tail(n)
    x = np.arange(len(d))
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    up = c >= o
    colors = np.where(up, UP, DOWN)

    fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    ax.vlines(x, l, h, colors=colors, linewidth=1.3, zorder=2)
    span = float(h.max() - l.min()) or 1.0
    body = np.maximum(np.abs(c - o), span * 0.0015)  # doji වලටත් පේන්න
    ax.bar(x, body, bottom=np.minimum(o, c), width=0.72, color=colors, edgecolor=colors, linewidth=0.6, zorder=3)

    lo, hi = float(l.min()), float(h.max())
    if zone:
        ax.axhspan(zone[0], zone[1], color="#2962ff", alpha=0.14, zorder=1)
        lo, hi = min(lo, zone[0]), max(hi, zone[1])
    for price, label, color in levels or []:
        ax.axhline(price, color=color, linestyle="--", linewidth=1.3, zorder=4)
        ax.text(0.5, price, f" {label} {price:.2f}", color=color, fontsize=11, va="bottom", fontweight="bold", zorder=5)
        lo, hi = min(lo, price), max(hi, price)
    pad = (hi - lo) * 0.04
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, len(d) + 6)

    last = float(c[-1])
    ax.axhline(last, color="#6b7280", linestyle=":", linewidth=1.0, zorder=1)
    ax.text(len(d) + 5.5, last, f"{last:.2f}", color="white", fontsize=11, ha="right", va="center",
            bbox=dict(boxstyle="square,pad=0.25", fc=UP if up[-1] else DOWN, ec="none"), zorder=6)

    # x labels (local time)
    k = max(1, len(d) // 10)
    ticks = list(range(0, len(d), k))
    local = d.index.tz_convert(tz)
    ax.set_xticks(ticks)
    ax.set_xticklabels([local[i].strftime(_tick_fmt(tf)) for i in ticks], fontsize=10, color=TXT)

    ax.yaxis.tick_right()
    ax.tick_params(axis="y", labelsize=11, colors=TXT)
    ax.grid(True, color=GRID, linewidth=0.8, zorder=0)
    for s in ("top", "left"):
        ax.spines[s].set_visible(False)
    for s in ("bottom", "right"):
        ax.spines[s].set_color(GRID)

    t_last = local[-1].strftime("%a %d %b %H:%M")
    ax.set_title(
        f"XAUUSD  ·  {TF_LABEL[tf]}  ·  {len(d)} candles  ·  last candle {t_last} ({tz}){('  ·  ' + note) if note else ''}",
        loc="left", fontsize=13, color=TXT, fontweight="bold",
    )
    fig.tight_layout()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig.savefig(path, dpi=100, facecolor=BG)
    plt.close(fig)
    return path


def render_all(frames: dict[str, pd.DataFrame], out_dir: str, tz: str, n: int) -> dict[str, str]:
    paths = {}
    for tf in ["D1", "H4", "H1", "M15", "M5"]:
        paths[tf] = render_chart(frames[tf], tf, os.path.join(out_dir, f"XAUUSD_{tf}.png"), tz, n)
    return paths
