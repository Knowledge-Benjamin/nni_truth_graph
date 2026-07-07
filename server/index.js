/**
 * Living Truth Graph — Express API Server
 *
 * Endpoints:
 *   GET  /api/health                      — Health check
 *   GET  /api/search?q=...                — Entity/Claim search
 *   GET  /api/entity/:name                — All claims for an entity
 *   GET  /api/claim/:id                   — Single claim + evidence + provenance
 *   GET  /api/timeline/:subject/:predicate — Full truth timeline for a claim type
 *   GET  /api/contradictions              — All open DISPUTED / Controversy nodes
 *   GET  /api/contradiction/:id           — Single controversy with competing claims
 *   GET  /api/human-review                — Queue of HUMAN_REVIEW claims (paginated)
 *   POST /api/human-review/:id/resolve    — Resolve a HUMAN_REVIEW claim
 *   GET  /api/sources                     — Source trust rankings
 *   GET  /api/stats                       — Graph statistics
 */

const express = require('express');
const cors = require('cors');
const dotenv = require('dotenv');
const neo4j = require('neo4j-driver');
const { Pool } = require('pg');
const cookieParser = require('cookie-parser');
const helmet = require('helmet');
const authModule = require('./routes/auth');
const graphRoutes = require('./routes/graph');
const fs = require('fs');
const path = require('path');

const MIGRATION_ADVISORY_LOCK_KEY = 1234567890;
if (process.env.NODE_ENV !== 'production') {
    dotenv.config({ path: path.join(__dirname, '../ai_engine/.env') });
}

const app = express();
const PORT = process.env.PORT || 4000;

// ─── Middleware ──────────────────────────────────────────────────────────────
app.use(helmet({
    contentSecurityPolicy: {
        directives: {
            defaultSrc: ["'self'"],
            scriptSrc: ["'self'", "'unsafe-inline'", "'unsafe-eval'"],
            styleSrc: ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
            imgSrc: ["'self'", "data:", "blob:"],
            connectSrc: ["'self'"],
            fontSrc: ["'self'", "https://fonts.gstatic.com"],
            objectSrc: ["'none'"],
            upgradeInsecureRequests: [],
        }
    },
    crossOriginResourcePolicy: { policy: "cross-origin" }
}));
app.use(cors({
    origin: [
        'http://localhost:5173',
        'http://127.0.0.1:5173',
        'http://localhost:3000',
        'http://localhost:5000',
        'https://truth-graph-frontend-umg3.onrender.com'
    ],
    credentials: true
}));
app.use(express.json({ limit: '10mb' })); // Increased for Base64 image uploads
app.use(cookieParser());

// ─── Database Connections ────────────────────────────────────────────────────
const neo4jDriver = neo4j.driver(
    process.env.NEO4J_URI,
    neo4j.auth.basic(process.env.NEO4J_USER, process.env.NEO4J_PASSWORD)
);

const pgPool = new Pool({ connectionString: process.env.DATABASE_URL });

// Make drivers available to routes
app.locals.neo4j = neo4jDriver;
app.locals.pgPool = pgPool;

