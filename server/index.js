const express = require("express");
const cors = require("cors");
const dotenv = require("dotenv");
const { driver, auth } = require("neo4j-driver");
const { Pool } = require("pg");
const { spawn } = require("child_process");
const axios = require("axios");

// Import validation and response utilities
const {
  validateQuery,
  validateFactId,
  validateNodeId,
  validatePagination,
  sendError,
  sendSuccess,
} = require("./validation");

// Import Cypher query validator
const {
  validateCypherQuery,
  sanitizeCypherInput,
} = require("./cypher-validator");

// Import CORS and security configuration
const { corsOptions, securityHeaders } = require("./cors-config");

// Import redirect security utilities
const { compatibilityRedirect } = require("./redirect-security");

// Import Cypher query builder
const cypherBuilder = require("./cypher-builder");

dotenv.config();

const app = express();

// ===== CORS & SECURITY CONFIGURATION =====
app.use(cors(corsOptions));
app.use(securityHeaders);

app.use(express.json({ limit: "10mb" })); // Limit payload size to prevent DOS
app.use(express.urlencoded({ limit: "10mb", extended: true }));

// ===== ENVIRONMENT VALIDATION =====
const REQUIRED_ENV_VARS = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"];
const OPTIONAL_ENV_VARS = {
  DATABASE_URL: "Required for PostgreSQL access and consistency checks",
  AI_ENGINE_URL: "Defaults to http://localhost:8001 if not set",
};

// Check required variables
const missingVars = REQUIRED_ENV_VARS.filter((v) => !process.env[v]);
if (missingVars.length > 0) {
  console.error(
    "❌ FATAL: Missing required environment variables:",
    missingVars.join(", ")
  );
  console.error("   Set these in .env or system environment");
  process.exit(1);
}

// Check optional but recommended variables
const missingOptional = Object.keys(OPTIONAL_ENV_VARS).filter(
  (v) => !process.env[v]
);
if (missingOptional.length > 0) {
  console.warn("\n⚠️  WARNING: Missing optional environment variables:");
  missingOptional.forEach((varName) => {
    console.warn(`   - ${varName}: ${OPTIONAL_ENV_VARS[varName]}`);
  });
  console.warn("   Some features may be unavailable\n");
}

const PORT = process.env.PORT || 3000;
const AI_ENGINE_URL = process.env.AI_ENGINE_URL || "http://localhost:8001";

// ===== POSTGRESQL CONNECTION POOL =====
let pgPool = null;
if (process.env.DATABASE_URL) {
  try {
    pgPool = new Pool({
      connectionString: process.env.DATABASE_URL,
      max: 10, // Maximum number of clients in the pool
      idleTimeoutMillis: 30000, // Close idle clients after 30 seconds
      connectionTimeoutMillis: 2000, // Return an error after 2 seconds if connection could not be established
    });

    // Test the connection
    pgPool.on("connect", () => {
      console.log("✅ PostgreSQL connection pool initialized");
    });

    pgPool.on("error", (err) => {
      console.error("❌ PostgreSQL pool error:", err);
    });
  } catch (error) {
    console.error("❌ Failed to initialize PostgreSQL pool:", error.message);
    console.warn("⚠️  PostgreSQL features will be unavailable");
  }
} else {
  console.warn(
    "⚠️  DATABASE_URL not set. PostgreSQL features will be unavailable"
  );
}

// Neo4j Aura Connection
console.log("=".repeat(60));
console.log("Neo4j Connection Configuration");
console.log("=".repeat(60));
console.log(`URI: ${process.env.NEO4J_URI}`);
console.log(`User: ${process.env.NEO4J_USER}`);
console.log(
  `Password: ${
    process.env.NEO4J_PASSWORD
      ? process.env.NEO4J_PASSWORD.substring(0, 4) + "..."
      : "NOT SET"
  }`
);
console.log("");

// ===== NEO4J DRIVER =====
const neo4jDriver = driver(
  process.env.NEO4J_URI,
  auth.basic(process.env.NEO4J_USER, process.env.NEO4J_PASSWORD)
);

