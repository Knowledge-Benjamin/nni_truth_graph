/**
 * Internal Enterprise Data Ingestion Router
 *
 * Provides secure endpoints for organizations to inject private, internal data
 * directly into the Truth Graph pipeline, bypassing the public web scrapers.
 *
 * All routes require authentication (admin or analyst role).
 *
 * Endpoints:
 *   POST /api/ingest/url        — Queue a private internal URL for scraping
 *   POST /api/ingest/text       — Inject a raw text memo/report directly
 *   POST /api/ingest/document   — Upload a PDF or TXT document
 *   GET  /api/ingest/queue      — View the current internal ingestion queue
 *   DELETE /api/ingest/:id      — Remove an item from the queue (before processing)
 */

const express = require('express');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { authenticateAdmin } = require('./auth');

const router = express.Router();

// ── Guard: all ingestion routes require a logged-in user ─────────────────────
router.use(authenticateAdmin);

// ── Constants ─────────────────────────────────────────────────────────────────
const INTERNAL_SOURCE_URL = 'internal://enterprise-data';
const INTERNAL_SOURCE_NAME = 'Internal Enterprise Data';
const INTERNAL_SOURCE_DOMAIN = 'enterprise.internal';
const INTERNAL_TRUST_SCORE = 0.9; // High trust — operator-curated data

// ── File Upload Configuration (multer) ────────────────────────────────────────
const UPLOAD_DIR = path.join(__dirname, '../../data/internal_documents');
if (!fs.existsSync(UPLOAD_DIR)) {
    fs.mkdirSync(UPLOAD_DIR, { recursive: true });
}

const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, UPLOAD_DIR),
    filename: (req, file, cb) => {
        const uniqueSuffix = `${Date.now()}-${crypto.randomBytes(6).toString('hex')}`;
        cb(null, `${uniqueSuffix}-${file.originalname.replace(/[^a-zA-Z0-9._-]/g, '_')}`);
    }
});

const fileFilter = (req, file, cb) => {
    const allowed = [
        'application/pdf',
        'text/plain',
        'text/csv',
        'application/msword', // .doc
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document', // .docx
        'text/markdown',
        'application/json',
    ];
    if (allowed.includes(file.mimetype)) {
        cb(null, true);
    } else {
        cb(new Error(`Unsupported file type: ${file.mimetype}. Allowed: PDF, TXT, CSV, DOC, DOCX, MD, JSON`), false);
    }
};

const upload = multer({
    storage,
    fileFilter,
    limits: {
        fileSize: 50 * 1024 * 1024, // 50MB limit
        files: 10, // Max 10 files per request (bulk upload)
    }
});

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Ensures the internal enterprise source record exists in the `sources` table.
 * Returns the source ID. Idempotent — safe to call on every request.
 */
async function ensureInternalSource(pgClient) {
    const result = await pgClient.query(
        `INSERT INTO sources (name, url, domain, category, tier, epistemic_trust_score)
         VALUES ($1, $2, $3, $4, $5, $6)
         ON CONFLICT (url) DO UPDATE SET epistemic_trust_score = EXCLUDED.epistemic_trust_score
         RETURNING id`,
        [INTERNAL_SOURCE_NAME, INTERNAL_SOURCE_URL, INTERNAL_SOURCE_DOMAIN, 'Internal', 'enterprise', INTERNAL_TRUST_SCORE]
    );
    return result.rows[0].id;
}

/**
 * Creates a raw_urls record (internal pseudo-URL) and then immediately
 * inserts the parsed text into raw_articles, bypassing the scraper stage.
 * Returns the created article ID.
 */
async function injectArticle(pgClient, { sourceId, pseudoUrl, title, rawText, metadata = {} }) {
    // 1. Insert into raw_urls (or get existing)
    const urlResult = await pgClient.query(
        `INSERT INTO raw_urls (source_id, url, metadata, status)
         VALUES ($1, $2, $3, 'INTERNAL_INJECTED')
         ON CONFLICT (url) DO UPDATE SET metadata = EXCLUDED.metadata
         RETURNING id`,
        [sourceId, pseudoUrl, JSON.stringify({ ...metadata, injected_at: new Date().toISOString() })]
    );
    const urlId = urlResult.rows[0].id;

    // 2. Insert into raw_articles — status PENDING_EXTRACTION so the AI pipeline picks it up
    const articleResult = await pgClient.query(
        `INSERT INTO raw_articles (url_id, title, raw_text, status, scraped_at)
         VALUES ($1, $2, $3, 'PENDING_EXTRACTION', NOW())
         RETURNING id`,
        [urlId, title, rawText]
    );
    return articleResult.rows[0].id;
}