// ─── Database Migrations ─────────────────────────────────────────────────────
// On first boot or after reset, ensures all required tables exist
async function runMigrations() {
    const client = await pgPool.connect();
    try {
        console.log('[Migrations] Acquiring advisory lock...');
        await client.query('SELECT pg_advisory_lock($1)', [MIGRATION_ADVISORY_LOCK_KEY]);
        console.log('[Migrations] Advisory lock acquired');

        console.log('[Migrations] Checking database state...');
        
        // Check if extracted_claims table exists
        const tableCheck = await client.query(
            `SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_name = 'extracted_claims'
            );`
        );
        
        const needsFullSetup = !tableCheck.rows[0].exists;
        
        if (needsFullSetup) {
            console.log('[Migrations] ⚠️  Schema incomplete. Running full setup...');
            
            // Enable pgvector
            console.log('[Migrations] Enabling pgvector...');
            await client.query('CREATE EXTENSION IF NOT EXISTS vector;');
            
            // 1. sources
            await client.query(`
                CREATE TABLE IF NOT EXISTS sources (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    domain TEXT NOT NULL,
                    category TEXT,
                    tier VARCHAR(50) DEFAULT 'tier3',
                    epistemic_trust_score FLOAT DEFAULT 0.40,
                    last_ingested_at TIMESTAMP WITH TIME ZONE,
                    feed_etag TEXT,
                    feed_modified TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            `);
            
            // 2. raw_urls
            await client.query(`
                CREATE TABLE IF NOT EXISTS raw_urls (
                    id SERIAL PRIMARY KEY,
                    source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
                    url TEXT UNIQUE NOT NULL,
                    metadata JSONB,
                    status TEXT DEFAULT 'PENDING_SCRAPE',
                    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            `);
            
            // 3. raw_articles
            await client.query(`
                CREATE TABLE IF NOT EXISTS raw_articles (
                    id SERIAL PRIMARY KEY,
                    url_id INTEGER REFERENCES raw_urls(id) ON DELETE CASCADE,
                    title TEXT,
                    author TEXT,
                    publish_date TIMESTAMP WITH TIME ZONE,
                    raw_text TEXT NOT NULL,
                    status TEXT DEFAULT 'PENDING_EXTRACTION',
                    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            `);
            
            // 4. article_categories
            await client.query(`
                CREATE TABLE IF NOT EXISTS article_categories (
                    id SERIAL PRIMARY KEY,
                    article_id INTEGER REFERENCES raw_articles(id) ON DELETE CASCADE,
                    embedding VECTOR(768),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            `);
            
            // Index for embeddings
            await client.query(`
                CREATE INDEX IF NOT EXISTS article_categories_embedding_idx 
                ON article_categories USING hnsw (embedding vector_cosine_ops);
            `);
            
            // 4b. media_provenance
            await client.query(`
                CREATE TABLE IF NOT EXISTS media_provenance (
                    id SERIAL PRIMARY KEY,
                    raw_article_id INTEGER REFERENCES raw_articles(id) ON DELETE CASCADE,
                    media_url TEXT NOT NULL,
                    phash TEXT,
                    clip_embedding VECTOR(512),
                    synthetic_probability FLOAT
                );
            `);
            
            await client.query(`
                CREATE INDEX IF NOT EXISTS media_provenance_clip_idx 
                ON media_provenance USING hnsw (clip_embedding vector_cosine_ops);
            `);
            
            // 5. extracted_claims
            await client.query(`
                CREATE TABLE IF NOT EXISTS extracted_claims (
                    id SERIAL PRIMARY KEY,
                    article_id INTEGER REFERENCES raw_articles(id) ON DELETE CASCADE,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_entity TEXT NOT NULL,
                    quote_context TEXT,
                    extraction_confidence FLOAT,
                    epistemic_score FLOAT DEFAULT NULL,
                    temporal_anchor VARCHAR(255),
                    spatial_anchor VARCHAR(255),
                    spo_fingerprint VARCHAR(255),
                    pipeline_stage VARCHAR(50) DEFAULT 'STAGE_4_RESOLUTION',
                    status VARCHAR(50) DEFAULT 'PROCESSING',
                    lifecycle VARCHAR(50) DEFAULT 'ACTIVE',
                    valid_from TIMESTAMP WITH TIME ZONE,
                    valid_until TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            `);
            
            // Indexes on claims
            await client.query(`
                CREATE INDEX IF NOT EXISTS idx_claims_status ON extracted_claims(status);
            `);
            await client.query(`
                CREATE INDEX IF NOT EXISTS idx_claims_spo ON extracted_claims(spo_fingerprint);
            `);
            
            // 6. claim_provenance (for human review tracking)
            await client.query(`
                CREATE TABLE IF NOT EXISTS claim_provenance (
                    id SERIAL PRIMARY KEY,
                    claim_id INTEGER,
                    neo4j_stance VARCHAR(50),
                    neo4j_matched_claim_id VARCHAR(255),
                    neo4j_similarity FLOAT,
                    internet_original_source TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            `);
            
            // 7. auth_users (for authentication)
            await client.query(`
                CREATE TABLE IF NOT EXISTS auth_users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role VARCHAR(50) DEFAULT 'reviewer',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            `);
            
            // 8. auth_invites (for user onboarding)
            await client.query(`
                CREATE TABLE IF NOT EXISTS auth_invites (
                    id SERIAL PRIMARY KEY,
                    token TEXT UNIQUE NOT NULL,
                    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    created_by_user_id INTEGER REFERENCES auth_users(id),
                    used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            `);
            
            console.log('[Migrations] ✓ All core tables created');
        } else {
            console.log('[Migrations] ✓ Schema exists, skipping full setup');
        }

        await client.query(`
            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES auth_users(id) ON DELETE CASCADE,
                api_key_hash TEXT NOT NULL,
                prefix VARCHAR(10) NOT NULL,
                tier VARCHAR(50) DEFAULT 'basic',
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        `);

        // system_settings for storing dynamic UI configurations (like external API URLs)
        console.log('[Migrations] Ensuring system_settings table...');
        await client.query(`
            CREATE TABLE IF NOT EXISTS system_settings (
                key VARCHAR(255) PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        `);

        await client.query(`
            CREATE OR REPLACE FUNCTION notify_claim_committed() 
            RETURNS trigger AS $$
            BEGIN
                IF NEW.status = 'GRAPH_COMMITTED' AND (OLD.status IS NULL OR OLD.status != 'GRAPH_COMMITTED') THEN
                    PERFORM pg_notify('claim_committed', row_to_json(NEW)::text);
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        `);
        
        await client.query(`
            DROP TRIGGER IF EXISTS trigger_claim_committed ON extracted_claims;
            CREATE TRIGGER trigger_claim_committed
            AFTER UPDATE ON extracted_claims
            FOR EACH ROW
            EXECUTE FUNCTION notify_claim_committed();
        `);

        // Always ensure graph_outbox exists (needed for outbox worker)
        console.log('[Migrations] Ensuring graph_outbox table...');
        await client.query(`
            CREATE TABLE IF NOT EXISTS graph_outbox (
                id SERIAL PRIMARY KEY,
                claim_id VARCHAR(255) NOT NULL,
                decision VARCHAR(50) NOT NULL,
                note TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                processed BOOLEAN DEFAULT FALSE
            );
        `);
        
        console.log('[Migrations] ✓ graph_outbox ready');

        // ── OSINT Investigator Tables ─────────────────────────────────────────
        // investigations: one row per long-running investigation session
        // investigation_leads: the priority queue of entities yet to be explored
        // These tables are additive and do NOT alter any existing columns.
        console.log('[Migrations] Ensuring OSINT investigations tables...');
        await client.query(`
            CREATE TABLE IF NOT EXISTS investigations (
                id SERIAL PRIMARY KEY,
                target TEXT NOT NULL,
                goal_type VARCHAR(50) DEFAULT 'PROFILING',
                -- PROFILING | EXHAUSTIVE_COLLECTION | INFRASTRUCTURE | FINANCIAL
                status VARCHAR(20) DEFAULT 'ACTIVE',
                -- ACTIVE | PAUSED | COMPLETED | FAILED
                findings JSONB DEFAULT '{}',
                -- Running summary: {answer, key_entities, confidence, novel_count}
                max_leads INTEGER DEFAULT 500,
                max_days INTEGER DEFAULT 14,
                concurrent_agents INTEGER DEFAULT 5,
                leads_explored INTEGER DEFAULT 0,
                novel_discoveries INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP WITH TIME ZONE
            );
        `);

        await client.query(`
            CREATE TABLE IF NOT EXISTS investigation_leads (
                id SERIAL PRIMARY KEY,
                investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
                entity_name TEXT NOT NULL,
                lead_type VARCHAR(50) DEFAULT 'GENERAL',
                -- GENERAL | EMAIL | IP | DOMAIN | WALLET | PERSON | ORGANISATION
                priority INTEGER DEFAULT 50,
                -- 0-100, higher is explored first
                status VARCHAR(20) DEFAULT 'PENDING',
                -- PENDING | CLAIMED | EXPLORED | IRRELEVANT
                claimed_at TIMESTAMP WITH TIME ZONE,
                explored_at TIMESTAMP WITH TIME ZONE,
                sub_leads_generated INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (investigation_id, entity_name)
            );
        `);

        await client.query(`
            CREATE INDEX IF NOT EXISTS idx_inv_leads_lookup
            ON investigation_leads (investigation_id, status, priority DESC);
        `);

        console.log('[Migrations] ✓ OSINT investigation tables ready');
        console.log('[Migrations] ✓ All migrations complete');
    } catch (err) {
        console.error('[Migrations] ✗ ERROR:', err.message);
        console.error(err);
        throw err;
    } finally {
        try {
            await client.query('SELECT pg_advisory_unlock($1)', [MIGRATION_ADVISORY_LOCK_KEY]);
            console.log('[Migrations] Advisory lock released');
        } catch (unlockErr) {
            console.error('[Migrations] Failed to release advisory lock:', unlockErr.message);
        }
        client.release();
    }

    // ─── Neo4j Schema Setup ───────────────────────────────────────────────────
    try {
        const session = neo4jDriver.session();
        try {
            console.log('[Neo4j] Setting up constraints...');
            
            // Entity constraint
            try {
                await session.run(`
                    CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE
                `);
            } catch (e) {
                // Constraint might already exist, ignore
            }
            
            // Claim constraint
            try {
                await session.run(`
                    CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (c:Claim) REQUIRE c.id IS UNIQUE
                `);
            } catch (e) {
                // Constraint might already exist, ignore
            }
            
            // Source constraint
            try {
                await session.run(`
                    CREATE CONSTRAINT source_url IF NOT EXISTS FOR (s:Source) REQUIRE s.url IS UNIQUE
                `);
            } catch (e) {
                // Constraint might already exist, ignore
            }
            
            // Indexes
            await session.run(`CREATE INDEX entity_name_idx IF NOT EXISTS FOR (e:Entity) ON (e.name)`);
            await session.run(`CREATE INDEX claim_epistemic_idx IF NOT EXISTS FOR (c:Claim) ON (c.epistemic_score)`);
            
            // Debunking Indexes
            await session.run(`CREATE INDEX claim_verdict_idx IF NOT EXISTS FOR (c:Claim) ON (c.verdict)`);
            await session.run(`CREATE INDEX controversy_subject_predicate_idx IF NOT EXISTS FOR (cv:Controversy) ON (cv.subject, cv.predicate)`);
            await session.run(`CREATE INDEX controversy_open_idx IF NOT EXISTS FOR (cv:Controversy) ON (cv.open)`);
            await session.run(`CREATE INDEX controversy_resolved_idx IF NOT EXISTS FOR (cv:Controversy) ON (cv.resolved)`);
            
            // Neo4j 5.x HNSW vector index for claim embeddings
            // Enables db.index.vector.queryNodes() in 5_resolution.py (single round-trip vs Python cosine loop)
            try {
                await session.run(`
                    CREATE VECTOR INDEX claim_embedding_idx IF NOT EXISTS
                    FOR (c:Claim) ON c.embedding
                    OPTIONS {indexConfig: {
                        \`vector.dimensions\`: 768,
                        \`vector.similarity_function\`: 'cosine'
                    }}
                `);
            } catch (e) {
                // Older Neo4j version or index already exists — non-fatal
                console.log('[Neo4j] Vector index skipped (may need Neo4j 5.11+):', e.message);
            }
            
            console.log('[Neo4j] ✓ Schema ready');
        } finally {
            await session.close();
        }
    } catch (err) {
        console.error('[Neo4j] ✗ ERROR:', err.message);
        // Don't fatal - Neo4j might just be slow, server can still work
    }
}

