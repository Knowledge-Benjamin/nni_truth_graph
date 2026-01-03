/**
 * Data Consistency Verification Module
 * Ensures PostgreSQL and Neo4j stay synchronized
 *
 * Usage:
 * - Run on startup to detect corruption
 * - Run periodically (e.g., hourly) to detect drift
 * - Run after bulk operations to verify success
 */

const { driver } = require("neo4j-driver");
const cypherBuilder = require("./cypher-builder");

class DataConsistencyChecker {
  constructor(neo4jDriver, pgPool) {
    this.neo4jDriver = neo4jDriver;
    this.pgPool = pgPool;
    this.lastCheckTime = null;
    this.inconsistencies = [];
  }

  /**
   * Check if all Neo4j Fact nodes have corresponding PostgreSQL records
   */
  async checkFactConsistency() {
    const issues = [];

    try {
      const session = this.neo4jDriver.session();

      // Get all facts from Neo4j using query builder
      const querySpec = cypherBuilder.buildGetAllFactIdsQuery();
      const neoResult = await session.run(querySpec.query, querySpec.params);
      const neoFacts = new Set(neoResult.records.map((r) => r.get("id")));

      // Get all facts from PostgreSQL
      const pgConn = await this.pgPool.connect();
      const pgResult = await pgConn.query("SELECT id FROM extracted_facts");
      const pgFacts = new Set(pgResult.rows.map((r) => r.id.toString()));
      pgConn.release();

      // Check for facts in Neo4j but not in PostgreSQL (orphaned)
      for (const factId of neoFacts) {
        if (!pgFacts.has(factId.toString())) {
          issues.push({
            type: "ORPHANED_NEO4J_FACT",
            factId: factId,
            severity: "HIGH",
            message: `Fact ${factId} exists in Neo4j but not in PostgreSQL`,
          });
        }
      }

      // Check for facts in PostgreSQL but not in Neo4j (not indexed)
      for (const factId of pgFacts) {
        if (!neoFacts.has(factId)) {
          issues.push({
            type: "MISSING_NEO4J_FACT",
            factId,
            severity: "MEDIUM",
            message: `Fact ${factId} exists in PostgreSQL but not in Neo4j`,
          });
        }
      }

      await session.close();
    } catch (error) {
      issues.push({
        type: "CONSISTENCY_CHECK_ERROR",
        severity: "CRITICAL",
        message: `Failed to check fact consistency: ${error.message}`,
      });
    }

    return issues;
  }

  /**
   * Check relationship count consistency
   */
  async checkRelationshipConsistency() {
    const issues = [];

    try {
      const session = this.neo4jDriver.session();

      // Count relationships by type in Neo4j using query builder
      const querySpec = cypherBuilder.buildRelationshipTypesQuery();
      const neoResult = await session.run(querySpec.query, querySpec.params);

      const neoRelationships = {};
      neoResult.records.forEach((r) => {
        neoRelationships[r.get("relType")] = r.get("count").toNumber();
      });

      // List expected relationship types
      const expectedRelationships = [
        "MENTIONS",
        "SUPPORTS",
        "CONTRADICTS",
        "CITES",
        "VERIFIES",
      ];

      // Check for unexpected relationship types
      for (const relType of Object.keys(neoRelationships)) {
        if (
          !expectedRelationships.includes(relType) &&
          relType !== "SIMILAR_TO"
        ) {
          issues.push({
            type: "UNEXPECTED_RELATIONSHIP_TYPE",
            relationshipType: relType,
            severity: "MEDIUM",
            message: `Unexpected relationship type: ${relType}. Expected: ${expectedRelationships.join(
              ", "
            )}`,
          });
        }
      }

      await session.close();
    } catch (error) {
      issues.push({
        type: "RELATIONSHIP_CHECK_ERROR",
        severity: "CRITICAL",
        message: `Failed to check relationships: ${error.message}`,
      });
    }

    return issues;
  }

  /**
   * Check for data integrity violations
   */
  async checkDataIntegrity() {
    const issues = [];

    try {
      const session = this.neo4jDriver.session();
      
      // Check confidence scores are within valid range using query builder
      const confQuerySpec = cypherBuilder.buildCheckInvalidConfidenceQuery();
      const result = await session.run(confQuerySpec.query, confQuerySpec.params);

      const invalidCount = result.records[0].get("count").toNumber();
      if (invalidCount > 0) {
        issues.push({
          type: "INVALID_CONFIDENCE_RANGE",
          severity: "HIGH",
          count: invalidCount,
          message: `Found ${invalidCount} facts with invalid confidence scores (not in 0-1 range)`,
        });
      }

      // Check for facts without text using query builder
      const textQuerySpec = cypherBuilder.buildCheckMissingTextQuery();
      const noTextResult = await session.run(textQuerySpec.query, textQuerySpec.params);

      const noTextCount = noTextResult.records[0].get("count").toNumber();
      if (noTextCount > 0) {
        issues.push({
          type: "MISSING_TEXT",
          severity: "MEDIUM",
          count: noTextCount,
          message: `Found ${noTextCount} facts without text`,
        });
      }

      await session.close();
    } catch (error) {
      issues.push({
        type: "INTEGRITY_CHECK_ERROR",
        severity: "CRITICAL",
        message: `Failed to check integrity: ${error.message}`,
      });
    }

    return issues;
  }

  /**
   * Run all consistency checks
   */
  async runAllChecks() {
    console.log("[CONSISTENCY] Starting full database consistency check...");

    const results = {
      timestamp: new Date().toISOString(),
      checks: {
        facts: [],
        relationships: [],
        integrity: [],
      },
      summary: {
        totalIssues: 0,
        criticalIssues: 0,
        highSeverityIssues: 0,
      },
    };

    try {
      // Run all checks in parallel
      const [factIssues, relIssues, integrityIssues] = await Promise.all([
        this.checkFactConsistency(),
        this.checkRelationshipConsistency(),
        this.checkDataIntegrity(),
      ]);

      results.checks.facts = factIssues;
      results.checks.relationships = relIssues;
      results.checks.integrity = integrityIssues;

      // Summarize
      const allIssues = [...factIssues, ...relIssues, ...integrityIssues];
      results.summary.totalIssues = allIssues.length;
      results.summary.criticalIssues = allIssues.filter(
        (i) => i.severity === "CRITICAL"
      ).length;
      results.summary.highSeverityIssues = allIssues.filter(
        (i) => i.severity === "HIGH"
      ).length;

      if (results.summary.totalIssues === 0) {
        console.log("[CONSISTENCY] ✅ All consistency checks passed");
      } else {
        console.warn(
          `[CONSISTENCY] ⚠️ Found ${results.summary.totalIssues} issues`
        );
        if (results.summary.criticalIssues > 0) {
          console.error(
            `[CONSISTENCY] 🔴 ${results.summary.criticalIssues} CRITICAL issues`
          );
        }
        if (results.summary.highSeverityIssues > 0) {
          console.error(
            `[CONSISTENCY] 🟠 ${results.summary.highSeverityIssues} HIGH severity issues`
          );
        }
      }
    } catch (error) {
      console.error("[CONSISTENCY] Fatal error during checks:", error);
      results.error = error.message;
    }

    this.lastCheckTime = new Date();
    this.inconsistencies = results;

    return results;
  }

  /**
   * Get latest check results
   */
  getLastCheckResults() {
    return {
      lastCheck: this.lastCheckTime,
      results: this.inconsistencies,
    };
  }
}

module.exports = DataConsistencyChecker;