// Test connection and setup schema immediately
(async () => {
  const session = neo4jDriver.session();
  try {
    console.log("🔄 Testing Neo4j Aura connection...");
    const serverInfo = await neo4jDriver.getServerInfo();
    console.log(`✅ Connected to Neo4j Aura!`);
    console.log(`   Address: ${serverInfo.address}`);
    console.log(`   Version: ${serverInfo.agent}`);

    // Auto-create Fulltext Index for Relevance
    console.log("🔍 Verifying Schema...");
    const indexCheckSpec = cypherBuilder.buildCheckFulltextIndexQuery();
    const indexCheck = await session.run(
      indexCheckSpec.query,
      indexCheckSpec.params
    );
    if (indexCheck.records.length === 0) {
      console.log("⚡ Creating fulltext index for search relevance...");
      const createIndexSpec = cypherBuilder.buildCreateFulltextIndexQuery();
      await session.run(createIndexSpec.query, createIndexSpec.params);
      console.log("✅ Fulltext index created.");
    } else {
      console.log("✅ Fulltext index ready.");
    }

    // Auto-create Vector Index for Semantic Linking
    console.log("🔍 Checking Vector Index...");
    const vectorIndexCheckSpec = cypherBuilder.buildCheckVectorIndexQuery();
    const vectorIndexCheck = await session.run(
      vectorIndexCheckSpec.query,
      vectorIndexCheckSpec.params
    );
    if (vectorIndexCheck.records.length === 0) {
      console.log("⚡ Creating vector index for semantic search...");
      try {
        const createVectorIndexSpec =
          cypherBuilder.buildCreateVectorIndexQuery();
        await session.run(
          createVectorIndexSpec.query,
          createVectorIndexSpec.params
        );
        console.log("✅ Vector index created.");
        global.VECTOR_INDEX_AVAILABLE = true;
      } catch (e) {
        console.warn(
          `⚠️  Vector index not available (free tier limitation): ${e.message}`
        );
        console.warn("    → Will use keyword search fallback");
        global.VECTOR_INDEX_AVAILABLE = false;
      }
    } else {
      console.log("✅ Vector index ready.");
      global.VECTOR_INDEX_AVAILABLE = true;
    }
    console.log("");

    // Check if database needs seeding
    console.log("🔍 Checking if database needs seeding...");
    const nodeCountSpec = cypherBuilder.buildHealthCheckQuery();
    const nodeCountResult = await session.run(
      nodeCountSpec.query,
      nodeCountSpec.params
    );
    const nodeCount = nodeCountResult.records[0].get("nodeCount").toNumber();

    if (nodeCount === 0) {
      console.log("📭 Database is empty. Starting auto-seed in background...");
      const path = require("path");
      const scriptPath = path.join(
        __dirname,
        "../scripts/backfill_nni_articles.py"
      );

      // ✅ SECURE: Use spawn() instead of exec() to prevent command injection
      const child = spawn("python", [scriptPath], {
        stdio: ["pipe", "pipe", "pipe"],
        shell: false, // Explicitly disable shell
      });

      let stdoutData = "";
      let stderrData = "";

      child.stdout.on("data", (data) => {
        stdoutData += data.toString();
      });

      child.stderr.on("data", (data) => {
        stderrData += data.toString();
      });

      child.on("close", (code) => {
        if (code !== 0) {
          console.error(`❌ Auto-seed failed with exit code ${code}`);
          if (stderrData) console.error(`⚠️  Error: ${stderrData}`);
        } else {
          console.log(`✅ Auto-seed completed successfully`);
        }
        if (stdoutData) console.log(`📋 Output: ${stdoutData}`);
      });

      child.on("error", (error) => {
        console.error(`❌ Auto-seed process error: ${error.message}`);
      });
    } else {
      console.log(`✅ Database already populated (${nodeCount} nodes)`);
    }
    console.log("");
  } catch (error) {
    console.error("❌ Neo4j connection/schema failed:");
    console.error(`   ${error.message}`);
    console.error("");
    console.error("Troubleshooting:");
    console.error("  1. Check instance status at console.neo4j.io");
    console.error("  2. Verify credentials in server/.env");
    console.error("  3. Ensure URI starts with neo4j+s://");
    console.error("");
  } finally {
    await session.close();
  }
})();