// ─── Routes ─────────────────────────────────────────────────────────────────
app.use('/api/auth', authModule.router);
app.use('/api', graphRoutes);

const b2bRoutes = require('./routes/b2b');
app.use('/api/v1/b2b', b2bRoutes);

const developerRoutes = require('./routes/developer');
app.use('/api/developer', developerRoutes);

const mediaRoutes = require('./routes/media');
app.use('/api/media', mediaRoutes);

const ingestRoutes = require('./routes/ingest');
app.use('/api/ingest', ingestRoutes);

const investigationsRoutes = require('./routes/investigations');
app.use('/api/investigations', investigationsRoutes);

const calibrationRoutes = require('./routes/calibration');
app.use('/api/calibration', calibrationRoutes);


app.get('/api/health', async (req, res) => {
    try {
        const session = neo4jDriver.session();
        await session.run('RETURN 1');
        await session.close();
        const pgClient = await pgPool.connect();
        pgClient.release();
        res.json({
            status: 'ok', neo4j: 'connected', postgres: 'connected',
            timestamp: new Date().toISOString()
        });
    } catch (e) {
        res.status(503).json({ status: 'error', message: e.message });
    }
});

// ─── Frontend Static Assets ──────────────────────────────────────────────────
const clientDistPath = path.join(__dirname, '../client/dist');
app.use(express.static(clientDistPath, { index: false })); // don't auto-serve index.html