/**
 * Extract text from a PDF buffer using pdf-parse.
 * Falls back to raw buffer.toString() for plain text files.
 */
async function extractTextFromFile(filePath, mimeType) {
    if (mimeType === 'application/pdf') {
        // Lazy-load pdf-parse to avoid top-level require issues in some environments
        const pdfParse = require('pdf-parse');
        const buffer = fs.readFileSync(filePath);
        const data = await pdfParse(buffer);
        return data.text || '';
    }

    if (mimeType === 'application/json') {
        const raw = fs.readFileSync(filePath, 'utf8');
        try {
            // Pretty-print JSON so the LLM extractor can parse structured data
            const parsed = JSON.parse(raw);
            return JSON.stringify(parsed, null, 2);
        } catch {
            return raw;
        }
    }

    // TXT, CSV, MD, DOC fallback — read as utf-8
    return fs.readFileSync(filePath, 'utf8');
}


// ═══════════════════════════════════════════════════════════════════════════════
// ENDPOINT 1: Queue a Private Internal URL
// POST /api/ingest/url
// Body: { url: string, label?: string, priority?: 'high' | 'normal' }
// ═══════════════════════════════════════════════════════════════════════════════
router.post('/url', async (req, res) => {
    const { url, label, priority = 'normal' } = req.body;

    if (!url || typeof url !== 'string') {
        return res.status(400).json({ error: 'A valid URL is required.' });
    }

    // Basic URL validation
    try {
        new URL(url);
    } catch {
        return res.status(400).json({ error: 'Invalid URL format.' });
    }

    const pgClient = await req.app.locals.pgPool.connect();
    try {
        const sourceId = await ensureInternalSource(pgClient);

        const metadata = {
            injected_by: req.user.email,
            label: label || url,
            priority,
            source: 'internal_url_submission',
        };

        const result = await pgClient.query(
            `INSERT INTO raw_urls (source_id, url, metadata, status)
             VALUES ($1, $2, $3, 'PENDING_SCRAPE')
             ON CONFLICT (url) DO UPDATE
               SET status = CASE WHEN raw_urls.status = 'SCRAPED' THEN 'PENDING_SCRAPE' ELSE raw_urls.status END,
                   metadata = EXCLUDED.metadata
             RETURNING id, status`,
            [sourceId, url, JSON.stringify(metadata)]
        );

        const { id, status } = result.rows[0];
        const wasQueued = status === 'PENDING_SCRAPE';

        res.json({
            success: true,
            message: wasQueued
                ? `URL queued for scraping. The AI pipeline will process it in the next cycle.`
                : `URL already exists with status "${status}". Metadata updated.`,
            url_id: id,
            status,
        });
    } catch (err) {
        console.error('[Ingest/URL] Error:', err);
        res.status(500).json({ error: 'Database error while queuing URL.' });
    } finally {
        pgClient.release();
    }
});


// ═══════════════════════════════════════════════════════════════════════════════
// ENDPOINT 2: Inject a Raw Text Memo / Intelligence Report
// POST /api/ingest/text
// Body: { title: string, text: string, source_label?: string, classification?: string }
// ═══════════════════════════════════════════════════════════════════════════════
router.post('/text', async (req, res) => {
    const { title, text, source_label, classification = 'INTERNAL' } = req.body;

    if (!title || typeof title !== 'string' || title.trim().length === 0) {
        return res.status(400).json({ error: 'A non-empty title is required.' });
    }
    if (!text || typeof text !== 'string' || text.trim().length < 50) {
        return res.status(400).json({ error: 'Text content must be at least 50 characters.' });
    }

    const pgClient = await req.app.locals.pgPool.connect();
    try {
        const sourceId = await ensureInternalSource(pgClient);

        // Generate a stable pseudo-URL from the title + content hash
        const contentHash = crypto.createHash('sha256').update(text).digest('hex').substring(0, 12);
        const pseudoUrl = `internal://memo/${contentHash}`;

        const metadata = {
            injected_by: req.user.email,
            classification,
            source_label: source_label || 'Internal Memo',
            type: 'text_injection',
        };

        const articleId = await injectArticle(pgClient, {
            sourceId,
            pseudoUrl,
            title: title.trim(),
            rawText: text.trim(),
            metadata,
        });

        res.json({
            success: true,
            message: 'Intelligence memo injected. The AI pipeline will extract facts within the next processing cycle.',
            article_id: articleId,
            pseudo_url: pseudoUrl,
        });
    } catch (err) {
        if (err.code === '23505') {
            return res.status(409).json({ error: 'This exact memo has already been ingested (duplicate content detected).' });
        }
        console.error('[Ingest/Text] Error:', err);
        res.status(500).json({ error: 'Database error while injecting text.' });
    } finally {
        pgClient.release();
    }
});


