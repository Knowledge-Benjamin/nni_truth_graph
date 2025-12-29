import React from 'react';

function ClaimCard({ claim, onViewGraph }) {
    // Determine color based on confidence
    // > 0.7 = Green (Verified)
    // > 0.4 = Yellow (Debated/Neutral)
    // <= 0.4 = Red (Debunked/Low Confidence)
    const confidenceColor = claim.confidence > 0.7 ? '#10b981' :
        claim.confidence > 0.4 ? '#f59e0b' : '#ef4444';

    const verdict = claim.confidence > 0.7 ? 'VERIFIED TRUE' :
        claim.confidence > 0.4 ? 'DEBATED' : 'FALSE / UNPROVEN';

    const verdictBg = claim.confidence > 0.7 ? 'rgba(16, 185, 129, 0.2)' :
        claim.confidence > 0.4 ? 'rgba(245, 158, 11, 0.2)' : 'rgba(239, 68, 68, 0.2)';

    const textColor = claim.confidence > 0.4 && claim.confidence <= 0.7 ? '#000' : '#fff';

    return (
        <div className="claim-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
                <span style={{
                    background: verdictBg,
                    color: confidenceColor,
                    padding: '4px 8px',
                    borderRadius: '4px',
                    fontWeight: 800,
                    fontSize: '0.75rem',
                    letterSpacing: '0.05em'
                }}>
                    {verdict}
                </span>
            </div>
            <p className="card-statement">
                {claim.statement.replace(/<[^>]*>?/gm, '').replace(/&nbsp;/g, ' ')}
            </p>

            <div className="confidence-section">
                <div className="confidence-header">
                    <span>Confidence Score</span>
                    <span>{Math.round(claim.confidence * 100)}%</span>
                </div>
                <div className="progress-track">
                    <div className="progress-fill" style={{
                        width: `${claim.confidence * 100}%`,
                        backgroundColor: confidenceColor
                    }} />
                </div>
            </div>

            <div className="card-meta">
                <span>ID: {claim.id ? claim.id.substring(0, 8) + '...' : 'N/A'}</span>
                <span>First Seen: {claim.first_seen ? new Date(claim.first_seen).toLocaleDateString() : 'Unknown'}</span>
                {claim.controversy_score > 0 && (
                    <span style={{ color: 'var(--warning-color)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                        ⚠️ Controversial
                    </span>
                )}

                <div className="card-actions">
                    <button
                        className="btn-view-graph"
                        onClick={() => onViewGraph(claim.id)}
                        disabled={!claim.id || claim.id === 'unknown'}
                        title={(!claim.id || claim.id === 'unknown') ? "ID Missing" : "View Graph"}
                    >
                        View in Graph →
                    </button>
                </div>
            </div>
        </div>
    );
}

export default ClaimCard;
