import React, { useState, useEffect } from 'react';
import { GitMerge, ArrowRight, ShieldAlert, GitCommit, FileText, CheckCircle2 } from 'lucide-react';
import { api } from '../api';
import { formatDistanceToNow } from 'date-fns';

export default function ContradictionsPanel() {
    const [controversies, setControversies] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        api.getControversies()
            .then(data => setControversies(Array.isArray(data) ? data : data?.items ?? []))
            .catch(console.error)
            .finally(() => setLoading(false));
    }, []);

    if (loading) {
        return (
            <div className="state-container" style={{opacity: 0.5}}>
                <GitMerge size={64} style={{color: 'var(--color-warning)', animation: 'spin 1s linear infinite', marginBottom: '8px'}} />
                <p>Loading Epistemic Disputes...</p>
            </div>
        );
    }

    if (controversies.length === 0) {
        return (
            <div className="state-container">
                <CheckCircle2 size={64} style={{color: 'var(--color-success)', opacity: 0.5, marginBottom: '8px'}} />
                <h2 className="app-title" style={{color: 'var(--text-primary)', marginBottom: '8px'}}>Consensus Reached</h2>
                <p>No open contradictions detected in the graph.</p>
            </div>
        );
    }

    return (
        <div className="page-container">
            <div className="page-content">
                <div className="page-header">
                    <div>
                        <h2 className="page-title" style={{backgroundImage: 'var(--grad-amber)', WebkitBackgroundClip: 'text', color: 'transparent'}}>
                            <GitMerge size={64} style={{color: 'var(--color-warning)'}} /> Active Controversies
                        </h2>
                        <p className="page-subtitle">
                            Truth does not collapse reality into a single answer. These are entities where
                            high-signal sources have asserted conflicting facts in the same temporal window.
                        </p>
                    </div>
                    <div className="stat-box glass">
                        <span className="stat-value large" style={{color: 'var(--color-warning)'}}>{controversies.length}</span>
                        <span className="stat-label">Disputes</span>
                    </div>
                </div>

                <div className="card-list">
                    {controversies.map((cv, idx) => (
                        <ControversyCard key={idx} data={cv} />
                    ))}
                </div>
            </div>
        </div>
    );
}

function ControversyCard({ data }) {
    const claims = data.competing_claims || [];
    const createdAt = data.created_at ? new Date(data.created_at) : new Date();

    return (
        <div className="glass-panel controversy-card">
            {/* Header */}
            <div className="cv-header">
                <div className="cv-header-left">
                    <div className="cv-icon-wrap">
                        <ShieldAlert size={20} />
                    </div>
                    <div>
                        <div className="cv-meta">
                            EPISTEMIC FORK <span className="cv-meta-bullet">•</span> Detected {formatDistanceToNow(createdAt)} ago
                        </div>
                        <h3 className="cv-title">
                            {data.subject} <ArrowRight size={16} style={{color: 'var(--text-muted)'}} /> <span className="cv-title-pred">{data.predicate}</span>
                        </h3>
                    </div>
                </div>
            </div>

            {/* Claims Split View */}
            <div className="cv-split">
                {claims.map((claim, idx) => (
                    <div key={idx} className="cv-branch" title={claim.id}>
                        <div className="cv-branch-header">
                            <span className="cv-branch-tag">
                                <GitCommit size={12} /> Branch {idx + 1}
                            </span>
                            <span className="cv-branch-signal">
                                Signal: {Math.round(claim.score * 100)}
                            </span>
                        </div>

                        <div className="cv-branch-text">
                            "{claim.object}"
                        </div>

                        <a href={`/claim/${claim.id}`} className="cv-branch-link" target="_blank" rel="noreferrer">
                            <FileText size={12} /> View Complete Evidence Chain
                        </a>
                    </div>
                ))}
            </div>
        </div>
    );
}
