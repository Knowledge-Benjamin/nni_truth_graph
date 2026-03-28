const express = require('express');
const crypto = require('crypto');
const { authenticateAdmin } = require('./auth');

const router = express.Router();

// Only allow authenticated users to manage their keys
router.use(authenticateAdmin);

// Get list of active keys (returns metadata and prefix only, NEVER the raw key)
router.get('/keys', async (req, res) => {
    try {
        const client = await req.app.locals.pgPool.connect();
        try {
            const { rows } = await client.query(
                `SELECT id, prefix, tier, active, created_at 
                 FROM api_keys 
                 WHERE user_id = $1 AND active = TRUE
                 ORDER BY created_at DESC`,
                [req.user.id]
            );
            res.json({ keys: rows });
        } finally {
            client.release();
        }
    } catch (e) {
        console.error('[Developer API]', e);
        res.status(500).json({ error: 'Failed to fetch API keys' });
    }
});

// Generate a new secure API key
router.post('/keys/generate', async (req, res) => {
    try {
        // Generate 32 bytes of secure random entropy
        const rawEntropy = crypto.randomBytes(32).toString('hex');
        const prefix = 'sk_live_';
        const rawKey = `${prefix}${rawEntropy}`;
        
        // Hash the key using SHA-256 for secure database storage
        const keyHash = crypto.createHash('sha256').update(rawKey).digest('hex');

        const client = await req.app.locals.pgPool.connect();
        try {
            const { rows } = await client.query(
                `INSERT INTO api_keys (user_id, api_key_hash, prefix, tier) 
                 VALUES ($1, $2, $3, $4) 
                 RETURNING id, prefix, tier, active, created_at`,
                [req.user.id, keyHash, prefix, 'enterprise'] // Default tier assignment
            );

            // Return the RAW key EXACTLY ONCE to the frontend.
            res.json({
                key_metadata: rows[0],
                raw_key: rawKey 
            });
        } finally {
            client.release();
        }
    } catch (e) {
        console.error('[Developer API Generate Error]', e);
        res.status(500).json({ error: 'Failed to generate API key' });
    }
});

// Revoke an existing API key
router.post('/keys/:id/revoke', async (req, res) => {
    try {
        const client = await req.app.locals.pgPool.connect();
        try {
            const { rowCount } = await client.query(
                'UPDATE api_keys SET active = FALSE WHERE id = $1 AND user_id = $2',
                [req.params.id, req.user.id] // Ensure users can only revoke their own keys
            );
            
            if (rowCount === 0) {
                return res.status(404).json({ error: 'Key not found or unauthorized' });
            }
            
            res.json({ success: true, message: 'Key revoked successfully' });
        } finally {
            client.release();
        }
    } catch (e) {
        console.error('[Developer API Revoke Error]', e);
        res.status(500).json({ error: 'Failed to revoke API key' });
    }
});

module.exports = router;
