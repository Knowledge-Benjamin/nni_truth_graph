/**
 * Cypher Query Builder
 * Centralized module for constructing and managing Neo4j Cypher queries
 *
 * Benefits:
 * - Single source of truth for query patterns
 * - Prevents duplication across endpoints
 * - Easier to update schema-wide (e.g., property names)
 * - Built-in parameter injection and sanitization
 * - Consistent query structure and performance
 */

/**
 * Search Facts by fulltext or hybrid search with scoring
 *
 * @param {Object} options
 * @param {string} options.fulltextQuery - Search term
 * @param {boolean} options.useHybrid - Whether to use hybrid scoring (default: true)
 * @param {number} options.limit - Result limit (default: 15)
 * @returns {Object} { query, params }
 */
function buildSearchFactsQuery({
  fulltextQuery,
  useHybrid = true,
  limit = 15,
} = {}) {
  const params = {
    fulltextQuery: fulltextQuery || "",
    limit,
  };

  if (useHybrid) {
    // Hybrid query with confidence-based scoring
    return {
      query: `
        // 1. Search Facts by fulltext (embedding index not yet implemented)
        MATCH (f:Fact)
        WHERE toLower(f.text) CONTAINS toLower($fulltextQuery)
           OR toLower(f.subject) CONTAINS toLower($fulltextQuery)
           OR toLower(f.predicate) CONTAINS toLower($fulltextQuery)
           OR toLower(f.object) CONTAINS toLower($fulltextQuery)
        
        // 2. TruthRank Scoring
        WITH f,
             (CASE WHEN f.confidence > 0.8 THEN 1.2 ELSE 1.0 END) AS confBoost,
             (CASE WHEN f.confidence > 0.9 THEN 1.5 ELSE 1.0 END) AS highConfBoost
        
        WITH f, (f.confidence * confBoost * highConfBoost) AS finalScore
        ORDER BY finalScore DESC
        LIMIT $limit
        
        RETURN f as fact, finalScore as relevance
      `,
      params,
    };
  } else {
    // Fallback: Simple fulltext search on Facts
    return {
      query: `
        MATCH (f:Fact)
        WHERE toLower(f.text) CONTAINS toLower($fulltextQuery)
           OR toLower(f.subject) CONTAINS toLower($fulltextQuery)
           OR toLower(f.predicate) CONTAINS toLower($fulltextQuery)
           OR toLower(f.object) CONTAINS toLower($fulltextQuery)
        RETURN f as fact, f.confidence as relevance
        ORDER BY relevance DESC
        LIMIT $limit
      `,
      params,
    };
  }
}

/**
 * Get a specific Fact by ID with related Articles
 *
 * @param {string} factId - Fact ID
 * @returns {Object} { query, params }
 */
function buildGetFactQuery(factId) {
  return {
    query: `
      MATCH (f:Fact {id: $id})
      OPTIONAL MATCH (a:Article)-[:ASSERTED]->(f)
      RETURN f, collect(a) as articles
    `,
    params: { id: factId },
  };
}

/**
 * Get neighbors of a node (progressive reveal for graph visualization)
 *
 * @param {string} nodeId - Node ID (Fact or Article)
 * @param {number} limit - Maximum records to return
 * @returns {Object} { query, params }
 */
function buildGetNeighborsQuery(nodeId, limit = 25) {
  return {
    query: `
      MATCH (n)
      WHERE n.id = $id AND (n:Fact OR n:Article)
      OPTIONAL MATCH (n)-[r]-(neighbor)
      WHERE neighbor:Fact OR neighbor:Article
      RETURN n, r, neighbor
      LIMIT $limit
    `,
    params: { id: nodeId, limit },
  };
}

/**
 * Get node count (health check)
 *
 * @returns {Object} { query, params }
 */
function buildHealthCheckQuery() {
  return {
    query: `MATCH (n) RETURN count(n) as nodeCount`,
    params: {},
  };
}

/**
 * Get all Facts with pagination
 *
 * @param {Object} options
 * @param {number} options.skip - Records to skip
 * @param {number} options.limit - Records to return
 * @returns {Object} { query, params }
 */
