const express = require('express');
const router = express.Router();

router.get('/curve', async (req, res) => {
    try {
        const pool = req.app.locals.pgPool;
        const result = await pool.query(`
            SELECT 
                CASE 
                    WHEN epistemic_score < 0.2 THEN '0.0-0.2' 
                    WHEN epistemic_score < 0.4 THEN '0.2-0.4' 
                    WHEN epistemic_score < 0.6 THEN '0.4-0.6' 
                    WHEN epistemic_score < 0.8 THEN '0.6-0.8' 
                    ELSE '0.8-1.0' 
                END AS bucket, 
                SUM(CASE WHEN status IN ('GRAPH_COMMITTED', 'AUTO_APPROVE') THEN 1 ELSE 0 END)::int as true_count, 
                SUM(CASE WHEN status = 'CONTRADICTED' THEN 1 ELSE 0 END)::int as false_count, 
                COUNT(*)::int as total_count 
            FROM extracted_claims 
            WHERE status IN ('GRAPH_COMMITTED', 'AUTO_APPROVE', 'CONTRADICTED') 
              AND epistemic_score IS NOT NULL 
            GROUP BY bucket 
            ORDER BY bucket;
        `);

        const data = result.rows.map(row => {
            const trueCount = parseInt(row.true_count) || 0;
            const falseCount = parseInt(row.false_count) || 0;
            const total = trueCount + falseCount;
            // Predicted probability is the midpoint of the bucket
            let predicted_prob = 0.9;
            if (row.bucket === '0.0-0.2') predicted_prob = 0.1;
            else if (row.bucket === '0.2-0.4') predicted_prob = 0.3;
            else if (row.bucket === '0.4-0.6') predicted_prob = 0.5;
            else if (row.bucket === '0.6-0.8') predicted_prob = 0.7;

            return {
                bucket: row.bucket,
                true_count: trueCount,
                false_count: falseCount,
                total_count: parseInt(row.total_count) || 0,
                predicted_prob: predicted_prob,
                actual_prob: total > 0 ? trueCount / total : null
            };
        });

        // Ensure all 5 buckets exist in the output even if empty
        const allBuckets = [
            { bucket: '0.0-0.2', predicted_prob: 0.1, actual_prob: null, true_count: 0, false_count: 0, total_count: 0 },
            { bucket: '0.2-0.4', predicted_prob: 0.3, actual_prob: null, true_count: 0, false_count: 0, total_count: 0 },
            { bucket: '0.4-0.6', predicted_prob: 0.5, actual_prob: null, true_count: 0, false_count: 0, total_count: 0 },
            { bucket: '0.6-0.8', predicted_prob: 0.7, actual_prob: null, true_count: 0, false_count: 0, total_count: 0 },
            { bucket: '0.8-1.0', predicted_prob: 0.9, actual_prob: null, true_count: 0, false_count: 0, total_count: 0 },
        ];

        data.forEach(d => {
            const index = allBuckets.findIndex(b => b.bucket === d.bucket);
            if (index !== -1) {
                allBuckets[index] = d;
            }
        });

        res.json({ success: true, data: allBuckets });
    } catch (error) {
        console.error('Calibration query error:', error);
        res.status(500).json({ success: false, error: 'Failed to fetch calibration curve' });
    }
});

module.exports = router;
