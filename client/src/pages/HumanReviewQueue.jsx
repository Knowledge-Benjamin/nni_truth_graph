import React, { useState, useEffect } from 'react';
import { ShieldCheck, ShieldClose, ShieldAlert, AlertTriangle, RefreshCcw, ExternalLink } from 'lucide-react';
import { api } from '../api';
import { formatDistanceToNow } from 'date-fns';

export default function HumanReviewQueue() {
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const limit = 10;

    const loadData = async () => {
        setLoading(true);
        try {
            const res = await api.getHumanReview(page, limit);
            setItems(res.items);
            setTotal(res.total);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { loadData(); }, [page]);

    const handleResolve = async (id, decision) => {
        try {
            await api.resolveHumanReview(id, decision, 'Manual review via Dashboard');
            setItems(prev => prev.filter(i => i.id !== id));
            setTotal(t => Math.max(0, t - 1));
        } catch (e) {
            alert('Failed to resolve claim.');
        }
    };

    if (loading && items.length === 0) {
        return (
            <div className="state-container">
                <RefreshCcw size={64} style={{color: 'var(--color-info)', animation: 'spin 1s linear infinite'}} />
            </div>
        );
    }

    if (items.length === 0) {
        return (
            <div className="state-container">
                <ShieldCheck size={64} style={{color: 'var(--color-success)', opacity: 0.5, marginBottom: '8px'}} />
                <h2 className="app-title" style={{color: 'var(--text-primary)', marginBottom: '8px'}}>Queue is Empty</h2>
                <p>All extracted claims have been algorithmically resolved.</p>
            </div>
        );
    }

    return (
        <div className="page-container">
            <div className="page-content">
                <div className="page-header">
                    <div>
                        <h2 className="page-title" style={{backgroundImage: 'var(--grad-red)', WebkitBackgroundClip: 'text', color: 'transparent'}}>
                            Needs Review
                        </h2>
                        <p className="page-subtitle">
                            These claims triggered high-profile controversies or fell below the required algorithmic confidence thresholds.
                            Human adjudication dictates the graph's ground truth.
                        </p>
                    </div>
                    <div className="stat-box">
                        <span className="stat-value large" style={{color: 'var(--color-danger)'}}>{total}</span>
                        <span className="stat-label">Pending</span>
                    </div>
                </div>

                <div className="card-list">
                    {items.map(item => (
                        <ReviewCard key={item.id} item={item} onResolve={handleResolve} />
                    ))}
                </div>

                <div className="pagination">
                    <button disabled={page === 1} onClick={() => setPage(p => p - 1)}>Prev</button>
                    <span>Page {page}</span>
                    <button disabled={items.length < limit} onClick={() => setPage(p => p + 1)}>Next</button>
                </div>
            </div>
        </div>
    );
}

function ReviewCard({ item, onResolve }) {
    const isContradiction = item.neo4j_stance === 'CONTRADICTS' || item.lifecycle === 'DISPUTED';
    const cardType = isContradiction ? 'contradiction' : 'low-signal';

    return (
        <div className={`glass-panel review-card is-${cardType}`}>
            <div className={`review-border-top ${cardType}`} />

            <div className="review-header">
                <div style={{display: 'flex', alignItems: 'center', gap: '12px'}}>
                    {isContradiction ? (
                        <div className="review-badge contradiction">
                            <AlertTriangle size={12} /> Graph Contradiction
                        </div>
                    ) : (
                        <div className="review-badge low-signal">
                            <ShieldAlert size={12} /> Low Signal
                        </div>
                    )}

                    <span className="review-time">
                        Added {formatDistanceToNow(new Date(item.valid_from))} ago
                    </span>
                </div>

                <div className="review-meta">
                    <div className="review-confidence">
                        Algorithmic Confidence: <span className={item.extraction_confidence < 0.6 ? 'low' : 'high'}>{Math.round(item.extraction_confidence * 100)}%</span>
                    </div>
                    <div className="review-trust">Source Trust: {Math.round(item.epistemic_trust_score * 100)} {item.epistemic_trust_score < 0.5 && '(Untrusted)'}</div>
                </div>
            </div>

            <div className="review-content">
                <div>
                    <h3 className="review-claim-text">
                        <span className="review-subject">[ {item.subject} ]</span>
                        <span className="review-predicate">{item.predicate}</span>
                        <span className="review-object">{item.object_entity}</span>
                    </h3>
                    {item.temporal_anchor && (
                        <span className="review-anchor">
                            • Temporal anchor: {item.temporal_anchor}
                        </span>
                    )}
                </div>
                <div className="review-source">
                    <p>"{item.article_title}"</p>
                    <a href={item.article_url} target="_blank" rel="noreferrer" className="review-link">
                        <ExternalLink size={14} /> Open Source Context
                    </a>
                </div>
            </div>

            <div className="review-actions">
                <button onClick={() => onResolve(item.id, 'REJECT')} className="action-btn discard">
                    <ShieldClose size={16} /> Discard
                </button>
                <button onClick={() => onResolve(item.id, 'RETRACT')} className="action-btn retract">
                    <ShieldAlert size={16} /> Flag as Retraction
                </button>
                <button onClick={() => onResolve(item.id, 'APPROVE')} className="action-btn approve">
                    <ShieldCheck size={16} /> Approve to Graph
                </button>
            </div>

        </div>
    );
}
