from datetime import datetime
import math
from typing import Optional

class EpistemicTrustScorer:
    """
    Implements the Epistemic Trust Algorithm for Truth.
    Calculates the Epistemic Score (ES) based on Extraction confidence,
    Source reliability, Network consensus, and Temporal decay.
    """
    
    def __init__(self, **kwargs):
        # Configurable mathematical weights for the Epistemic Score optimization
        self.alpha_weight = kwargs.get("alpha_weight", 0.4)
        self.beta_weight = kwargs.get("beta_weight", 0.6)
        self.gamma_corrob_factor = kwargs.get("gamma_corrob_factor", 0.1)
        self.gamma_contra_factor = kwargs.get("gamma_contra_factor", 0.15)
        self.max_corrob_bonus = kwargs.get("max_corrob_bonus", 0.3)
        self.delta_decay_rate = kwargs.get("delta_decay_rate", 0.01)

        # Base source trust initialization (Tiers)
        self.DEFAULT_TIER_TRUST = {
            1: 0.90,  # Official Gov, Peer-reviewed Journals
            2: 0.70,  # Major News Outlets
            3: 0.40,  # Blogs, Social Media
        }

    def calculate_epistemic_score(self, 
                                  extraction_confidence: float, 
                                  source_tier: int, 
                                  support_count: int = 0, 
                                  contradiction_weights: list[float] = None, 
                                  days_since_extracted: int = 0,
                                  historical_source_reliability: Optional[float] = None,
                                  corroboration_records: Optional[list[dict]] = None,
                                  media_synthetic_prob: Optional[float] = None) -> float:
        """
        Calculates the final Epistemic Score [0.0 - 1.0] for a claim.
        
        Args:
            extraction_confidence: Float [0.0 - 1.0] from the AI Extraction (Stage 3).
            source_tier: Int (1, 2, or 3) representing the domain tier of the origin.
            support_count: Legacy static count of independent sources.
            contradiction_weights: List of Epistemic Scores of all contradicting claims.
            days_since_extracted: Age of the original claim.
            historical_source_reliability: Dynamically calculated reliability of the source.
            corroboration_records: Fossil Record array containing dicts of {'timestamp': datetime, 'source_tier': int, 'source_trust': float}.
            media_synthetic_prob: Float [0.0 - 1.0] from the VisionInferenceServer (Deepfake/AI synthesis likelihood).
        """
        # 0. Epsilon (Visual Evidence) Absolute Overrides
        epsilon_visual = 0.0
        if media_synthetic_prob is not None:
            if media_synthetic_prob > 0.85:
                return 0.0  # Nuclear Epsilon Strike: Deepfake detected. Epistemic credibility is completely shattered.
            elif media_synthetic_prob > 0.70:
                epsilon_visual = -0.40  # Massive penalty: High suspicion of synthesis
            elif media_synthetic_prob < 0.10:
                epsilon_visual = 0.25   # The Photographic Proof Bonus: Cryptographically raw real media
            elif media_synthetic_prob < 0.30:
                epsilon_visual = 0.15   # Generally untouched visual support
            else:
                epsilon_visual = 0.0    # Ambiguous/Low-res media, rely on text algorithms
                
        if contradiction_weights is None:
            contradiction_weights = []

        # 1. Base Extraction Score (Alpha)
        alpha_ext = extraction_confidence
        
        # 2. Source Reliability (Beta)
        beta_src = historical_source_reliability if historical_source_reliability else self.DEFAULT_TIER_TRUST.get(source_tier, 0.40)
        
        # 3. Network Consensus (Gamma Variance) & Temporal Decay (Delta) Integration
        effective_support_count = support_count
        effective_days_old = days_since_extracted
        corroboration_bonus = 0.0

        if corroboration_records and len(corroboration_records) > 0:
            effective_support_count = len(corroboration_records)
            now = datetime.now()
            
            # --- Delta Decay Revitalization ---
            # Extract the youngest corroboration to reset decay.
            youngest_days = effective_days_old
            for rec in corroboration_records:
                ts = rec.get("timestamp")
                if ts:
                    # Ignore naive/timezone differences for the simplistic math engine scale
                    try:
                        diff = (now.astimezone() - ts.astimezone()).days if ts.tzinfo else (now - ts).days
                        if 0 <= diff < youngest_days:
                            youngest_days = diff
                    except Exception:
                        pass
            effective_days_old = youngest_days

            # --- Gamma Variance & Breaking News Detection ---
            from collections import defaultdict
            windows = defaultdict(list)
            has_tier_1 = False
            for rec in corroboration_records:
                ts = rec.get("timestamp") or now
                # Check for Tier 1 confirmation
                if rec.get("source_tier", 3) == 1:
                    has_tier_1 = True
                # Group by 4-hour windows for burst detection
                try:
                    window_key = ts.strftime('%Y-%m-%d-%H')[:-1] 
                except AttributeError:
                    window_key = "unknown"
                windows[window_key].append(rec)
            
            max_window = max(windows.values(), key=len) if windows else []
            burst_ratio = len(max_window) / effective_support_count if effective_support_count else 0

            if has_tier_1:
                # Premium Corroboration: A Tier 1 source instantly validates the claim
                corroboration_bonus = self.max_corrob_bonus
            elif effective_support_count >= 5 and burst_ratio >= 0.8:
                # Sudden Burst detected. Check source tiers.
                tiers = [r.get("source_tier", 3) for r in max_window]
                if all(t == 3 for t in tiers):
                    # Flag: Bot Swarm / Syndication
                    corroboration_bonus = math.log10(effective_support_count + 1) * (self.gamma_corrob_factor * 0.2)
                elif any(t in (1, 2) for t in tiers):
                    # Flag: Viral Breaking News (Premium Independent Consensus)
                    corroboration_bonus = self.max_corrob_bonus
                else:
                    corroboration_bonus = min(self.max_corrob_bonus, math.log10(effective_support_count + 1) * self.gamma_corrob_factor)
            else:
                # Healthy temporal spread
                raw_bonus = math.log10(effective_support_count + 1) * self.gamma_corrob_factor
                # Reward structural multi-domain independence
                unique_tiers = len(set(r.get("source_tier", 3) for r in corroboration_records))
                if unique_tiers > 1:
                    raw_bonus *= 1.2
                corroboration_bonus = min(self.max_corrob_bonus, raw_bonus)
                
        elif effective_support_count > 0:
             # Legacy linear math fallback
             corroboration_bonus = min(self.max_corrob_bonus, math.log10(effective_support_count + 1) * self.gamma_corrob_factor)
             
        # Calculate contradiction penalty
        contradiction_penalty = sum((weight * self.gamma_contra_factor) for weight in contradiction_weights)
        gamma_net = corroboration_bonus - contradiction_penalty
        
        # 4. Temporal Decay (Delta) Calculation
        # Decay slowly kicks in after 30 days of absolute silence
        delta_decay = 0.0
        if effective_days_old > 30:
            months_old = (effective_days_old - 30) / 30.0
            delta_decay = min(0.2, months_old * self.delta_decay_rate)
            
        # Compile final score
        base_score = (alpha_ext * self.alpha_weight) + (beta_src * self.beta_weight)
        final_score = base_score + gamma_net - delta_decay + epsilon_visual
        
        # Clamp between 0.0 and 1.0
        return max(0.0, min(1.0, final_score))
        
    def determine_routing(self, epistemic_score: float) -> str:
        """
        Routes the claim based on its Epistemic Score.
        """
        if epistemic_score >= 0.85:
            return "AUTO_APPROVE"
        elif epistemic_score >= 0.40:
             return "HUMAN_REVIEW"
        else:
             return "AUTO_REJECT"

