# Server Security Best Practices

This document outlines security patterns and best practices used throughout the NNI Truth Graph server.

## 1. Redirects - Preventing Open Redirect Attacks

### Problem

Open redirects allow attackers to redirect users to malicious URLs:

```
GET /api/redirect?url=https://evil.com → Redirects to evil.com
```

### Solution

Use the `redirect-security.js` module for all redirects:

```javascript
// ❌ INSECURE - Don't do this
app.get("/api/redirect/:url", (req, res) => {
  res.redirect(req.params.url); // Open redirect vulnerability
});

// ✅ SECURE - Use redirect security module
const { compatibilityRedirect } = require("./redirect-security");

app.get("/api/fact_graph/:factId", (req, res) => {
  compatibilityRedirect(res, {
    from: "/api/fact_graph/:factId",
    toPath: "/api/fact_graph",
    params: { factId },
    validateParam: validateNodeId,
    statusCode: 301,
  });
});
```

### Key Rules

1. **Always validate parameters** before using them in redirects
2. **Always URL-encode** parameters using `encodeURIComponent()`
3. **Only allow relative URLs** (must start with `/`)
4. **Never redirect to user input** directly
5. **Log all redirects** for security audit trails

---

## 2. CORS - Preventing Cross-Origin Attacks

### Problem

CORS misconfiguration allows unauthorized origins to access your API:

```
app.use(cors()); // ❌ Allows ALL origins
```

### Solution

Use whitelisted origins with `cors-config.js`:

```javascript
const { corsOptions, securityHeaders } = require("./cors-config");

app.use(cors(corsOptions));
app.use(securityHeaders);
```

### Configuration

**Development (.env):**

```
APP_ENV=development
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
```

**Production (.env):**

```
APP_ENV=production
ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
```

### Key Rules

1. **Set `APP_ENV`** to control CORS policy
2. **Use `ALLOWED_ORIGINS`** environment variable in production
3. **Localhost is auto-allowed** in development only
4. **Credentials are enabled** for authenticated requests
5. **Methods are restricted** to GET, POST, OPTIONS only

---

## 3. Query Validation - Preventing Injection Attacks

### Problem

Unvalidated user input can lead to injection attacks:

```
// ❌ Cypher injection
MATCH (f:Fact) WHERE f.text = "'; DELETE f; //"
```

### Solution

Use the validation module:

```javascript
const { validateQuery, validateNodeId } = require("./validation");

// Validate search queries
const validation = validateQuery(userInput);
if (!validation.valid) {
  return sendError(res, validation.error, validation.code, 400);
}

// Validate node IDs (before using in redirects or queries)
const idValidation = validateNodeId(nodeId);
if (!idValidation.valid) {
  return sendError(res, idValidation.error, "INVALID_ID", 400);
}
```

### Cypher Query Sanitization

```javascript
const { sanitizeCypherInput } = require("./cypher-validator");

const result = sanitizeCypherInput(userInput);
if (!result.valid) {
  return sendError(res, result.error, "INVALID_INPUT", 400);
}

// Use parameterized queries - NEVER string concatenation
const cypherResult = await session.run(
  "MATCH (f:Fact) WHERE f.text CONTAINS toLower($search) RETURN f",
  { search: result.sanitized } // ✅ Safe parameterized query
);
```

### Key Rules

1. **Always validate input** before using it
2. **Use parameterized queries** (never concatenate user input)
3. **Check for injection patterns** (SQL comments, templates, etc.)
4. **Enforce length limits** (query max 500 chars, LIMIT max 100)
5. **Use strict regex patterns** for IDs

---

## 4. Authentication & Authorization

### Current Status

- ❌ NOT IMPLEMENTED - Server is currently read-only

### Future Implementation

When authentication is added:

```javascript
// JWT middleware
const authenticateToken = (req, res, next) => {
  const authHeader = req.headers["authorization"];
  const token = authHeader && authHeader.split(" ")[1];

  if (!token) {
    return sendError(res, "Missing authorization token", "NO_TOKEN", 401);
  }

  jwt.verify(token, process.env.JWT_SECRET, (err, user) => {
    if (err) {
      return sendError(res, "Invalid token", "INVALID_TOKEN", 403);
    }
    req.user = user;
    next();
  });
};

// Protected endpoints
app.post("/api/admin/reset", authenticateToken, (req, res) => {
  // Only authenticated users can reset
});
```

---

## 5. Input Size Limits - Preventing DOS Attacks

### Configuration

```javascript
app.use(express.json({ limit: "10mb" })); // Limit payload size
app.use(express.urlencoded({ limit: "10mb", extended: true }));
```

### Query Limits

