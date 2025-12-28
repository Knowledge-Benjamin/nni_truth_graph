const express = require('express');
const cors = require('cors');
const dotenv = require('dotenv');
const { driver, auth } = require('neo4j-driver');
const { exec } = require('child_process');

dotenv.config();

const app = express();
app.use(cors());
app.use(express.json());

const PORT = 3000;
const AI_ENGINE_URL = process.env.AI_ENGINE_URL || 'http://localhost:8001';

// Neo4j Aura Connection
console.log('='.repeat(60));
console.log('Neo4j Connection Configuration');
console.log('='.repeat(60));
console.log(`URI: ${process.env.NEO4J_URI}`);
console.log(`User: ${process.env.NEO4J_USER}`);
console.log(`Password: ${process.env.NEO4J_PASSWORD ? process.env.NEO4J_PASSWORD.substring(0, 4) + '...' : 'NOT SET'}`);
console.log('');

const neo4jDriver = driver(
  process.env.NEO4J_URI,
  auth.basic(process.env.NEO4J_USER, process.env.NEO4J_PASSWORD)
);

// Test connection and setup schema immediately
(async () => {
  const session = neo4jDriver.session();
  try {
    console.log('🔄 Testing Neo4j Aura connection...');
    const serverInfo = await neo4jDriver.getServerInfo();
    console.log(`✅ Connected to Neo4j Aura!`);
    console.log(`   Address: ${serverInfo.address}`);
    console.log(`   Version: ${serverInfo.agent}`);

    // Auto-create Fulltext Index for Relevance
    console.log('🔍 Verifying Schema...');
    const indexCheck = await session.run("SHOW INDEXES YIELD name WHERE name = 'claim_statement_fulltext'");
    if (indexCheck.records.length === 0) {
      console.log('⚡ Creating fulltext index for search relevance...');
      await session.run("CREATE FULLTEXT INDEX claim_statement_fulltext IF NOT EXISTS FOR (c:Claim) ON EACH [c.statement]");
      console.log('✅ Fulltext index created.');
    } else {
      console.log('✅ Fulltext index ready.');
    }
    console.log('');
  } catch (error) {
    console.error('❌ Neo4j connection/schema failed:');
    console.error(`   ${error.message}`);
    console.error('');
    console.error('Troubleshooting:');
    console.error('  1. Check instance status at console.neo4j.io');
    console.error('  2. Verify credentials in server/.env');
    console.error('  3. Ensure URI starts with neo4j+s://');
    console.error('');
  } finally {
    await session.close();
  }
})();

const axios = require('axios');

app.get('/', async (req, res) => {
  let dbStatus = 'Disconnected';
  try {
    const serverInfo = await neo4jDriver.getServerInfo();
    dbStatus = `Connected to ${serverInfo.address}`;
  } catch (err) {
    dbStatus = `Error: ${err.message}`;
  }
  res.json({ status: 'Core Server Running', db: dbStatus, ai: AI_ENGINE_URL });
});

// Ingest Article & Extract Claims
app.post('/api/ingest', async (req, res) => {
  const { text, title } = req.body;

  if (!text) return res.status(400).json({ error: 'Text required' });

  try {
    // 1. Call AI Engine
    console.log('Sending text to AI Engine...');
    const aiResponse = await axios.post(`${AI_ENGINE_URL}/extract_claims`, {
      content: text,
      article_id: title || 'unknown'
    });
    const claims = aiResponse.data;

    // 2. Save to Neo4j
    const session = neo4jDriver.session();
    try {
      await session.writeTransaction(async tx => {
        // Create Article Node
        await tx.run(
          `MERGE (a:Article {title: $title}) SET a.text = $text, a.timestamp = datetime()`,
          { title: title || 'Untitled', text: text.substring(0, 100) + '...' }
        );

        // Create Claim Nodes & Relationships
        for (const claim of claims) {
          await tx.run(
            `
            MATCH (a:Article {title: $title})
            MERGE (c:Claim {statement: $stmt})
            SET c.confidence = $conf
            MERGE (a)-[:MENTIONS]->(c)
            `,
            { title: title || 'Untitled', stmt: claim.statement, conf: claim.confidence }
          );
        }
      });
      res.json({ success: true, claims_extracted: claims.length, data: claims });
    } finally {
      await session.close();
    }
  } catch (error) {
    console.error('Ingestion Error:', error.message);
    res.status(500).json({ error: error.message });
  }
});

