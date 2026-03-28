import React, { useState, useEffect } from 'react';
import {
    Key, Plus, Trash2, Copy, CheckCircle2, AlertTriangle, ShieldCheck,
    Zap, Globe, Code2, Terminal, BookOpen, ChevronRight, Activity,
    Clock, Lock, RefreshCw, ExternalLink, BarChart3, Layers, Wifi
} from 'lucide-react';
import { api } from '../api';

const BASE_URL = 'https://truth-graph-server.onrender.com';

const TIERS = {
    basic:      { color: '#64748b', bg: '#1e293b', label: 'Basic',      rpm: '60 req/min',   features: ['REST Claims','REST Sources'] },
    pro:        { color: '#a78bfa', bg: '#2e1065', label: 'Pro',        rpm: '500 req/min',  features: ['REST Claims','REST Sources','Bulk CSV Snapshot'] },
    enterprise: { color: '#38bdf8', bg: '#0c2d48', label: 'Enterprise', rpm: '5,000 req/min', features: ['REST Claims','REST Sources','Bulk CSV Snapshot','Real-Time Firehose'] },
};

const CODE_EXAMPLES = {
    curl: (key) => `curl -X GET \\
  "${BASE_URL}/api/v1/b2b/claims?subject=Tesla&min_score=0.85&limit=10" \\
  -H "Authorization: Bearer ${key}"`,
    node: (key) => `const fetch = require('node-fetch');

const resp = await fetch(
  '${BASE_URL}/api/v1/b2b/claims?subject=Tesla&limit=10',
  { headers: { Authorization: 'Bearer ${key}' } }
);
const { data } = await resp.json();
console.log(data);`,
    python: (key) => `import requests

resp = requests.get(
    '${BASE_URL}/api/v1/b2b/claims',
    params={'subject': 'Tesla', 'limit': 10},
    headers={'Authorization': f'Bearer ${key}'}
)
print(resp.json()['data'])`,
    websocket: (key) => `const WebSocket = require('ws');
const ws = new WebSocket(
  'wss://truth-graph-server.onrender.com/firehose?api_key=${key}'
);

ws.on('open', () => {
  ws.send(JSON.stringify({ action: 'subscribe', subject: 'OpenAI' }));
});

ws.on('message', (raw) => {
  const { event, data } = JSON.parse(raw);
  if (event === 'claim_committed') {
    // Real-time trading signal received!
    console.log('SIGNAL:', data.subject, data.predicate, data.epistemic_score);
  }
});`,
};

const ENDPOINTS = [
    {
        method: 'GET', badge: '#0ea5e9', path: '/api/v1/b2b/claims',
        desc: 'Query canonical SPO facts from the Truth Graph.',
        params: [
            { name: 'subject', type: 'string', desc: 'Filter by entity name (e.g. "Tesla", "WHO")' },
            { name: 'min_score', type: 'float', desc: 'Minimum epistemic trust score (0.0 – 1.0)' },
            { name: 'limit', type: 'int', desc: 'Max results to return (default: 50, max: 1000)' },
            { name: 'offset', type: 'int', desc: 'Pagination offset' },
        ],
        tier: 'basic',
    },
    {
        method: 'GET', badge: '#0ea5e9', path: '/api/v1/b2b/sources',
        desc: 'Retrieve source epistemic trust rankings across all ingested domains.',
        params: [
            { name: 'limit', type: 'int', desc: 'Max results to return (default: 50)' },
        ],
        tier: 'basic',
    },
    {
        method: 'GET', badge: '#10b981', path: '/api/v1/b2b/datasets/daily-snapshot',
        desc: 'Download the full CSV snapshot of the entire truth graph. Enterprise only.',
        params: [],
        tier: 'enterprise',
    },
    {
        method: 'WSS', badge: '#f59e0b', path: '/firehose',
        desc: 'Connect via WebSocket for real-time claim events. Pass API key as query param.',
        params: [
            { name: 'api_key', type: 'string', desc: 'Your secret API key (query parameter)' },
        ],
        tier: 'enterprise',
    },
];

