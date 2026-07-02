import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
    Key, Plus, Trash2, Copy, CheckCircle2, AlertTriangle, ShieldCheck,
    Zap, Globe, Code2, Terminal, BookOpen, ChevronRight, Activity,
    Clock, Lock, RefreshCw, ExternalLink, BarChart3, Layers, Wifi, Settings,
    Upload, Link, FileText, File, Database, X, Loader2, CheckCircle, XCircle, ChevronDown
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
    const [tab, setTab] = useState('overview');        // overview | keys | endpoints | quickstart | federation
    const [codeTab, setCodeTab] = useState('curl');
    const [expandedEndpoint, setExpandedEndpoint] = useState(null);

    const [settings, setSettings] = useState({ EXTERNAL_B2B_API_URL: '', EXTERNAL_B2B_API_KEY: '' });
    const [savingSettings, setSavingSettings] = useState(false);

    // ── License State ─────────────────────────────────────────────────────────
    const [licenseCredits, setLicenseCredits] = useState(null);
    const [licenseToken, setLicenseToken] = useState('');
    const [applyingLicense, setApplyingLicense] = useState(false);

    // ── Ingest Tab State ──────────────────────────────────────────────────────
    const [ingestMode, setIngestMode] = useState('url'); // 'url' | 'text' | 'document'
    const [ingestStatus, setIngestStatus] = useState(null); // { type: 'success'|'error', message }
    const [ingestLoading, setIngestLoading] = useState(false);

    // URL form
    const [ingestUrl, setIngestUrl] = useState('');
    const [ingestUrlLabel, setIngestUrlLabel] = useState('');
    const [ingestUrlPriority, setIngestUrlPriority] = useState('normal');

    // Text memo form
    const [ingestTitle, setIngestTitle] = useState('');
    const [ingestText, setIngestText] = useState('');
    const [ingestSourceLabel, setIngestSourceLabel] = useState('');
    const [ingestClassification, setIngestClassification] = useState('INTERNAL');

    // Document upload
    const [dragOver, setDragOver] = useState(false);
    const [uploadedFiles, setUploadedFiles] = useState([]);
    const [uploadResults, setUploadResults] = useState([]);
    const fileInputRef = useRef(null);

    // Queue viewer
    const [queueData, setQueueData] = useState(null);
    const [queueLoading, setQueueLoading] = useState(false);
    const [showQueue, setShowQueue] = useState(false);


    useEffect(() => { 
        fetchKeys(); 
        fetchSettings();
        fetchLicense();
    }, []);

    const fetchLicense = async () => {
        try {
            const data = await api.getLicenseStatus();
            setLicenseCredits(data.total_credits);
        } catch (e) {
            console.error('Failed to fetch license', e);
        }
    };

    const handleApplyLicense = async () => {
        if (!licenseToken.trim()) return alert('Please enter a license JWT');
        setApplyingLicense(true);
        try {
            const result = await api.applyLicense(licenseToken.trim());
            alert(`License applied! Added ${result.added} credits.`);
            setLicenseToken('');
            fetchLicense();
        } catch (e) {
            alert(e.message);
        } finally {
            setApplyingLicense(false);
        }
    };

    const fetchSettings = async () => {
        try {
            const data = await api.getSettings();
            setSettings({
                EXTERNAL_B2B_API_URL: data.EXTERNAL_B2B_API_URL || '',
                EXTERNAL_B2B_API_KEY: data.EXTERNAL_B2B_API_KEY || ''
            });
        } catch (e) { console.error('Failed to fetch settings', e); }
    };

    const handleSaveSettings = async () => {
        setSavingSettings(true);
        try {
            await api.updateSettings(settings);
            alert('Settings saved successfully!');
        } catch (e) {
            alert('Failed to save settings.');
        } finally {
            setSavingSettings(false);
        }
    };

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

    // ── Ingest Handlers ───────────────────────────────────────────────────────
    const showIngestFeedback = (type, message) => {
        setIngestStatus({ type, message });
        setTimeout(() => setIngestStatus(null), 6000);
    };

    const handleIngestUrl = async () => {
        if (!ingestUrl.trim()) return showIngestFeedback('error', 'Please enter a URL.');
        setIngestLoading(true);
        try {
            const result = await api.ingestUrl(ingestUrl.trim(), ingestUrlLabel, ingestUrlPriority);
            showIngestFeedback('success', result.message);
            setIngestUrl('');
            setIngestUrlLabel('');
        } catch (e) {
            showIngestFeedback('error', e.message);
        } finally { setIngestLoading(false); }
    };

    const handleIngestText = async () => {
        if (!ingestTitle.trim()) return showIngestFeedback('error', 'Please enter a title.');
        if (!ingestText.trim() || ingestText.trim().length < 50) return showIngestFeedback('error', 'Text must be at least 50 characters.');
        setIngestLoading(true);
        try {
            const result = await api.ingestText(ingestTitle.trim(), ingestText.trim(), ingestSourceLabel, ingestClassification);
            showIngestFeedback('success', result.message);
            setIngestTitle('');
            setIngestText('');
            setIngestSourceLabel('');
        } catch (e) {
            showIngestFeedback('error', e.message);
        } finally { setIngestLoading(false); }
    };

    const handleIngestDocuments = async () => {
        if (!uploadedFiles.length) return showIngestFeedback('error', 'Please select at least one file.');
        setIngestLoading(true);
        setUploadResults([]);
        try {
            const result = await api.ingestDocuments(uploadedFiles);
            setUploadResults(result.results || []);
            showIngestFeedback(result.success ? 'success' : 'error', result.summary);
            if (result.success) setUploadedFiles([]);
        } catch (e) {
            showIngestFeedback('error', e.message);
        } finally { setIngestLoading(false); }
    };

    const handleDrop = (e) => {
        e.preventDefault();
        setDragOver(false);
        const files = Array.from(e.dataTransfer.files);
        setUploadedFiles(prev => {
            const existing = new Set(prev.map(f => f.name));
            return [...prev, ...files.filter(f => !existing.has(f.name))];
        });
    };

    const handleFileSelect = (e) => {
        const files = Array.from(e.target.files);
        setUploadedFiles(prev => {
            const existing = new Set(prev.map(f => f.name));
            return [...prev, ...files.filter(f => !existing.has(f.name))];
        });
    };

    const fetchQueue = async () => {
        setQueueLoading(true);
        try {
            const data = await api.getIngestQueue();
            setQueueData(data);
        } catch (e) {
            showIngestFeedback('error', 'Failed to fetch queue: ' + e.message);
        } finally { setQueueLoading(false); }
    };

    const handleDeleteQueueItem = async (type, id) => {
        if (!window.confirm(`Remove this ${type} from the queue?`)) return;
        try {
            await api.deleteIngestItem(type, id);
            fetchQueue();
        } catch (e) {
            showIngestFeedback('error', e.message);
        }
    };

    const formatBytes = (bytes) => {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    const topTier = keys.length ? (keys.find(k => k.tier === 'enterprise') ? 'enterprise' : keys.find(k => k.tier === 'pro') ? 'pro' : 'basic') : 'basic';
    const tierInfo = TIERS[topTier];
    const previewKey = rawKeyModal || (keys[0] ? keys[0].prefix + '••••••••••••••' : 'sk_live_your_key_here');


    const tabs = [
        { id: 'overview',   icon: <BarChart3 size={15} />,  label: 'Overview' },
        { id: 'keys',       icon: <Key size={15} />,         label: 'API Keys' },
        { id: 'license',    icon: <ShieldCheck size={15} />, label: 'License & Credits' },
        { id: 'endpoints',  icon: <Layers size={15} />,      label: 'Endpoints' },
        { id: 'quickstart', icon: <Zap size={15} />,         label: 'Quick Start' },
        { id: 'federation', icon: <Settings size={15} />,    label: 'Federation' },
        { id: 'ingest',     icon: <Upload size={15} />,      label: 'Internal Ingest' },
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

                        {/* Webhook Alert Tip */}
                        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', backgroundColor: '#0f172a', border: '1px solid #38bdf844', borderRadius: 12, padding: '16px 20px', marginBottom: 24 }}>
                            <AlertTriangle size={20} color="#38bdf8" style={{ flexShrink: 0, marginTop: 2 }} />
                            <div>
                                <h3 style={{ margin: '0 0 6px 0', fontSize: 14, color: '#e0f2fe', fontWeight: 600 }}>Threat Priority Webhooks</h3>
                                <p style={{ margin: 0, fontSize: 13, color: '#94a3b8', lineHeight: 1.6 }}>
                                    To enable the external webhook for zero-day threat alerts, simply add <code style={{ backgroundColor: '#1e293b', padding: '2px 6px', borderRadius: 4, color: '#38bdf8', fontFamily: 'monospace' }}>THREAT_ALERT_WEBHOOK=https://your-slack-or-teams-url</code> to your <code style={{ backgroundColor: '#1e293b', padding: '2px 6px', borderRadius: 4, color: '#94a3b8', fontFamily: 'monospace' }}>ai_engine/.env</code> file.
                                </p>
                            </div>
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
                                { icon: <Settings size={18} color="#f59e0b" />, title: 'Federation Settings', desc: 'Connect to external master graph', action: () => setTab('federation') },
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

                {/* ══════════ LICENSE TAB ══════════ */}
                {tab === 'license' && (
                    <div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
                            <div>
                                <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Commercial License & Credits</h2>
                                <p style={{ margin: '4px 0 0 0', color: '#64748b', fontSize: 14 }}>Apply signed JWT keys for offline Air-Gapped credit top-ups.</p>
                            </div>
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 24 }}>
                            {/* Credits Display */}
                            <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, padding: '32px 24px', textAlign: 'center' }}>
                                <ShieldCheck size={48} color="#38bdf8" style={{ margin: '0 auto 16px auto' }} />
                                <h3 style={{ fontSize: 14, color: '#94a3b8', margin: '0 0 8px 0', textTransform: 'uppercase', letterSpacing: 1 }}>Remaining Compute Credits</h3>
                                <div style={{ fontSize: 48, fontWeight: 800, color: '#f8fafc', marginBottom: 8 }}>
                                    {licenseCredits !== null ? licenseCredits.toLocaleString() : '...'}
                                </div>
                                <p style={{ color: '#64748b', fontSize: 13, margin: 0 }}>Credits are consumed by the OSINT orchestrator during investigations.</p>
                            </div>

                            {/* Apply License Form */}
                            <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, padding: 24 }}>
                                <h3 style={{ fontSize: 16, fontWeight: 600, color: '#f8fafc', margin: '0 0 16px 0' }}>Apply Offline License Token</h3>
                                <p style={{ color: '#94a3b8', fontSize: 13, marginBottom: 20 }}>
                                    Paste your securely signed JWT below. The token will be cryptographically verified using the appliance's public key before credits are added.
                                </p>
                                <textarea
                                    value={licenseToken}
                                    onChange={e => setLicenseToken(e.target.value)}
                                    placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                                    style={{ width: '100%', height: 120, padding: 12, backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: 8, color: '#cbd5e1', fontFamily: 'monospace', fontSize: 13, resize: 'vertical', marginBottom: 16 }}
                                />
                                <button
                                    onClick={handleApplyLicense}
                                    disabled={applyingLicense || !licenseToken.trim()}
                                    style={{ width: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 8, padding: '12px', background: applyingLicense ? '#334155' : 'linear-gradient(90deg,#10b981,#059669)', color: 'white', border: 'none', borderRadius: 8, cursor: applyingLicense ? 'not-allowed' : 'pointer', fontWeight: 600, fontSize: 14 }}
                                >
                                    {applyingLicense ? <Loader2 size={16} className="spin" /> : <Plus size={16} />}
                                    {applyingLicense ? 'Verifying Cryptography...' : 'Apply License'}
                                </button>
                            </div>
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

                {/* ══════════ FEDERATION SETTINGS TAB ══════════ */}
                {tab === 'federation' && (
                    <div style={{ maxWidth: 780 }}>
                        <h2 style={{ margin: '0 0 6px 0', fontSize: 20, fontWeight: 600 }}>Federation Settings</h2>
                        <p style={{ color: '#64748b', fontSize: 14, margin: '0 0 32px 0' }}>Configure connection to an external Master Truth Graph API to enable Hybrid Federated Architecture queries.</p>

                        <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: 24 }}>
                            <div style={{ marginBottom: 20 }}>
                                <label style={{ display: 'block', marginBottom: 8, fontSize: 14, fontWeight: 500, color: '#cbd5e1' }}>External Master Graph API URL</label>
                                <input 
                                    type="text" 
                                    value={settings.EXTERNAL_B2B_API_URL}
                                    onChange={(e) => setSettings(s => ({...s, EXTERNAL_B2B_API_URL: e.target.value}))}
                                    placeholder="e.g. https://api.truthgraph.com/v1"
                                    style={{ width: '100%', padding: '10px 14px', borderRadius: 6, border: '1px solid #334155', backgroundColor: '#020617', color: '#f8fafc', fontSize: 14 }}
                                />
                                <p style={{ margin: '6px 0 0', fontSize: 12, color: '#64748b' }}>The base URL of the remote Truth Graph B2B API.</p>
                            </div>

                            <div style={{ marginBottom: 24 }}>
                                <label style={{ display: 'block', marginBottom: 8, fontSize: 14, fontWeight: 500, color: '#cbd5e1' }}>External API Key (sk_live_...)</label>
                                <input 
                                    type="password" 
                                    value={settings.EXTERNAL_B2B_API_KEY}
                                    onChange={(e) => setSettings(s => ({...s, EXTERNAL_B2B_API_KEY: e.target.value}))}
                                    placeholder="sk_live_..."
                                    style={{ width: '100%', padding: '10px 14px', borderRadius: 6, border: '1px solid #334155', backgroundColor: '#020617', color: '#f8fafc', fontSize: 14 }}
                                />
                                <p style={{ margin: '6px 0 0', fontSize: 12, color: '#64748b' }}>Your secret API key for the remote Truth Graph.</p>
                            </div>

                            <button 
                                onClick={handleSaveSettings}
                                disabled={savingSettings}
                                style={{
                                    background: 'linear-gradient(90deg,#3b82f6,#06b6d4)', color: 'white', border: 'none', 
                                    padding: '10px 20px', borderRadius: 6, cursor: 'pointer', fontWeight: 600, fontSize: 14,
                                    opacity: savingSettings ? 0.7 : 1
                                }}
                            >
                                {savingSettings ? 'Saving...' : 'Save Federation Settings'}
                            </button>
                        </div>
                    </div>
                )}

                {/* ══════════ INGEST TAB ══════════ */}
                {tab === 'ingest' && (() => {
                    const inputStyle = {
                        width: '100%', padding: '10px 14px', backgroundColor: '#020617',
                        border: '1px solid #334155', borderRadius: 8, color: '#f8fafc',
                        fontSize: 14, outline: 'none', boxSizing: 'border-box',
                        fontFamily: "'Inter', system-ui, sans-serif",
                    };
                    const labelStyle = { fontSize: 13, color: '#94a3b8', marginBottom: 6, display: 'block', fontWeight: 500 };
                    const sectionStyle = { backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 12, padding: 28, marginBottom: 24 };

                    const modeButtons = [
                        { id: 'url',      icon: <Link size={16} />,     label: 'Submit URL' },
                        { id: 'text',     icon: <FileText size={16} />, label: 'Inject Memo' },
                        { id: 'document', icon: <File size={16} />,     label: 'Upload Files' },
                    ];

                    const FILE_ICONS = { pdf: '📄', txt: '📝', csv: '📊', doc: '📋', docx: '📋', md: '📝', json: '🔧' };
                    const getFileIcon = (name) => FILE_ICONS[name.split('.').pop()?.toLowerCase()] || '📎';
                    const CLASSIFICATIONS = ['INTERNAL', 'CONFIDENTIAL', 'SECRET', 'TOP_SECRET', 'RESTRICTED'];

                    return (
                        <div>
                            {/* Header */}
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 28 }}>
                                <div>
                                    <h2 style={{ margin: '0 0 6px 0', fontSize: 20, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 10 }}>
                                        <div style={{ background: 'linear-gradient(135deg,#7c3aed,#a855f7)', padding: 8, borderRadius: 8 }}><Upload size={18} color="white" /></div>
                                        Internal Data Ingestion
                                    </h2>
                                    <p style={{ margin: 0, fontSize: 13, color: '#64748b' }}>
                                        Securely inject private internal data — documents, memos, and URLs — into your local Truth Graph. Data stays within your VPC.
                                    </p>
                                </div>
                                <button
                                    onClick={() => { setShowQueue(!showQueue); if (!showQueue) fetchQueue(); }}
                                    style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 16px', background: showQueue ? '#7c3aed22' : 'transparent', border: '1px solid #334155', borderRadius: 8, color: '#94a3b8', cursor: 'pointer', fontSize: 13 }}
                                >
                                    <Database size={15} /> {showQueue ? 'Hide Queue' : 'View Queue'}
                                </button>
                            </div>

                            {/* Global Status Feedback */}
                            {ingestStatus && (
                                <div style={{
                                    display: 'flex', alignItems: 'flex-start', gap: 12, padding: '14px 18px', borderRadius: 10, marginBottom: 20,
                                    backgroundColor: ingestStatus.type === 'success' ? '#052e1688' : '#2d041488',
                                    border: `1px solid ${ingestStatus.type === 'success' ? '#10b98166' : '#ef444466'}`,
                                }}>
                                    {ingestStatus.type === 'success' ? <CheckCircle size={18} color="#10b981" style={{ flexShrink: 0 }} /> : <XCircle size={18} color="#ef4444" style={{ flexShrink: 0 }} />}
                                    <span style={{ fontSize: 14, color: ingestStatus.type === 'success' ? '#6ee7b7' : '#fca5a5', lineHeight: 1.5 }}>{ingestStatus.message}</span>
                                </div>
                            )}

                            {/* Queue Viewer */}
                            {showQueue && (
                                <div style={{ ...sectionStyle, borderColor: '#7c3aed44' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                                        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}><Database size={16} color="#a78bfa" /> Ingestion Queue</h3>
                                        <button onClick={fetchQueue} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: 'transparent', border: '1px solid #334155', borderRadius: 6, color: '#94a3b8', cursor: 'pointer', fontSize: 12 }}>
                                            <RefreshCw size={12} /> Refresh
                                        </button>
                                    </div>
                                    {queueLoading && <div style={{ color: '#64748b', fontSize: 13, textAlign: 'center', padding: 20 }}>Loading queue...</div>}
                                    {queueData && !queueLoading && (
                                        <div>
                                            {/* Pending URL scrapes */}
                                            {queueData.pending_scrape?.length > 0 && (
                                                <div style={{ marginBottom: 16 }}>
                                                    <div style={{ fontSize: 12, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>Awaiting Scrape ({queueData.pending_scrape.length})</div>
                                                    {queueData.pending_scrape.map(item => (
                                                        <div key={item.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', backgroundColor: '#020617', borderRadius: 8, marginBottom: 6, border: '1px solid #1e293b' }}>
                                                            <div>
                                                                <div style={{ fontSize: 13, color: '#f8fafc', fontFamily: 'monospace', wordBreak: 'break-all' }}>{item.url}</div>
                                                                <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{new Date(item.ingested_at).toLocaleString()}</div>
                                                            </div>
                                                            <button onClick={() => handleDeleteQueueItem('url', item.id)} style={{ background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer', padding: 6, flexShrink: 0 }}><X size={14} /></button>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                            {/* Articles in pipeline */}
                                            {queueData.articles?.items?.length > 0 && (
                                                <div>
                                                    <div style={{ fontSize: 12, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>Articles in Pipeline ({queueData.articles.total})</div>
                                                    {queueData.articles.items.map(item => (
                                                        <div key={item.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', backgroundColor: '#020617', borderRadius: 8, marginBottom: 6, border: '1px solid #1e293b' }}>
                                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                                <div style={{ fontSize: 13, color: '#f8fafc', marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</div>
                                                                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                                                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, backgroundColor: item.status === 'GRAPH_COMMITTED' ? '#052e16' : item.status === 'PENDING_EXTRACTION' ? '#0c2d48' : '#1c1407', color: item.status === 'GRAPH_COMMITTED' ? '#6ee7b7' : item.status === 'PENDING_EXTRACTION' ? '#7dd3fc' : '#fcd34d', fontWeight: 600 }}>{item.status}</span>
                                                                    <span style={{ fontSize: 11, color: '#64748b' }}>{new Date(item.scraped_at).toLocaleString()}</span>
                                                                </div>
                                                            </div>
                                                            {['PENDING_EXTRACTION', 'PENDING_CLASSIFICATION'].includes(item.status) && (
                                                                <button onClick={() => handleDeleteQueueItem('article', item.id)} style={{ background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer', padding: 6, flexShrink: 0 }}><X size={14} /></button>
                                                            )}
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                            {!queueData.pending_scrape?.length && !queueData.articles?.items?.length && (
                                                <div style={{ color: '#64748b', fontSize: 13, textAlign: 'center', padding: 16 }}>Queue is empty.</div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Mode Selector */}
                            <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
                                {modeButtons.map(m => (
                                    <button key={m.id} onClick={() => { setIngestMode(m.id); setIngestStatus(null); setUploadResults([]); }} style={{
                                        display: 'flex', alignItems: 'center', gap: 8, padding: '10px 20px', borderRadius: 8, border: '1px solid',
                                        borderColor: ingestMode === m.id ? '#7c3aed' : '#334155',
                                        backgroundColor: ingestMode === m.id ? '#7c3aed22' : 'transparent',
                                        color: ingestMode === m.id ? '#c4b5fd' : '#64748b',
                                        cursor: 'pointer', fontSize: 14, fontWeight: 500, transition: 'all 0.15s',
                                    }}>{m.icon}{m.label}</button>
                                ))}
                            </div>

                            {/* ── URL Mode ── */}
                            {ingestMode === 'url' && (
                                <div style={sectionStyle}>
                                    <h3 style={{ margin: '0 0 6px', fontSize: 15, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}><Link size={16} color="#38bdf8" /> Submit Internal URL</h3>
                                    <p style={{ margin: '0 0 24px', fontSize: 13, color: '#64748b' }}>Queue a private intranet page, internal web app, or secure portal URL for AI fact extraction.</p>
                                    <div style={{ display: 'grid', gap: 16 }}>
                                        <div>
                                            <label style={labelStyle}>URL <span style={{ color: '#ef4444' }}>*</span></label>
                                            <input style={inputStyle} placeholder="https://intranet.company.com/report/q4-2024" value={ingestUrl} onChange={e => setIngestUrl(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleIngestUrl()} />
                                        </div>
                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                            <div>
                                                <label style={labelStyle}>Label (optional)</label>
                                                <input style={inputStyle} placeholder="Q4 2024 Board Report" value={ingestUrlLabel} onChange={e => setIngestUrlLabel(e.target.value)} />
                                            </div>
                                            <div>
                                                <label style={labelStyle}>Priority</label>
                                                <select style={{ ...inputStyle, cursor: 'pointer' }} value={ingestUrlPriority} onChange={e => setIngestUrlPriority(e.target.value)}>
                                                    <option value="normal">Normal</option>
                                                    <option value="high">High — Process Next</option>
                                                </select>
                                            </div>
                                        </div>
                                        <div style={{ backgroundColor: '#0c1a2e', border: '1px solid #1e3a5f', borderRadius: 8, padding: '12px 16px', fontSize: 13, color: '#7dd3fc' }}>
                                            ℹ️ The system's scraper will crawl this URL and extract all text. Make sure the server running the AI pipeline has network access to this URL.
                                        </div>
                                        <button onClick={handleIngestUrl} disabled={ingestLoading} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '11px 24px', background: 'linear-gradient(90deg,#7c3aed,#a855f7)', color: 'white', border: 'none', borderRadius: 8, cursor: ingestLoading ? 'not-allowed' : 'pointer', fontWeight: 600, fontSize: 14, opacity: ingestLoading ? 0.7 : 1 }}>
                                            {ingestLoading ? <><Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> Queueing...</> : <><Upload size={16} /> Queue URL</>}
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* ── Text Memo Mode ── */}
                            {ingestMode === 'text' && (
                                <div style={sectionStyle}>
                                    <h3 style={{ margin: '0 0 6px', fontSize: 15, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}><FileText size={16} color="#a78bfa" /> Inject Intelligence Memo</h3>
                                    <p style={{ margin: '0 0 24px', fontSize: 13, color: '#64748b' }}>Paste raw text, reports, or intelligence memos directly. Bypasses all scrapers — text enters the AI pipeline immediately.</p>
                                    <div style={{ display: 'grid', gap: 16 }}>
                                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                            <div>
                                                <label style={labelStyle}>Title <span style={{ color: '#ef4444' }}>*</span></label>
                                                <input style={inputStyle} placeholder="OSINT Report: Target Entity Q3" value={ingestTitle} onChange={e => setIngestTitle(e.target.value)} />
                                            </div>
                                            <div>
                                                <label style={labelStyle}>Source Label</label>
                                                <input style={inputStyle} placeholder="Analyst: Jane Doe" value={ingestSourceLabel} onChange={e => setIngestSourceLabel(e.target.value)} />
                                            </div>
                                        </div>
                                        <div>
                                            <label style={labelStyle}>Classification</label>
                                            <select style={{ ...inputStyle, cursor: 'pointer' }} value={ingestClassification} onChange={e => setIngestClassification(e.target.value)}>
                                                {CLASSIFICATIONS.map(c => <option key={c} value={c}>{c}</option>)}
                                            </select>
                                        </div>
                                        <div>
                                            <label style={labelStyle}>Intelligence Text <span style={{ color: '#ef4444' }}>*</span> <span style={{ color: '#64748b', fontWeight: 400 }}>(min. 50 chars)</span></label>
                                            <textarea
                                                style={{ ...inputStyle, minHeight: 220, resize: 'vertical', lineHeight: 1.6 }}
                                                placeholder="Paste the full body of your intelligence report, internal memo, translated intercept, or raw source text here..."
                                                value={ingestText}
                                                onChange={e => setIngestText(e.target.value)}
                                            />
                                            <div style={{ fontSize: 12, color: ingestText.length < 50 ? '#f59e0b' : '#10b981', marginTop: 6, textAlign: 'right' }}>
                                                {ingestText.length} characters {ingestText.length < 50 && `(need ${50 - ingestText.length} more)`}
                                            </div>
                                        </div>
                                        <button onClick={handleIngestText} disabled={ingestLoading} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '11px 24px', background: 'linear-gradient(90deg,#7c3aed,#a855f7)', color: 'white', border: 'none', borderRadius: 8, cursor: ingestLoading ? 'not-allowed' : 'pointer', fontWeight: 600, fontSize: 14, opacity: ingestLoading ? 0.7 : 1 }}>
                                            {ingestLoading ? <><Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> Injecting...</> : <><FileText size={16} /> Inject Memo</>}
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* ── Document Upload Mode ── */}
                            {ingestMode === 'document' && (
                                <div style={sectionStyle}>
                                    <h3 style={{ margin: '0 0 6px', fontSize: 15, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}><File size={16} color="#34d399" /> Upload Documents</h3>
                                    <p style={{ margin: '0 0 24px', fontSize: 13, color: '#64748b' }}>Upload PDFs, Word documents, CSVs, text files, or JSON. Text is extracted automatically and queued for AI analysis. Max 50MB / 10 files.</p>
                                    {/* Drop Zone */}
                                    <div
                                        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                                        onDragLeave={() => setDragOver(false)}
                                        onDrop={handleDrop}
                                        onClick={() => fileInputRef.current?.click()}
                                        style={{
                                            border: `2px dashed ${dragOver ? '#7c3aed' : '#334155'}`,
                                            borderRadius: 12, padding: '40px 24px', textAlign: 'center',
                                            backgroundColor: dragOver ? '#7c3aed11' : '#020617',
                                            cursor: 'pointer', transition: 'all 0.2s', marginBottom: 16,
                                        }}
                                    >
                                        <Upload size={32} color={dragOver ? '#a78bfa' : '#475569'} style={{ marginBottom: 12 }} />
                                        <div style={{ fontSize: 15, fontWeight: 600, color: dragOver ? '#c4b5fd' : '#94a3b8', marginBottom: 6 }}>
                                            {dragOver ? 'Drop files here' : 'Drag & drop files, or click to browse'}
                                        </div>
                                        <div style={{ fontSize: 13, color: '#475569' }}>PDF, TXT, CSV, DOC, DOCX, MD, JSON — up to 50MB each</div>
                                        <input ref={fileInputRef} type="file" multiple accept=".pdf,.txt,.csv,.doc,.docx,.md,.json" onChange={handleFileSelect} style={{ display: 'none' }} />
                                    </div>
                                    {/* File List */}
                                    {uploadedFiles.length > 0 && (
                                        <div style={{ marginBottom: 20 }}>
                                            <div style={{ fontSize: 12, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Ready to Upload ({uploadedFiles.length})</div>
                                            {uploadedFiles.map((file, idx) => (
                                                <div key={idx} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', backgroundColor: '#020617', borderRadius: 8, marginBottom: 6, border: '1px solid #1e293b' }}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                                        <span style={{ fontSize: 20 }}>{getFileIcon(file.name)}</span>
                                                        <div>
                                                            <div style={{ fontSize: 13, color: '#f8fafc' }}>{file.name}</div>
                                                            <div style={{ fontSize: 11, color: '#64748b' }}>{formatBytes(file.size)}</div>
                                                        </div>
                                                    </div>
                                                    <button onClick={() => setUploadedFiles(prev => prev.filter((_, i) => i !== idx))} style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', padding: 6 }}><X size={14} /></button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                    {/* Upload Results */}
                                    {uploadResults.length > 0 && (
                                        <div style={{ marginBottom: 20 }}>
                                            <div style={{ fontSize: 12, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Upload Results</div>
                                            {uploadResults.map((r, idx) => (
                                                <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 14px', backgroundColor: '#020617', borderRadius: 8, marginBottom: 6, border: `1px solid ${r.status === 'success' ? '#10b98133' : '#ef444433'}` }}>
                                                    {r.status === 'success' ? <CheckCircle size={16} color="#10b981" style={{ flexShrink: 0, marginTop: 1 }} /> : <XCircle size={16} color="#ef4444" style={{ flexShrink: 0, marginTop: 1 }} />}
                                                    <div>
                                                        <div style={{ fontSize: 13, color: '#f8fafc' }}>{r.filename}</div>
                                                        <div style={{ fontSize: 12, color: r.status === 'success' ? '#6ee7b7' : '#fca5a5', marginTop: 2 }}>{r.message || r.error}</div>
                                                        {r.chars_extracted && <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{r.chars_extracted.toLocaleString()} characters extracted</div>}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                    <button onClick={handleIngestDocuments} disabled={ingestLoading || !uploadedFiles.length} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '11px 24px', background: 'linear-gradient(90deg,#059669,#34d399)', color: 'white', border: 'none', borderRadius: 8, cursor: (ingestLoading || !uploadedFiles.length) ? 'not-allowed' : 'pointer', fontWeight: 600, fontSize: 14, opacity: (ingestLoading || !uploadedFiles.length) ? 0.6 : 1 }}>
                                        {ingestLoading ? <><Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> Uploading & Extracting...</> : <><Upload size={16} /> Upload & Ingest {uploadedFiles.length > 0 ? `(${uploadedFiles.length} file${uploadedFiles.length > 1 ? 's' : ''})` : ''}</>}
                                    </button>
                                </div>
                            )}
                        </div>
                    );
                })()}

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
