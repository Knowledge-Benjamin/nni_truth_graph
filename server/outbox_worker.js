require('dotenv').config();
const { Pool } = require('pg');

function getReviewResolutionUpdate(decision) {
    if (decision === 'APPROVE') {
        return {
            sql: `
                UPDATE extracted_claims
                SET pipeline_stage = 'STAGE_6_DEDUP',
                    status = 'PROCESSING'
                WHERE id = $1
            `,
            pipelineStage: 'STAGE_6_DEDUP',
            status: 'PROCESSING'
        };
    }

    if (decision === 'REJECT') {
        return {
            sql: `
                UPDATE extracted_claims
                SET status = 'AUTO_REJECT'
                WHERE id = $1
            `,
            pipelineStage: null,
            status: 'AUTO_REJECT'
        };
    }

    return {
        sql: `
            UPDATE extracted_claims
            SET status = 'RETRACTED'
            WHERE id = $1
        `,
        pipelineStage: null,
        status: 'RETRACTED'
    };
}

const pgPool = new Pool({
    connectionString: process.env.DATABASE_URL
});

const POLL_INTERVAL_MS = 5000;

async function processOutbox() {
    const client = await pgPool.connect();

    try {
        // Lock rows to prevent concurrent workers from processing the same event
        const { rows } = await client.query(`
            SELECT id, claim_id, decision, note
            FROM graph_outbox 
            WHERE processed = FALSE 
            ORDER BY created_at ASC 
            LIMIT 10 
            FOR UPDATE SKIP LOCKED
        `);

        if (rows.length === 0) return;

        console.log(`[Outbox Worker] Found ${rows.length} pending graph synchronization events.`);

        for (const event of rows) {
            const { id, claim_id, decision, note } = event;

            try {
                if (decision === 'APPROVE') {
                    const update = getReviewResolutionUpdate(decision);
                    await client.query(update.sql, [claim_id]);

                    console.log(`  -> Re-enqueued Claim ${claim_id} to ${update.pipelineStage} for deduplication review.`);
                } else if (decision === 'REJECT') {
                    // Mark as rejected, don't enqueue to mutation
                    await client.query(`
                        UPDATE extracted_claims
                        SET status = 'AUTO_REJECT'
                        WHERE id = $1
                    `, [claim_id]);

                    console.log(`  -> Rejected Claim ${claim_id}`);
                } else if (decision === 'RETRACT') {
                    // Mark as retracted
                    await client.query(`
                        UPDATE extracted_claims
                        SET status = 'RETRACTED'
                        WHERE id = $1
                    `, [claim_id]);

                    console.log(`  -> Retracted Claim ${claim_id}`);
                }

                // Mark event as processed
                await client.query('UPDATE graph_outbox SET processed = TRUE WHERE id = $1', [id]);
                console.log(`  -> Successfully processed event ${id}.`);

            } catch (err) {
                console.error(`  -> [ERROR] Failed to process event ${id}. Will retry later.`, err);
            }
        }
    } catch (err) {
        console.error('[Outbox Worker] Fatal error checking outbox:', err);
    } finally {
        client.release();
    }
}

async function loop() {
    console.log(`[Outbox Worker] Started. Polling for dual-write transactions every ${POLL_INTERVAL_MS}ms...`);
    while (true) {
        await processOutbox();
        await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS));
    }
}

// Handle graceful shutdown
process.on('SIGINT', async () => {
    console.log("\n[Outbox Worker] Shutting down...");
    await pgPool.end();
    process.exit(0);
});

loop();
