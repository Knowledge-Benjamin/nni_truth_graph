/**
 * Cypher Query Validator
 * Validates and sanitizes Cypher queries to prevent injection attacks
 */

// Maximum input length for search/filter terms
const MAX_INPUT_LENGTH = 500;
const MAX_QUERY_LENGTH = 10000;

const FORBIDDEN_PATTERNS = [
  // Destructive operations
  /DELETE\s+/i,
  /DROP\s+/i,
  /REMOVE\s+/i,
  /DETACH\s+DELETE/i,

  // Schema modifications
  /CREATE\s+(CONSTRAINT|INDEX|UNIQUE|FULLTEXT)/i,
  /DROP\s+(CONSTRAINT|INDEX)/i,

  // User management
  /CREATE\s+USER/i,
  /DROP\s+USER/i,
  /ALTER\s+USER/i,
  /ROLES?/i,

  // System operations
  /CALL\s+dbms/i,
  /CALL\s+apoc/i,

  // File system access
  /LOAD\s+CSV/i,
  /file:\/\//i,

  // Dangerous procedures
  /CALL\s+.*\.(write|create|delete|drop)/i,
];

// Dangerous characters in search queries
const DANGEROUS_CHARS = [
  /[;\\`$]/, // SQL/shell injection chars
  /\$\{.*?\}/, // Template injection
  /\{.*?\}/, // Potential object injection
];

const ALLOWED_OPERATIONS = [
  "MATCH",
  "RETURN",
  "WHERE",
  "WITH",
  "OPTIONAL MATCH",
  "ORDER BY",
  "LIMIT",
  "SKIP",
  "CALL db.index",
  "CALL db.index.fulltext.queryNodes",
  "CALL db.index.vector.queryNodes",
];

/**
 * Validate Cypher query for security
 * @param {string} query - Cypher query to validate
 * @returns {{valid: boolean, error?: string, sanitized?: string}}
 */
function validateCypherQuery(query) {
  if (!query || typeof query !== "string") {
    return { valid: false, error: "Query must be a non-empty string" };
  }

  const trimmed = query.trim();

  if (trimmed.length === 0) {
    return { valid: false, error: "Query cannot be empty" };
  }

  if (trimmed.length > MAX_QUERY_LENGTH) {
    return {
      valid: false,
      error: `Query exceeds maximum length of ${MAX_QUERY_LENGTH} characters`,
    };
  }

  // Check for forbidden patterns
  for (const pattern of FORBIDDEN_PATTERNS) {
    if (pattern.test(trimmed)) {
      return {
        valid: false,
        error: `Query contains forbidden operation: ${pattern.source}`,
      };
    }
  }

  // Check that query starts with allowed operation
  const upperQuery = trimmed.toUpperCase();
  const startsWithAllowed = ALLOWED_OPERATIONS.some((op) =>
    upperQuery.startsWith(op)
  );

  if (!startsWithAllowed) {
    return {
      valid: false,
      error: `Query must start with one of: ${ALLOWED_OPERATIONS.join(", ")}`,
    };
  }

  // Ensure LIMIT is present for queries that could return large results
  // Index queries can handle unlimited results safely
  const isIndexQuery = /CALL\s+db\.index/i.test(trimmed);
  const hasLimit = /LIMIT\s+\d+/i.test(trimmed);

  if (!hasLimit && !isIndexQuery) {
    return {
      valid: false,
      error:
        "Query must include LIMIT clause for safety (prevents large result sets)",
    };
  }

  // Validate LIMIT value is reasonable
  const limitMatch = trimmed.match(/LIMIT\s+(\d+)/i);
  if (limitMatch) {
    const limitValue = parseInt(limitMatch[1], 10);
    if (limitValue > 100) {
      return {
        valid: false,
        error: "LIMIT value must not exceed 100 to prevent resource exhaustion",
      };
    }
  }

  return { valid: true, sanitized: trimmed };
}

/**
 * Sanitize user input for use in Cypher queries
 * ✅ IMPORTANT: Always use parameterized queries with this sanitized input
 * Never concatenate sanitized input directly into Cypher strings
 *
 * @param {string} input - User input to sanitize (e.g., search terms, filter values)
 * @returns {{valid: boolean, sanitized?: string, error?: string}}
 */
function sanitizeCypherInput(input) {
  // Type validation
  if (input === null || input === undefined) {
    return { valid: false, error: "Input cannot be null or undefined" };
  }

  if (typeof input !== "string") {
    return { valid: false, error: "Input must be a string" };
  }

  // Length validation
  if (input.length === 0) {
    return { valid: false, error: "Input cannot be empty" };
  }

  if (input.length > MAX_INPUT_LENGTH) {
    return {
      valid: false,
      error: `Input exceeds maximum length of ${MAX_INPUT_LENGTH} characters`,
    };
  }

  // Remove control characters and null bytes
  let sanitized = input
    .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, "") // Remove control chars
    .trim();

  // Check for dangerous character patterns
  for (const pattern of DANGEROUS_CHARS) {
    if (pattern.test(sanitized)) {
      return {
        valid: false,
        error: `Input contains forbidden characters`,
      };
    }
  }

  // Check for common injection patterns
  const injectionPatterns = [
    /['"].*['"]/, // Unescaped quotes
    /--|\/\*/, // SQL comments
    /\$\{|__proto__|constructor/, // Template/prototype pollution
  ];

  for (const pattern of injectionPatterns) {
    if (pattern.test(sanitized)) {
      return {
        valid: false,
        error: `Input contains potential injection payload`,
      };
    }
  }

  // Escape Neo4j special characters for string context
  // Note: Parameters are safe, but if you must include in string literals:
  sanitized = sanitized
    .replace(/\\/g, "\\\\") // Backslash
    .replace(/"/g, '\\"') // Double quote
    .replace(/'/g, "\\'") // Single quote
    .replace(/\n/g, "\\n") // Newline
    .replace(/\r/g, "\\r") // Carriage return
    .replace(/\t/g, "\\t"); // Tab

  return { valid: true, sanitized };
}

/**
 * Validate pagination parameters to prevent DOS attacks
 * @param {number} limit - Result limit
 * @param {number} offset - Result offset
 * @returns {{valid: boolean, error?: string, limit: number, offset: number}}
 */
function validatePaginationParams(limit, offset) {
  const MAX_LIMIT = 100;
  const MAX_OFFSET = 1000000; // 1M - prevent offset-based enumeration attacks

  // Convert to integers
  let parsedLimit = parseInt(limit, 10);
  let parsedOffset = parseInt(offset, 10);

  // Validate limit
  if (isNaN(parsedLimit) || parsedLimit <= 0) {
    parsedLimit = 20; // Default
  } else if (parsedLimit > MAX_LIMIT) {
    return {
      valid: false,
      error: `Limit must not exceed ${MAX_LIMIT}`,
      limit: MAX_LIMIT,
      offset: parsedOffset,
    };
  }

  // Validate offset
  if (isNaN(parsedOffset) || parsedOffset < 0) {
    parsedOffset = 0;
  } else if (parsedOffset > MAX_OFFSET) {
    return {
      valid: false,
      error: `Offset must not exceed ${MAX_OFFSET}`,
      limit: parsedLimit,
      offset: MAX_OFFSET,
    };
  }

  return { valid: true, limit: parsedLimit, offset: parsedOffset };
}

module.exports = {
  validateCypherQuery,
  sanitizeCypherInput,
  validatePaginationParams,
  MAX_INPUT_LENGTH,
  MAX_QUERY_LENGTH,
};