function buildListFactsQuery({ skip = 0, limit = 20 } = {}) {
  return {
    query: `
      MATCH (f:Fact)
      RETURN f
      ORDER BY f.confidence DESC
      SKIP $skip
      LIMIT $limit
    `,
    params: { skip, limit },
  };
}

/**
 * Get all Articles with pagination
 *
 * @param {Object} options
 * @param {number} options.skip - Records to skip
 * @param {number} options.limit - Records to return
 * @returns {Object} { query, params }
 */
function buildListArticlesQuery({ skip = 0, limit = 20 } = {}) {
  return {
    query: `
      MATCH (a:Article)
      RETURN a
      ORDER BY a.published_date DESC
      SKIP $skip
      LIMIT $limit
    `,
    params: { skip, limit },
  };
}

/**
 * Get relationship statistics (facts per article, etc.)
 *
 * @returns {Object} { query, params }
 */
function buildRelationshipStatsQuery() {
  return {
    query: `
      MATCH (a:Article)-[rel]->(f:Fact)
      RETURN 
        count(DISTINCT a) as totalArticles,
        count(DISTINCT f) as totalFacts,
        count(rel) as totalAssertions,
        count(rel) / count(DISTINCT a) as avgAssertionsPerArticle
    `,
    params: {},
  };
}

/**
 * Get Facts by subject (knowledge graph entity)
 *
 * @param {string} subject - Subject entity name
 * @param {number} limit - Result limit
 * @returns {Object} { query, params }
 */
function buildFactsBySubjectQuery(subject, limit = 20) {
  return {
    query: `
      MATCH (f:Fact)
      WHERE toLower(f.subject) = toLower($subject)
      RETURN f
      ORDER BY f.confidence DESC
      LIMIT $limit
    `,
    params: { subject, limit },
  };
}

/**
 * Get contradiction relationships between Facts
 *
 * @param {number} minConfidence - Minimum confidence threshold
 * @param {number} limit - Result limit
 * @returns {Object} { query, params }
 */
function buildContradictionsQuery(minConfidence = 0.5, limit = 50) {
  return {
    query: `
      MATCH (f1:Fact)-[rel:CONTRADICTS]-(f2:Fact)
      WHERE f1.confidence > $minConfidence AND f2.confidence > $minConfidence
      RETURN f1, rel, f2, f1.confidence as conf1, f2.confidence as conf2
      ORDER BY rel.weight DESC
      LIMIT $limit
    `,
    params: { minConfidence, limit },
  };
}

/**
 * Get evolution chain for a fact (temporal changes)
 *
 * @param {string} factId - Starting fact ID
 * @param {number} limit - Result limit
 * @returns {Object} { query, params }
 */
function buildFactEvolutionQuery(factId, limit = 25) {
  return {
    query: `
      MATCH path = (f:Fact {id: $id})-[*]->(evolved:Fact)
      RETURN f, evolved, length(path) as distance
      ORDER BY distance
      LIMIT $limit
    `,
    params: { id: factId, limit },
  };
}

/**
 * Full schema introspection (for debugging/validation)
 *
 * @returns {Object} { query, params }
 */
function buildSchemaIntrospectionQuery() {
  return {
    query: `
      MATCH (n)
      WITH labels(n) as label
      RETURN DISTINCT label
      ORDER BY label
    `,
    params: {},
  };
}

/**
 * Get all Fact nodes by multiple search criteria (simple keyword search)
 *
 * @param {Object} options
 * @param {string} options.searchTerm - Text to search for
 * @param {number} options.limit - Result limit
 * @returns {Object} { query, params }
 */
function buildSearchFactsByKeywordQuery({ searchTerm, limit = 1 } = {}) {
  return {
    query: `
      MATCH (f:Fact)
      WHERE toLower(f.text) CONTAINS toLower($searchTerm)
         OR toLower(f.subject) CONTAINS toLower($searchTerm)
         OR toLower(f.object) CONTAINS toLower($searchTerm)
      RETURN f.text as statement, f.confidence as confidence, f.id as id
      ORDER BY f.confidence DESC
      LIMIT $limit
    `,
    params: { searchTerm, limit },
  };
}

