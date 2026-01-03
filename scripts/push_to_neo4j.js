const fs = require("fs");
const path = require("path");

// Use standard Node.js module resolution
// Dependencies should be installed via: npm install (in scripts directory)
const dotenv = require("dotenv");
const { driver, auth } = require("neo4j-driver");

// Import the shared cypher builder (from server module)
const cypherBuilder = require("../server/cypher-builder");

// Load Server Env (for Neo4j Creds)
dotenv.config({ path: path.join(__dirname, "../server/.env") });

const DATA_FILE = path.join(__dirname, "temp_graph_data.json");

async function pushToNeo4j() {
  if (!fs.existsSync(DATA_FILE)) {
    console.error(`❌ Data file not found: ${DATA_FILE}`);
    process.exit(1);
  }

  const rawData = fs.readFileSync(DATA_FILE);
  const { facts, articles, assertions } = JSON.parse(rawData);

  console.log(`🔌 Connecting to Neo4j: ${process.env.NEO4J_URI}`);

  // Use exact same config as server
  const neoDriver = driver(
    process.env.NEO4J_URI,
    auth.basic(process.env.NEO4J_USER, process.env.NEO4J_PASSWORD)
  );

  const session = neoDriver.session();

  try {
    // 1. Constraints - use query builder for consistency
    console.log("🛡️  Applying Constraints...");
    const factConstraintSpec = cypherBuilder.buildCreateFactConstraintQuery();
    await session.run(factConstraintSpec.query, factConstraintSpec.params);

    const articleConstraintSpec =
      cypherBuilder.buildCreateArticleConstraintQuery();
    await session.run(
      articleConstraintSpec.query,
      articleConstraintSpec.params
    );

    // 2. Articles
    console.log(`📡 Syncing ${articles.length} Articles...`);
    for (const art of articles) {
      const mergeArticleSpec = cypherBuilder.buildMergeArticleQuery(art);
      await session.run(mergeArticleSpec.query, mergeArticleSpec.params);
    }

    // 3. Facts
    console.log(`🧠 Syncing ${facts.length} Facts...`);
    for (const f of facts) {
      const mergeFactSpec = cypherBuilder.buildMergeFactQuery(f);
      await session.run(mergeFactSpec.query, mergeFactSpec.params);
    }

    // 4. Edges
    console.log(`🔗 Building ${assertions.length} Relationships...`);
    for (const edge of assertions) {
      // Logic: cid is the claim ID. prov_id is provenance. is_orig is bool.
      // Target Fact ID = cid (if orig) else prov_id
      const targetFactId = edge.is_original ? edge.id : edge.provenance_id;

      if (targetFactId && edge.article_id) {
        const createAssertionSpec = cypherBuilder.buildCreateAssertionQuery(
          edge.article_id,
          targetFactId
        );
        await session.run(
          createAssertionSpec.query,
          createAssertionSpec.params
        );
      }
    }

    console.log("✅ Graph Sync Complete (JS).");
  } catch (err) {
    console.error("❌ Sync Error:", err);
    process.exit(1);
  } finally {
    await session.close();
    await neoDriver.close();
  }
}

pushToNeo4j();
