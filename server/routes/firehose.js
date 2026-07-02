const { WebSocketServer } = require('ws');
const crypto = require('crypto');
const url = require('url');

function setupFirehose(server, pgPool) {
    const wss = new WebSocketServer({ noServer: true });

    // Handle initial HTTP upgrade request
    server.on('upgrade', async (request, socket, head) => {
        const { pathname, query } = url.parse(request.url, true);
        
        if (pathname === '/firehose') {
            const token = query.api_key;
            if (!token || token.length < 32) {
                socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
                socket.destroy();
                return;
            }

            const prefix = token.slice(0, 8);
            const keyHash = crypto.createHash('sha256').update(token).digest('hex');

            try {
                const client = await pgPool.connect();
                const { rows } = await client.query(
                    "SELECT user_id, tier, active FROM api_keys WHERE prefix = $1 AND api_key_hash = $2 AND active = TRUE",
                    [prefix, keyHash]
                );
                client.release();

                if (rows.length === 0) {
                    socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
                    socket.destroy();
                    return;
                }

                // If tier doesn't allow firehose, reject (e.g., enterprise only)
                if (rows[0].tier !== 'enterprise') {
                    socket.write('HTTP/1.1 403 Forbidden\r\n\r\n');
                    socket.destroy();
                    return;
                }

                wss.handleUpgrade(request, socket, head, (ws) => {
                    ws.b2bUser = rows[0];
                    wss.emit('connection', ws, request);
                });
            } catch (err) {
                console.error('[Firehose Upgrade Error]', err);
                socket.write('HTTP/1.1 500 Internal Server Error\r\n\r\n');
                socket.destroy();
                return;
            }
        }
    });

    wss.on('connection', (ws) => {
        ws.subscriptions = new Set();
        console.log(`[Firehose] Client connected (User ${ws.b2bUser.user_id})`);

        ws.on('message', (message) => {
            try {
                const data = JSON.parse(message);
                if (data.action === 'subscribe' && data.subject) {
                    ws.subscriptions.add(data.subject.toLowerCase());
                    ws.send(JSON.stringify({ status: 'subscribed', subject: data.subject }));
                } else if (data.action === 'unsubscribe' && data.subject) {
                    ws.subscriptions.delete(data.subject.toLowerCase());
                }
            } catch (e) {
                ws.send(JSON.stringify({ error: 'Invalid message format' }));
            }
        });

        ws.on('close', () => {
            console.log(`[Firehose] Client disconnected (User ${ws.b2bUser.user_id})`);
        });
    });

    // Start Postgres LISTEN worker
    startListenWorker(pgPool, wss);

    return { wss };
}

async function startListenWorker(pgPool, wss) {
    let client;
    try {
        client = await pgPool.connect();
        await client.query('LISTEN claim_committed');
        await client.query('LISTEN investigation_update');
        console.log('[Firehose Worker] Listening for Postgres claim_committed and investigation_update events...');

        client.on('notification', (msg) => {
            if (msg.channel === 'claim_committed') {
                try {
                    const claim = JSON.parse(msg.payload);
                    const subjectLower = claim.subject.toLowerCase();
                    const objectLower = claim.object_entity.toLowerCase();

                    // Broadcast to subscribed clients securely
                    wss.clients.forEach((ws) => {
                        if (ws.readyState === 1 /* WebSocket.OPEN */ && 
                            (ws.subscriptions.has(subjectLower) || 
                             ws.subscriptions.has(objectLower) || 
                             ws.subscriptions.has('*'))) {
                            ws.send(JSON.stringify({
                                event: 'claim_committed',
                                data: claim
                            }));
                        }
                    });
                } catch (e) {
                    console.error('[Firehose Worker] Error parsing Postgres payload:', e);
                }
            } else if (msg.channel === 'investigation_update') {
                try {
                    const update = JSON.parse(msg.payload);
                    // Broadcast investigation updates to clients subscribed to '*' or 'investigations'
                    wss.clients.forEach((ws) => {
                        if (ws.readyState === 1 /* WebSocket.OPEN */ && 
                            (ws.subscriptions.has('investigations') || ws.subscriptions.has('*'))) {
                            ws.send(JSON.stringify({
                                event: 'INVESTIGATION_UPDATE',
                                data: update
                            }));
                        }
                    });
                } catch (e) {
                    console.error('[Firehose Worker] Error parsing investigation_update payload:', e);
                }
            }
        });
        
        client.on('end', () => {
             console.error('[Firehose Worker] Postgres connection ended unexpectedly, reconnecting...');
             setTimeout(() => startListenWorker(pgPool, wss), 5000);
        });
        
    } catch (err) {
        console.error('[Firehose Worker] Failed to connect to Postgres:', err);
        if (client) client.release();
        // Retry connection with backoff
        setTimeout(() => startListenWorker(pgPool, wss), 5000);
    }
}

module.exports = setupFirehose;