/**
 * Vector similarity search for Facts using embeddings
 *
 * @param {Object} options
 * @param {Array<number>} options.embedding - Query embedding vector (384-dim)
 * @param {number} options.similarityThreshold - Minimum similarity (0.0-1.0, default 0.7)
 * @param {number} options.limit - Result limit
 * @returns {Object} { query, params }
 */
function buildVectorSearchQuery({
  embedding,
  similarityThreshold = 0.7,
  limit = 15,
} = {}) {
  return {
    query: `
      // Vector similarity search using cosine distance
      MATCH (f:Fact)
      WHERE f.embedding IS NOT NULL
      WITH f,
           1 - gds.similarity.cosine(f.embedding, $embedding) AS distance
      WITH f,
           distance,
           (1 - distance) AS similarity
      WHERE similarity >= $similarityThreshold
      ORDER BY similarity DESC
      LIMIT $limit
      RETURN f as fact, similarity as relevance
    `,
    params: {
      embedding,
      similarityThreshold,
      limit,
    },
  };
}

/**
 * Hybrid search combining keyword search and vector similarity
 *
 * @param {Object} options
 * @param {string} options.fulltextQuery - Keyword search term
 * @param {Array<number>} options.embedding - Query embedding vector
 * @param {number} options.vectorWeight - Weight for vector results (0-1, default 0.5)
 * @param {number} options.limit - Result limit
 * @returns {Object} { query, params }
 */
function buildHybridSearchQuery({
  fulltextQuery,
  embedding,
  vectorWeight = 0.5,
  limit = 15,
} = {}) {
  const keywordWeight = 1 - vectorWeight;

  return {
    query: `
      // Hybrid search: Combine keyword and vector similarity scoring
      
      // 1. Keyword search scoring
      MATCH (f1:Fact)
      WHERE toLower(f1.text) CONTAINS toLower($fulltextQuery)
         OR toLower(f1.subject) CONTAINS toLower($fulltextQuery)
         OR toLower(f1.predicate) CONTAINS toLower($fulltextQuery)
         OR toLower(f1.object) CONTAINS toLower($fulltextQuery)
      WITH f1,
           $keywordWeight * f1.confidence AS keywordScore
      
      // 2. Vector similarity scoring
      MATCH (f2:Fact)
      WHERE f2.embedding IS NOT NULL
      WITH f1, keywordScore, f2,
           1 - gds.similarity.cosine(f2.embedding, $embedding) AS distance,
           $vectorWeight * (1 - distance) AS vectorScore
      
      // 3. Combine scores if same fact, or create separate results
      WITH COALESCE(f1, f2) as f,
           COALESCE(keywordScore, 0) + COALESCE(vectorScore, 0) AS hybridScore,
           COALESCE(f1.confidence, f2.confidence) AS confidence
      
      // 4. Apply TruthRank boost
      WITH f,
           hybridScore,
           confidence,
           (CASE WHEN confidence > 0.8 THEN 1.2 ELSE 1.0 END) AS confBoost,
           (CASE WHEN confidence > 0.9 THEN 1.5 ELSE 1.0 END) AS highConfBoost
      
      WITH f,
           (hybridScore * confidence * confBoost * highConfBoost) AS finalScore
      
      ORDER BY finalScore DESC
      LIMIT $limit
      RETURN f as fact, finalScore as relevance
    `,
    params: {
      fulltextQuery,
      embedding,
      keywordWeight,
      vectorWeight,
      limit,
    },
  };
}

/**
 * Create Fact constraint (idempotent)
 *
 * @returns {Object} { query, params }
 */
function buildCreateFactConstraintQuery() {
  return {
    query: `CREATE CONSTRAINT IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE`,
    params: {},
  };
}

/**
 * Create Article constraint (idempotent)
 *
 * @returns {Object} { query, params }
 */
function buildCreateArticleConstraintQuery() {
  return {
    query: `CREATE CONSTRAINT IF NOT EXISTS FOR (a:Article) REQUIRE a.id IS UNIQUE`,
    params: {},
  };
}

