"""Render the same real example as state_event_example.py to a PNG image,
styled after Menon et al.'s (S, E, Delta) figure -- three tables:
State (S) before Delta | Event (E) | State (S) after Delta.

Every number is the same real data state_event_example.py prints (see that
module's docstring for exactly where each one comes from); this module only
adds the matplotlib rendering on top.

Run from the buildings_prototype/ directory (needs matplotlib):

    python -m dsts.examples.render_state_event_image
    python -m dsts.examples.render_state_event_image --event-id E000494
    python -m dsts.examples.render_state_event_image --candidates 5 --out out.png
"""
from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from dsts.examples.state_event_example import ZONES, gather

HEADER_BG = "#4472C4"
HEADER_FG = "white"
ROW_BG_EVEN = "#D9E2F3"
ROW_BG_ODD = "white"
IDENTIFIED_BG = "#FFF2CC"
TEXT_FG = "#1A1A1A"


def _draw_table(ax, headers, rows, row_labels, bold_mask=None, highlight_row=None, col_width=1.0, label_width=1.3):
    """rows: list[list[str]] of formatted cell text, one row per occupant."""
    n_rows = len(rows)
    n_cols = len(headers)
    total_w = label_width + col_width * n_cols
    row_h = 1.0

    ax.set_xlim(0, total_w)
    ax.set_ylim(0, row_h * (n_rows + 1))
    ax.invert_yaxis()
    ax.axis("off")

    # header row
    ax.add_patch(Rectangle((0, 0), label_width, row_h, facecolor=HEADER_BG, edgecolor="white"))
    ax.text(label_width / 2, row_h / 2, "Occ", ha="center", va="center", color=HEADER_FG, fontweight="bold", fontsize=11)
    for c, h in enumerate(headers):
        x = label_width + c * col_width
        ax.add_patch(Rectangle((x, 0), col_width, row_h, facecolor=HEADER_BG, edgecolor="white"))
        ax.text(x + col_width / 2, row_h / 2, h, ha="center", va="center", color=HEADER_FG, fontweight="bold", fontsize=11)

    # body rows
    for r, (label, row) in enumerate(zip(row_labels, rows)):
        y = row_h * (r + 1)
        is_highlight = highlight_row is not None and r == highlight_row
        bg = IDENTIFIED_BG if is_highlight else (ROW_BG_EVEN if r % 2 == 0 else ROW_BG_ODD)

        ax.add_patch(Rectangle((0, y), label_width, row_h, facecolor=HEADER_BG, edgecolor="white"))
        ax.text(label_width / 2, y + row_h / 2, label, ha="center", va="center", color=HEADER_FG, fontweight="bold", fontsize=9.5)

        for c, val in enumerate(row):
            x = label_width + c * col_width
            ax.add_patch(Rectangle((x, y), col_width, row_h, facecolor=bg, edgecolor="white"))
            bold = bool(bold_mask and bold_mask[r][c])
            ax.text(
                x + col_width / 2, y + row_h / 2, val,
                ha="center", va="center", color=TEXT_FG,
                fontweight="bold" if bold else "normal", fontsize=10,
            )


def render(data: dict, out_path: str) -> None:
    occupants = data["occupants"]
    before, after = data["before"], data["after"]
    occupant_probs, identified, zone = data["occupant_probs"], data["identified"], data["zone"]

    def state_rows(state_dict):
        rows, bold_mask = [], []
        for occ in occupants:
            values = state_dict[occ]
            if values is None:
                rows.append(["--"] * len(ZONES))
                bold_mask.append([False] * len(ZONES))
                continue
            best_zone = max(values, key=values.get)
            row, mask = [], []
            for z in ZONES:
                v = values.get(z, 0.0)
                row.append(f"{v:.3f}")
                mask.append(z == best_zone and v > 0)
            rows.append(row)
            bold_mask.append(mask)
        return rows, bold_mask

    before_rows, before_bold = state_rows(before)
    after_rows, after_bold = state_rows(after)
    event_rows = [[f"{occupant_probs[occ]:.3f}"] for occ in occupants]
    event_bold = [[occ == identified] for occ in occupants]
    highlight_row = occupants.index(identified) if identified in occupants else None

    fig = plt.figure(figsize=(20, 1.1 * (len(occupants) + 1) + 1.6))
    gs = fig.add_gridspec(1, 3, width_ratios=[len(ZONES) + 1.3, 2.0, len(ZONES) + 1.3], wspace=0.08)

    ax_before = fig.add_subplot(gs[0, 0])
    ax_event = fig.add_subplot(gs[0, 1])
    ax_after = fig.add_subplot(gs[0, 2])

    _draw_table(ax_before, list(ZONES), before_rows, occupants, before_bold, highlight_row, label_width=1.7)
    _draw_table(ax_event, [f"z{zone[1:]}" if zone != "zT" else "zT"], event_rows, occupants, event_bold, highlight_row, col_width=1.6, label_width=1.7)
    _draw_table(ax_after, list(ZONES), after_rows, occupants, after_bold, highlight_row, label_width=1.7)

    fig.text(0.24, 0.06, r"$\Delta$ : State", ha="center", fontsize=13, color="#C00000", fontweight="bold")
    fig.text(0.50, 0.06, r"$\times$ Event $\rightarrow$", ha="center", fontsize=13, color="#C00000", fontweight="bold")
    fig.text(0.78, 0.06, "State", ha="center", fontsize=13, color="#C00000", fontweight="bold")

    event = data["event"]
    fig.suptitle(
        f"Real DSTS/BSTS example — {data['building']}, zone {zone}, "
        f"event {event['event_id']} (t={data['time']})  —  "
        f"identified: {identified} (p={occupant_probs[identified]:.3f})",
        fontsize=13, y=0.98,
    )
    fig.text(
        0.5, 0.015,
        "Source: dsts/output/phase4_results.json + nodes/<building>/state/visitor.db "
        "— no invented numbers. Bold = each occupant's highest-probability zone.",
        ha="center", fontsize=9, color="#555555",
    )

    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    print(f"wrote {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event-id", default=None)
    ap.add_argument("--candidates", type=int, default=5)
    ap.add_argument("--out", default="state_event_example.png")
    args = ap.parse_args()

    data = gather(args.event_id, args.candidates)
    render(data, args.out)


if __name__ == "__main__":
    main()
