require('dotenv').config({ path: '../ai_engine/.env' });
const { Client } = require('pg');
const client = new Client({ connectionString: process.env.DATABASE_URL });
client.connect().then(() => {
    return client.query("SELECT COUNT(*) FROM sources;");
}).then(res => {
    console.log('Source count:', res.rows[0].count);
    return client.query("SELECT id, name, url FROM sources LIMIT 5;");
}).then(res => {
    console.log('Sample sources:', JSON.stringify(res.rows, null, 2));
    process.exit(0);
}).catch(e => {
    console.error(e);
    process.exit(1);
});