function CodeBlock({ code, lang, onCopy }) {
    const [copied, setCopied] = useState(false);
    const handle = () => {
        navigator.clipboard.writeText(code);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };
    return (
        <div style={{ position: 'relative', backgroundColor: '#020617', borderRadius: 8, border: '1px solid #1e293b', overflow: 'hidden' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '1px solid #1e293b', backgroundColor: '#0f172a' }}>
                <span style={{ fontSize: 12, color: '#64748b', fontFamily: 'monospace' }}>{lang}</span>
                <button onClick={handle} style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: '1px solid #334155', color: copied ? '#10b981' : '#94a3b8', borderRadius: 4, padding: '3px 10px', cursor: 'pointer', fontSize: 12 }}>
                    {copied ? <CheckCircle2 size={12} /> : <Copy size={12} />} {copied ? 'Copied!' : 'Copy'}
                </button>
            </div>
            <pre style={{ margin: 0, padding: '16px', overflowX: 'auto', fontFamily: 'monospace', fontSize: 13, lineHeight: 1.6, color: '#e2e8f0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{code}</pre>
        </div>
    );
}

function StatCard({ icon, label, value, sub, color }) {
    return (
        <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '20px 24px', display: 'flex', alignItems: 'flex-start', gap: 16 }}>
            <div style={{ backgroundColor: color + '22', padding: 10, borderRadius: 8 }}>{icon}</div>
            <div>
                <div style={{ fontSize: 13, color: '#64748b', marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: '#f8fafc' }}>{value}</div>
                {sub && <div style={{ fontSize: 12, color: '#475569', marginTop: 3 }}>{sub}</div>}
            </div>
        </div>
    );
}