/**
 * Create fulltext index for Fact search (idempotent)
 *
 * @returns {Object} { query, params }
 */
function buildCreateFulltextIndexQuery() {
  return {
    query: `CREATE FULLTEXT INDEX fact_statement_fulltext IF NOT EXISTS FOR (f:Fact) ON EACH [f.text]`,
    params: {},
  };
}

/**
 * Create vector index for embeddings (idempotent)
 *
 * @returns {Object} { query, params }
 */
function buildCreateVectorIndexQuery() {
  return {
    query: `
      CREATE VECTOR INDEX fact_embeddings IF NOT EXISTS
      FOR (f:Fact) ON f.embedding
      OPTIONS { indexConfig: { vector.dimensions: 384, vector.similarity_function: 'cosine' } }
    `,
    params: {},
  };
}

/**
 * Check if fulltext index exists
 *
 * @returns {Object} { query, params }
 */
function buildCheckFulltextIndexQuery() {
  return {
    query: `SHOW INDEXES YIELD name WHERE name = 'fact_statement_fulltext'`,
    params: {},
  };
}

/**
 * Check if vector index exists
 *
 * @returns {Object} { query, params }
 */
function buildCheckVectorIndexQuery() {
  return {
    query: `SHOW INDEXES YIELD name WHERE name = 'fact_embeddings'`,
    params: {},
  };
}

/**
 * Merge/Upsert an Article node
 *
 * @param {Object} article - Article data
 * @returns {Object} { query, params }
 */
function buildMergeArticleQuery(article) {
  return {
    query: `
      MERGE (a:Article {id: $id})
      SET a.title = $title, 
          a.url = $url, 
          a.date = toString($date),
          a.is_reference = $is_ref
    `,
    params: {
      id: article.id,
      title: article.title,
      url: article.url,
      date: article.published_date || article.date,
      is_ref: article.is_reference || false,
    },
  };
}

/**
 * Merge/Upsert a Fact node
 *
 * @param {Object} fact - Fact data
 * @returns {Object} { query, params }
 */
function buildMergeFactQuery(fact) {
  // Parse embedding if it's a string
  let embedding = fact.embedding;
  if (typeof embedding === "string") {
    try {
      embedding = JSON.parse(embedding);
    } catch {
      embedding = null;
    }
  }

  return {
    query: `
      MERGE (f:Fact {id: $id})
      SET f.text = $text,
          f.subject = $subject,
          f.predicate = $predicate,
          f.object = $object,
          f.confidence = $confidence,
          f.embedding = $embedding
    `,
    params: {
      id: fact.id,
      text: fact.text || `${fact.subject} ${fact.predicate} ${fact.object}`,
      subject: fact.subject,
      predicate: fact.predicate,
      object: fact.object,
      confidence: fact.confidence,
      embedding,
    },
  };
}

/**
 * Create ASSERTED relationship between Article and Fact
 *
 * @param {string} articleId - Article ID
 * @param {string} factId - Fact ID
 * @returns {Object} { query, params }
 */
function buildCreateAssertionQuery(articleId, factId) {
  return {
    query: `
      MATCH (a:Article {id: $articleId})
      MATCH (f:Fact {id: $factId})
      MERGE (a)-[:ASSERTED]->(f)
    `,
    params: { articleId, factId },
  };
}

/**
 * Count Fact nodes
 *
 * @returns {Object} { query, params }
 */
function buildCountFactsQuery() {
  return {
    query: `MATCH (f:Fact) RETURN count(f) as c`,
    params: {},
  };
}

/**
 * Count Article nodes
 *
 * @returns {Object} { query, params }
 */
function buildCountArticlesQuery() {
  return {
    query: `MATCH (a:Article) RETURN count(a) as c`,
    params: {},
  };
}

/**
 * Count ASSERTED relationships
 *
 * @returns {Object} { query, params }
 */
function buildCountAssertionsQuery() {
  return {
    query: `MATCH ()-[r:ASSERTED]->() RETURN count(r) as c`,
    params: {},
  };
}

