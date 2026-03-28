import json
import os
import sys

# Ensure ai_engine is in path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ai_engine.core.epistemic_trust import EpistemicTrustScorer  # type: ignore
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss  # type: ignore
import numpy as np  # type: ignore
from scipy.optimize import minimize  # type: ignore

def load_dataset(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        records = [json.loads(line) for line in f]
    return records

def evaluate_weights(weights, records):
    """
    weights dict: alpha, beta, gamma_corrob, gamma_contra, max_corrob, delta_decay
    Returns the average Brier Score (lower is better, 0 is perfect calibration).
    """
    scorer = EpistemicTrustScorer(**weights)
    
    y_true = []
    y_pred = []
    
    for r in records:
        # We only evaluate on pure True (1.0) and False (0.0) for standard Binary calibration
        # For Mixture (0.5), it hurts log_loss / ROC, but we keep it for Brier score continuous calibration
        y_true.append(r["ground_truth_label"])
        
        score = scorer.calculate_epistemic_score(
            extraction_confidence=r["extraction_confidence"],
            source_tier=r["source_tier"],
            support_count=r["support_count"],
            contradiction_weights=r["contradiction_weights"],
            days_since_extracted=r["days_since_extracted"]
        )
        y_pred.append(score)
        
    y_true_np = np.array(y_true)
    y_pred_np = np.array(y_pred)
    
    # Both Brier Score and ROC-AUC require binary labels.
    # We filter out 0.5 (Mixture) labels.
    binary_mask = (y_true_np == 1.0) | (y_true_np == 0.0)
    if sum(binary_mask) > 0:  # type: ignore
        brier = brier_score_loss(y_true_np[binary_mask], y_pred_np[binary_mask])
        roc_auc = roc_auc_score(y_true_np[binary_mask], y_pred_np[binary_mask])
    else:
        brier = 1.0
        roc_auc = 0.5
        
    return brier, roc_auc

def objective_function(x, records):
    """
    Objective function for scipy.optimize.minimize
    x is an array of 6 parameters:
    [alpha, beta, gamma_corrob, gamma_contra, max_corrob, delta_decay]
    """
    weights = {
        "alpha_weight": x[0],
        "beta_weight": x[1],
        "gamma_corrob_factor": x[2],
        "gamma_contra_factor": x[3],
        "max_corrob_bonus": x[4],
        "delta_decay_rate": x[5]
    }
    brier, _ = evaluate_weights(weights, records)
    return brier

def run_optimization(dataset_path):
    records = load_dataset(dataset_path)
    print(f"Loaded {len(records)} records for optimization.")
    
    # 1. Baseline Evaluation (current hardcoded weights)
    baseline_weights = {
        "alpha_weight": 0.4,
        "beta_weight": 0.6,
        "gamma_corrob_factor": 0.1,
        "gamma_contra_factor": 0.15,
        "max_corrob_bonus": 0.3,
        "delta_decay_rate": 0.01
    }
    
    b_brier, b_roc = evaluate_weights(baseline_weights, records)
    print("--- BASELINE PERFORMANCE ---")
    print(f"Brier Score: {b_brier:.4f} (Lower is better)")
    print(f"ROC-AUC:     {b_roc:.4f} (Higher is better)")
    
    # 2. Optimization
    print("\n--- RUNNING L-BFGS-B OPTIMIZATION ---")
    
    # Initial guess is the baseline
    x0 = [0.4, 0.6, 0.1, 0.15, 0.3, 0.01]
    
    # Bounds for the parameters
    # alpha + beta don't strictly have to sum to 1 in the algorithm, but we cap them.
    bounds = (
        (0.1, 0.9),  # alpha_weight
        (0.1, 0.9),  # beta_weight
        (0.01, 0.5), # gamma_corrob_factor
        (0.05, 0.5), # gamma_contra_factor
        (0.1, 0.5),  # max_corrob_bonus
        (0.001, 0.1) # delta_decay_rate
    )
    
    result = minimize(
        objective_function, 
        x0, 
        args=(records,), 
        method='L-BFGS-B', 
        bounds=bounds
    )
    
    opt_x = result.x
    opt_weights = {
        "alpha_weight": float(opt_x[0]),
        "beta_weight": float(opt_x[1]),
        "gamma_corrob_factor": float(opt_x[2]),
        "gamma_contra_factor": float(opt_x[3]),
        "max_corrob_bonus": float(opt_x[4]),
        "delta_decay_rate": float(opt_x[5])
    }
    
    o_brier, o_roc = evaluate_weights(opt_weights, records)
    
    print("\n--- OPTIMIZED PERFORMANCE ---")
    print(f"Brier Score: {o_brier:.4f}  (Improvement: {(b_brier - o_brier):.4f})")
    print(f"ROC-AUC:     {o_roc:.4f}  (Improvement: {(o_roc - b_roc):.4f})")
    
    print("\n--- OPTIMAL HYPERPARAMETERS ---")
    for k, v in opt_weights.items():
        print(f"'{k}': {v:.4f}")

    # Output to file for reporting
    report = {
        "baseline": {"brier": b_brier, "roc_auc": b_roc},
        "optimized": {"brier": o_brier, "roc_auc": o_roc},
        "optimal_weights": opt_weights
    }
    
    out_path = os.path.join(os.path.dirname(dataset_path), "optimization_results.json")
    with open(out_path, 'w') as f:
        json.dump(report, f, indent=4)
        
    print(f"\nOptimization complete. Results saved to {out_path}")

if __name__ == "__main__":
    dataset_path = os.path.join(os.path.dirname(__file__), "data", "mock_ground_truth.jsonl")
    run_optimization(dataset_path)
