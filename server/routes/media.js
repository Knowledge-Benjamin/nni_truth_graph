const express = require('express');
const router = express.Router();
const http = require('http');
const https = require('https');

// Minimal POST helper — replaces axios with zero extra deps
function postJSON(url, body, timeoutMs = 45000) {   // 45s: covers Cloud Run cold start
    return new Promise((resolve, reject) => {
        const parsed = new URL(url);
        const payload = JSON.stringify(body);
        const lib = parsed.protocol === 'https:' ? https : http;
        const req = lib.request({
            hostname: parsed.hostname,
            port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
            path: parsed.pathname + parsed.search,
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
        }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    try { resolve(JSON.parse(data)); } catch (e) { reject(new Error('Invalid JSON from Vision server')); }
                } else {
                    reject(new Error(`Vision server responded HTTP ${res.statusCode}`));
                }
            });
        });
        req.setTimeout(timeoutMs, () => { req.destroy(); reject(new Error('VISION_TIMEOUT')); });
        req.on('error', (e) => {
            if (e.code === 'ECONNREFUSED') reject(new Error('VISION_UNREACHABLE'));
            else reject(e);
        });
        req.write(payload);
        req.end();
    });
}

router.post('/verify', async (req, res) => {
    const { image, intent } = req.body;

    if (!image || !intent) {
        return res.status(400).json({ error: 'Missing image or intent' });
    }

    const visionUrl = process.env.VISION_INFERENCE_URL;
    if (!visionUrl) {
        console.error('[Media API] VISION_INFERENCE_URL env var is not set on this deployment.');
        return res.status(503).json({
            error: 'Vision service not configured',
            detail: 'VISION_INFERENCE_URL is not set in this deployment\'s environment variables. Add it in the Render dashboard under Environment.'
        });
    }

    try {
        // Send Base64 to VisionInferenceServer — 45s timeout covers Cloud Run cold start
        let data;
        try {
            data = await postJSON(`${visionUrl}/embed_media`, {
                image_urls: [`data:image/jpeg;base64,${image}`]
            });
        } catch (visionErr) {
            if (visionErr.message === 'VISION_UNREACHABLE') {
                return res.status(503).json({ error: 'Vision server is offline or unreachable', visionUrl });
            }
            if (visionErr.message === 'VISION_TIMEOUT') {
                return res.status(503).json({ error: 'Vision server is cold-starting — retry in 30s', visionUrl });
            }
            throw visionErr;
        }

        const synthProb = data.synthetic_prob?.[0] ?? 0.0;
        const embedding = data.embeddings?.[0] ?? null;

        const pgPool = req.app.locals.pgPool;

        // Intent Switch
        if (intent === 'deepfake') {
            // Bypass Neo4j and Postgres entirely. Just return the live tensor score.
            return res.json({
                intent: 'deepfake',
                matchFound: false,
                syntheticProbability: synthProb,
                message: `Media successfully parsed. Authenticity confidence evaluated.`
            });
        }

        if (intent === 'trace') {
            // Find earliest match in media_provenance
            if (!embedding) {
                return res.status(500).json({ error: 'Failed to generate CLIP embedding' });
            }

            const query = `
                SELECT mp.id, mp.media_url, mp.synthetic_probability, ra.title, ra.author, ra.publish_date,
                       1 - (mp.clip_embedding <=> $1::vector) AS similarity
                FROM media_provenance mp
                JOIN raw_articles ra ON mp.raw_article_id = ra.id
                WHERE (1 - (mp.clip_embedding <=> $1::vector)) > 0.94
                ORDER BY ra.publish_date ASC
                LIMIT 5;
            `;

            const client = await pgPool.connect();
            try {
                const results = await client.query(query, [JSON.stringify(embedding)]);
                
                if (results.rows.length === 0) {
                    return res.json({
                        intent: 'trace',
                        matchFound: false,
                        syntheticProbability: synthProb,
                        message: `This exact media has never been tracked by the Truth Graph.`
                    });
                }

                return res.json({
                    intent: 'trace',
                    matchFound: true,
                    syntheticProbability: synthProb, // The live calculation
                    patientZero: results.rows[0],
                    allMatches: results.rows
                });
            } finally {
                client.release();
            }
        }

        if (intent === 'debunk') {
            // For 'debunk', we assume cross-modal semantic bridge, but since the frontend 
            // doesn't pass audio transcripts yet, we bind to the Extracted Claims table via the matching article.
            
            if (!embedding) return res.status(500).json({ error: 'Missing CLIP embedding' });
            
            const client = await pgPool.connect();
            try {
                // Find nearest media match
                const matchRes = await client.query(`
                    SELECT raw_article_id FROM media_provenance 
                    WHERE (1 - (clip_embedding <=> $1::vector)) > 0.94 
                    LIMIT 1;
                `, [JSON.stringify(embedding)]);

                if (matchRes.rows.length === 0) {
                     return res.json({
                        intent: 'debunk',
                        matchFound: false,
                        syntheticProbability: synthProb,
                        message: `Novel media detected. No semantic fact nodes found in our context engine.`
                    });
                }

                const articleId = matchRes.rows[0].raw_article_id;

                // Pull the Extracted Claims that were derived from the article where this image was found
                const claimsRes = await client.query(`
                    SELECT id, subject, predicate, object_entity, quote_context, epistemic_score, status 
                    FROM extracted_claims 
                    WHERE article_id = $1;
                `, [articleId]);

                return res.json({
                    intent: 'debunk',
                    matchFound: true,
                    syntheticProbability: synthProb,
                    claims: claimsRes.rows
                });

            } finally {
                client.release();
            }
        }

        return res.status(400).json({ error: 'Invalid intent selection' });

    } catch (err) {
        console.error('[Media API Error]', err.message);
        res.status(500).json({ error: 'Processing failed: ' + err.message });
    }
});

module.exports = router;