/**
 * Count reference Articles
 *
 * @returns {Object} { query, params }
 */
function buildCountReferenceArticlesQuery() {
  return {
    query: `MATCH (a:Article {is_reference: true}) RETURN count(a) as c`,
    params: {},
  };
}

/**
 * Get sample of Article-Fact assertions
 *
 * @param {number} limit - Sample size
 * @returns {Object} { query, params }
 */
function buildSampleAssertionsQuery(limit = 5) {
  return {
    query: `
      MATCH (a:Article)-[:ASSERTED]->(f:Fact)
      RETURN a.title, f.text, a.is_reference
      LIMIT $limit
    `,
    params: { limit },
  };
}

/**
 * Check Fact integrity - confidence out of range
 *
 * @returns {Object} { query, params }
 */
function buildCheckInvalidConfidenceQuery() {
  return {
    query: `
      MATCH (f:Fact)
      WHERE f.confidence < 0 OR f.confidence > 1
      RETURN count(f) as count
    `,
    params: {},
  };
}

/**
 * Check Fact integrity - missing text
 *
 * @returns {Object} { query, params }
 */
function buildCheckMissingTextQuery() {
  return {
    query: `
      MATCH (f:Fact)
      WHERE f.text IS NULL OR f.text = ''
      RETURN count(f) as count
    `,
    params: {},
  };
}

/**
 * Get relationship type statistics
 *
 * @returns {Object} { query, params }
 */
function buildRelationshipTypesQuery() {
  return {
    query: `
      MATCH (f:Fact)-[r]->() 
      RETURN type(r) as relType, count(r) as count
    `,
    params: {},
  };
}

/**
 * Get all Fact IDs (for consistency checks)
 *
 * @returns {Object} { query, params }
 */
function buildGetAllFactIdsQuery() {
  return {
    query: `MATCH (f:Fact) RETURN f.id as id`,
    params: {},
  };
}

module.exports = {
  // Core query builders
  buildSearchFactsQuery,
  buildGetFactQuery,
  buildGetNeighborsQuery,
  buildHealthCheckQuery,
  buildListFactsQuery,
  buildListArticlesQuery,
  buildRelationshipStatsQuery,

  // Advanced queries
  buildFactsBySubjectQuery,
  buildContradictionsQuery,
  buildFactEvolutionQuery,
  buildSchemaIntrospectionQuery,

  // Search & verification
  buildSearchFactsByKeywordQuery,
  buildVectorSearchQuery,
  buildHybridSearchQuery,

  // Schema management
  buildCreateFactConstraintQuery,
  buildCreateArticleConstraintQuery,
  buildCreateFulltextIndexQuery,
  buildCreateVectorIndexQuery,
  buildCheckFulltextIndexQuery,
  buildCheckVectorIndexQuery,

  // Data creation/mutation
  buildMergeArticleQuery,
  buildMergeFactQuery,
  buildCreateAssertionQuery,

  // Counting & statistics
  buildCountFactsQuery,
  buildCountArticlesQuery,
  buildCountAssertionsQuery,
  buildCountReferenceArticlesQuery,

  // Sampling & inspection
  buildSampleAssertionsQuery,

  // Integrity checks
  buildCheckInvalidConfidenceQuery,
  buildCheckMissingTextQuery,
  buildRelationshipTypesQuery,
  buildGetAllFactIdsQuery,

  /**
   * Helper: Execute a query built by this module
   *
   * @param {Object} session - Neo4j session
   * @param {Object} querySpec - { query, params }
   * @returns {Promise<Array>} Query results
   */
  async executeQuery(session, querySpec) {
    if (!querySpec || !querySpec.query) {
      throw new Error("Invalid query specification");
    }
    const result = await session.run(querySpec.query, querySpec.params);
    return result.records;
  },

  /**
   * Helper: Get properties from record safely
   *
   * @param {Record} record - Neo4j record
   * @param {string} key - Key to extract
   * @returns {Object} Node properties or null
   */
  getNodeProperties(record, key) {
    try {
      const node = record.get(key);
      return node ? node.properties : null;
    } catch {
      return null;
    }
  },
};