// Helper to inject SSR metadata into index.html
const serveSSR = async (req, res, fetchMeta) => {
    try {
        const indexPath = path.join(clientDistPath, 'index.html');
        if (!fs.existsSync(indexPath)) return res.status(404).send('Frontend not built. Run npm run build in client.');

        let html = fs.readFileSync(indexPath, 'utf8');
        const meta = await fetchMeta(req);

        if (meta) {
            const ogTags = `
                <title>${meta.title}</title>
                <meta property="og:title" content="${meta.title}">
                <meta property="og:description" content="${meta.description}">
                <meta name="description" content="${meta.description}">
                <meta property="og:type" content="website">
                <meta name="twitter:card" content="summary_large_image">
            `;
            html = html.replace('</head>', `${ogTags}</head>`);
        }
        res.send(html);
    } catch (err) {
        console.error('SSR Error:', err);
        res.sendFile(path.join(clientDistPath, 'index.html'));
    }
};

// Intercept specific routes for OpenGraph insertion
app.get('/entity/:slug', async (req, res) => {
    await serveSSR(req, res, async (req) => {
        const session = neo4jDriver.session();
        try {
            const result = await session.run(
                'MATCH (e:Entity) WHERE toLower(e.name) = toLower($slug) RETURN e.name AS name, e.mention_count AS mentions LIMIT 1',
                { slug: decodeURIComponent(req.params.slug) }
            );
            if (result.records.length > 0) {
                const name = result.records[0].get('name');
                const mentions = result.records[0].get('mentions')?.toNumber() || 0;
                return {
                    title: `Explore '${name}' on Truth`,
                    description: `Discover the interconnected facts, claims, and sources surrounding ${name} (${mentions} mentions).`
                };
            }
        } finally {
            await session.close();
        }
        return null;
    });
});

