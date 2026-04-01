import React from 'react';
import { Sparkles, Link as LinkIcon, BookOpen, FileText, Clock } from 'lucide-react';
import ArticleRenderer from './ArticleRenderer';

// ── Score Bar ─────────────────────────────────────────────────────────────────
function ScoreBar({ score }) {
    const pct = Math.round((score || 0) * 100);
    const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444';
    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 12, color: '#94a3b8' }}>Epistemic Score</span>
                <span style={{ fontSize: 14, fontWeight: 700, color }}>{pct}%</span>
            </div>
            <div style={{ height: 6, background: 'rgba(255,255,255,0.08)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.6s ease' }} />
            </div>
        </div>
    );
}

const formatSocialCount = (count) => {
    if (!count) return '0';
    if (count >= 1000000) return (count / 1000000).toFixed(1).replace(/\.0$/, '') + 'm';
    if (count >= 1000) return (count / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return count.toString();
};

/**
 * NodeInspector
 * Slide-in right sidebar that renders Entity / Claim / Evidence detail views.
 *
 * Props:
 *   selectedNode   — cytoscape node data object
 *   onClose        — () => void
 *   inspectorTab   — 'article' | 'facts' | 'timeline'
 *   setInspectorTab — (tab: string) => void
 *   entityArticle  — article data or null
 *   onEntityClick  — async (entityName: string) => void
 */
export default function NodeInspector({
    selectedNode,
    onClose,
    inspectorTab,
    setInspectorTab,
    entityArticle,
    onEntityClick,
}) {
    if (!selectedNode) return null;

    return (
        <div className="inspector-sidebar glass-panel" style={{
            position: 'fixed',
            top: 56,
            right: 0,
            width: 360,
            height: 'calc(100vh - 56px)',
            borderLeft: '1px solid rgba(255,255,255,0.08)',
            backgroundColor: 'rgba(10, 15, 30, 0.95)',
            backdropFilter: 'blur(20px)',
            boxShadow: '-12px 0 40px rgba(0,0,0,0.6)',
            zIndex: 100,
            display: 'flex',
            flexDirection: 'column',
            animation: 'slideInRight 0.22s ease',
        }}>
            <div className="inspector-header" style={{
                padding: '20px', borderBottom: '1px solid rgba(255,255,255,0.05)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center'
            }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                    {selectedNode.type === 'Entity' ? 'Entity Details'
                        : selectedNode.type === 'Claim' ? 'Claim Analysis'
                            : 'Evidence Provenance'}
                </h2>
                <button onClick={onClose} style={{
                    background: 'transparent', border: 'none', color: '#94a3b8',
                    fontSize: 20, cursor: 'pointer', padding: 0
                }}>×</button>
            </div>

            <div className="inspector-body" style={{ padding: 20, overflowY: 'auto', height: 'calc(100% - 65px)' }}>

                {/* ── ENTITY VIEW ─────────────────────────────────────────── */}
                {selectedNode.type === 'Entity' && (
                    <div>
                        <h3 style={{ fontSize: 18, color: '#f8fafc', marginBottom: 8 }}>{selectedNode.label}</h3>
                        <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
                            <p style={{ margin: 0, color: '#94a3b8', fontSize: 13, background: 'rgba(255,255,255,0.05)', padding: '6px 12px', borderRadius: 6, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                <Sparkles size={14} color="#3b82f6" />
                                <strong style={{ color: '#e2e8f0' }}>{formatSocialCount(selectedNode.mention_count || 0)}</strong> mentions
                            </p>
                            <button onClick={() => {
                                navigator.clipboard.writeText(`${window.location.origin}/entity/${encodeURIComponent(selectedNode.label)}`);
                                alert('Entity link copied to clipboard!');
                            }} style={{
                                background: 'rgba(59, 130, 246, 0.2)', border: '1px solid #3b82f6', color: '#60a5fa',
                                borderRadius: 6, padding: '4px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 500
                            }}>
                                <LinkIcon size={14} /> Copy Link
                            </button>
                        </div>

                        {/* Tab Nav */}
                        <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: 8 }}>
                            {[
                                { key: 'article', icon: <BookOpen size={14} />, label: 'Article' },
                                { key: 'facts',   icon: <FileText size={14} />, label: 'Raw Facts' },
                                { key: 'timeline',icon: <Clock size={14} />,    label: 'Timeline' },
                            ].map(({ key, icon, label }) => (
                                <button key={key} onClick={() => setInspectorTab(key)} style={{
                                    background: inspectorTab === key ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                                    color: inspectorTab === key ? '#60a5fa' : '#94a3b8',
                                    border: 'none', padding: '6px 12px', borderRadius: 6, cursor: 'pointer',
                                    display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 500,
                                    transition: 'all 0.2s'
                                }}>
                                    {icon} {label}
                                </button>
                            ))}
                        </div>

                        {/* Tab Content */}
                        {inspectorTab === 'article' && (
                            <ArticleRenderer
                                articleObj={entityArticle}
                                onEntityClick={async (entityName) => {
                                    if (entityName !== selectedNode.label) {
                                        await onEntityClick(entityName);
                                    }
                                }}
                            />
                        )}
                        {inspectorTab === 'facts' && (
                            <div style={{ color: '#94a3b8', fontSize: 13, lineHeight: 1.6 }}>
                                <p>This tab displays the raw, atomic claims extracted by the pipeline before they are synthesized into narrative text.</p>
                                <div style={{ marginTop: 16, padding: 12, background: 'rgba(255,255,255,0.03)', borderRadius: 8, border: '1px solid rgba(255,255,255,0.05)' }}>
                                    <em>Explore the graph canvas to the left to see these relationships visually.</em>
                                </div>
                            </div>
                        )}
                        {inspectorTab === 'timeline' && (
                            <div style={{ color: '#94a3b8', fontSize: 13, lineHeight: 1.6 }}>
                                <p>Chronological breakdown of how facts about this entity have evolved over time.</p>
                            </div>
                        )}
                    </div>
                )}

                {/* ── CLAIM VIEW ──────────────────────────────────────────── */}
                {selectedNode.type === 'Claim' && (
                    <>
                        <div style={{ marginBottom: 24 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                                <h3 style={{ fontSize: 12, color: '#94a3b8', textTransform: 'uppercase', margin: 0 }}>Full Statement</h3>
                                <button onClick={() => {
                                    navigator.clipboard.writeText(`${window.location.origin}/claim/${selectedNode.id}`);
                                    alert('Fact Citation link copied to clipboard!');
                                }} style={{
                                    background: 'rgba(124, 58, 237, 0.15)', border: '1px solid #7c3aed', color: '#c4b5fd',
                                    borderRadius: 4, padding: '4px 8px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600
                                }}>
                                    <LinkIcon size={12} /> Cite Fact
                                </button>
                            </div>
                            <p style={{ fontSize: 15, lineHeight: 1.6, color: '#f8fafc', margin: '0 0 8px 0' }}>
                                <strong>{selectedNode.subject}</strong>{' '}
                                <span style={{ color: '#8b5cf6' }}>{selectedNode.predicate?.replace(/_/g, ' ').toLowerCase()}</span>{' '}
                                <strong>{selectedNode.object}</strong>
                            </p>
                            {selectedNode.temporal && (
                                <p style={{ fontSize: 12, color: '#64748b', marginTop: 8, display: 'flex', gap: 6 }}>
                                    ⏱ {selectedNode.temporal}
                                    {selectedNode.spatial ? <span>· 📍 {selectedNode.spatial}</span> : ''}
                                </p>
                            )}
                        </div>

                        <div style={{
                            background: 'rgba(0,0,0,0.3)', padding: 16, borderRadius: 12,
                            border: '1px solid rgba(255,255,255,0.05)', marginBottom: 24
                        }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
                                <span style={{ fontSize: 12, color: '#94a3b8' }}>Status</span>
                                <span style={{
                                    fontSize: 12, fontWeight: 600,
                                    color: selectedNode.lifecycle === 'ACTIVE' ? '#10b981' : selectedNode.lifecycle === 'DISPUTED' ? '#f59e0b' : '#ef4444'
                                }}>
                                    {selectedNode.lifecycle || 'ACTIVE'}
                                </span>
                            </div>
                            <ScoreBar score={selectedNode.score} />
                        </div>

                        {/* Visual Media evidence */}
                        {selectedNode.media_items?.length > 0 && (
                            <div style={{ marginBottom: 24 }}>
                                <h3 style={{ fontSize: 12, color: '#94a3b8', textTransform: 'uppercase', marginBottom: 8 }}>Visual Intelligence</h3>
                                {selectedNode.media_items.map((m, i) => (
                                    <div key={i} style={{
                                        marginBottom: 12, background: 'rgba(0,0,0,0.4)', borderRadius: 12,
                                        overflow: 'hidden', border: `1px solid ${m.synthetic_probability > 0.85 ? 'rgba(239, 68, 68, 0.5)' : 'rgba(16, 185, 129, 0.5)'}`
                                    }}>
                                        {m.url && (
                                            <div style={{ position: 'relative' }}>
                                                <img src={m.url} alt="Evidence" style={{
                                                    width: '100%', maxHeight: 200, objectFit: 'cover',
                                                    filter: m.synthetic_probability > 0.85 ? 'grayscale(80%) sepia(20%) hue-rotate(300deg)' : 'none'
                                                }} />
                                                {m.synthetic_probability > 0.85 && (
                                                    <div style={{
                                                        position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                                        background: 'rgba(239, 68, 68, 0.2)', backdropFilter: 'blur(1px)'
                                                    }}>
                                                        <span style={{ background: '#ef4444', color: 'white', padding: '4px 12px', borderRadius: 4, fontWeight: 700, fontSize: 13, textTransform: 'uppercase', letterSpacing: 1 }}>Deepfake Rejected</span>
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                        <div style={{ padding: 12 }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                                <span style={{ fontSize: 12, fontWeight: 600, color: m.synthetic_probability > 0.85 ? '#fca5a5' : '#6ee7b7' }}>
                                                    {m.synthetic_probability > 0.85 ? 'Detected: AI Synthetic Media' : 'Authentic Media Verified'}
                                                </span>
                                                <span style={{ fontSize: 11, color: '#94a3b8' }}>{Math.round(m.synthetic_probability * 100)}% Synthetic</span>
                                            </div>
                                            {m.cross_modal_similarity > 0 && (
                                                <div style={{ fontSize: 11, color: '#cbd5e1', marginTop: 6, display: 'flex', justifyContent: 'space-between' }}>
                                                    <span>Text-Image Alignment:</span>
                                                    <strong style={{ color: '#8b5cf6' }}>{Math.round(m.cross_modal_similarity * 100)}%</strong>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}

                        {selectedNode.quote_context && (
                            <div style={{ marginBottom: 24 }}>
                                <h3 style={{ fontSize: 12, color: '#94a3b8', textTransform: 'uppercase', marginBottom: 8 }}>Source Excerpt</h3>
                                <div style={{ fontSize: 13, lineHeight: 1.6, color: '#cbd5e1', fontStyle: 'italic', borderLeft: '2px solid #3b82f6', paddingLeft: 12 }}>
                                    "{selectedNode.quote_context}"
                                </div>
                            </div>
                        )}

                        {selectedNode.source_name && (
                            <div>
                                <h3 style={{ fontSize: 12, color: '#94a3b8', textTransform: 'uppercase', marginBottom: 8 }}>Provenance</h3>
                                <div style={{ fontSize: 13, color: '#cbd5e1', marginBottom: 4 }}>
                                    <strong style={{ color: '#94a3b8' }}>Publisher:</strong> {selectedNode.source_name}
                                </div>
                                {selectedNode.publish_date && (
                                    <div style={{ fontSize: 13, color: '#cbd5e1', marginBottom: 8 }}>
                                        <strong style={{ color: '#94a3b8' }}>Date:</strong> {new Date(selectedNode.publish_date).toLocaleDateString()}
                                    </div>
                                )}
                                {selectedNode.source_url && (
                                    <a href={selectedNode.source_url} target="_blank" rel="noreferrer"
                                        style={{ color: '#3b82f6', fontSize: 13, textDecoration: 'none', display: 'inline-block', marginTop: 4 }}>
                                        Read Article ↗
                                    </a>
                                )}
                            </div>
                        )}
                    </>
                )}

                {/* ── EVIDENCE VIEW ────────────────────────────────────────── */}
                {selectedNode.type === 'Evidence' && (
                    <>
                        <div style={{ marginBottom: 24 }}>
                            <h3 style={{ fontSize: 12, color: '#94a3b8', textTransform: 'uppercase', marginBottom: 8 }}>Raw Evidence Text</h3>
                            <div style={{ fontSize: 13, lineHeight: 1.6, color: '#cbd5e1', fontStyle: 'italic', borderLeft: '2px solid #8b5cf6', paddingLeft: 12 }}>
                                {selectedNode.raw_text || '—'}
                            </div>
                        </div>
                        {selectedNode.published_by && (
                            <div>
                                <div style={{ fontSize: 13, color: '#cbd5e1', marginBottom: 4 }}>
                                    <strong style={{ color: '#94a3b8' }}>Source:</strong> {selectedNode.published_by}
                                </div>
                                {selectedNode.epistemic_conf > 0 && (
                                    <div style={{ fontSize: 13, color: '#cbd5e1' }}>
                                        <strong style={{ color: '#94a3b8' }}>Confidence:</strong> {Math.round(selectedNode.epistemic_conf * 100)}%
                                    </div>
                                )}
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
