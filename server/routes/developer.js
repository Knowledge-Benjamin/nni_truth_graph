const express = require('express');
const crypto = require('crypto');
const jwt = require('jsonwebtoken');
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

// Get system settings
router.get('/settings', async (req, res) => {
    try {
        const client = await req.app.locals.pgPool.connect();
        try {
            const { rows } = await client.query('SELECT key, value FROM system_settings');
            const settings = rows.reduce((acc, row) => {
                acc[row.key] = row.value;
                return acc;
            }, {});
            res.json(settings);
        } finally {
            client.release();
        }
    } catch (e) {
        console.error('[Developer API]', e);
        res.status(500).json({ error: 'Failed to fetch settings' });
    }
});

// Update system settings
router.post('/settings', async (req, res) => {
    try {
        const settings = req.body;
        const client = await req.app.locals.pgPool.connect();
        try {
            await client.query('BEGIN');
            for (const [key, value] of Object.entries(settings)) {
                await client.query(`
                    INSERT INTO system_settings (key, value) 
                    VALUES ($1, $2)
                    ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = CURRENT_TIMESTAMP
                `, [key, value]);
            }
            await client.query('COMMIT');
            res.json({ success: true });
        } catch(err) {
            await client.query('ROLLBACK');
            throw err;
        } finally {
            client.release();
        }
    } catch (e) {
        console.error('[Developer API Update Settings Error]', e);
        res.status(500).json({ error: 'Failed to update settings' });
    }
});

// ── License Management ──

// Apply a new license key (JWT)
router.post('/license/apply', async (req, res) => {
    try {
        const { token } = req.body;
        if (!token) return res.status(400).json({ error: 'Token required' });

        const pubKey = process.env.LICENSE_PUB_KEY;
        if (!pubKey) return res.status(500).json({ error: 'LICENSE_PUB_KEY not configured on server' });

        // Verify token
        let payload;
        try {
            payload = jwt.verify(token, pubKey, { algorithms: ['HS256', 'RS256'] });
        } catch (err) {
            return res.status(400).json({ error: 'Invalid or expired license key' });
        }

        const creditsToAdd = payload.credits || 0;
        const validUntil = payload.exp_date ? new Date(payload.exp_date) : null;

        const client = await req.app.locals.pgPool.connect();
        try {
            // Ensure table exists (usually python does this, but just in case)
            await client.query(`
                CREATE TABLE IF NOT EXISTS system_licenses (
                    id SERIAL PRIMARY KEY,
                    license_key TEXT UNIQUE NOT NULL,
                    credits_remaining INT NOT NULL DEFAULT 0,
                    valid_until TIMESTAMP WITH TIME ZONE,
                    metadata JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            `);

            const result = await client.query(
                `INSERT INTO system_licenses (license_key, credits_remaining, valid_until, metadata)
                 VALUES ($1, $2, $3, $4)
                 ON CONFLICT (license_key) DO NOTHING
                 RETURNING id`,
                [token, creditsToAdd, validUntil, payload]
            );

            if (result.rowCount === 0) {
                return res.status(400).json({ error: 'License key already applied.' });
            }
            res.json({ success: true, added: creditsToAdd });
        } finally {
            client.release();
        }
    } catch (e) {
        console.error('[Developer API License Apply Error]', e);
        res.status(500).json({ error: 'Failed to apply license' });
    }
});

// Get current license status and remaining credits
router.get('/license/status', async (req, res) => {
    try {
        const client = await req.app.locals.pgPool.connect();
        try {
            // Ensure table exists
            await client.query(`
                CREATE TABLE IF NOT EXISTS system_licenses (
                    id SERIAL PRIMARY KEY,
                    license_key TEXT UNIQUE NOT NULL,
                    credits_remaining INT NOT NULL DEFAULT 0,
                    valid_until TIMESTAMP WITH TIME ZONE,
                    metadata JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            `);

            const { rows } = await client.query(`
                SELECT SUM(credits_remaining) as total_credits
                FROM system_licenses
                WHERE valid_until > NOW() OR valid_until IS NULL
            `);
            
            const total = rows[0]?.total_credits || 0;
            res.json({ total_credits: parseInt(total) });
        } finally {
            client.release();
        }
    } catch (e) {
        console.error('[Developer API License Status Error]', e);
        res.status(500).json({ error: 'Failed to fetch license status' });
    }
});

module.exports = router;
