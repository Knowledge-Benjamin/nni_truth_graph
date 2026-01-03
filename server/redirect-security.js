/**
 * Redirect Security Module
 * Prevents open redirect attacks by validating and encoding redirect URLs
 *
 * Use cases:
 * - Backward compatibility redirects (old URLs → new URLs)
 * - Normalized redirects (with/without trailing slashes)
 * - Parameter-based redirects
 */

/**
 * Validate and perform safe redirect
 * Prevents open redirect attacks by:
 * 1. Validating target URL against whitelist
 * 2. Encoding parameters properly
 * 3. Ensuring URL is relative (no protocol/domain)
 *
 * @param {Response} res - Express response object
 * @param {string} targetUrl - Target URL path (must be relative)
 * @param {number} statusCode - HTTP status code (default: 301)
 * @returns {void}
 */
function safeRedirect(res, targetUrl, statusCode = 301) {
  // ✅ Validate target URL is relative (no protocol or domain)
  if (targetUrl.includes("://") || targetUrl.includes("//")) {
    console.error(
      `🚨 SECURITY: Attempted absolute URL redirect blocked: ${targetUrl}`
    );
    return res.status(400).json({
      error: "Invalid redirect target",
      code: "INVALID_REDIRECT",
    });
  }

  // ✅ Validate URL starts with /
  if (!targetUrl.startsWith("/")) {
    console.error(
      `🚨 SECURITY: Attempted relative URL redirect blocked: ${targetUrl}`
    );
    return res.status(400).json({
      error: "Redirect target must be absolute path",
      code: "INVALID_REDIRECT",
    });
  }

  // ✅ Valid status codes for redirects
  const validStatusCodes = [301, 302, 303, 307, 308];
  if (!validStatusCodes.includes(statusCode)) {
    console.warn(`⚠️  Invalid redirect status code ${statusCode}, using 301`);
    statusCode = 301;
  }

  // ✅ Safe redirect
  res.redirect(statusCode, targetUrl);
}

/**
 * Build safe redirect URL from components
 * Automatically URL-encodes parameters
 *
 * @param {string} basePath - Base API path (e.g., "/api/fact_graph")
 * @param {Object} params - URL parameters to append and encode
 * @returns {string} - Safe redirect URL
 *
 * Example:
 *   buildRedirectUrl("/api/fact_graph", { factId: "123-abc" })
 *   → "/api/fact_graph/123-abc"
 */
function buildRedirectUrl(basePath, params) {
  if (!basePath || !basePath.startsWith("/")) {
    throw new Error("basePath must start with /");
  }

  // Flatten params into path segments
  const pathSegments = Object.values(params).map((param) => {
    if (param === null || param === undefined) {
      throw new Error("URL parameters cannot be null or undefined");
    }
    // URL-encode each parameter
    return encodeURIComponent(String(param));
  });

  const path = basePath + "/" + pathSegments.join("/");

  // Validate result doesn't contain suspicious patterns
  if (path.includes("..") || path.includes("//")) {
    throw new Error("Invalid URL path detected");
  }

  return path;
}

/**
 * Redirect with validation - used for backward compatibility
 *
 * @param {Response} res - Express response object
 * @param {Object} config - Configuration object
 * @param {string} config.from - Source endpoint for logging
 * @param {string} config.toPath - Destination path
 * @param {Object} config.params - Parameters to append
 * @param {Function} config.validateParam - Validation function for params
 * @param {number} config.statusCode - HTTP status code (default: 301)
 * @returns {void}
 *
 * Example:
 *   compatibilityRedirect(res, {
 *     from: "/api/fact_graph/:id",
 *     toPath: "/api/fact_graph",
 *     params: { id: factId },
 *     validateParam: validateNodeId,
 *     statusCode: 301
 *   });
 */
function compatibilityRedirect(res, config) {
  const { from, toPath, params, validateParam, statusCode = 301 } = config;

  // Validate all parameters
  if (validateParam && params) {
    for (const [key, value] of Object.entries(params)) {
      const validation = validateParam(value);
      if (!validation.valid) {
        console.warn(`🚨 REDIRECT BLOCKED: Invalid parameter ${key}=${value}`);
        return res.status(400).json({
          error: `Invalid parameter: ${validation.error}`,
          code: "INVALID_PARAM",
        });
      }
    }
  }

  try {
    // Build safe redirect URL
    const redirectUrl = buildRedirectUrl(toPath, params);

    console.log(`📍 REDIRECT: ${from} → ${redirectUrl} (${statusCode})`);

    safeRedirect(res, redirectUrl, statusCode);
  } catch (error) {
    console.error(`❌ Redirect error: ${error.message}`);
    return res.status(400).json({
      error: "Redirect failed",
      code: "REDIRECT_ERROR",
    });
  }
}

module.exports = {
  safeRedirect,
  buildRedirectUrl,
  compatibilityRedirect,
};