// Natural Language Query
app.post('/api/query/natural', async (req, res) => {
  const { query } = req.body;
  if (!query) return res.status(400).json({ error: 'Query required' });

  try {
    // 1. Call AI Engine to translate NL -> Cypher
    console.log(` Translating query: "${query}"...`);
    const translationResp = await axios.post(`${AI_ENGINE_URL}/translate_query`, { query });
    const { query: cypherQuery, explanation } = translationResp.data;

    if (!cypherQuery) {
      return res.status(500).json({ error: 'Failed to generate Cypher query' });
    }

    console.log(` Executing Cypher: ${cypherQuery}`);

    // 2. Execute on Neo4j
    const session = neo4jDriver.session();
    try {
      const result = await session.run(cypherQuery);

      // Format results
      const records = result.records.map(record => {
        return record.keys.reduce((acc, key) => {
          const val = record.get(key);
          // Handle Neo4j Integers and Nodes
          if (val && val.low !== undefined) acc[key] = val.low; // simple integer handling
          else if (val && val.labels) acc[key] = val.properties; // Node
          else acc[key] = val;
          return acc;
        }, {});
      });

      res.json({
        cypher: cypherQuery,
        explanation: explanation,
        results: records
      });
    } finally {
      await session.close();
    }
  } catch (error) {
    console.error('Query Error:', error.message);
    if (error.response) console.error('AI Engine Response:', error.response.data);
    res.status(500).json({ error: 'Search failed', details: error.message });
  }
});

