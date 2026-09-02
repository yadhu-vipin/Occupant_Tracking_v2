"""
evaluate_metrics.py
===================
Calculates Precision and Recall for the face recognition system across 
varying distance thresholds (theta). Generates a PR Curve and identifies
the optimal threshold where Precision and Recall are balanced.
"""

import json
import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from face_recognition import face_distance

# ─── Config ──────────────────────────────────────────────────────────────────

REF_DB_PATH  = 'reference_db.json'
TEST_DB_PATH = 'test_db.json'
OUTPUT_PLOT  = 'precision_recall_curve.png'

# Range of distance thresholds to test
THRESHOLDS = np.linspace(0.1, 1.0, 50)


# ─── Load Data ───────────────────────────────────────────────────────────────

def load_data():
    if not os.path.exists(REF_DB_PATH) or not os.path.exists(TEST_DB_PATH):
        print("ERROR: Database files not found. Run generate_db.py first.")
        return None, None
        
    with open(REF_DB_PATH, 'r') as f:
        ref_db = json.load(f)
    with open(TEST_DB_PATH, 'r') as f:
        test_db = json.load(f)
        
    # Convert lists to numpy arrays
    ref_data = {k: [np.array(e) for e in v] for k, v in ref_db.items()}
    test_data = {k: [np.array(e) for e in v] for k, v in test_db.items()}
    
    return ref_data, test_data


# ─── Evaluation ───────────────────────────────────────────────────────────────

def evaluate():
    ref_data, test_data = load_data()
    if not ref_data: return

    # Flatten reference DB into [encodings] and [labels]
    all_ref_encs = []
    all_ref_labels = []
    for label, encs in ref_data.items():
        all_ref_encs.extend(encs)
        all_ref_labels.extend([label] * len(encs))

    precisions = []
    recalls = []
    f1_scores = []

    print(f"Evaluating {len(THRESHOLDS)} thresholds...")

    for theta in THRESHOLDS:
        tp = 0  # Correct identity and dist <= theta
        fp = 0  # Incorrect identity and dist <= theta
        fn = 0  # Correct identity missed because dist > theta

        # Iterate through every test image
        for true_label, encs in test_data.items():
            for test_enc in encs:
                # Calculate distances to all reference images
                distances = face_distance(all_ref_encs, test_enc)
                min_idx = np.argmin(distances)
                min_dist = distances[min_idx]
                pred_label = all_ref_labels[min_idx]

                if min_dist <= theta:
                    if pred_label == true_label:
                        tp += 1
                    else:
                        fp += 1
                else:
                    # System didn't find a match within threshold
                    # Since it IS actually a person in the DB, this is a FN
                    fn += 1

        # Calculate metrics
        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        precisions.append(precision)
        recalls.append(recall)
        f1_scores.append(f1)

    # ─── Find Optimal Theta ──────────────────────────────────────────────────
    
    # Method: Maximize F1 Score
    best_idx = np.argmax(f1_scores)
    best_theta = THRESHOLDS[best_idx]
    best_p = precisions[best_idx]
    best_r = recalls[best_idx]

    print(f"\nResults:")
    print(f"  Optimal Theta (theta): {best_theta:.3f}")
    print(f"  Precision    : {best_p:.4f}")
    print(f"  Recall       : {best_r:.4f}")
    print(f"  F1 Score     : {f1_scores[best_idx]:.4f}")

    # ─── Plotting ─────────────────────────────────────────────────────────────
    
    plt.figure(figsize=(10, 7))
    plt.plot(recalls, precisions, color='#2563EB', linewidth=3, label='PR Curve')
    plt.scatter(best_r, best_p, color='#E8392A', s=100, zorder=5, 
                label=f'Optimal theta={best_theta:.2f}')
    
    # Styling
    plt.title('Precision-Recall Curve (BrotherEye Face ID)', fontsize=14, fontweight='bold', pad=20)
    plt.xlabel('Recall (True Positive Rate)', fontsize=12)
    plt.ylabel('Precision (Positive Predictive Value)', fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.xlim(0, 1.05)
    plt.ylim(0, 1.05)
    plt.legend(loc='lower left')

    # Annotation for the optimal point
    plt.annotate(f'Best theta={best_theta:.2f}\n(P={best_p:.2f}, R={best_r:.2f})',
                 xy=(best_r, best_p), xytext=(best_r-0.2, best_p-0.2),
                 arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=6))

    plt.tight_layout()
    plt.savefig(OUTPUT_PLOT, dpi=150)
    print(f"\nPlot saved to: {os.path.abspath(OUTPUT_PLOT)}")


if __name__ == "__main__":
    evaluate()
