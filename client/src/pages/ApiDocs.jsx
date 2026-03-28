import React, { useState } from 'react';
import { Book, Code, Terminal, Zap, Shield, Globe } from 'lucide-react';

export default function ApiDocs() {
    const [activeSection, setActiveSection] = useState('intro');

    const styles = {
        container: { display: 'flex', height: 'calc(100vh - 60px)', color: '#f8fafc', backgroundColor: '#020617', fontFamily: 'system-ui, sans-serif' },
        sidebar: { width: 280, borderRight: '1px solid #1e293b', backgroundColor: '#0f172a', overflowY: 'auto' },
        main: { flex: 1, overflowY: 'auto', padding: '40px 60px' },
        navItem: (active) => ({
            padding: '12px 24px', cursor: 'pointer', fontSize: 14, fontWeight: 500,
            color: active ? '#38bdf8' : '#94a3b8',
            backgroundColor: active ? '#0c2d48' : 'transparent',
            borderLeft: active ? '3px solid #38bdf8' : '3px solid transparent',
            display: 'flex', alignItems: 'center', gap: 10
        }),
        h1: { fontSize: 32, fontWeight: 700, margin: '0 0 20px 0' },
        h2: { fontSize: 24, fontWeight: 600, margin: '40px 0 20px 0', borderBottom: '1px solid #1e293b', paddingBottom: 10 },
        p: { color: '#cbd5e1', lineHeight: 1.7, fontSize: 16, marginBottom: 20 },
        codeBlock: { backgroundColor: '#0f172a', padding: 20, borderRadius: 8, fontFamily: 'monospace', fontSize: 14, overflowX: 'auto', border: '1px solid #1e293b', color: '#e2e8f0', marginBottom: 20 },
        badge: { padding: '2px 6px', borderRadius: 4, fontSize: 12, fontWeight: 'bold', marginRight: 10 }
    };

    const GetBadge = () => <span style={{ ...styles.badge, backgroundColor: '#0ea5e9', color: '#fff' }}>GET</span>;
    const WsBadge = () => <span style={{ ...styles.badge, backgroundColor: '#f59e0b', color: '#fff' }}>WSS</span>;

    const sections = [
        { id: 'intro', icon: <Book size={16}/>, title: 'Introduction' },
        { id: 'auth', icon: <Shield size={16}/>, title: 'Authentication' },
        { id: 'historical', icon: <Globe size={16}/>, title: 'Historical REST Data' },
        { id: 'firehose', icon: <Zap size={16}/>, title: 'Real-Time Firehose' }
    ];

    return (
        <div style={styles.container}>
            <div style={styles.sidebar}>
                <div style={{ padding: '30px 24px', borderBottom: '1px solid #1e293b' }}>
                    <div style={{ letterSpacing: 1, fontSize: 12, textTransform: 'uppercase', color: '#64748b', fontWeight: 700 }}>Truth Graph</div>
                    <div style={{ fontSize: 18, fontWeight: 600, marginTop: 5 }}>API Reference</div>
                </div>
                <div style={{ paddingTop: 20 }}>
                    {sections.map(sec => (
                        <div key={sec.id} style={styles.navItem(activeSection === sec.id)} onClick={() => setActiveSection(sec.id)}>
                            {sec.icon} {sec.title}
                        </div>
                    ))}
                </div>
            </div>

            <div style={styles.main}>
                <div style={{ maxWidth: 800 }}>

                    {activeSection === 'intro' && (
                        <div>
                            <h1 style={styles.h1}>Introduction to the Truth Protocol</h1>
                            <p style={styles.p}>
                                The Truth Graph B2B Enterprise API provides programmatic access to our entirely autonomous, self-verifying knowledge graph. You can utilize our REST endpoints to pull massive historical timelines for training AI agents, or connect to our Real-Time WebSocket Firehose for algorithmic trading signals based on global news flow.
                            </p>
                            <div style={{ backgroundColor: '#1e293b', padding: 20, borderRadius: 8, display: 'flex', gap: 15, alignItems: 'flex-start' }}>
                                <Terminal size={24} color="#38bdf8" style={{ flexShrink: 0 }} />
                                <div>
                                    <h4 style={{ margin: '0 0 5px 0', fontSize: 15 }}>Developer Dashboard</h4>
                                    <p style={{ margin: 0, fontSize: 14, color: '#94a3b8' }}>To interact with these endpoints, you must generate a secure API Key from your <a href="/developer" style={{ color: '#38bdf8', textDecoration: 'none', fontWeight: 'bold' }}>Developer Portal</a>, accessible from the top navigation bar when logged in.</p>
                                </div>
                            </div>
                        </div>
                    )}

                    {activeSection === 'auth' && (
                        <div>
                            <h1 style={styles.h1}>Authentication</h1>
                            <p style={styles.p}>
                                We use standard Bearer Token authentication. Include your secret API key in the `Authorization` header of your HTTP requests.
                            </p>
                            <div style={styles.codeBlock}>
                                Authorization: Bearer sk_live_your_secret_crypto_string_here
                            </div>
                            <p style={styles.p}>
                                All requests lacking a valid key or passing a revoked/malformed key will receive a <code>401 Unauthorized</code> response. Exceeding your billing tier's rate limit will yield a <code>429 Too Many Requests</code> response.
                            </p>
                        </div>
                    )}

                    {activeSection === 'historical' && (
                        <div>
                            <h1 style={styles.h1}>Historical REST Data</h1>
                            <p style={styles.p}>Query structured SPO (Subject-Predicate-Object) facts mapped by the AI Engine across time.</p>

                            <h2 style={styles.h2}><GetBadge/> /api/v1/b2b/claims</h2>
                            <p style={styles.p}>Retrieve paginated canonical facts currently anchored to the Truth Graph.</p>
                            <div style={styles.codeBlock}>
<pre style={{ margin: 0, color: '#fcd34d' }}>curl -X GET \</pre>
<pre style={{ margin: 0 }}>  "https://truth-graph-server.onrender.com/api/v1/b2b/claims?subject=Tesla&min_score=0.85&limit=100"</pre>
<pre style={{ margin: 0, color: '#fcd34d' }}>  -H 'Authorization: Bearer sk_live_...'</pre>
                            </div>

                            <p style={styles.p}><strong>Returns:</strong></p>
                            <div style={styles.codeBlock}>
<pre style={{ margin: 0 }}>{`{
  "data": [
    {
      "id": 8421,
      "subject": "Tesla",
      "predicate": "ACQUIRED",
      "object_entity": "StartupX",
      "epistemic_score": 0.94,
      "quote_context": "...Tesla has finalized the acquisition of...",
      "status": "GRAPH_COMMITTED"
    }
  ],
  "meta": { "limit": 100, "offset": 0 }
}`}</pre>
                            </div>
                            
                            <h2 style={styles.h2}><GetBadge/> /api/v1/b2b/datasets/daily-snapshot</h2>
                            <p style={styles.p}>Download the 50GB CSV file containing a full mathematical snapshot of the entire graph database. Generates zero compute load on the REST API. (Enterprise Base Feature).</p>
                        </div>
                    )}

                    {activeSection === 'firehose' && (
                        <div>
                            <h1 style={styles.h1}>Real-Time WebSocket Firehose</h1>
                            <p style={styles.p}>
                                Connect an open socket directly to our PostgreSQL internal trigger bus, allowing your trading algorithms to receive fact-verification signals milliseconds after our AI parses the global news.
                            </p>

                            <h2 style={styles.h2}><WsBadge/> /firehose</h2>
                            <p style={styles.p}>Pass your token as a query parameter during the socket handshake.</p>
                            <div style={styles.codeBlock}>
<pre style={{ margin: 0 }}>{`const WebSocket = require('ws');
const ws = new WebSocket('wss://truth-graph-server.onrender.com/firehose?api_key=sk_live_...');

ws.on('open', () => {
  // Subscribe to entities of interest
  ws.send(JSON.stringify({ 
    action: 'subscribe', 
    subject: 'OpenAI' 
  }));
});

ws.on('message', (event) => {
  const payload = JSON.parse(event);
  if (payload.event === 'claim_committed') {
    console.log("MARKET SIGNAL DETECTED:", payload.data);
  }
});`}</pre>
                            </div>
                        </div>
                    )}

                </div>
            </div>
        </div>
    );
}
