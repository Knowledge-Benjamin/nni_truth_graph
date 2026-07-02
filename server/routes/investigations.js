/**
 * server/routes/investigations.js
 * ─────────────────────────────────────────────────────────────────────────────
 * REST API for the OSINT Investigation Orchestrator.
 *
 * Endpoints:
 *   POST /api/investigations              — Start a new investigation
 *   GET  /api/investigations              — List all investigations (with status)
 *   GET  /api/investigations/:id          — Get a single investigation + findings
 *   GET  /api/investigations/:id/leads    — Paginated leads for an investigation
 *   POST /api/investigations/:id/pause    — Pause an active investigation
 *   POST /api/investigations/:id/resume   — Resume a paused investigation
 *   DELETE /api/investigations/:id        — Cancel & delete an investigation
 */

const express = require('express');
const router  = express.Router();
const { authenticateAdmin } = require('./auth');

// ─── POST /api/investigations ─────────────────────────────────────────────────
// Body: { target, goal_type?, max_leads?, max_days?, concurrent_agents? }
router.post('/', authenticateAdmin, async (req, res) => {
    const { target, goal_type, max_leads, max_days, concurrent_agents } = req.body;
    if (!target || typeof target !== 'string' || !target.trim()) {
        return res.status(400).json({ error: 'target is required' });
    }

    const pgPool = req.app.locals.pgPool;
    try {
        const result = await pgPool.query(
            `INSERT INTO investigations
                 (target, goal_type, max_leads, max_days, concurrent_agents, status)
             VALUES ($1, $2, $3, $4, $5, 'ACTIVE')
             RETURNING id, target, goal_type, status, created_at`,
            [
                target.trim(),
                goal_type             || 'PROFILING',
                max_leads             || 500,
                max_days              || 14,
                concurrent_agents     || 5,
            ]
        );
        res.status(201).json(result.rows[0]);
    } catch (err) {
        console.error('[Investigations API] POST error:', err.message);
        res.status(500).json({ error: 'Failed to create investigation' });
    }
});

// ─── GET /api/investigations ──────────────────────────────────────────────────
router.get('/', authenticateAdmin, async (req, res) => {
    const pgPool = req.app.locals.pgPool;
    try {
        const result = await pgPool.query(
            `SELECT i.id, i.target, i.goal_type, i.status,
                    i.leads_explored, i.novel_discoveries, i.max_leads,
                    i.created_at, i.completed_at,
                    COUNT(il.id) FILTER (WHERE il.status = 'PENDING')  AS pending_leads,
                    COUNT(il.id) FILTER (WHERE il.status = 'EXPLORED') AS explored_leads,
                    i.findings->>'last_harvest_summary' AS last_summary
             FROM investigations i
             LEFT JOIN investigation_leads il ON il.investigation_id = i.id
             GROUP BY i.id
             ORDER BY i.created_at DESC
             LIMIT 100`
        );
        res.json(result.rows);
    } catch (err) {
        console.error('[Investigations API] GET list error:', err.message);
        res.status(500).json({ error: 'Failed to list investigations' });
    }
});

// ─── GET /api/investigations/:id ─────────────────────────────────────────────
router.get('/:id', authenticateAdmin, async (req, res) => {
    const pgPool = req.app.locals.pgPool;
    const id = parseInt(req.params.id);
    if (isNaN(id)) return res.status(400).json({ error: 'Invalid id' });

    try {
        const result = await pgPool.query(
            `SELECT i.*,
                    COUNT(il.id) FILTER (WHERE il.status = 'PENDING')  AS pending_leads,
                    COUNT(il.id) FILTER (WHERE il.status = 'EXPLORED') AS explored_leads,
                    COUNT(il.id) FILTER (WHERE il.status = 'CLAIMED')  AS active_leads
             FROM investigations i
             LEFT JOIN investigation_leads il ON il.investigation_id = i.id
             WHERE i.id = $1
             GROUP BY i.id`,
            [id]
        );
        if (!result.rows.length) return res.status(404).json({ error: 'Investigation not found' });
        res.json(result.rows[0]);
    } catch (err) {
        console.error('[Investigations API] GET single error:', err.message);
        res.status(500).json({ error: 'Failed to fetch investigation' });
    }
});

// ─── GET /api/investigations/:id/leads ───────────────────────────────────────
router.get('/:id/leads', authenticateAdmin, async (req, res) => {
    const pgPool  = req.app.locals.pgPool;
    const id      = parseInt(req.params.id);
    const page    = parseInt(req.query.page  || '1');
    const limit   = Math.min(parseInt(req.query.limit || '50'), 200);
    const status  = req.query.status || null;
    if (isNaN(id)) return res.status(400).json({ error: 'Invalid id' });

    try {
        const params  = [id, limit, (page - 1) * limit];
        const filter  = status ? `AND status = $4` : '';
        if (status) params.push(status);

        const result = await pgPool.query(
            `SELECT id, entity_name, lead_type, priority, status,
                    claimed_at, explored_at, sub_leads_generated, created_at
             FROM investigation_leads
             WHERE investigation_id = $1 ${filter}
             ORDER BY priority DESC, created_at ASC
             LIMIT $2 OFFSET $3`,
            params
        );
        res.json(result.rows);
    } catch (err) {
        console.error('[Investigations API] GET leads error:', err.message);
        res.status(500).json({ error: 'Failed to fetch leads' });
    }
});

// ─── POST /api/investigations/:id/pause ──────────────────────────────────────
router.post('/:id/pause', authenticateAdmin, async (req, res) => {
    const pgPool = req.app.locals.pgPool;
    const id = parseInt(req.params.id);
    if (isNaN(id)) return res.status(400).json({ error: 'Invalid id' });
    try {
        await pgPool.query(
            `UPDATE investigations SET status = 'PAUSED' WHERE id = $1 AND status = 'ACTIVE'`,
            [id]
        );
        res.json({ success: true, status: 'PAUSED' });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

// ─── POST /api/investigations/:id/resume ─────────────────────────────────────
router.post('/:id/resume', authenticateAdmin, async (req, res) => {
    const pgPool = req.app.locals.pgPool;
    const id = parseInt(req.params.id);
    if (isNaN(id)) return res.status(400).json({ error: 'Invalid id' });
    try {
        await pgPool.query(
            `UPDATE investigations SET status = 'ACTIVE' WHERE id = $1 AND status = 'PAUSED'`,
            [id]
        );
        res.json({ success: true, status: 'ACTIVE' });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

// ─── DELETE /api/investigations/:id ──────────────────────────────────────────
router.delete('/:id', authenticateAdmin, async (req, res) => {
    const pgPool = req.app.locals.pgPool;
    const id = parseInt(req.params.id);
    if (isNaN(id)) return res.status(400).json({ error: 'Invalid id' });
    try {
        await pgPool.query(`DELETE FROM investigations WHERE id = $1`, [id]);
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

module.exports = router;