// Verify Single Claim
app.get('/api/verify', async (req, res) => {
  const { claim } = req.query;
  if (!claim) return res.status(400).json({ error: 'Claim query required' });

  const session = neo4jDriver.session();
  try {
    const result = await session.run(`
      MATCH (c:Claim)
      WHERE toLower(c.statement) CONTAINS toLower($claim)
      RETURN c.statement as statement, c.confidence as confidence, c.first_seen as first_seen, c.id as id
      ORDER BY c.confidence DESC
      LIMIT 1
    `, { claim });

    if (result.records.length === 0) {
      return res.status(404).json({ found: false, message: "No matching claim found" });
    }

    const record = result.records[0];
    res.json({
      found: true,
      statement: record.get('statement'),
      confidence: record.get('confidence'),
      first_seen: record.get('first_seen'),
      id: record.get('id')
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  } finally {
    await session.close();
  }
});

// Export Subgraph (Basic JSON-LD)
app.get('/api/export/:claimId', async (req, res) => {
  const { claimId } = req.params;
  const session = neo4jDriver.session();

  try {
    const result = await session.run(`
      MATCH (c:Claim {id: $id})
      OPTIONAL MATCH (c)-[r]-(related)
      RETURN c, collect(related) as neighbors
    `, { id: claimId });

    if (result.records.length === 0) return res.status(404).json({ error: "Claim not found" });

    const record = result.records[0];
    const claim = record.get('c').properties;
    const neighbors = record.get('neighbors').map(n => n.properties);

    // Simple JSON-LD construction
    const jsonld = {
      "@context": "https://schema.org",
      "@type": "ClaimReview",
      "claimReviewed": claim.statement,
      "reviewRating": {
        "@type": "Rating",
        "ratingValue": claim.confidence,
        "bestRating": 1.0,
        "worstRating": 0.0
      },
      "itemReviewed": {
        "@type": "CreativeWork",
        "datePublished": claim.first_seen,
        "relatedItems": neighbors
      }
    };

    res.json(jsonld);
  } catch (error) {
    res.status(500).json({ error: error.message });
  } finally {
    await session.close();
  }
});

// Get Claim Subgraph for Frontend
app.get('/api/claim_graph/:claimId', async (req, res) => {
  const { claimId } = req.params;
  const session = neo4jDriver.session();

  try {
    // Fetch Claim, its Neighbors (Source, Article), and relationships
    // 1-hop for now to keep it clean, maybe 2-hop later
    const result = await session.run(`
      MATCH (c:Claim {id: $id})
      OPTIONAL MATCH (c)-[r]-(n)
      RETURN c, r, n
    `, { id: claimId });

    if (result.records.length === 0) return res.status(404).json({ error: "Claim not found" });

    const nodes = new Map();
    const edges = [];

    // Add central claim
    const centralClaim = result.records[0].get('c');
    nodes.set(centralClaim.properties.id, {
      data: {
        id: centralClaim.properties.id,
        label: centralClaim.properties.statement,
        type: 'Claim',
        details: {
          confidence: (centralClaim.properties.confidence * 100).toFixed(1),
          text: centralClaim.properties.statement,
          first_seen: centralClaim.properties.first_seen
        }
      }
    });

    result.records.forEach(record => {
      const neighbor = record.get('n');
      const rel = record.get('r');

      if (neighbor && rel) {
        // Add neighbor node
        const nProps = neighbor.properties;
        let type = 'Source'; // default
        if (neighbor.labels.includes('Article')) type = 'Article';
        else if (neighbor.labels.includes('Source')) {
          // Refine Source label needed on backend or infer from relationship?
          // Actually, the frontend infers color from type.
          // We can check relationship type to hint node type
          if (rel.type === 'VERIFIED_BY') type = 'VerifiedSource';
          else if (rel.type === 'SUPPORTS') type = 'SupportSource';
          else if (rel.type === 'CONTRADICTS') type = 'ContradictSource';
        }

        if (!nodes.has(nProps.id || neighbor.elementId)) { // Fallback ID
          const nid = nProps.id || `node_${neighbor.elementId}`; // Use generated ID if missing
          // Ideally all nodes have IDs. Articles might fallback to title.
          const label = nProps.title || nProps.publisher || "Unknown";

          nodes.set(nid, {
            data: {
              id: nid,
              label: label,
              type: type,
              url: nProps.url,
              date: nProps.published_date,
              snippet: nProps.snippet
            }
          });
        }

        // Add Edge
        edges.push({
          data: {
            source: rel.startNodeElementId === centralClaim.elementId ? centralClaim.properties.id : (neighbor.properties.id || `node_${neighbor.elementId}`),
            target: rel.endNodeElementId === centralClaim.elementId ? centralClaim.properties.id : (neighbor.properties.id || `node_${neighbor.elementId}`),
            label: rel.type
          }
        });
      }
    });

    res.json({ elements: [...nodes.values(), ...edges] });
  } catch (error) {
    console.error("Graph Fetch Error:", error);
    res.status(500).json({ error: error.message });
  } finally {
    await session.close();
  }
});


// Generalized Neighbor Fetch for Progressive Reveal
app.get('/api/graph/neighbors/:id', async (req, res) => {
  const { id } = req.params;
  const session = neo4jDriver.session();
  try {
    // Match any node by ID property
    const result = await session.run(`
      MATCH (n {id: $id})
      MATCH (n)-[r]-(neighbor)
      RETURN n, r, neighbor
      LIMIT 25
    `, { id });

    const nodes = new Map();
    const edges = [];

    result.records.forEach(record => {
      const source = record.get('n');
      const neighbor = record.get('neighbor');
      const rel = record.get('r');

      [source, neighbor].forEach(node => {
        const props = node.properties;
        if (!nodes.has(props.id)) {
          let type = 'Source';
          if (node.labels.includes('Claim')) type = 'Claim';
          else if (node.labels.includes('Article')) type = 'Article';
          else if (node.labels.includes('Entity')) type = 'Entity';

          // Refine Source/Entity
          // Simple mapping for visualizer

          nodes.set(props.id, {
            data: {
              id: props.id,
              label: props.statement || props.title || props.name || props.publisher || "Unknown",
              type: type,
              // Keep extra props
              ...props
            }
          });
        }
      });

      edges.push({
        data: {
          source: rel.startNodeElementId === source.elementId ? source.properties.id : neighbor.properties.id,
          target: rel.endNodeElementId === source.elementId ? source.properties.id : neighbor.properties.id,
          label: rel.type
        }
      });
    });

    res.json({ elements: [...nodes.values(), ...edges] });
  } catch (error) {
    res.status(500).json({ error: error.message });
  } finally {
    await session.close();
  }
});

app.listen(PORT, () => {
  console.log('='.repeat(60));
  console.log(`✅ Truth Graph Server Running`);
  console.log('='.repeat(60));
  console.log(`   Port: ${PORT}`);
  console.log(`   AI Engine: ${AI_ENGINE_URL}`);
  console.log(`   Status: http://localhost:${PORT}`);
  console.log('');

  const path = require('path');

  // ... (existing code)

  // Schedule Nightly Job (Auto-Linking)
  console.log('⏰ Scheduling nightly optimization job...');
  const runLinkingJob = () => {
    console.log('🔄 Starting nightly claim linking...');
    const scriptPath = path.join(__dirname, '../scripts/link_claims.py');
    console.log(`   Script Path: ${scriptPath}`);
    exec(`python "${scriptPath}"`, (error, stdout, stderr) => {
      if (error) console.error(`❌ Linking Job Error: ${error.message}`);
      if (stderr) console.error(`⚠️ Linking Job Stderr: ${stderr}`);
      if (stdout) console.log(`✅ Linking Job Output:\n${stdout}`);
    });
  };

  // Run every 24 hours
  setInterval(runLinkingJob, 24 * 60 * 60 * 1000);

  // Run once on startup (1 min delay) to sync
  setTimeout(runLinkingJob, 60 * 1000);
});
