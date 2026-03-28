import json
import random
import os

def generate_dataset(num_records=1000, output_path="mock_ground_truth.jsonl"):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    records = []
    
    for i in range(num_records):
        # 40% True, 40% False, 20% Mixture
        rand_val = random.random()
        if rand_val < 0.4:
            ground_truth = 1.0  # True
        elif rand_val < 0.8:
            ground_truth = 0.0  # False
        else:
            ground_truth = 0.5  # Mixture
            
        if ground_truth == 1.0:
            ext_conf = random.uniform(0.7, 0.99)
            tier = random.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]
            support = random.randint(1, 15)
            # Maybe 0 or 1 low-weight contradiction
            num_contra = random.randint(0, 1)
            contra = [random.uniform(0.1, 0.5) for _ in range(num_contra)]
            age = random.randint(1, 90)
            
        elif ground_truth == 0.0:
            ext_conf = random.uniform(0.2, 0.75)
            tier = random.choices([1, 2, 3], weights=[0.1, 0.3, 0.6])[0]
            support = random.randint(0, 2)
            # Many high-weight contradictions
            num_contra = random.randint(1, 5)
            contra = [random.uniform(0.6, 0.95) for _ in range(num_contra)]
            age = random.randint(1, 180)
            
        else: # Mixture 0.5
            ext_conf = random.uniform(0.4, 0.85)
            tier = random.choices([1, 2, 3], weights=[0.2, 0.5, 0.3])[0]
            support = random.randint(0, 5)
            num_contra = random.randint(0, 3)
            contra = [random.uniform(0.3, 0.8) for _ in range(num_contra)]
            age = random.randint(1, 90)

        records.append({
            "claim_id": f"claim_{i}",
            "extraction_confidence": round(ext_conf, 3),  # type: ignore
            "source_tier": tier,
            "support_count": support,
            "contradiction_weights": [round(w, 3) for w in contra],  # type: ignore
            "days_since_extracted": age,
            "ground_truth_label": ground_truth
        })

    with open(output_path, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    print(f"Generated {num_records} standardized truth evaluation records at {output_path}")

if __name__ == "__main__":
    generate_dataset(1500, os.path.join(os.path.dirname(__file__), "data", "mock_ground_truth.jsonl"))
