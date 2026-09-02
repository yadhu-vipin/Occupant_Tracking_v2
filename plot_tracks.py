"""
plot_tracks.py
==============
Generates a visual track plot for each occupant in a building.

Layout:
  - X-axis : Zones  (zT -> z1 -> z2 -> z3 -> z4)
  - Y-axis : Time   (09:00 at top -> 17:00 at bottom, 24-min steps)
  - Face thumbnails placed at each (zone, time) detection point
  - Red  line = Estimated track (L-shaped step: vertical in zone, horizontal on transition)
  - Blue line = Ground Truth (same display)
  - State S0 : All persons start at zT at 09:00

Usage:
  python plot_tracks.py --building B0
  python plot_tracks.py --building B0 --person B0_Person_1
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import numpy as np
from PIL import Image

# ─── Config ──────────────────────────────────────────────────────────────────

ZONE_ORDER  = ['zT_{bid}', 'z1_{bid}', 'z2_{bid}', 'z3_{bid}', 'z4_{bid}']

TIME_START  = datetime.strptime("09:00", "%H:%M")
TIME_STEP_M = 24       # minutes per synthetic step
MAX_EVENTS  = 20       # cap at 20 detections per person

THUMB_ZOOM  = 0.48     # face image zoom
FIG_W       = 16
FIG_H       = 13

COLOR_EST   = '#D0312D'   # red  – estimated
COLOR_GT    = '#1A56DB'   # blue – ground truth
GRID_COLOR  = '#D1D5DB'
BG_COLOR    = '#FFFFFF'
AXIS_COLOR  = '#374151'


# ─── Helpers ─────────────────────────────────────────────────────────────────

def synthetic_times(n):
    """09:00, 09:24, 09:48, … for n steps (step 0 = S0 at 09:00)."""
    return [TIME_START + timedelta(minutes=i * TIME_STEP_M) for i in range(n)]


def load_history(building_folder, occupant_id):
    path = os.path.join(building_folder, f"occupant_{occupant_id}_history.json")
    if not os.path.exists(path):
        return []
    with open(path, 'r') as f:
        return json.load(f)


def load_face_sequence(gallery_dir, occupant_id, count):
    """Return up to `count` face images (cycling through ref/test images)."""
    occ_dir = os.path.join(gallery_dir, occupant_id)
    if not os.path.exists(occ_dir):
        return [None] * count
    imgs = []
    for prefix in ['ref', 'test']:
        for i in range(1, 21):
            p = os.path.join(occ_dir, f"{prefix}_{i:02d}.jpg")
            if os.path.exists(p):
                imgs.append(np.array(Image.open(p).convert('RGB')))
    if not imgs:
        return [None] * count
    return [imgs[i % len(imgs)] for i in range(count)]


def zone_x(zone, bid):
    order = [z.replace('{bid}', bid) for z in ZONE_ORDER]
    try:
        return order.index(zone)
    except ValueError:
        return 0


def build_step_path(xs, ys):
    """
    Convert a sequence of (x, y) points into an L-shaped step path:
      - Horizontal segment: move to new x at old y  (zone transition)
      - Vertical segment  : drop to new y at new x  (time within zone)
    This eliminates diagonal zig-zag lines.
    """
    if not xs:
        return [], []
    px, py = [xs[0]], [ys[0]]
    for i in range(1, len(xs)):
        if xs[i] != xs[i - 1]:
            # Zone changed: go horizontal first (at the old time row)
            px.append(xs[i])
            py.append(ys[i - 1])
        # Then drop vertically to the new time row
        px.append(xs[i])
        py.append(ys[i])
    return px, py


def load_manifest(gallery_dir):
    p = os.path.join(gallery_dir, 'manifest.json')
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


# ─── Core Plot ────────────────────────────────────────────────────────────────

def plot_occupant_track(occupant_id, events, gallery_dir, building_id, output_path):
    bid    = building_id
    zones  = [z.replace('{bid}', bid) for z in ZONE_ORDER]
    labels = [z.replace(f'_{bid}', '') for z in zones]   # 'zT', 'z1', …

    # ── Trim & assign synthetic timestamps ───────────────────────────────────
    evs       = events[:MAX_EVENTS]
    n         = len(evs)
    all_times = synthetic_times(n + 1)   # index 0 = S0

    # Detection points: [(time, zone), ...]  index 0 = S0 (zT, 09:00)
    pts = [(all_times[0], f'zT_{bid}')]
    for i, ev in enumerate(evs):
        pts.append((all_times[i + 1], ev.get('zone', f'zT_{bid}')))

    xs = [zone_x(z, bid) for _, z in pts]
    ys = list(range(len(pts)))           # integer y: 0 = top, n = bottom

    # ── Face images ──────────────────────────────────────────────────────────
    faces = load_face_sequence(gallery_dir, occupant_id, len(pts))

    # ── Figure setup ─────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    n_zones = len(zones)

    # Vertical grid lines (one per zone column)
    for xi in range(n_zones):
        ax.axvline(xi, color=GRID_COLOR, linewidth=0.9, zorder=0)

    # ── Step-path lines ───────────────────────────────────────────────────────
    px, py = build_step_path(xs, ys)

    # Ground truth (blue, drawn first / behind)
    ax.plot(px, py, color=COLOR_GT,  linewidth=2.2, zorder=1, solid_capstyle='round')
    # Estimated   (red, drawn on top)
    ax.plot(px, py, color=COLOR_EST, linewidth=2.2, zorder=2, solid_capstyle='round', alpha=0.85)

    # ── Face thumbnails ───────────────────────────────────────────────────────
    for i, ((t, z), face_img) in enumerate(zip(pts, faces)):
        xi = zone_x(z, bid)
        yi = i

        if face_img is not None:
            pil   = Image.fromarray(face_img).resize((96, 96), Image.LANCZOS)
            imgob = OffsetImage(np.array(pil), zoom=THUMB_ZOOM)
            imgob.image.axes = ax
            ab = AnnotationBbox(
                imgob, (xi, yi),
                frameon=True,
                bboxprops=dict(
                    boxstyle="square,pad=0.04",
                    edgecolor=COLOR_GT if i == 0 else COLOR_EST,
                    linewidth=2.5,
                    facecolor='none'
                ),
                zorder=5
            )
            ax.add_artist(ab)
        else:
            ax.scatter(xi, yi, s=90, color=COLOR_EST, zorder=5)

        # Label S0 next to the first thumbnail
        if i == 0:
            ax.text(xi - 0.18, yi, 'S0', fontsize=8, color='#555555',
                    va='center', ha='right', fontweight='bold', zorder=6)

    # ── Axes ─────────────────────────────────────────────────────────────────
    margin = 0.55
    ax.set_xlim(-margin, n_zones - 1 + margin)
    ax.set_ylim(len(pts) - 1 + margin, -margin)   # inverted: 09:00 at top

    # X-axis (zones) at top
    ax.set_xticks(range(n_zones))
    ax.set_xticklabels(labels, fontsize=12, fontweight='bold', color=AXIS_COLOR)
    ax.xaxis.set_label_position('top')
    ax.xaxis.tick_top()

    # Y-axis (times)
    ax.set_yticks(range(len(pts)))
    ax.set_yticklabels([t.strftime("%H:%M") for t in all_times],
                       fontsize=9, color='#4B5563')

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis='x', length=0, pad=10)
    ax.tick_params(axis='y', length=0, pad=8)

    # Subtle horizontal dashed grid at each time row
    for yi in range(len(pts)):
        ax.axhline(yi, color='#F3F4F6', linewidth=0.6, zorder=0)

    # ── Title & Legend ────────────────────────────────────────────────────────
    manifest  = load_manifest(gallery_dir)
    lfw_name  = manifest.get(occupant_id, "")
    title     = f"{occupant_id}"
    if lfw_name:
        title += f"  [LFW: {lfw_name}]"
    title += f"  -  Building {building_id} Track"

    ax.set_title(title, fontsize=13, fontweight='bold', pad=16,
                 loc='left', color='#111827')

    est_patch = mpatches.Patch(color=COLOR_EST, label='Estimated Track')
    gt_patch  = mpatches.Patch(color=COLOR_GT,  label='Ground Truth')
    ax.legend(handles=[est_patch, gt_patch], loc='upper right',
              fontsize=10, framealpha=0.95, edgecolor='#E5E7EB',
              bbox_to_anchor=(1.0, 1.13))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight',
                facecolor=BG_COLOR, edgecolor='none')
    plt.close('all')
    print(f"  Saved -> {output_path}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Plot occupant tracks for a building.")
    parser.add_argument('--building', default='B0', help='Building ID (default: B0)')
    parser.add_argument('--person',   default=None, help='Specific occupant ID (optional)')
    args = parser.parse_args()

    bid = args.building

    script_dir      = os.path.dirname(os.path.abspath(__file__))
    building_folder = os.path.join(script_dir, f'Building_{bid}')
    gallery_dir     = os.path.join(script_dir, 'face_gallery')

    if not os.path.exists(building_folder):
        print(f"ERROR: Building folder not found: {building_folder}")
        sys.exit(1)

    if not os.path.exists(gallery_dir):
        print(f"WARNING: face_gallery/ not found. Run generate_db.py first.")

    # Auto-detect occupants from history files
    if args.person:
        occupants = [args.person]
    else:
        occupants = []
        for fname in sorted(os.listdir(building_folder)):
            if fname.startswith('occupant_') and fname.endswith('_history.json'):
                occ_id = fname.replace('occupant_', '').replace('_history.json', '')
                occupants.append(occ_id)

    if not occupants:
        print(f"No occupant history files found in {building_folder}")
        sys.exit(1)

    print(f"\nGenerating track plots for Building {bid}...")
    print(f"  Occupants : {', '.join(occupants)}")
    print(f"  Gallery   : {gallery_dir}\n")

    for occ_id in occupants:
        events = load_history(building_folder, occ_id)
        if not events:
            print(f"  [{occ_id}] No history found - skipping.")
            continue
        out_path = os.path.join(building_folder, f"{occ_id}_track_visual.png")
        print(f"  [{occ_id}] {len(events)} events -> plotting...")
        plot_occupant_track(
            occupant_id=occ_id,
            events=events,
            gallery_dir=gallery_dir,
            building_id=bid,
            output_path=out_path
        )

    print(f"\nAll plots saved to: {building_folder}")


if __name__ == "__main__":
    main()