app.get("/", async (req, res) => {
  let dbStatus = "Disconnected";
  try {
    const serverInfo = await neo4jDriver.getServerInfo();
    dbStatus = `Connected to ${serverInfo.address}`;
  } catch (err) {
    dbStatus = `Error: ${err.message}`;
  }
  res.json({ status: "Core Server Running", db: dbStatus, ai: AI_ENGINE_URL });
});

// ===== QUERY ENDPOINTS ONLY =====
// Server is read-only. AI Engine handles all data ingestion.
// Removed /api/ingest - data flows: RSS/GDELT → AI Engine → PostgreSQL/Neo4j → Server queries

// Natural Language Query (Hybrid Search)
app.post("/api/query/natural", async (req, res) => {
  let { query } = req.body;

  // Validate input using standardized validation
  const validation = validateQuery(query);
  if (!validation.valid) {
    return sendError(
      res,
      validation.error,
      validation.code || "INVALID_QUERY",
      400
    );
  }

  // Sanitize user input
  query = sanitizeCypherInput(query);

  try {
    console.log(`🔎 TruthRank: Processing "${query}"...`);

    // 1. Parallel: Expand Query & Generate Embedding via AI Engine
    const [expansionResp, embeddingResp] = await Promise.allSettled([
      axios.post(`${AI_ENGINE_URL}/expand_query`, { query }),
      axios.post(`${AI_ENGINE_URL}/embed_query`, { query }),
    ]);

    // Process Expansion
    let searchTerms = [query];
    if (
      expansionResp.status === "fulfilled" &&
      expansionResp.value.data.variations
    ) {
      searchTerms = [...searchTerms, ...expansionResp.value.data.variations];
    }
    // Ensure all search terms are strings and non-empty
    searchTerms = searchTerms.filter((t) => typeof t === "string" && t.trim().length > 0);
    const fulltextQuery = searchTerms
      .map((t) => `"${String(t).replace(/"/g, '')}"`)
      .join(" OR ");
    console.log(`   ✨ Expansion: ${searchTerms.join(", ")}`);

    // Process Embedding
    let vector = null;
    if (
      embeddingResp.status === "fulfilled" &&
      embeddingResp.value.data.embedding
    ) {
      vector = embeddingResp.value.data.embedding;
      console.log(`   🧬 Embedding generated (384-dim)`);
    } else {
      console.log(`   ⚠️ Vector search unavailable (using keyword only)`);
    }

    // 2. Execute Hybrid Cypher Query
    const session = neo4jDriver.session();
    try {
      // CRITICAL: Server reads what AI Engine creates
      // Neo4j Schema: Article -[:ASSERTED]-> Fact (not Claim nodes)
      // Query matches the actual push_to_neo4j.js output

      let querySpec;

      // Choose query based on vector availability
      if (vector && vector.length === 384) {
        // Use hybrid search (keyword + vector similarity)
        console.log(`   🔀 Using hybrid search (keyword + vector)`);
        querySpec = cypherBuilder.buildHybridSearchQuery({
          fulltextQuery,
          embedding: vector,
          vectorWeight: 0.5,
          limit: 15,
        });
      } else if (vector) {
        // Vector-only search (if embedding available but keyword is weak)
        console.log(`   🧬 Using vector similarity search`);
        querySpec = cypherBuilder.buildVectorSearchQuery({
          embedding: vector,
          similarityThreshold: 0.65,
          limit: 15,
        });
      } else {
        // Fallback to keyword search
        console.log(`   🔍 Using keyword search (no embedding)`);
        querySpec = cypherBuilder.buildSearchFactsQuery({
          fulltextQuery,
          useHybrid: false,
          limit: 15,
        });
      }

      const result = await session.run(querySpec.query, querySpec.params);

      // Format results - map Fact nodes properly
      const records = result.records
        .map((record) => {
          const fact = record.get("fact");
          const relevance = record.get("relevance");

          // Handle Neo4j Fact node
          if (fact && fact.properties) {
            return {
              id: fact.properties.id,
              text: fact.properties.text,
              subject: fact.properties.subject,
              predicate: fact.properties.predicate,
              object: fact.properties.object,
              confidence: fact.properties.confidence,
              relevance:
                typeof relevance === "object" ? relevance.low : relevance,
            };
          }
          return null;
        })
        .filter((r) => r !== null);

      console.log(`   📊 Found ${records.length} relevant facts.`);

      // 3. AI Analysis (Synthesis)
      let analysis = null;
      let cleanedResults = records;

      if (records.length > 0) {
        console.log(`   🤖 Analyzing results...`);
        try {
          const analysisResp = await axios.post(
            `${AI_ENGINE_URL}/analyze_results`,
            {
              query,
              results: records,
            }
          );

          if (analysisResp.data) {
            analysis = analysisResp.data.analysis;
            cleanedResults = analysisResp.data.cleaned_results;
            console.log("✅ Analysis complete.");
          }
        } catch (aiErr) {
          console.error(`   ⚠️ Analysis failed: ${aiErr.message}`);
          console.warn("⚠️ AI Analysis skipped - using raw TruthRank results");
        }
      }

      // Standardized response format
      res.json({
        success: true,
        query: query,
        analysis:
          analysis || "AI Analysis unavailable. Showing raw TruthRank results.",
        results: cleanedResults,
        count: cleanedResults.length,
        timestamp: new Date().toISOString(),
      });
    } finally {
      await session.close();
    }
  } catch (error) {
    console.error("Query Error:", error.message);
    res.status(500).json({ error: "Search failed", details: error.message });
  }
});