if __name__ == "__main__":
    # Test Scenarios
    scorer = EpistemicTrustScorer()
    
    print("--- Testing Epistemic Trust Scorer ---")
    
    # Scenario 1: Perfect Tier 1 Claim, High Confidence, No Contradictions
    score1 = scorer.calculate_epistemic_score(0.95, 1, 5, [], 5)
    print(f"Scenario 1 (Strong Truth):             {score1:.3f} -> {scorer.determine_routing(score1)}")
    
    # Scenario 2: Social media claim, low extraction confidence, heavily contradicted
    score2 = scorer.calculate_epistemic_score(0.30, 3, 0, [0.85, 0.90], 10)
    print(f"Scenario 2 (Debunked Rumor):           {score2:.3f} -> {scorer.determine_routing(score2)}")
    
    # Scenario 3: Novel scientific claim (Tier 1), moderate extraction confidence, highly controversial
    score3 = scorer.calculate_epistemic_score(0.75, 1, 1, [0.80, 0.82], 2)
    print(f"Scenario 3 (Controversial Science):    {score3:.3f} -> {scorer.determine_routing(score3)}")

    # Scenario 4: Deepfake Detected (Tier 3), High Corroboration (Bot Swarm)
    score4 = scorer.calculate_epistemic_score(0.90, 3, 10, [], 1, media_synthetic_prob=0.99)
    print(f"Scenario 4 (Deepfake Bot Swarm):       {score4:.3f} -> {scorer.determine_routing(score4)}")

    # Scenario 5: Photographic Raw Proof (Tier 3 User filmed an event)
    score5 = scorer.calculate_epistemic_score(0.85, 3, 0, [], 1, media_synthetic_prob=0.03)
    print(f"Scenario 5 (Raw Citizen Journalism):   {score5:.3f} -> {scorer.determine_routing(score5)}")

