import React, { useState, useEffect } from 'react';
import { BookOpen, AlertCircle, Loader2, Sparkles, AlertTriangle, Layers, Zap, Activity, Clock } from 'lucide-react';
import { api, apiBase } from '../api';

export default function ArticleDashboard() {
    const [stats, setStats] = useState({ overview: null, entities: [] });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        fetchDashboardStats();
    }, []);

    const fetchDashboardStats = async () => {
        try {
            setLoading(true);
            const token = localStorage.getItem('token');
            const res = await fetch(`${apiBase}/admin/articles`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            if (!res.ok) throw new Error('Failed to fetch article stats');
            const data = await res.json();
            setStats(data);
        } catch (err) {
            console.error(err);
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '100px', color: 'var(--text-muted)' }}>
                <Loader2 className="spin" size={40} color="var(--color-blue)" style={{ marginBottom: '16px' }} />
                <span style={{ fontSize: '18px', letterSpacing: '0.05em' }}>Initializing Semantic Core...</span>
            </div>
        );
    }

    if (error) {
        return (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '48px', color: 'var(--color-red)' }}>
                <AlertCircle size={24} style={{ marginRight: '8px' }} />
                <span>Error: {error}</span>
            </div>
        );
    }

    const { overview, entities } = stats;
    
    // Calculate the precise breakdown
    const liveArticles = (overview?.articles_generated || 0) - (overview?.stale_articles || 0);
    const staleArticles = overview?.stale_articles || 0;
    const missingArticles = overview?.missing_articles || 0;

    return (
        <div style={{ width: '100%', height: '100%', overflowY: 'auto' }}>
            <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '32px' }}>
                {/* Header section */}
                <div style={{ marginBottom: '48px' }}>
                <h1 style={{ display: 'flex', alignItems: 'center', gap: '16px', fontSize: '36px', fontWeight: 800, margin: 0, color: '#fff' }}>
                    <div style={{ padding: '12px', background: 'var(--color-blue-alpha)', borderRadius: '16px', border: '1px solid rgba(59, 130, 246, 0.3)' }}>
                        <BookOpen color="var(--color-blue)" size={32} />
                    </div>
                    Living Entity Articles
                </h1>
                <p style={{ color: 'var(--text-secondary)', marginTop: '16px', fontSize: '14px', maxWidth: '800px', lineHeight: 1.6 }}>
                    The autonomous knowledge synthesis engine. This dashboard tracks the real-time Map-Reduce generation of encyclopedic articles for every entity mapped in Truth.
                </p>
            </div>

            {/* Overview Cards */}
            {overview && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '24px', marginBottom: '48px' }}>
                    {/* Live Hub */}
                    <div className="glass-panel" style={{ padding: '24px', position: 'relative', overflow: 'hidden' }}>
                        <div style={{ opacity: 0.1, position: 'absolute', top: 10, right: 10 }}>
                            <Activity size={80} color="var(--color-emerald)" strokeWidth={1} />
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--color-emerald-light)', fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '12px' }}>
                            <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--color-emerald-light)', boxShadow: '0 0 10px var(--color-emerald)' }} />
                            Live Articles
                        </div>
                        <div style={{ fontSize: '48px', fontWeight: 300, color: '#fff', marginBottom: '4px' }}>{liveArticles.toLocaleString()}</div>
                        <div style={{ fontSize: '12px', color: 'var(--color-emerald)', opacity: 0.8, fontWeight: 500 }}>Up-to-date synthesized knowledge</div>
                    </div>
                    
                    {/* Evolving Hub */}
                    <div className="glass-panel" style={{ padding: '24px', position: 'relative', overflow: 'hidden' }}>
                        <div style={{ opacity: 0.1, position: 'absolute', top: 10, right: 10 }}>
                            <AlertTriangle size={80} color="var(--color-amber)" strokeWidth={1} />
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--color-amber-light)', fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '12px' }}>
                            <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--color-amber-light)', boxShadow: '0 0 10px var(--color-amber)' }} />
                            Evolving (Stale)
                        </div>
                        <div style={{ fontSize: '48px', fontWeight: 300, color: '#fff', marginBottom: '4px' }}>{staleArticles.toLocaleString()}</div>
                        <div style={{ fontSize: '12px', color: 'var(--color-amber)', opacity: 0.8, fontWeight: 500 }}>Awaiting Map-Reduce append cycles</div>
                    </div>

                    {/* Queued Hub */}
                    <div className="glass-panel" style={{ padding: '24px', position: 'relative', overflow: 'hidden' }}>
                        <div style={{ opacity: 0.1, position: 'absolute', top: 10, right: 10 }}>
                            <Clock size={80} color="var(--text-muted)" strokeWidth={1} />
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-secondary)', fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '12px' }}>
                            <Loader2 size={12} style={{animation: 'spin 1s linear infinite'}} color="var(--text-secondary)" />
                            Queued (Missing)
                        </div>
                        <div style={{ fontSize: '48px', fontWeight: 300, color: '#fff', marginBottom: '4px' }}>{missingArticles.toLocaleString()}</div>
                        <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 500 }}>Entities awaiting initial generation</div>
                    </div>

                    {/* Global Stats */}
                    <div className="glass-panel" style={{ padding: '24px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                        <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '8px' }}>
                                <Layers size={14} /> Total Entities Mapped
                            </div>
                            <div style={{ fontSize: '28px', fontWeight: 300, color: '#fff' }}>{overview.total_entities.toLocaleString()}</div>
                        </div>
                        <div style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border-subtle)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--color-purple)', fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '8px' }}>
                                <Zap size={14} /> Validated Facts
                            </div>
                            <div style={{ fontSize: '28px', fontWeight: 300, color: '#fff' }}>{overview.total_claims.toLocaleString()}</div>
                        </div>
                    </div>
                </div>
            )}

            {/* Entity Table */}
            <div className="glass-panel" style={{ overflow: 'hidden', borderRadius: 'var(--radius-xl)' }}>
                <div style={{ padding: '24px', borderBottom: '1px solid var(--border-subtle)', background: 'rgba(0,0,0,0.2)' }}>
                    <h2 style={{ fontSize: '20px', fontWeight: 700, color: '#fff', margin: 0 }}>Daemon Priority Engine</h2>
                    <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px', margin: 0 }}>Real-time processing queue sorted by topological weight and search popularity</p>
                </div>
                
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse', fontSize: '14px', whiteSpace: 'nowrap' }}>
                        <thead style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-secondary)', fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            <tr>
                                <th style={{ padding: '20px 24px', fontWeight: 600 }}>Entity Profile</th>
                                <th style={{ padding: '20px 24px', fontWeight: 600 }}>Evolution State</th>
                                <th style={{ padding: '20px 24px', fontWeight: 600, textAlign: 'right' }}>Search Volume</th>
                                <th style={{ padding: '20px 24px', fontWeight: 600, textAlign: 'right' }}>Graph Mentions</th>
                                <th style={{ padding: '20px 24px', fontWeight: 600, textAlign: 'right' }}>Facts Included</th>
                            </tr>
                        </thead>
                        <tbody style={{ color: '#fff' }}>
                            {entities.length === 0 ? (
                                <tr>
                                    <td colSpan="5" style={{ padding: '64px', textAlign: 'center', color: 'var(--text-muted)' }}>
                                        <Layers size={32} style={{ margin: '0 auto 12px auto', opacity: 0.2, display: 'block' }} />
                                        No entity intelligence available in the graph.
                                    </td>
                                </tr>
                            ) : entities.map((e, i) => (
                                <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.02)', transition: 'background 0.2s' }} onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.02)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                                    <td style={{ padding: '16px 24px', fontWeight: 700, letterSpacing: '0.02em' }}>
                                        {e.name}
                                    </td>
                                    <td style={{ padding: '16px 24px' }}>
                                        {e.has_article && !e.is_stale ? (
                                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '6px 12px', borderRadius: '8px', fontSize: '11px', fontWeight: 700, background: 'var(--color-emerald-alpha)', color: 'var(--color-emerald)', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
                                                <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--color-emerald)' }} />
                                                LIVE
                                            </span>
                                        ) : e.has_article && e.is_stale ? (
                                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '6px 12px', borderRadius: '8px', fontSize: '11px', fontWeight: 700, background: 'var(--color-amber-alpha)', color: 'var(--color-amber)', border: '1px solid rgba(245, 158, 11, 0.2)' }}>
                                                <AlertTriangle size={12} color="var(--color-amber)" />
                                                EVOLVING
                                            </span>
                                        ) : (
                                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '6px 12px', borderRadius: '8px', fontSize: '11px', fontWeight: 700, background: 'rgba(255,255,255,0.05)', color: 'var(--text-secondary)', border: '1px solid var(--border-subtle)' }}>
                                                <Loader2 size={12} style={{animation: 'spin 1s linear infinite'}} color="var(--text-secondary)" />
                                                QUEUED
                                            </span>
                                        )}
                                    </td>
                                    <td style={{ padding: '16px 24px', textAlign: 'right' }}>
                                        {e.popularity > 0 ? (
                                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--color-blue-light)', fontWeight: 500, background: 'var(--color-blue-alpha)', padding: '4px 10px', borderRadius: '6px' }}>
                                                <Sparkles size={12} /> {e.popularity.toLocaleString()}
                                            </span>
                                        ) : (
                                            <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace' }}>0</span>
                                        )}
                                    </td>
                                    <td style={{ padding: '16px 24px', textAlign: 'right', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                                        {e.mentions.toLocaleString()}
                                    </td>
                                    <td style={{ padding: '16px 24px', textAlign: 'right', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                                        {e.facts_used > 0 ? e.facts_used.toLocaleString() : <span>—</span>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
            </div>
        </div>
    );
}
