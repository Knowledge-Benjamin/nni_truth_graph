const crypto = require('crypto');
const { rateLimit } = require('express-rate-limit');

// Rate limiters based on B2B tier
const rateLimiters = {
    enterprise: rateLimit({
        windowMs: 60 * 1000, // 1 minute
        max: 5000, // 5000 requests per minute
        message: { error: 'Tier limit exceeded' }
    }),
    pro: rateLimit({
        windowMs: 60 * 1000, 
        max: 1000, 
        message: { error: 'Tier limit exceeded' }
    }),
    basic: rateLimit({
        windowMs: 60 * 1000,
        max: 100,
        message: { error: 'Tier limit exceeded' }
    })
};

// Middleware to authenticate B2B API Keys
const b2bAuth = async (req, res, next) => {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
        return res.status(401).json({ error: 'Missing or malformed Authorization header' });
    }

    const token = authHeader.split(' ')[1];
    if (token.length < 32) {
        return res.status(401).json({ error: 'Invalid token format' });
    }

    const [prefix, rawKey] = [token.slice(0, 8), token]; // Assuming token structure
    
    // Hash the incoming key to compare with DB
    const keyHash = crypto.createHash('sha256').update(rawKey).digest('hex');

    try {
        const client = await req.app.locals.pgPool.connect();
        try {
            const { rows } = await client.query(
                'SELECT user_id, tier, active FROM api_keys WHERE prefix = $1 AND api_key_hash = $2',
                [prefix, keyHash]
            );

            if (rows.length === 0 || !rows[0].active) {
                return res.status(401).json({ error: 'Invalid or revoked API key' });
            }

            req.b2bUser = {
                userId: rows[0].user_id,
                tier: rows[0].tier
            };
            
            // Apply dynamic rate limiting based on tier
            const limiter = rateLimiters[rows[0].tier] || rateLimiters.basic;
            return limiter(req, res, next);
            
        } finally {
            client.release();
        }
    } catch (err) {
        console.error('[B2B Auth Error]', err);
        return res.status(500).json({ error: 'Internal Server Error during authentication' });
    }
};

module.exports = { b2bAuth, rateLimiters };
