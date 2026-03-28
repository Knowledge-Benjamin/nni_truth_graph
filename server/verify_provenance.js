require('dotenv').config({ path: '../ai_engine/.env' });
const { Client } = require('pg');
const client = new Client({ connectionString: process.env.DATABASE_URL });
client.connect().then(() => {
    return client.query("SELECT id, subject, model_version, prompt_version, ai_metadata FROM extracted_claims ORDER BY id DESC LIMIT 2");
}).then(res => {
    console.log(JSON.stringify(res.rows, null, 2));
    process.exit(0);
}).catch(e => {
    console.error(e);
    process.exit(1);
});
