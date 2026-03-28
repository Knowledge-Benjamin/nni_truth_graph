require('dotenv').config({ path: '../ai_engine/.env' });
const { Pool } = require('pg');
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
pool.query("UPDATE raw_articles SET status = 'PENDING_EXTRACTION' WHERE status IN ('EXTRACTED', 'FAILED_EXTRACTION') AND id IN (SELECT id FROM raw_articles WHERE status IN ('EXTRACTED', 'FAILED_EXTRACTION') LIMIT 1) RETURNING id, status").then(res => {
    console.log('Reset article:', res.rows);
    process.exit(0);
}).catch(e => {
    console.error(e);
    process.exit(1);
});