// ═══════════════════════════════════════════════════════════════════════════════
// ENDPOINT 3: Upload Documents (PDF, TXT, CSV, DOCX, MD, JSON)
// POST /api/ingest/document
// Multipart form data. Supports single or bulk uploads (up to 10 files).
// ═══════════════════════════════════════════════════════════════════════════════
router.post('/document', upload.array('files', 10), async (req, res) => {
    if (!req.files || req.files.length === 0) {
        return res.status(400).json({ error: 'No files received. Ensure the form field is named "files".' });
    }

    const pgClient = await req.app.locals.pgPool.connect();
    const results = [];

    try {
        const sourceId = await ensureInternalSource(pgClient);

        for (const file of req.files) {
            let rawText = '';
            let articleId = null;
            let extractionError = null;

            try {
                rawText = await extractTextFromFile(file.path, file.mimetype);

                if (!rawText || rawText.trim().length < 20) {
                    throw new Error('Extracted text is too short or empty. File may be a scanned image or encrypted PDF.');
                }

                const contentHash = crypto.createHash('sha256').update(rawText).digest('hex').substring(0, 12);
                const pseudoUrl = `internal://document/${contentHash}`;
                const title = req.body[`title_${file.originalname}`] || file.originalname.replace(/\.[^/.]+$/, '');

                const metadata = {
                    injected_by: req.user.email,
                    original_filename: file.originalname,
                    mime_type: file.mimetype,
                    file_size_bytes: file.size,
                    stored_path: file.path,
                    type: 'document_upload',
                };

                articleId = await injectArticle(pgClient, {
                    sourceId,
                    pseudoUrl,
                    title,
                    rawText: rawText.trim(),
                    metadata,
                });

                results.push({
                    filename: file.originalname,
                    status: 'success',
                    article_id: articleId,
                    chars_extracted: rawText.length,
                    message: 'Document ingested and queued for AI extraction.',
                });
            } catch (err) {
                extractionError = err.message;
                // Clean up the uploaded file if we couldn't process it
                try { fs.unlinkSync(file.path); } catch {}

                results.push({
                    filename: file.originalname,
                    status: 'error',
                    error: extractionError,
                });
            }
        }

        const successCount = results.filter(r => r.status === 'success').length;
        const failCount = results.filter(r => r.status === 'error').length;

        res.status(failCount === req.files.length ? 422 : 200).json({
            success: successCount > 0,
            summary: `${successCount} document(s) ingested, ${failCount} failed.`,
            results,
        });
    } catch (err) {
        console.error('[Ingest/Document] Error:', err);
        // Cleanup all files on catastrophic DB failure
        for (const file of req.files) {
            try { fs.unlinkSync(file.path); } catch {}
        }
        res.status(500).json({ error: 'Database error during document ingestion.' });
    } finally {
        pgClient.release();
    }
});


