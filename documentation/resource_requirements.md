# System Resource Requirements & Scaling Analysis (100 Users)

**Architecture Model:** Hybrid Edge-Cloud (Edge = Client VM running logic/DBs; Cloud = Central GPU Inference API)
**Scale:** 100 Concurrent Enterprise Users running active, long-running OSINT investigations.

---

## 1. The "Edge" (Client-Side Virtual Appliance)
*This is the VM or Bare Metal server the client provisions in their own datacenter to run your compiled Docker containers.*

Since the heavy lifting (LLM inference) is offloaded to your cloud, the Edge appliance is strictly bound by **I/O (Database reads/writes)**, **Memory (Neo4j graph traversals)**, and **Network (Playwright web scraping)**.

### Optimal Specs per Client Appliance:
*   **CPU:** 16 vCPUs 
    *   *Why:* Playwright running 5 concurrent headless browsers (`2_scrape.py`) is surprisingly CPU-intensive. Python worker thread context switching also requires dedicated cores.
*   **RAM:** 64 GB
    *   *Why:* Neo4j is a memory-hog. To traverse complex financial graphs quickly, it needs to hold the graph in RAM (page cache). Postgres needs ~16GB for connection pooling. Playwright needs ~8GB.
*   **Storage:** 1 TB NVMe SSD
    *   *Why:* The `snapshots/` folder will grow rapidly. Gzipped HTML of 100,000 scraped pages is ~20-50GB. Neo4j and Postgres indexes require fast NVMe drives to prevent I/O bottlenecks during massive parallel `EXTRACTING` and `GRAPH_COMMITTED` writes.
*   **Network:** 1 Gbps symmetric fiber
    *   *Why:* The appliance is simultaneously downloading massive HTML payloads/PDFs and firing thousands of token-heavy JSON payloads up to your cloud API.

---

## 2. The "Cloud" (Your Central Inference API)
*This is the infrastructure you pay for to support the 100 clients.*

This is the most critical chokepoint. If 100 users each have an active investigation, and each orchestrator spawns 5 concurrent agents, you could be facing **500 concurrent LLM inference requests** at peak load.

### Compute Power (GPU Cluster)
Assuming you are serving **Gemma 4 (e.g., a 27B parameter class model)** using a high-throughput engine like **vLLM** or **TensorRT-LLM**:

*   **GPU Requirement:** 1x 8-GPU Node of **NVIDIA H100s (80GB)** *OR* 2x 8-GPU Nodes of **NVIDIA A100s (80GB)**.
    *   *The Math:* A single A100 can process roughly 30-50 concurrent requests (assuming 4k token context lengths for scraped HTML). To handle a peak surge of 500 concurrent extraction requests without massive latency/timeout spikes, you need roughly 10 to 16 A100s, or 8 of the significantly faster H100s.
*   **CPU/RAM (Host Machine):** 128 vCPUs, 1 TB RAM (To handle the massive KV-cache and networking overhead of vLLM).
*   **Estimated Cloud Cost:** ~$15,000 - $25,000 / month (depending on reserved instance pricing on AWS/Lambda/RunPod). 

### Storage (Cloud)
*   **Storage:** 500 GB NVMe
    *   *Why:* Your cloud is completely stateless. It does not store target data or Neo4j graphs. The storage is only needed to hold the massive model weights (Gemma 4) and the OS/CUDA toolkit. 

### Network (Cloud)
*   **Bandwidth:** 10+ Gbps Egress/Ingress. 
    *   *Why:* You are receiving massive context windows (full scraped articles) and returning structured JSON.

---

## 3. Financial Viability & Unit Economics

If you have 100 active enterprise users under this model:

1.  **Your Infrastructure Cost:** ~$20,000/month (for the centralized GPU cluster).
2.  **Client Licensing Revenue:** If you charge $5,000/month per enterprise client (which is very cheap for defense/corporate intelligence software): 
    *   *Gross Revenue:* $500,000 / month.
3.  **Gross Margin:** ~96%.

## 4. The Air-Gapped (BYOG) Exception
If 10 of those 100 clients are "Air-Gapped", your math gets even better.
*   You shift the $20,000-per-server GPU cost entirely to them.
*   They buy an NVIDIA DGX or an 8x A100 server for their datacenter.
*   You charge them $10,000/month for the license.
*   Your cloud infrastructure costs go down, and your revenue goes up.

---

## Conclusion
To comfortably support 100 concurrent enterprise users running full-throttle, multi-agent investigations, your primary bottleneck will be **GPU VRAM and KV-Cache management** in your centralized cloud. 

You should design your backend infrastructure to auto-scale GPU nodes based on the length of the pending inference queue. By keeping the storage and database heavy-lifting on the client's local VM, you've created a highly profitable, scalable architecture.
