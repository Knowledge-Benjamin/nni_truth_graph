import React, { useState, useRef } from 'react';
import { Camera, Search, RefreshCw, AlertTriangle, ShieldCheck, Activity, Image as ImageIcon, Network, User, BookOpen } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

export default function MediaPortal() {
    const [file, setFile] = useState(null);
    const [previewUrl, setPreviewUrl] = useState('');
    const [intent, setIntent] = useState('deepfake'); // 'deepfake', 'trace', 'debunk'
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState(null);
    const [error, setError] = useState('');
    
    const fileInputRef = useRef(null);

    const handleFileChange = (e) => {
        const selected = e.target.files[0];
        if (!selected) return;
        setFile(selected);
        setPreviewUrl(URL.createObjectURL(selected));
        setResult(null);
        setError('');
    };

    const handleDrop = (e) => {
        e.preventDefault();
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            handleFileChange({ target: { files: e.dataTransfer.files } });
        }
    };

    const handleVerify = async () => {
        if (!file) return;
        setLoading(true);
        setResult(null);
        setError('');

        try {
            // Convert to base64
            const reader = new FileReader();
            reader.readAsDataURL(file);
            reader.onload = async () => {
                const base64Content = reader.result.split(',')[1];
                
                try {
                    const res = await fetch('/api/media/verify', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image: base64Content, intent })
                    });
                    
                    const data = await res.json();
                    
                    if (!res.ok) {
                        throw new Error(data.error || 'Server processing failed');
                    }
                    
                    setResult(data);
                } catch (err) {
                    setError(err.message);
                } finally {
                    setLoading(false);
                }
            };
        } catch (err) {
            setError(err.message);
            setLoading(false);
        }
    };

    const dials = {
        deepfake: { icon: <Activity size={18} />, title: "Authenticity Engine" },
        trace: { icon: <Search size={18} />, title: "Chrono-Trace Engine" },
        debunk: { icon: <ShieldCheck size={18} />, title: "Cross-Modal Truth Check" }
    };

    return (
        <div className="pane media-portal-pane">
            <div className="pane-header">
                <h2>Multimedia Verification Portal</h2>
                <div className="header-actions">
                    <span className="api-badge realtime" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <div className="live-dot" style={{ width: 8, height: 8, background: '#10b981', borderRadius: '50%', boxShadow: '0 0 8px #10b981' }}/>
                        Vision Node Online
                    </span>
                </div>
            </div>

            <div className="portal-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(400px, 1fr) minmax(400px, 1.2fr)', gap: '24px', padding: '24px' }}>
                
                {/* LEFT: UPLOAD & CONTROLS */}
                <div className="portal-controls" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                    
                    {/* Intent Switcher */}
                    <div className="intent-switch" style={{ display: 'flex', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '12px', padding: '4px' }}>
                        {Object.entries(dials).map(([key, val]) => (
                            <button
                                key={key}
                                onClick={() => setIntent(key)}
                                style={{
                                    flex: 1, padding: '10px 12px', borderRadius: '8px', border: 'none',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                                    background: intent === key ? 'rgba(56, 189, 248, 0.1)' : 'transparent',
                                    color: intent === key ? '#38bdf8' : '#94a3b8',
                                    fontWeight: intent === key ? 600 : 400, cursor: 'pointer', transition: 'all 0.2s'
                                }}
                            >
                                {val.icon} {key.charAt(0).toUpperCase() + key.slice(1)}
                            </button>
                        ))}
                    </div>

                    {/* Drag and Drop Zone */}
                    <div 
                        onDragOver={e => e.preventDefault()} 
                        onDrop={handleDrop}
                        className="upload-zone"
                        style={{
                            flex: 1, minHeight: '300px', border: '2px dashed rgba(255,255,255,0.1)', borderRadius: '16px',
                            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                            background: 'rgba(15, 23, 42, 0.4)', position: 'relative', overflow: 'hidden', cursor: 'pointer', transition: 'all 0.2s'
                        }}
                        onClick={() => !file && fileInputRef.current?.click()}
                    >
                        {previewUrl ? (
                            <div style={{ position: 'relative', width: '100%', height: '100%' }}>
                                <img src={previewUrl} alt="Preview" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
                                <button 
                                    onClick={(e) => { e.stopPropagation(); setFile(null); setPreviewUrl(''); setResult(null); }}
                                    style={{ position: 'absolute', top: 10, right: 10, background: 'rgba(0,0,0,0.6)', border: 'none', color: 'white', padding: '6px', borderRadius: '8px', cursor: 'pointer' }}
                                >
                                    ✕
                                </button>
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px', color: '#64748b' }}>
                                <ImageIcon size={48} strokeWidth={1} />
                                <div style={{ fontSize: '18px', fontWeight: 500, color: '#e2e8f0' }}>Drag & Drop Media</div>
                                <div style={{ fontSize: '13px' }}>Supports JPG, PNG, WEBP, and MP4 Frames</div>
                                <button style={{ marginTop: '12px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', color: 'white', padding: '8px 16px', borderRadius: '8px', cursor: 'pointer' }}>
                                    Browse Files
                                </button>
                            </div>
                        )}
                        <input type="file" ref={fileInputRef} onChange={handleFileChange} style={{ display: 'none' }} accept="image/*,video/*" />
                    </div>

                    <button 
                        onClick={handleVerify}
                        disabled={!file || loading}
                        style={{
                            background: !file ? 'rgba(255,255,255,0.05)' : 'linear-gradient(90deg, #38bdf8, #818cf8)',
                            color: !file ? '#64748b' : 'white',
                            padding: '14px', borderRadius: '12px', border: 'none', fontSize: '16px', fontWeight: 600,
                            cursor: (!file || loading) ? 'not-allowed' : 'pointer',
                            display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px'
                        }}
                    >
                        {loading ? <RefreshCw className="spin" size={20} /> : <Camera size={20} />}
                        {loading ? 'Analyzing Tensors...' : `Engage ${dials[intent].title}`}
                    </button>
                    {error && <div style={{ background: 'rgba(239, 68, 68, 0.1)', color: '#f87171', padding: '12px', borderRadius: '8px', fontSize: '13px', display: 'flex', gap: '8px', alignItems: 'center' }}><AlertTriangle size={16}/> {error}</div>}
                </div>

                {/* RIGHT: RESULTS DASHBOARD */}
                <div className="portal-results glass-panel" style={{ background: 'rgba(15, 23, 42, 0.6)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '16px', padding: '24px', display: 'flex', flexDirection: 'column' }}>
                    {!result && !loading && (
                        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#475569', gap: '16px' }}>
                            <Activity size={48} strokeWidth={1} />
                            <div style={{ fontSize: '16px' }}>Awaiting visual inquiry...</div>
                        </div>
                    )}

                    {loading && (
                        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '20px' }}>
                            <div className="radar-spinner" style={{ width: '80px', height: '80px', borderRadius: '50%', border: '2px solid rgba(56, 189, 248, 0.2)', borderTopColor: '#38bdf8', animation: 'spin 1.5s linear infinite' }} />
                            <div style={{ color: '#38bdf8', fontFamily: 'monospace', fontSize: '14px' }}>Querying Truth Graph Vectors...</div>
                        </div>
                    )}

                    {result && !loading && (
                        <AnimatePresence>
                            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
                                
                                {/* Deepfake Authenticity Dial */}
                                <div style={{ display: 'flex', alignItems: 'center', gap: '24px', background: 'rgba(255,255,255,0.02)', padding: '20px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.05)' }}>
                                    <div style={{ position: 'relative', width: '100px', height: '100px' }}>
                                        <svg viewBox="0 0 36 36" style={{ width: '100%', height: '100%', transform: 'rotate(-90deg)' }}>
                                            <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="3" />
                                            <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke={result.syntheticProbability > 0.5 ? '#f43f5e' : '#10b981'} strokeWidth="3" strokeDasharray={`${result.syntheticProbability * 100}, 100`} style={{ transition: 'stroke-dasharray 1s ease-out' }} />
                                        </svg>
                                        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
                                            <div style={{ fontSize: '24px', fontWeight: 700, color: result.syntheticProbability > 0.5 ? '#f43f5e' : '#10b981' }}>{Math.round(result.syntheticProbability * 100)}<span style={{ fontSize: '14px' }}>%</span></div>
                                        </div>
                                    </div>
                                    <div>
                                        <h3 style={{ margin: '0 0 8px 0', fontSize: '18px', color: '#f8fafc' }}>
                                            {result.syntheticProbability > 0.5 ? 'AI Generation Detected' : 'Authentic Media'}
                                        </h3>
                                        <p style={{ margin: 0, fontSize: '14px', color: '#94a3b8', lineHeight: 1.5 }}>
                                            Vision nodes analyzed the pixel tensors and derived a <strong>{(result.syntheticProbability * 100).toFixed(1)}%</strong> probability that this media relies on synthetic manipulations.
                                        </p>
                                    </div>
                                </div>

                                {/* Graph Context Block */}
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                    <h4 style={{ margin: 0, color: '#e2e8f0', display: 'flex', alignItems: 'center', gap: '8px' }}><Network size={16} color="#38bdf8"/> Truth Graph Context</h4>
                                    
                                    <div style={{ background: 'rgba(0,0,0,0.3)', padding: '16px', borderRadius: '8px', borderLeft: `3px solid ${result.matchFound ? '#38bdf8' : '#64748b'}`, fontSize: '14px', color: '#cbd5e1', lineHeight: 1.6 }}>
                                        {result.message || 'Analysis complete. Displaying contextual data below based on Intent selector.'}
                                    </div>

                                    {/* Intent-specific Renderings */}
                                    {intent === 'trace' && result.patientZero && (
                                        <div className="trace-card" style={{ background: 'rgba(255,255,255,0.02)', padding: '16px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)', marginTop: '8px' }}>
                                            <div style={{ fontSize: '12px', color: '#fbbf24', fontWeight: 700, letterSpacing: '1px', marginBottom: '8px', textTransform: 'uppercase' }}>Chronological Origin (Patient Zero)</div>
                                            <div style={{ color: '#f8fafc', fontSize: '15px', fontWeight: 500, marginBottom: '4px' }}>{result.patientZero.title}</div>
                                            <div style={{ display: 'flex', gap: '16px', fontSize: '13px', color: '#94a3b8' }}>
                                                <span><User size={12} style={{marginRight: 4, display:'inline'}}/> {result.patientZero.author}</span>
                                                <span><BookOpen size={12} style={{marginRight: 4, display:'inline'}}/> {new Date(result.patientZero.publish_date).toLocaleDateString()}</span>
                                            </div>
                                        </div>
                                    )}

                                    {intent === 'debunk' && result.claims && result.claims.length > 0 && (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                                            {result.claims.map(claim => (
                                                <div key={claim.id} style={{ background: 'rgba(56, 189, 248, 0.05)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(56, 189, 248, 0.1)' }}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                                                        <span style={{ fontSize: '11px', background: 'rgba(255,255,255,0.1)', padding: '2px 6px', borderRadius: '4px', color: '#e2e8f0' }}>Fact Node</span>
                                                        <span style={{ fontSize: '11px', color: claim.epistemic_score > 0.6 ? '#10b981' : '#f43f5e' }}>Score: {Math.round(claim.epistemic_score * 100)}%</span>
                                                    </div>
                                                    <div style={{ fontSize: '14px', color: '#f8fafc', fontWeight: 500 }}>
                                                        {claim.subject} <span style={{ color: '#38bdf8' }}>{claim.predicate}</span> {claim.object_entity}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </motion.div>
                        </AnimatePresence>
                    )}
                </div>
            </div>
        </div>
    );
}
