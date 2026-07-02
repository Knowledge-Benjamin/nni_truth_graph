# NNI Truth Graph: Pricing & Monetization Strategy

**Objective:** Establish a high-margin, highly scalable pricing model that significantly undercuts monolithic players like Palantir ($1M+ minimums) while commanding a massive premium over manual OSINT tools like Maltego ($3k/seat), by pricing the software not as a *tool*, but as an *autonomous analyst*.

---

## 1. Competitive Benchmarking

To understand your pricing power, look at the two ends of your market:

*   **The High End (Palantir Gotham / IBM i2):** 
    *   *Pricing:* $1,000,000 to $5,000,000+ per year.
    *   *Model:* Massive upfront software cost + heavy reliance on Forward Deployed Engineers (consulting).
    *   *Vulnerability:* It takes 6 months to deploy, costs millions, and still requires humans to connect the dots.
*   **The Low End (Maltego / Chorus / Spiderfoot):**
    *   *Pricing:* $1,000 to $5,000 per seat/year.
    *   *Model:* Per-user SaaS or desktop license.
    *   *Vulnerability:* These are manual graph-drawing tools. The analyst still has to spend 40 hours reading the documents and dragging the nodes.

**Your Value Proposition:** NNI Truth Graph does the work of 10 analysts automatically. Therefore, you do not price per seat. You price by **Platform Access** and **Autonomous Output**.

---

## 2. The Pricing Tiers

Based on the Hybrid Edge-Cloud architecture and the resource requirements, here is the optimal, high-margin pricing structure.

### Tier 1: Corporate Intelligence (Hybrid Edge-Cloud)
*Target Market: Top 100 Law Firms, Hedge Funds, Private Intelligence Agencies.*
*Deployment: OVM runs locally; Inference hits your central Gemma 4 Cloud.*

*   **Base Platform License:** **$60,000 / year**
    *   *What they get:* The encrypted Docker/OVM appliance installed in their environment. Updates to the extraction logic and Neo4j schemas.
*   **Inference Usage (Compute Credits):** **$500 per Investigation** (Sold in blocks of $10,000)
    *   *How it works:* Because they are hitting your cloud for heavy Gemma 4 inference, you charge per "Autonomous Investigation." 
    *   *The Margin:* 100 users running 10 investigations a month generates $500,000 in usage fees. As calculated in the resource spec, your raw GPU cost for this is ~$20,000. **That is a ~96% gross margin on compute.**

### Tier 2: The "Air-Gapped" Enterprise (BYOG)
*Target Market: Defense Contractors, Intelligence Agencies (Five Eyes), Tier-1 Banks.*
*Deployment: 100% On-Premise. They provide the NVIDIA A100/H100 GPUs.*

*   **Air-Gapped Enterprise License:** **$250,000 to $500,000 / year**
    *   *What they get:* The fully unlocked OVM containing the Gemma 4 weights and local vLLM inference engine. Unlimited autonomous investigations. Zero data leaves their building.
    *   *The Margin:* **99.9%.** Because they are providing the $50k+ GPU servers and paying the electricity, you have literally zero marginal cost. You are purely selling the cryptographic license key and software updates.

## 3. Regional Strategy: East Africa & Emerging Markets

If your initial launch market is Uganda and the broader East African Community (EAC - Kenya, Rwanda, Tanzania), the Western enterprise pricing of $60k-$250k is not viable. In OSINT and enterprise software, **you are competing against the cost of human labor**. If a mid-level investigative analyst in Nairobi or Kampala costs $8,000 - $15,000 USD per year, a $60k software license will be rejected. 

You must apply **Purchasing Power Parity (PPP) Pricing**, while protecting your future global margins.

### East Africa Pricing Tiers

**Tier 1: Local Corporate Intelligence (Hybrid Edge-Cloud)**
*Target: Ugandan Law Firms, Kenyan Financial Institutions, Regional Investigative Journalists.*
*   **Base Platform License:** **$5,000 - $10,000 / year** (Priced to roughly equal the cost of one junior analyst).
*   **Inference Usage:** **$50 per Investigation** 
    *   *The Margin:* If your raw GPU cost per investigation is ~$20, charging $50 still yields a **60% gross margin**. You make money on volume as local firms realize the AI is vastly more efficient than manual OSINT.

**Tier 2: Regional Defense & Government (Air-Gapped BYOG)**
*Target: UPDF, KDF, National Intelligence Services, Central Banks.*
*   **Air-Gapped Enterprise License:** **$50,000 - $100,000 / year**
    *   *The Pitch:* Selling software to African defense sectors often involves high procurement friction. A $50k-$100k annual license is well within the budget for national security tools, especially when they control the hardware (GPUs) and guarantee data sovereignty. 

### Preventing Geographic Arbitrage
When you expand globally (e.g., selling to London or New York), you cannot let a Wall Street hedge fund buy the $5k "Uganda Edition." 
*   **License Fencing:** Your licensing server must bind the software to a verified African corporate entity and restrict IP access.
*   **Feature Gating:** The "Global Enterprise Edition" (the $60k+ version) should include premium integrations (e.g., direct API access to Western financial databases like SEC EDGAR or premium Bloomberg feeds) that the East African edition lacks.

---

## 4. Why This Strategy Works (The Psychology of Pricing)

1. **Labor Replacement Value:** You are not charging based on AWS GPU costs. You are charging based on replacing human analyst hours. By matching the base license to local salaries, the CFO's decision becomes a simple math equation.
2. **Undercutting Palantir (Globally and Locally):** Western defense contractors completely ignore or severely overcharge emerging markets. You have a massive opportunity to capture the East African defense and intelligence sector by offering Tier-1 autonomous capabilities at a price point they can actually procure.
3. **The Razor and Blades Model (Tier 1):** The $5k base license gets them in the door. As they realize the AI is faster and more accurate than manual OSINT, their usage will skyrocket, generating steady, high-margin compute credit revenue.

## 5. Immediate Execution Steps

1. **Do not put pricing on the website.** Require a "Request Demo" flow. This allows you to qualify the lead. If it's a small journalist outfit, you can offer a stripped-down $1k/mo SaaS version. If it's a bank, you immediately pitch the $250k Air-Gapped tier.
2. **Implement Credit Metering:** In your Orchestrator (`__init__.py`), implement a check against a central license server to decrement their "Investigation Credits" every time `status = COMPLETED`. 
3. **Develop the "White Glove" Pitch:** Prepare a slide deck demonstrating how NNI Truth Graph replaces a 5-person OSINT team, making the $60k to $250k price tag easily justifiable to a CFO.
