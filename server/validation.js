/**
 * Input Validation & Security Module
 * Provides standardized validation for all server endpoints
 */

const MAX_QUERY_LENGTH = 500;
const MIN_QUERY_LENGTH = 3;

// Query validation - prevents injection and DOS attacks
function validateQuery(query) {
  if (!query || typeof query !== "string") {
    return {
      valid: false,
      error: "Query must be a non-empty string",
      code: "INVALID_QUERY",
    };
  }

  const trimmed = query.trim();
  if (trimmed.length === 0) {
    return {
      valid: false,
      error: "Query cannot be empty or whitespace only",
      code: "EMPTY_QUERY",
    };
  }

  if (query.length > MAX_QUERY_LENGTH) {
    return {
      valid: false,
      error: `Query too long (max ${MAX_QUERY_LENGTH} characters)`,
      code: "QUERY_TOO_LONG",
    };
  }

  if (trimmed.length < MIN_QUERY_LENGTH) {
    return {
      valid: false,
      error: `Query too short (min ${MIN_QUERY_LENGTH} characters)`,
      code: "QUERY_TOO_SHORT",
    };
  }

  // ✅ Check for injection patterns
  const injectionPatterns = [
    /['"].*['"](?:\s*;|\s*or|\s*and|\s*where)/i, // SQL-like injection
    /[\$`\\]/, // Shell metacharacters
    /\$\{.*?\}/, // Template injection
    /--\s*$/, // SQL comment
  ];

  for (const pattern of injectionPatterns) {
    if (pattern.test(trimmed)) {
      return {
        valid: false,
        error: "Query contains potentially malicious characters",
        code: "SUSPICIOUS_QUERY",
      };
    }
  }

  return { valid: true };
}

// Fact ID validation (alphanumeric, hyphens, underscores, dots) - UUID format
function validateFactId(id) {
  if (!id || typeof id !== "string")
    return { valid: false, error: "Invalid fact ID format" };

  // Allow UUID, numeric, alphanumeric with hyphens/underscores/dots
  const isValid =
    /^[a-zA-Z0-9_\-.]+$/.test(id) && id.length <= 255 && id.length >= 1;

  if (!isValid) {
    return {
      valid: false,
      error:
        "Fact ID must be alphanumeric (with hyphens, underscores, dots only) and max 255 chars",
    };
  }

  return { valid: true };
}

// Node ID validation - strict format to prevent injection
function validateNodeId(id) {
  if (!id || typeof id !== "string") {
    return { valid: false, error: "Invalid node ID format" };
  }

  // Stricter: Allow only alphanumeric, hyphens, underscores
  const isValid =
    /^[a-zA-Z0-9_-]+$/.test(id) && id.length <= 255 && id.length >= 1;

  if (!isValid) {
    return {
      valid: false,
      error:
        "Node ID must be alphanumeric (with hyphens, underscores only) and max 255 chars",
    };
  }

  return { valid: true };
}

// Pagination validation - prevents DOS via huge offsets/limits
function validatePagination(limit, offset) {
  const limitNum = parseInt(limit, 10);
  const offsetNum = parseInt(offset, 10);

  const MAX_LIMIT = 100;
  const MAX_OFFSET = 1000000; // 1M max offset prevents enumeration attacks

  if (isNaN(limitNum) || limitNum < 1) {
    return {
      valid: false,
      error: `Limit must be at least 1`,
      code: "INVALID_LIMIT",
    };
  }

  if (limitNum > MAX_LIMIT) {
    return {
      valid: false,
      error: `Limit must not exceed ${MAX_LIMIT}`,
      code: "LIMIT_TOO_HIGH",
    };
  }

  if (isNaN(offsetNum) || offsetNum < 0) {
    return {
      valid: false,
      error: "Offset must be non-negative",
      code: "INVALID_OFFSET",
    };
  }

  if (offsetNum > MAX_OFFSET) {
    return {
      valid: false,
      error: `Offset must not exceed ${MAX_OFFSET}`,
      code: "OFFSET_TOO_HIGH",
    };
  }

  return { valid: true, limit: limitNum, offset: offsetNum };
}

// Standardized error response
function sendError(res, message, code = "ERROR", statusCode = 500) {
  res.status(statusCode).json({
    success: false,
    error: {
      code,
      message,
    },
    timestamp: new Date().toISOString(),
  });
}

// Standardized success response
function sendSuccess(res, data, statusCode = 200) {
  res.status(statusCode).json({
    success: true,
    data,
    timestamp: new Date().toISOString(),
  });
}

module.exports = {
  validateQuery,
  validateFactId,
  validateNodeId,
  validatePagination,
  sendError,
  sendSuccess,
};
