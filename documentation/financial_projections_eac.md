# Financial Projections: East African Market Launch (Year 1)

**Market Target:** Uganda, Kenya, Rwanda (Corporate Intelligence, Law Firms, Defense)
**Objective:** Scale to 15 Corporate Clients (Hybrid Cloud) and 2 Defense/Government Clients (Air-Gapped BYOG) within 12 months.
**Currency:** All figures in USD. Prices are set as definite numbers based on current cloud compute rates and regional purchasing power parity.

---

## 1. Anticipated Annual Expenses (The Burn Rate)

To serve ~17 enterprise clients efficiently, you do **not** need to rent dedicated servers that sit idle at night. Instead, you use a **Serverless / Pay-Per-Token Inference API** (e.g., DeepInfra, Groq, Together AI). Your expense is 100% pegged to system activity. If zero clients use the system on a Sunday, your cloud bill is $0.

| Expense Category | Definite Monthly Cost | Definite Annual Cost | Details / Justification |
| :--- | :--- | :--- | :--- |
| **Serverless GPU Compute (Pay-Per-Token)** | ~$60 | **$720** | **The Game Changer.** Using providers like DeepInfra or Groq, Gemma models cost ~$0.20 per 1 Million tokens. Assuming 15 clients run 240 investigations/yr, and one massive investigation uses 500,000 tokens (inputs + outputs), your raw compute cost is **$0.10 per investigation**. 15 clients * 240 envs * $0.10 = $360/year. Let's double it to $720 for testing overhead. |
| **Cloud Storage & Network** | $50 | **$600** | Only needed for lightweight routing and API gateway logs (as databases live on the client's OVM). |
| **Enterprise OSINT APIs** | $2,000 | **$24,000** | Commercial API access for Shodan ($1k/mo) and Censys ($1k/mo). *Note: You can pass this cost directly to the client via "Bring Your Own Key" (BYOK) to reduce this to $0.* |
| **Software Infrastructure** | $150 | **$1,800** | GitHub Enterprise, CI/CD pipelines, Docker Hub, and Licensing Server (e.g., Keygen.sh at $99/mo). |
| **Local DevOps / Support** | $1,500 | **$18,000** | Retaining one senior East African DevOps/Support engineer to manage client deployments and OVM updates. |
| **Total Anticipated Expense** | **$3,760 / mo** | **$45,120 / year** | *By dropping dedicated servers, your burn rate is slashed in half.* |

---

## 2. Anticipated Annual Revenue (The Yield)

This assumes a localized East African pricing model (PPP) where you charge significantly less than Western rates, but make it up in volume and high compute margins.

### Revenue Stream A: Corporate Intelligence (Hybrid Edge-Cloud)
*Targeting 15 local entities (e.g., Ugandan law firms, Kenyan banks, regional investigative firms).*
*   **Base Platform License:** $7,500 / year per client.
*   **Inference Compute Credits:** $50 per Investigation. 
    *   *Assumption:* A firm runs an average of 20 deep-dive investigations per month (240/year).
    *   *Compute Revenue per client:* 240 × $50 = $12,000 / year.
*   **Total Revenue per Hybrid Client:** $19,500 / year.
*   **Total Annual Revenue (15 Clients):** **$292,500**

### Revenue Stream B: Defense & Government (Air-Gapped BYOG)
*Targeting 2 national security entities (e.g., UPDF, KDF, or National Intelligence).*
*   **Air-Gapped License:** $65,000 / year per client.
    *   *Note:* They purchase their own GPUs. You have **zero** compute expenses for these clients.
*   **Total Annual Revenue (2 Clients):** **$130,000**

### Total Anticipated Gross Revenue
*   **$422,500 / year**

---

## 3. Financial Summary & Profitability

| Metric | Definite Figure (USD) |
| :--- | :--- |
| **Gross Annual Revenue** | $422,500 |
| **Gross Annual Expenses** | $45,120 |
| **Net Operating Profit** | **$377,380** |
| **Profit Margin** | **89%** |

### Critical Takeaways for the Pay-As-You-Go Pivot

1. **The Serverless Superpower:** By abandoning fixed $3,500/month dedicated GPU rentals and switching to a Pay-Per-Token Inference API, you completely eliminate the risk of idle servers burning your cash. You only pay ~$0.10 in cloud compute when a client actually pays you $50 in compute credits.
2. **Further Cost Cutting (BYOK):** If $45k/year is still too high, you can push the $24,000/year OSINT API costs (Shodan/Censys) directly to the client by requiring them to enter their own API keys into their local OVM. This drops your annual expense to **~$21,000**, raising margins to 95%.
3. **Gov/Defense is Pure Profit:** Securing just one military or intelligence contract at $65,000 immediately covers your entire startup's annual operating expenses for the year.
