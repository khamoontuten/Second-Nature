"""Static Matplotlib figures for the PDF report (generated from stored results only)."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BLUE, RED, GREEN, GREY, AMBER = "#2563eb", "#dc2626", "#16a34a", "#6b7280", "#d97706"
plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def bar(labels, values, title, ylabel, colors=None, baseline=None) -> bytes:
    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    ax.bar(labels, values, color=colors or [GREY] + [BLUE] * (len(values) - 1))
    if baseline is not None:
        ax.axhline(baseline, color=RED, lw=0.8, ls="--", label="baseline")
        ax.legend(frameon=False)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=30)
    return _png(fig)


def monthly_compare(months, base, design, title, ylabel, label_b="Baseline (S0)", label_d="Recommended") -> bytes:
    fig, ax = plt.subplots(figsize=(6.5, 2.4))
    x = np.arange(len(months))
    ax.bar(x - 0.2, base, 0.4, color=GREY, label=label_b)
    ax.bar(x + 0.2, design, 0.4, color=BLUE, label=label_d)
    ax.set_xticks(x, [pd.Timestamp(2001, int(m), 1).strftime("%b") for m in months])
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False, ncol=2)
    return _png(fig)


def mode_heatmap(hourly: pd.DataFrame, title: str) -> bytes:
    order = ["mechanical", "mech_precool", "econ_direct_fan", "econ_natural", "econ_eahe", "econ_indirect", "iec_wet", "dec_direct"]
    code = hourly["mode"].map({m: i for i, m in enumerate(order)}).to_numpy(dtype=float)
    code = np.where(hourly["partial"].to_numpy() & (code > 0), code + 0.5, code)
    n = len(code) // 24 * 24
    grid = code[:n].reshape(-1, 24).T
    fig, ax = plt.subplots(figsize=(6.5, 2.2))
    cmap = matplotlib.colors.ListedColormap([RED, AMBER, "#60a5fa", GREEN, "#a16207", "#0ea5e9", "#14b8a6", "#8b5cf6"])
    ax.imshow(np.floor(grid), aspect="auto", cmap=cmap, vmin=0, vmax=len(order) - 1, interpolation="nearest")
    ax.set_xlabel("day of record")
    ax.set_ylabel("hour (UTC)")
    ax.set_title(title)
    used = sorted(set(np.floor(code[~np.isnan(code)]).astype(int)))
    handles = [matplotlib.patches.Patch(color=cmap(i), label=order[i]) for i in used]
    ax.legend(handles=handles, fontsize=6, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.35), ncol=4)
    return _png(fig)


def duration_curve(series: dict[str, np.ndarray], title: str, ylabel: str) -> bytes:
    fig, ax = plt.subplots(figsize=(6.5, 2.4))
    for (label, v), c in zip(series.items(), [GREY, BLUE, GREEN, AMBER], strict=False):
        ax.plot(np.sort(v)[::-1], label=label, color=c, lw=1)
    ax.set_xlabel("hours (sorted)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(frameon=False)
    return _png(fig)


def tornado(rows: list[dict], title: str) -> bytes:
    rows = rows[:10][::-1]
    fig, ax = plt.subplots(figsize=(6.5, 0.3 * len(rows) + 0.9))
    c = rows[0]["low_saving"] if rows else 0
    for i, r in enumerate(rows):
        lo, hi = r["low_saving"] or 0, r["high_saving"] or 0
        ax.barh(i, hi - lo, left=lo, color=BLUE if hi > lo else RED)
    ax.set_yticks(range(len(rows)), [r["label"][:40] for r in rows])
    ax.set_xlabel("annual facility-energy saving vs baseline (MWh)")
    ax.set_title(title)
    _ = c
    return _png(fig)


def temperature_profile(t_local: pd.Series, title: str) -> bytes:
    fig, ax = plt.subplots(figsize=(6.5, 2.2))
    daily = t_local.resample("D")
    ax.fill_between(daily.min().index, daily.min(), daily.max(), color="#93c5fd", alpha=0.6, label="daily range")
    ax.plot(daily.mean().index, daily.mean(), color=BLUE, lw=0.8, label="daily mean")
    ax.set_ylabel("dry-bulb (°C)")
    ax.set_title(title)
    ax.legend(frameon=False)
    return _png(fig)
