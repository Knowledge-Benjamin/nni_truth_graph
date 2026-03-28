require('dotenv').config({ path: '../ai_engine/.env' });
const { Pool } = require('pg');
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const query = `
ALTER TABLE extracted_claims
ADD COLUMN IF NOT EXISTS model_version VARCHAR(100),
ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(50),
ADD COLUMN IF NOT EXISTS ai_metadata JSONB;
`;
pool.query(query).then(() => {
    console.log('Provenance columns added!');
    process.exit(0);
}).catch(e => {
    console.error(e);
    process.exit(1);
});
