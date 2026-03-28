require('dotenv').config({ path: '../ai_engine/.env' });
const { Pool } = require('pg');
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
pool.query("CREATE TABLE IF NOT EXISTS graph_outbox (id SERIAL PRIMARY KEY, claim_id VARCHAR(255) NOT NULL, decision VARCHAR(50) NOT NULL, note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, processed BOOLEAN DEFAULT FALSE);").then(() => {
    console.log('Table created!');
    process.exit(0);
}).catch(e => {
    console.error(e);
    process.exit(1);
});