// Verify Single Fact
app.get("/api/verify", async (req, res) => {
  const { fact } = req.query;
  if (!fact) return res.status(400).json({ error: "Fact query required" });

  const session = neo4jDriver.session();
  try {
    // Use centralized query builder to prevent duplication
    const querySpec = cypherBuilder.buildSearchFactsByKeywordQuery({
      searchTerm: fact,
      limit: 1,
    });

    const result = await session.run(querySpec.query, querySpec.params);

    if (result.records.length === 0) {
      return res
        .status(404)
        .json({ found: false, message: "No matching fact found" });
    }

    const record = result.records[0];
    res.json({
      found: true,
      statement: record.get("statement"),
      confidence: record.get("confidence"),
      id: record.get("id"),
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  } finally {
    await session.close();
  }
});

// Export Subgraph (Basic JSON-LD)
app.get("/api/export/:factId", async (req, res) => {
  const { factId } = req.params;

  // Validate ID
  const validation = validateNodeId(factId);
  if (!validation.valid) {
    return sendError(res, validation.error, "INVALID_ID", 400);
  }

  const session = neo4jDriver.session();

  try {
    // Use centralized query builder to prevent duplication
    const querySpec = cypherBuilder.buildGetFactQuery(factId);
    const result = await session.run(querySpec.query, querySpec.params);

    if (result.records.length === 0)
      return sendError(res, "Fact not found", "FACT_NOT_FOUND", 404);

    const record = result.records[0];
    const fact = record.get("f").properties;
    const articles = record
      .get("articles")
      .filter((a) => a !== null)
      .map((a) => a.properties);

    // Simple JSON-LD construction
    const jsonld = {
      "@context": "https://schema.org",
      "@type": "ClaimReview", // Schema.org standard (cannot change)
      factReviewed:
        fact.text || `${fact.subject} ${fact.predicate} ${fact.object}`,
      reviewRating: {
        "@type": "Rating",
        ratingValue: fact.confidence,
        bestRating: 1.0,
        worstRating: 0.0,
      },
      itemReviewed: {
        "@type": "CreativeWork",
        sources: articles,
      },
    };

    res.json(jsonld);
  } catch (error) {
    sendError(res, error.message, "EXPORT_ERROR", 500);
  } finally {
    await session.close();
  }
});

// Get Fact Subgraph for Frontend (Graph visualization)
app.get("/api/fact_graph/:factId", async (req, res) => {
  const { factId } = req.params;

  // Validate ID
  const validation = validateNodeId(factId);
  if (!validation.valid) {
    return sendError(res, validation.error, "INVALID_ID", 400);
  }

  const session = neo4jDriver.session();

  try {
    // Use centralized query builder to prevent duplication
    const querySpec = cypherBuilder.buildGetFactQuery(factId);
    const result = await session.run(querySpec.query, querySpec.params);

    if (result.records.length === 0) {
      return sendError(res, "Fact not found", "FACT_NOT_FOUND", 404);
    }

    const nodes = new Map();
    const edges = [];

    // Add central fact
    const factNode = result.records[0].get("f");
    const factProps = factNode.properties;

    nodes.set(factProps.id, {
      data: {
        id: factProps.id,
        label:
          factProps.text ||
          `${factProps.subject} ${factProps.predicate} ${factProps.object}`,
        type: "Fact",
        details: {
          confidence: (factProps.confidence * 100).toFixed(1),
          text: factProps.text,
          subject: factProps.subject,
          predicate: factProps.predicate,
          object: factProps.object,
        },
      },
    });

    // Add related articles
    const articles = result.records[0].get("articles");
    articles.forEach((article) => {
      if (article !== null) {
        const aProps = article.properties;
        const aid =
          aProps.id || `article_${Math.random().toString(36).substr(2, 9)}`;

        nodes.set(aid, {
          data: {
            id: aid,
            label: aProps.title || "Unknown Article",
            type: "Article",
            url: aProps.url,
            date: aProps.date,
            is_reference: aProps.is_reference,
          },
        });

        // Add edge: Article -[ASSERTED]-> Fact
        edges.push({
          data: {
            source: aid,
            target: factProps.id,
            label: "ASSERTED",
          },
        });
      }
    });

    return sendSuccess(res, { elements: [...nodes.values(), ...edges] });
  } catch (error) {
    console.error("Graph Fetch Error:", error);
    sendError(res, error.message, "GRAPH_ERROR", 500);
  } finally {
    await session.close();
  }
});

// Generalized Neighbor Fetch for Progressive Reveal (Query nodes by ID)
app.get("/api/graph/neighbors/:id", async (req, res) => {
  const { id } = req.params;

  // Validate ID
  const validation = validateNodeId(id);
  if (!validation.valid) {
    return sendError(res, validation.error, "INVALID_ID", 400);
  }

  const session = neo4jDriver.session();
  try {
    // Use centralized query builder to prevent duplication
    const querySpec = cypherBuilder.buildGetNeighborsQuery(id, 25);
    const result = await session.run(querySpec.query, querySpec.params);

    const nodes = new Map();
    const edges = [];

    result.records.forEach((record) => {
      const source = record.get("n");
      const neighbor = record.get("neighbor");
      const rel = record.get("r");

      [source, neighbor].forEach((node) => {
        if (!node) return; // Skip null neighbors

        const props = node.properties;
        if (!nodes.has(props.id)) {
          let type = "Unknown";
          if (node.labels && node.labels.includes("Fact")) type = "Fact";
          else if (node.labels && node.labels.includes("Article"))
            type = "Article";

          nodes.set(props.id, {
            data: {
              id: props.id,
              label: props.text || props.title || props.name || "Unknown",
              type: type,
              ...props,
            },
          });
        }
      });

      if (rel && neighbor && source) {
        edges.push({
          data: {
            source: source.properties.id,
            target: neighbor.properties.id,
            label: rel.type,
          },
        });
      }
    });

    sendSuccess(res, { elements: [...nodes.values(), ...edges] });
  } catch (error) {
    console.error("Neighbors Fetch Error:", error);
    sendError(res, error.message, "NEIGHBORS_ERROR", 500);
  } finally {
    await session.close();
  }
});

// ===== HEALTH CHECK & CONSISTENCY VERIFICATION =====
const DataConsistencyChecker = require("./consistency-checker");
let consistencyChecker = null;

// ✅ NEW: Scheduled Consistency Checks (every 10 minutes)
let consistencyCheckInterval = null;

function startScheduledConsistencyChecks() {
  if (consistencyCheckInterval) {
    console.log("⚠️  Consistency check already scheduled");
    return;
  }

  console.log("🔄 Starting scheduled consistency checks (every 10 minutes)...");

  consistencyCheckInterval = setInterval(async () => {
    try {
      if (!consistencyChecker) {
        if (neo4jDriver && pgPool) {
          try {
            consistencyChecker = new DataConsistencyChecker(
              neo4jDriver,
              pgPool
            );
          } catch (err) {
            console.warn(
              "[CONSISTENCY CHECK] Failed to initialize checker:",
              err.message
            );
            return;
          }
        } else {
          console.debug(
            "[CONSISTENCY CHECK] Databases not ready, skipping check"
          );
          return;
        }
      }

      console.log("📊 Running scheduled consistency check...");
      const issues = await consistencyChecker.runAllChecks();

      // Log summary
      console.log(
        `[CONSISTENCY CHECK] Issues found: ${issues.summary.totalIssues}`
      );

      // Alert on critical issues
      if (issues.summary.criticalIssues > 0) {
        console.error("🚨 CRITICAL DATA INCONSISTENCIES DETECTED");
        console.error(`   Total Issues: ${issues.summary.totalIssues}`);
        console.error(`   Critical: ${issues.summary.criticalIssues}`);
        console.error(`   Warnings: ${issues.summary.warningIssues}`);

        // Log each critical issue for debugging
        if (issues.issues && Array.isArray(issues.issues)) {
          issues.issues.forEach((issue) => {
            if (issue.severity === "critical") {
              console.error(`   ❌ ${issue.category}: ${issue.message}`);
            }
          });
        }

        // ✅ TODO: Add webhook/alert integration here (email, Slack, PagerDuty, etc.)
        // Example:
        // await sendAlertToSlack({
        //   title: "Critical Data Inconsistencies",
        //   issues: issues,
        //   timestamp: new Date().toISOString()
        // });
      } else if (issues.summary.warningIssues > 0) {
        console.warn(
          `⚠️  Data consistency warnings: ${issues.summary.warningIssues}`
        );
      } else {
        console.log("✅ All data consistency checks passed");
      }
    } catch (err) {
      console.error(
        "[CONSISTENCY CHECK] Error during scheduled check:",
        err.message
      );
    }
  }, 600000); // 10 minutes = 600000 ms

  console.log("✅ Scheduled consistency checks started");
}

app.get("/health", async (req, res) => {
  const health = {
    status: "starting",
    timestamp: new Date().toISOString(),
    components: {
      server: "running",
      neo4j: "checking",
      postgres: "checking",
      ai_engine: "checking",
    },
    issues: [],
  };

  try {
    // Check Neo4j
    try {
      if (!neo4jDriver) {
        health.components.neo4j = "not_initialized";
        health.issues.push({
          component: "neo4j",
          error: "Neo4j driver not initialized",
        });
      } else {
        try {
          const serverInfo = await neo4jDriver.getServerInfo();
          health.components.neo4j = "connected";
          health.components.neo4j_version = serverInfo.version;
        } catch (connectionErr) {
          health.components.neo4j = "error";
          health.issues.push({
            component: "neo4j",
            error: `Connection failed: ${connectionErr.message}`,
          });
        }
      }
    } catch (err) {
      health.components.neo4j = "error";
      health.issues.push({ component: "neo4j", error: err.message });
    }

    // Check PostgreSQL
    try {
      if (!pgPool) {
        health.components.postgres = "not_configured";
        health.issues.push({
          component: "postgres",
          error: "DATABASE_URL not set",
        });
      } else {
        try {
          const result = await pgPool.query("SELECT NOW()");
          health.components.postgres = "connected";
        } catch (queryErr) {
          // Connection pool exists but query failed
          health.components.postgres = "error";
          health.issues.push({
            component: "postgres",
            error: `Query failed: ${queryErr.message}`,
          });
        }
      }
    } catch (err) {
      // Pool initialization or other error
      health.components.postgres = "error";
      health.issues.push({ component: "postgres", error: err.message });
    }

    // Check AI Engine
    try {
      const pingResp = await axios.get(`${AI_ENGINE_URL}/`, {
        timeout: 5000,
      });
      if (pingResp.status === 200) {
        health.components.ai_engine = "responding";
      } else {
        health.components.ai_engine = "error";
        health.issues.push({
          component: "ai_engine",
          error: `Unexpected status: ${pingResp.status}`,
        });
      }
    } catch (err) {
      if (err.code === "ECONNREFUSED" || err.code === "ETIMEDOUT") {
        health.components.ai_engine = "unreachable";
        health.issues.push({
          component: "ai_engine",
          error: `Cannot reach AI Engine at ${AI_ENGINE_URL}: ${err.message}`,
        });
      } else {
        health.components.ai_engine = "error";
        health.issues.push({
          component: "ai_engine",
          error: err.message,
        });
      }
    }

    // Run consistency check if both databases OK
    if (
      health.components.neo4j === "connected" &&
      health.components.postgres === "connected" &&
      pgPool
    ) {
      if (!consistencyChecker) {
        try {
          consistencyChecker = new DataConsistencyChecker(neo4jDriver, pgPool);
        } catch (err) {
          console.warn(
            "Could not initialize consistency checker:",
            err.message
          );
        }
      }

      if (consistencyChecker) {
        try {
          const consistencyResults = await consistencyChecker.runAllChecks();
          health.consistency = {
            checked: consistencyResults.timestamp,
            summary: consistencyResults.summary,
            hasIssues: consistencyResults.summary.totalIssues > 0,
          };

          if (consistencyResults.summary.criticalIssues > 0) {
            health.issues.push({
              component: "consistency",
              severity: "critical",
              count: consistencyResults.summary.criticalIssues,
            });
          }
        } catch (consistencyErr) {
          console.error("Consistency check failed:", consistencyErr);
          health.issues.push({
            component: "consistency",
            severity: "error",
            error: consistencyErr.message,
          });
          health.consistency = {
            checked: new Date().toISOString(),
            error: "Consistency check failed",
            summary: {
              totalIssues: 0,
              criticalIssues: 0,
              highSeverityIssues: 0,
            },
            hasIssues: false,
          };
        }
      }
    }

    // Determine overall status
    const allConnected =
      health.components.neo4j === "connected" &&
      (health.components.postgres === "connected" ||
        health.components.postgres === "not_configured");

    health.status = allConnected
      ? health.issues.length === 0
        ? "healthy"
        : "degraded"
      : "unhealthy";

    const statusCode = health.status === "healthy" ? 200 : 503;
    res.status(statusCode).json(health);
  } catch (error) {
    health.status = "error";
    health.error = error.message;
    res.status(503).json(health);
  }
});

app.listen(PORT, () => {
  console.log("=".repeat(60));
  console.log(`✅ Truth Graph Server Running`);
  console.log("=".repeat(60));
  console.log(`   Port: ${PORT}`);
  console.log(`   AI Engine: ${AI_ENGINE_URL}`);
  console.log(`   Status: http://localhost:${PORT}`);
  console.log("");

  // ✅ NEW: Start scheduled consistency checks
  if (neo4jDriver && pgPool) {
    startScheduledConsistencyChecks();
  } else {
    console.warn(
      "⚠️  Cannot start consistency checks - databases not connected"
    );
  }

  const path = require("path");

  // ... (existing code)

  // Note: link_facts.py removed - embeddings are already synced via push_to_neo4j.js
});

// ===== GLOBAL ERROR HANDLER =====
app.use((err, req, res, next) => {
  console.error("🔴 UNHANDLED ERROR:", err);
  console.error("   Stack:", err.stack);
  res.status(500).json({
    success: false,
    error: "Internal server error",
    message: err.message,
    ...(process.env.NODE_ENV === "development" && { stack: err.stack }),
  });
});

// Handle uncaught exceptions
process.on("unhandledRejection", (reason, promise) => {
  console.error("🔴 UNHANDLED PROMISE REJECTION:", reason);
});

process.on("uncaughtException", (error) => {
  console.error("🔴 UNCAUGHT EXCEPTION:", error);
  process.exit(1);
});

// ===== EXPORT FOR MODULE REUSE =====
module.exports = { pgPool, neo4jDriver };