app.get('/claim/:id', async (req, res) => {
    await serveSSR(req, res, async (req) => {
        const session = neo4jDriver.session();
        try {
            const result = await session.run(
                'MATCH (c:Claim {id: $id}) RETURN c.subject AS subj, c.predicate AS pred, c.object AS obj, c.epistemic_score AS score',
                { id: req.params.id }
            );
            if (result.records.length > 0) {
                const rec = result.records[0];
                const stmt = `${rec.get('subj')} ${rec.get('pred').replace(/_/g, ' ').toLowerCase()} ${rec.get('obj')}`;
                const score = Math.round(rec.get('score') * 100);
                return {
                    title: `Fact Check: ${stmt}`,
                    description: `Truth Analysis: Verified with an epistemic confidence score of ${score}%. Explore the evidence and provenance.`
                };
            }
        } finally {
            await session.close();
        }
        return null;
    });
});

// ─── Catch-All for React Router ──────────────────────────────────────────────
app.use((req, res) => {
    res.sendFile(path.join(clientDistPath, 'index.html'));
});

// ─── Start ───────────────────────────────────────────────────────────────────
const { fork } = require('child_process');

(async () => {
    try {
        // Run migrations with a longer timeout for deploys and DDL operations
        const migrationPromise = runMigrations();
        const timeoutPromise = new Promise((_, reject) =>
            setTimeout(() => reject(new Error('Migrations timeout after 60s')), 60000)
        );
        await Promise.race([migrationPromise, timeoutPromise]);
    } catch (err) {
        console.error('[Truth API] Failed to run migrations:', err);
        process.exit(1);
    }

    const outboxWorker = fork(path.join(__dirname, 'outbox_worker.js'));

    outboxWorker.on('error', (err) => {
        console.error('[Truth API] Outbox Worker Error:', err);
    });

    const server = app.listen(PORT, '0.0.0.0', () => {
        console.log(`[Truth API] Running externally on port ${PORT}`);
        console.log(`[Truth API] Outbox Background Worker started.`);
    });
    
    // Attach WebSocket Firehose Handler
    const setupFirehose = require('./routes/firehose');
    const { wss, firehoseWorker } = setupFirehose(server, pgPool);

    process.on('exit', () => {
        neo4jDriver.close();
        pgPool.end();
        if (outboxWorker) outboxWorker.kill();
    });
})();