// ═══════════════════════════════════════════════════════════════════════════════
// ENDPOINT 4: View Ingestion Queue (Internal items only)
// GET /api/ingest/queue?status=PENDING_EXTRACTION&page=1&limit=20
// ═══════════════════════════════════════════════════════════════════════════════
router.get('/queue', async (req, res) => {
    const { status, page = 1, limit = 20 } = req.query;
    const offset = (parseInt(page) - 1) * parseInt(limit);

    const pgClient = await req.app.locals.pgPool.connect();
    try {
        // Base query — only show items from the internal source
        let whereClause = `WHERE s.url = $1`;
        const params = [INTERNAL_SOURCE_URL];

        if (status) {
            params.push(status);
            whereClause += ` AND a.status = $${params.length}`;
        }

        const countRes = await pgClient.query(
            `SELECT COUNT(*)
             FROM raw_articles a
             JOIN raw_urls u ON a.url_id = u.id
             JOIN sources s ON u.source_id = s.id
             ${whereClause}`,
            params
        );

        const dataParams = [...params, parseInt(limit), offset];
        const dataRes = await pgClient.query(
            `SELECT a.id, a.title, a.status, a.scraped_at, u.url, u.metadata
             FROM raw_articles a
             JOIN raw_urls u ON a.url_id = u.id
             JOIN sources s ON u.source_id = s.id
             ${whereClause}
             ORDER BY a.scraped_at DESC
             LIMIT $${params.length + 1} OFFSET $${params.length + 2}`,
            dataParams
        );

        // Also fetch URL-only queue items (queued but not yet scraped)
        const urlQueueRes = await pgClient.query(
            `SELECT u.id, u.url, u.status, u.ingested_at, u.metadata
             FROM raw_urls u
             JOIN sources s ON u.source_id = s.id
             WHERE s.url = $1 AND u.status = 'PENDING_SCRAPE'
             ORDER BY u.ingested_at DESC`,
            [INTERNAL_SOURCE_URL]
        );

        res.json({
            articles: {
                total: parseInt(countRes.rows[0].count),
                page: parseInt(page),
                limit: parseInt(limit),
                items: dataRes.rows,
            },
            pending_scrape: urlQueueRes.rows,
        });
    } catch (err) {
        console.error('[Ingest/Queue] Error:', err);
        res.status(500).json({ error: 'Failed to fetch ingestion queue.' });
    } finally {
        pgClient.release();
    }
});


// ═══════════════════════════════════════════════════════════════════════════════
// ENDPOINT 5: Remove an item from the queue (before it is processed)
// DELETE /api/ingest/:type/:id   type = 'article' | 'url'
// ═══════════════════════════════════════════════════════════════════════════════
router.delete('/:type/:id', async (req, res) => {
    const { type, id } = req.params;

    if (!['article', 'url'].includes(type)) {
        return res.status(400).json({ error: 'Type must be "article" or "url".' });
    }

    const pgClient = await req.app.locals.pgPool.connect();
    try {
        if (type === 'article') {
            // Only allow deletion if still pending (don't disrupt processed items)
            const result = await pgClient.query(
                `DELETE FROM raw_articles
                 WHERE id = $1 AND status IN ('PENDING_EXTRACTION', 'PENDING_CLASSIFICATION')
                 RETURNING id`,
                [id]
            );
            if (result.rowCount === 0) {
                return res.status(404).json({ error: 'Article not found or already processing. Cannot delete.' });
            }
        } else {
            const result = await pgClient.query(
                `DELETE FROM raw_urls
                 WHERE id = $1 AND status = 'PENDING_SCRAPE'
                 RETURNING id, url`,
                [id]
            );
            if (result.rowCount === 0) {
                return res.status(404).json({ error: 'URL not found or already scraped. Cannot delete.' });
            }
        }

        res.json({ success: true, message: `${type} #${id} removed from the queue.` });
    } catch (err) {
        console.error('[Ingest/Delete] Error:', err);
        res.status(500).json({ error: 'Failed to remove item from queue.' });
    } finally {
        pgClient.release();
    }
});


// ── multer error handler ──────────────────────────────────────────────────────
router.use((err, req, res, next) => {
    if (err instanceof multer.MulterError) {
        if (err.code === 'LIMIT_FILE_SIZE') {
            return res.status(413).json({ error: 'File too large. Maximum allowed size is 50MB.' });
        }
        if (err.code === 'LIMIT_FILE_COUNT') {
            return res.status(400).json({ error: 'Too many files. Maximum 10 files per request.' });
        }
        return res.status(400).json({ error: `Upload error: ${err.message}` });
    }
    if (err) {
        return res.status(400).json({ error: err.message });
    }
    next();
});

module.exports = router;