export default function DeveloperDashboard() {
    const [keys, setKeys] = useState([]);
    const [loading, setLoading] = useState(true);
    const [rawKeyModal, setRawKeyModal] = useState(null);
    const [copied, setCopied] = useState(false);
    const [tab, setTab] = useState('overview');        // overview | keys | endpoints | quickstart
    const [codeTab, setCodeTab] = useState('curl');
    const [expandedEndpoint, setExpandedEndpoint] = useState(null);

    useEffect(() => { fetchKeys(); }, []);

    const fetchKeys = async () => {
        setLoading(true);
        try {
            const data = await api.getApiKeys();
            setKeys(data.keys || []);
        } catch (e) { console.error(e); }
        finally { setLoading(false); }
    };

    const handleGenerate = async () => {
        try {
            const result = await api.generateApiKey();
            setRawKeyModal(result.raw_key);
            fetchKeys();
        } catch (e) { alert('Failed to generate key'); }
    };

    const handleRevoke = async (id) => {
        if (!window.confirm('Revoke this key? Active applications will break immediately.')) return;
        try { await api.revokeApiKey(id); fetchKeys(); }
        catch (e) { alert('Failed to revoke key'); }
    };

    const handleCopy = () => {
        navigator.clipboard.writeText(rawKeyModal);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const topTier = keys.length ? (keys.find(k => k.tier === 'enterprise') ? 'enterprise' : keys.find(k => k.tier === 'pro') ? 'pro' : 'basic') : 'basic';
    const tierInfo = TIERS[topTier];
    const previewKey = rawKeyModal || (keys[0] ? keys[0].prefix + '••••••••••••••' : 'sk_live_your_key_here');

    const tabs = [
        { id: 'overview',   icon: <BarChart3 size={15} />,  label: 'Overview' },
        { id: 'keys',       icon: <Key size={15} />,         label: 'API Keys' },
        { id: 'endpoints',  icon: <Layers size={15} />,      label: 'Endpoints' },
        { id: 'quickstart', icon: <Zap size={15} />,         label: 'Quick Start' },
    ];

    return (
        <div style={{ minHeight: 'calc(100vh - 60px)', backgroundColor: '#020617', color: '#f8fafc', fontFamily: "'Inter', system-ui, sans-serif" }}>
            {/* ── Top Header ── */}
            <div style={{ borderBottom: '1px solid #1e293b', backgroundColor: '#0a0f1e', padding: '24px 40px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                    <div style={{ background: 'linear-gradient(135deg,#3b82f6,#06b6d4)', padding: 10, borderRadius: 10 }}>
                        <ShieldCheck size={22} color="white" />
                    </div>
                    <div>
                        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>Developer Portal</h1>
                        <p style={{ margin: 0, fontSize: 13, color: '#64748b' }}>Truth Graph Enterprise API</p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    <a href="/docs" style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#94a3b8', fontSize: 13, textDecoration: 'none', padding: '7px 14px', border: '1px solid #1e293b', borderRadius: 6 }}>
                        <BookOpen size={14} /> API Docs <ExternalLink size={12} />
                    </a>
                    <button onClick={handleGenerate} style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'linear-gradient(90deg,#3b82f6,#06b6d4)', color: 'white', border: 'none', padding: '8px 18px', borderRadius: 6, cursor: 'pointer', fontWeight: 600, fontSize: 14 }}>
                        <Plus size={16} /> Create New Key
                    </button>
                </div>
            </div>

            {/* ── Tab Nav ── */}
            <div style={{ display: 'flex', gap: 4, padding: '0 40px', borderBottom: '1px solid #1e293b', backgroundColor: '#0a0f1e' }}>
                {tabs.map(t => (
                    <button key={t.id} onClick={() => setTab(t.id)} style={{
                        display: 'flex', alignItems: 'center', gap: 7, padding: '14px 20px', background: 'none', border: 'none',
                        cursor: 'pointer', fontSize: 14, fontWeight: 500,
                        color: tab === t.id ? '#38bdf8' : '#64748b',
                        borderBottom: tab === t.id ? '2px solid #38bdf8' : '2px solid transparent',
                        transition: 'all 0.2s'
                    }}>{t.icon}{t.label}</button>
                ))}
            </div>

            <div style={{ padding: '36px 40px', maxWidth: 1100, margin: '0 auto' }}>

                {/* ══════════ OVERVIEW TAB ══════════ */}
                {tab === 'overview' && (
                    <div>
                        {/* Stats row */}
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginBottom: 32 }}>
                            <StatCard icon={<Key size={20} color="#38bdf8" />} label="Active API Keys" value={keys.length} sub={loading ? 'Loading...' : 'across your account'} color="#38bdf8" />
                            <StatCard icon={<Activity size={20} color="#a78bfa" />} label="Current Tier" value={tierInfo.label} sub={tierInfo.rpm} color="#a78bfa" />
                            <StatCard icon={<Wifi size={20} color="#10b981" />} label="Firehose" value={topTier === 'enterprise' ? 'Enabled' : 'Locked'} sub={topTier !== 'enterprise' ? 'Upgrade to Enterprise' : 'Real-time stream active'} color="#10b981" />
                            <StatCard icon={<Globe size={20} color="#f59e0b" />} label="Base URL" value="Render.com" sub="Auto-scaling infrastructure" color="#f59e0b" />
                        </div>

                        {/* Tier info card */}
                        <div style={{ backgroundColor: '#0f172a', border: `1px solid ${tierInfo.color}44`, borderRadius: 12, padding: 28, marginBottom: 24 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 16 }}>
                                <div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                                        <span style={{ backgroundColor: tierInfo.bg, color: tierInfo.color, padding: '4px 12px', borderRadius: 20, fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1 }}>{tierInfo.label}</span>
                                        <span style={{ color: '#64748b', fontSize: 13 }}>Current Plan</span>
                                    </div>
                                    <p style={{ color: '#94a3b8', fontSize: 14, margin: 0 }}>Rate limit: <strong style={{ color: '#f8fafc' }}>{tierInfo.rpm}</strong></p>
                                </div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                                    {tierInfo.features.map(f => (
                                        <span key={f} style={{ display: 'flex', alignItems: 'center', gap: 5, backgroundColor: '#1e293b', padding: '5px 12px', borderRadius: 6, fontSize: 13, color: '#cbd5e1' }}>
                                            <CheckCircle2 size={13} color="#10b981" />{f}
                                        </span>
                                    ))}
                                </div>
                            </div>
                        </div>

                        {/* Quick links */}
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
                            {[
                                { icon: <Key size={18} color="#38bdf8" />, title: 'Manage API Keys', desc: 'Create, view, and revoke your secret keys', action: () => setTab('keys') },
                                { icon: <Layers size={18} color="#a78bfa" />, title: 'Browse Endpoints', desc: 'Explore available REST and WebSocket APIs', action: () => setTab('endpoints') },
                                { icon: <Zap size={18} color="#f59e0b" />, title: 'Quick Start Guide', desc: 'Get up and running in under 5 minutes', action: () => setTab('quickstart') },
                                { icon: <BookOpen size={18} color="#10b981" />, title: 'Full API Reference', desc: 'Complete interactive documentation', action: () => window.location.href = '/docs' },
                            ].map(item => (
                                <button key={item.title} onClick={item.action} style={{ display: 'flex', alignItems: 'flex-start', gap: 14, backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '18px 20px', cursor: 'pointer', textAlign: 'left', width: '100%', transition: 'border-color 0.2s' }}
                                    onMouseEnter={e => e.currentTarget.style.borderColor = '#334155'}
                                    onMouseLeave={e => e.currentTarget.style.borderColor = '#1e293b'}>
                                    <div style={{ marginTop: 2 }}>{item.icon}</div>
                                    <div>
                                        <div style={{ color: '#f8fafc', fontWeight: 600, fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 }}>{item.title} <ChevronRight size={14} color="#475569" /></div>
                                        <div style={{ color: '#64748b', fontSize: 13, marginTop: 3 }}>{item.desc}</div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {/* ══════════ KEYS TAB ══════════ */}
                {tab === 'keys' && (
                    <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
                            <div>
                                <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Secret API Keys</h2>
                                <p style={{ margin: '4px 0 0 0', color: '#64748b', fontSize: 14 }}>Your keys carry privileges — do not share them in public repositories or client-side code.</p>
                            </div>
                            <button onClick={handleGenerate} style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'linear-gradient(90deg,#3b82f6,#06b6d4)', color: 'white', border: 'none', padding: '8px 18px', borderRadius: 6, cursor: 'pointer', fontWeight: 600, fontSize: 14 }}>
                                <Plus size={16} /> Create Key
                            </button>
                        </div>

                        {/* Security notice */}
                        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', backgroundColor: '#1c1407', border: '1px solid #b45309', borderRadius: 8, padding: '14px 18px', marginBottom: 24 }}>
                            <Lock size={18} color="#f59e0b" style={{ flexShrink: 0, marginTop: 1 }} />
                            <p style={{ margin: 0, fontSize: 14, color: '#fcd34d', lineHeight: 1.6 }}>
                                We only display key prefixes after creation. The full secret is shown <strong>exactly once</strong> and then cryptographically hashed — we cannot recover it. Store it in an environment variable, never in source code.
                            </p>
                        </div>

                        <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, overflow: 'hidden' }}>
                            {loading ? (
                                <div style={{ padding: 40, textAlign: 'center', color: '#64748b', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
                                    <RefreshCw size={18} style={{ animation: 'spin 1s linear infinite' }} /> Loading keys…
                                </div>
                            ) : keys.length === 0 ? (
                                <div style={{ padding: '60px 40px', textAlign: 'center', color: '#64748b' }}>
                                    <Key size={40} style={{ marginBottom: 12, opacity: 0.4 }} />
                                    <p style={{ margin: 0, fontSize: 16, fontWeight: 500 }}>No API keys yet</p>
                                    <p style={{ margin: '6px 0 20px', fontSize: 14 }}>Create your first key to start querying the Truth Graph.</p>
                                    <button onClick={handleGenerate} style={{ background: 'linear-gradient(90deg,#3b82f6,#06b6d4)', color: 'white', border: 'none', padding: '10px 22px', borderRadius: 6, cursor: 'pointer', fontWeight: 600 }}>Create Secret Key</button>
                                </div>
                            ) : (
                                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                    <thead>
                                        <tr style={{ backgroundColor: '#0a0f1e' }}>
                                            {['Key Prefix', 'Tier', 'Created', 'Status', ''].map(h => (
                                                <th key={h} style={{ textAlign: 'left', padding: '12px 20px', color: '#475569', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5 }}>{h}</th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {keys.map(k => {
                                            const t = TIERS[k.tier] || TIERS.basic;
                                            return (
                                                <tr key={k.id} style={{ borderTop: '1px solid #1e293b' }}>
                                                    <td style={{ padding: '16px 20px', fontFamily: 'monospace', fontSize: 14, color: '#94a3b8' }}>
                                                        {k.prefix}<span style={{ color: '#334155' }}>{'•'.repeat(20)}</span>
                                                    </td>
                                                    <td style={{ padding: '16px 20px' }}>
                                                        <span style={{ backgroundColor: t.bg, color: t.color, padding: '3px 10px', borderRadius: 20, fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5 }}>{t.label}</span>
                                                    </td>
                                                    <td style={{ padding: '16px 20px', color: '#64748b', fontSize: 13 }}>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                                            <Clock size={13} /> {new Date(k.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                                                        </div>
                                                    </td>
                                                    <td style={{ padding: '16px 20px' }}>
                                                        <span style={{ display: 'flex', alignItems: 'center', gap: 5, color: '#10b981', fontSize: 13 }}>
                                                            <span style={{ width: 7, height: 7, borderRadius: '50%', backgroundColor: '#10b981', display: 'inline-block' }} /> Active
                                                        </span>
                                                    </td>
                                                    <td style={{ padding: '16px 20px', textAlign: 'right' }}>
                                                        <button onClick={() => handleRevoke(k.id)} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, backgroundColor: 'transparent', color: '#ef4444', border: '1px solid #ef444444', padding: '6px 14px', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }}
                                                            onMouseEnter={e => { e.currentTarget.style.backgroundColor = '#ef444420'; }}
                                                            onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; }}>
                                                            <Trash2 size={14} /> Revoke
                                                        </button>
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            )}
                        </div>
                    </div>
                )}

                {/* ══════════ ENDPOINTS TAB ══════════ */}
                {tab === 'endpoints' && (
                    <div>
                        <h2 style={{ margin: '0 0 6px 0', fontSize: 20, fontWeight: 600 }}>API Endpoints</h2>
                        <p style={{ color: '#64748b', fontSize: 14, margin: '0 0 28px 0' }}>All endpoints require a valid <code style={{ backgroundColor: '#1e293b', padding: '2px 6px', borderRadius: 4 }}>Authorization: Bearer &lt;key&gt;</code> header.</p>

                        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                            {ENDPOINTS.map((ep, i) => {
                                const isOpen = expandedEndpoint === i;
                                const t = TIERS[ep.tier];
                                return (
                                    <div key={i} style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, overflow: 'hidden' }}>
                                        <button onClick={() => setExpandedEndpoint(isOpen ? null : i)} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 14, padding: '16px 20px', background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left' }}>
                                            <span style={{ backgroundColor: ep.badge, color: 'white', padding: '3px 10px', borderRadius: 4, fontSize: 12, fontWeight: 700, fontFamily: 'monospace', minWidth: 42, textAlign: 'center' }}>{ep.method}</span>
                                            <code style={{ color: '#e2e8f0', fontSize: 14, flex: 1, fontFamily: 'monospace' }}>{ep.path}</code>
                                            <span style={{ backgroundColor: t.bg, color: t.color, padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 700, textTransform: 'uppercase' }}>{t.label}+</span>
                                            <ChevronRight size={16} color="#64748b" style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: '0.2s' }} />
                                        </button>
                                        {isOpen && (
                                            <div style={{ padding: '0 20px 20px 20px', borderTop: '1px solid #1e293b' }}>
                                                <p style={{ color: '#94a3b8', fontSize: 14, margin: '16px 0 12px' }}>{ep.desc}</p>
                                                {ep.params.length > 0 && (
                                                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                                                        <thead>
                                                            <tr>
                                                                {['Parameter', 'Type', 'Description'].map(h => (
                                                                    <th key={h} style={{ textAlign: 'left', padding: '8px 12px', color: '#475569', backgroundColor: '#0a0f1e', fontWeight: 600 }}>{h}</th>
                                                                ))}
                                                            </tr>
                                                        </thead>
                                                        <tbody>
                                                            {ep.params.map(p => (
                                                                <tr key={p.name} style={{ borderTop: '1px solid #1e293b' }}>
                                                                    <td style={{ padding: '10px 12px', fontFamily: 'monospace', color: '#38bdf8' }}>{p.name}</td>
                                                                    <td style={{ padding: '10px 12px', color: '#a78bfa' }}>{p.type}</td>
                                                                    <td style={{ padding: '10px 12px', color: '#94a3b8' }}>{p.desc}</td>
                                                                </tr>
                                                            ))}
                                                        </tbody>
                                                    </table>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* ══════════ QUICK START TAB ══════════ */}
                {tab === 'quickstart' && (
                    <div style={{ maxWidth: 780 }}>
                        <h2 style={{ margin: '0 0 6px 0', fontSize: 20, fontWeight: 600 }}>Quick Start Guide</h2>
                        <p style={{ color: '#64748b', fontSize: 14, margin: '0 0 32px 0' }}>Make your first API call in under 2 minutes.</p>

                        {/* Step 1 */}
                        <div style={{ display: 'flex', gap: 16, marginBottom: 28 }}>
                            <div style={{ width: 32, height: 32, borderRadius: '50%', background: 'linear-gradient(135deg,#3b82f6,#06b6d4)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14, flexShrink: 0, color: 'white' }}>1</div>
                            <div style={{ flex: 1 }}>
                                <h3 style={{ margin: '4px 0 8px', fontSize: 16 }}>Create an API Key</h3>
                                <p style={{ color: '#94a3b8', fontSize: 14, margin: '0 0 12px' }}>Go to the <button onClick={() => setTab('keys')} style={{ background: 'none', border: 'none', color: '#38bdf8', cursor: 'pointer', fontSize: 14, padding: 0, fontWeight: 600 }}>API Keys tab</button> and click <strong>"Create New Key"</strong>. Save it immediately — it won't be shown again.</p>
                            </div>
                        </div>

                        {/* Step 2 — code examples with language tabs */}
                        <div style={{ display: 'flex', gap: 16, marginBottom: 28 }}>
                            <div style={{ width: 32, height: 32, borderRadius: '50%', background: 'linear-gradient(135deg,#3b82f6,#06b6d4)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14, flexShrink: 0, color: 'white' }}>2</div>
                            <div style={{ flex: 1 }}>
                                <h3 style={{ margin: '4px 0 8px', fontSize: 16 }}>Make your first request</h3>
                                <p style={{ color: '#94a3b8', fontSize: 14, margin: '0 0 12px' }}>Replace <code style={{ backgroundColor: '#1e293b', padding: '2px 6px', borderRadius: 4 }}>sk_live_...</code> with your actual key.</p>

                                <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                                    {['curl', 'node', 'python', 'websocket'].map(lang => (
                                        <button key={lang} onClick={() => setCodeTab(lang)} style={{
                                            padding: '5px 14px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 500,
                                            backgroundColor: codeTab === lang ? '#1e40af' : '#1e293b',
                                            color: codeTab === lang ? '#93c5fd' : '#64748b'
                                        }}>{lang}</button>
                                    ))}
                                </div>
                                <CodeBlock code={CODE_EXAMPLES[codeTab](previewKey)} lang={codeTab} />
                            </div>
                        </div>

                        {/* Step 3 */}
                        <div style={{ display: 'flex', gap: 16, marginBottom: 28 }}>
                            <div style={{ width: 32, height: 32, borderRadius: '50%', background: 'linear-gradient(135deg,#3b82f6,#06b6d4)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14, flexShrink: 0, color: 'white' }}>3</div>
                            <div style={{ flex: 1 }}>
                                <h3 style={{ margin: '4px 0 8px', fontSize: 16 }}>Parse the response</h3>
                                <CodeBlock lang="json" code={`{
  "data": [
    {
      "id": 8421,
      "subject": "Tesla",
      "predicate": "ACQUIRED",
      "object_entity": "StartupX",
      "epistemic_score": 0.94,
      "quote_context": "Tesla has finalized the acquisition of...",
      "status": "GRAPH_COMMITTED"
    }
  ],
  "meta": { "limit": 10, "offset": 0 }
}`} />
                                <p style={{ color: '#94a3b8', fontSize: 13, marginTop: 8 }}>
                                    The <code style={{ backgroundColor: '#1e293b', padding: '1px 5px', borderRadius: 4 }}>epistemic_score</code> (0–1.0) represents the Truth Engine's confidence. Scores above 0.85 are considered high-trust signals.
                                </p>
                            </div>
                        </div>

                        {/* Rate limits table */}
                        <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: 24, marginTop: 16 }}>
                            <h3 style={{ margin: '0 0 16px', fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}><Activity size={16} color="#a78bfa" /> Rate Limits by Tier</h3>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                                <thead>
                                    <tr>
                                        {['Tier', 'Req/Min', 'Firehose', 'Bulk Export'].map(h => (
                                            <th key={h} style={{ textAlign: 'left', padding: '8px 12px', color: '#64748b', fontWeight: 600, borderBottom: '1px solid #1e293b' }}>{h}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {Object.entries(TIERS).map(([key, t]) => (
                                        <tr key={key} style={{ borderBottom: '1px solid #0f172a' }}>
                                            <td style={{ padding: '12px', }}><span style={{ backgroundColor: t.bg, color: t.color, padding: '2px 10px', borderRadius: 12, fontSize: 12, fontWeight: 700, textTransform: 'uppercase' }}>{t.label}</span></td>
                                            <td style={{ padding: '12px', color: '#cbd5e1' }}>{t.rpm}</td>
                                            <td style={{ padding: '12px' }}>{key === 'enterprise' ? <CheckCircle2 size={16} color="#10b981" /> : <span style={{ color: '#ef4444', fontSize: 18, lineHeight: 1 }}>✕</span>}</td>
                                            <td style={{ padding: '12px' }}>{key !== 'basic' ? <CheckCircle2 size={16} color="#10b981" /> : <span style={{ color: '#ef4444', fontSize: 18, lineHeight: 1 }}>✕</span>}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>

            {/* ══ Raw Key Modal ══ */}
            {rawKeyModal && (
                <div style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.85)', display: 'flex', justifyContent: 'center', alignItems: 'center', zIndex: 100, backdropFilter: 'blur(4px)' }}>
                    <div style={{ backgroundColor: '#0f172a', padding: 36, borderRadius: 16, border: '1px solid #334155', maxWidth: 520, width: '90%', boxShadow: '0 25px 50px rgba(0,0,0,0.6)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                            <div style={{ backgroundColor: '#451a03', padding: 10, borderRadius: 8 }}><AlertTriangle size={22} color="#f59e0b" /></div>
                            <div>
                                <h3 style={{ margin: 0, fontSize: 18 }}>Save your Secret Key</h3>
                                <p style={{ margin: '2px 0 0', color: '#64748b', fontSize: 13 }}>This key will not be shown again</p>
                            </div>
                        </div>
                        <p style={{ color: '#94a3b8', fontSize: 14, lineHeight: 1.6, margin: '0 0 20px' }}>
                            Copy your API key now and store it in a secure location such as an environment variable. For security, we store only a cryptographic hash and <strong style={{ color: '#f8fafc' }}>cannot recover the raw key</strong>.
                        </p>
                        <div style={{ backgroundColor: '#020617', padding: '14px 16px', borderRadius: 8, fontFamily: 'monospace', fontSize: 13, color: '#10b981', display: 'flex', justifyContent: 'space-between', alignItems: 'center', border: '1px solid #1e293b', marginBottom: 20, gap: 12 }}>
                            <span style={{ wordBreak: 'break-all', lineHeight: 1.5 }}>{rawKeyModal}</span>
                            <button onClick={handleCopy} style={{ background: 'none', border: '1px solid #1e293b', color: copied ? '#10b981' : '#94a3b8', cursor: 'pointer', padding: '6px 10px', borderRadius: 6, flexShrink: 0 }}>
                                {copied ? <CheckCircle2 size={18} /> : <Copy size={18} />}
                            </button>
                        </div>
                        <div style={{ backgroundColor: '#1c1407', border: '1px solid #b4530966', borderRadius: 8, padding: '10px 14px', marginBottom: 24, fontSize: 13, color: '#fcd34d' }}>
                            ⚠️ Never commit this key to version control or paste it into client-side code.
                        </div>
                        <button onClick={() => setRawKeyModal(null)} style={{ width: '100%', padding: 13, background: 'linear-gradient(90deg,#3b82f6,#06b6d4)', color: 'white', border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 700, fontSize: 15 }}>
                            I've saved my key securely
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
