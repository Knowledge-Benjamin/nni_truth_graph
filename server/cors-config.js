/**
 * CORS Configuration Module
 * Centralized CORS security policy configuration
 * Prevents unauthorized cross-origin access
 */

/**
 * Get list of allowed origins based on environment
 * @returns {string[]} Array of allowed origins
 */
const getAllowedOrigins = () => {
  const env = process.env.APP_ENV || "development";

  // Development: Allow localhost variations for easier testing
  if (env === "development") {
    return [
      "http://localhost:3000", // Standard dev port
      "http://localhost:5173", // Vite dev server
      "http://localhost:3001", // Alternative port
      "http://127.0.0.1:3000",
      "http://127.0.0.1:5173",
      "http://127.0.0.1:3001",
    ];
  }

  // Production: Read from environment variable
  const productionOrigins = process.env.ALLOWED_ORIGINS;
  if (!productionOrigins) {
    console.warn(
      "⚠️  WARNING: ALLOWED_ORIGINS environment variable not set in production."
    );
    console.warn(
      "   Please set ALLOWED_ORIGINS=<comma-separated-urls> in .env"
    );
    console.warn(
      "   Example: ALLOWED_ORIGINS=https://mydomain.com,https://app.mydomain.com"
    );
    return ["https://yourdomain.com"]; // Placeholder - will block most requests
  }

  return productionOrigins.split(",").map((origin) => origin.trim());
};

/**
 * CORS options configuration
 * Implements defense-in-depth CORS security
 */
const corsOptions = {
  // Validate origin against whitelist
  origin: (origin, callback) => {
    const allowedOrigins = getAllowedOrigins();

    // Allow requests with no origin (e.g., health checks, internal requests, mobile apps, Postman)
    if (!origin) {
      return callback(null, true);
    }

    if (allowedOrigins.includes(origin)) {
      callback(null, true);
    } else {
      const message = `CORS policy: origin "${origin}" is not in the allowed list`;
      console.warn(`🚨 ${message}`);
      console.debug(`   Allowed origins: ${allowedOrigins.join(", ")}`);
      callback(new Error(message));
    }
  },

  // Allow credentials (cookies, authorization headers)
  credentials: true,

  // Only allow necessary HTTP methods
  methods: ["GET", "POST", "OPTIONS"],

  // Whitelist specific request headers
  allowedHeaders: ["Content-Type", "Authorization", "X-Requested-With"],

  // Headers that JavaScript can read from responses
  exposedHeaders: [
    "X-Total-Count", // For pagination info
    "X-Page-Number", // For pagination info
  ],

  // Cache preflight requests for 1 hour (3600 seconds)
  // Reduces unnecessary preflight requests
  maxAge: 3600,
};

/**
 * Security headers middleware
 * Adds headers to prevent common attacks
 */
const securityHeaders = (req, res, next) => {
  // Prevent clickjacking - disallow embedding in iframes
  res.setHeader("X-Frame-Options", "DENY");

  // Prevent MIME type sniffing
  res.setHeader("X-Content-Type-Options", "nosniff");

  // Enable XSS filter in older browsers
  res.setHeader("X-XSS-Protection", "1; mode=block");

  // Restrict referrer information
  res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");

  // Disable dangerous browser features
  res.setHeader(
    "Permissions-Policy",
    "geolocation=(), microphone=(), camera=()"
  );

  // Strict Transport Security (HTTPS only) - 1 year
  if (process.env.APP_ENV === "production") {
    res.setHeader(
      "Strict-Transport-Security",
      "max-age=31536000; includeSubDomains"
    );
  }

  next();
};

module.exports = {
  corsOptions,
  securityHeaders,
  getAllowedOrigins,
};