```javascript
// In cypher-validator.js
const MAX_QUERY_LENGTH = 500; // Max search query length
const MAX_INPUT_LENGTH = 500; // Max filter input length

// In validation.js
const MAX_LIMIT = 100; // Max result set size
const MAX_OFFSET = 1000000; // Max offset (prevents enumeration)
```

### Key Rules

1. **Limit payload size** to 10MB
2. **Limit query length** to 500 chars
3. **Limit result set** to 100 records
4. **Limit offset** to 1M (prevents DB enumeration)

---

## 6. Response Format - Standardized Error Handling

### Use Standardized Response Format

```javascript
// ✅ Standard error response
sendError(res, "User not found", "NOT_FOUND", 404);
// Returns:
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "User not found"
  },
  "timestamp": "2026-01-03T12:00:00.000Z"
}

// ✅ Standard success response
sendSuccess(res, { data: [...] }, 200);
// Returns:
{
  "success": true,
  "data": [...],
  "timestamp": "2026-01-03T12:00:00.000Z"
}
```

### Key Rules

1. **Always use `sendError()` or `sendSuccess()`**
2. **Include error codes** for debugging
3. **Never expose stack traces** in production
4. **Always include timestamps**
5. **Log errors** for audit trails

---

## 7. Security Headers - Preventing Browser-Based Attacks

### Headers Configured

| Header                      | Value                                      | Purpose                       |
| --------------------------- | ------------------------------------------ | ----------------------------- |
| `X-Frame-Options`           | `DENY`                                     | Prevent clickjacking          |
| `X-Content-Type-Options`    | `nosniff`                                  | Prevent MIME type sniffing    |
| `X-XSS-Protection`          | `1; mode=block`                            | Enable XSS filter             |
| `Referrer-Policy`           | `strict-origin-when-cross-origin`          | Restrict referrer info        |
| `Permissions-Policy`        | `geolocation=(), microphone=(), camera=()` | Disable dangerous features    |
| `Strict-Transport-Security` | `max-age=31536000`                         | Force HTTPS (production only) |

---

## 8. Environment Configuration

### Required Variables

- `NEO4J_URI` - Neo4j connection string
- `NEO4J_USER` - Neo4j username
- `NEO4J_PASSWORD` - Neo4j password

### Recommended Variables

- `APP_ENV` - `development` or `production`
- `ALLOWED_ORIGINS` - Comma-separated allowed origins (production)
- `DATABASE_URL` - PostgreSQL connection (optional)
- `AI_ENGINE_URL` - AI Engine URL (optional)
- `PORT` - Server port (default: 3000)

### Security Rules

1. **Never commit .env files** to git
2. **Use different credentials** for dev/prod
3. **Rotate credentials regularly**
4. **Use .env.example** as template
5. **Validate all env vars** on startup

---

## 9. Logging & Monitoring

### Security Events to Log

- ✅ CORS rejections
- ✅ Validation failures
- ✅ Injection attempts
- ✅ Redirects (all)
- ✅ Unauthorized access attempts (when auth implemented)
- ✅ Database errors
- ✅ API rate limit exceeded

### Example

```javascript
console.warn(`🚨 CORS blocked request from: ${origin}`);
console.warn(`🚨 REDIRECT BLOCKED: Invalid parameter`);
console.error(`❌ Query validation failed: ${error}`);
```

---

## 10. Testing Security

### Test Cases to Add

- [ ] Invalid redirect URLs are blocked
- [ ] CORS rejects unauthorized origins
- [ ] Query injection patterns are detected
- [ ] Oversized payloads are rejected
- [ ] Rate limits are enforced
- [ ] Authentication tokens are validated
- [ ] Error messages don't expose internals

---

## Checklist for New Endpoints

When adding new endpoints, ensure:

- [ ] Input is validated (use `validation.js` or `cypher-validator.js`)
- [ ] Queries use parameterized format (not string concatenation)
- [ ] Responses use `sendError()` / `sendSuccess()`
- [ ] CORS is tested with different origins
- [ ] Rate limiting is considered
- [ ] Error messages are generic (no stack traces)
- [ ] Security headers are applied
- [ ] Logging is comprehensive
- [ ] Documentation is updated

---

## References

- [OWASP Open Redirect Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Unvalidated_Redirects_and_Forwards_Cheat_Sheet.html)
- [OWASP CORS Misconfiguration](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Origin_Resource_Sharing_Cheat_Sheet.html)
- [OWASP Query Injection](https://owasp.org/www-community/attacks/injection)
- [CWE-601: URL Redirection to Untrusted Site](https://cwe.mitre.org/data/definitions/601.html)
- [Express.js Security Best Practices](https://expressjs.com/en/advanced/best-practice-security.html)
