import React, { useMemo } from 'react';
import { BookOpen, ExternalLink, AlertTriangle, Clock, ArrowDown } from 'lucide-react';

/**
 * ArticleRenderer
 *
 * Supports both legacy format (articleObj.article = markdown string)
 * and the new sectioned format where articleObj.article is an object:
 *   {
 *     "Section Title": { hash, content, used_uuids, last_updated },
 *     "_references": []
 *   }
 */
export default function ArticleRenderer({ articleObj, onEntityClick }) {
    if (!articleObj || articleObj.status === 'generating') {
        return (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '32px', color: 'var(--text-muted)' }}>
                <div className="spin" style={{ color: 'var(--color-blue)', marginBottom: '16px' }}>
                    <BookOpen size={32} />
                </div>
                <p style={{ fontSize: '14px', fontWeight: 500, margin: '0 0 8px 0' }}>Generating article…</p>
                <p style={{ fontSize: '12px', textAlign: 'center', maxWidth: '320px', margin: 0 }}>
                    Our AI is synthesizing verified facts and source excerpts. Check back soon.
                </p>
            </div>
        );
    }

    const { article: rawArticle, references } = articleObj;

    // The server stores article as a JSON string in Neo4j — parse it if needed
    let article = rawArticle;
    if (typeof rawArticle === 'string') {
        try {
            const parsed = JSON.parse(rawArticle);
            // Only treat as sectioned if it really is a plain object (not an array)
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                article = parsed;
            }
        } catch {
            // Not JSON — leave as the original markdown string
        }
    }


    // ── Detect format ──────────────────────────────────────────────────────────
    const isSectioned = article && typeof article === 'object' && !Array.isArray(article);
    const isLegacyString = typeof article === 'string';

    // References either come from top-level or from _references key inside article
    const refs = references
        || (isSectioned && Array.isArray(article._references) ? article._references : []);

    const refMap = useMemo(() => {
        const map = {};
        refs.forEach((ref, index) => {
            const refKey = ref.uuid || ref.claim_id || ref.claim_uuid || ref.id || ref._id || ref.source_id;
            if (refKey) {
                map[String(refKey)] = { ...ref, displayIndex: index + 1 };
            }
        });
        return map;
    }, [refs]);

    // ── Inline text renderer (entity links + [REF:uuid] citations) ─────────────
    const renderInline = (text, keyPrefix) => {
        if (!text) return null;
        const parts = text.split(/(\[\[.*?\]\]|\[(?:REF|ref):[a-zA-Z0-9\-_]+\])/g);
        return parts.map((part, i) => {
            if (part.startsWith('[[') && part.endsWith(']]')) {
                const entityName = part.slice(2, -2);
                return (
                    <button
                        key={`${keyPrefix}-e${i}`}
                        onClick={() => onEntityClick && onEntityClick(entityName)}
                        style={{
                            color: 'var(--color-blue-light)', fontWeight: 500,
                            textDecoration: 'underline', textDecorationStyle: 'dashed',
                            textDecorationColor: 'var(--color-blue-alpha)', textUnderlineOffset: '3px',
                            background: 'transparent', border: 'none', cursor: 'pointer', padding: 0,
                            fontSize: 'inherit', lineHeight: 'inherit'
                        }}
                    >
                        {entityName}
                    </button>
                );
            }
            const citationMatch = part.match(/^\[(?:REF|ref):([a-zA-Z0-9\-_]+)\]$/);
            if (citationMatch) {
                const refKey = citationMatch[1];
                const refInfo = refMap[refKey];
                if (!refInfo) return null;
                const targetUrl = refInfo.source_url || refInfo.original_url || `#ref-${refKey}`;
                const isExternal = /^https?:\/\//i.test(targetUrl);
                return (
                    <sup key={`${keyPrefix}-r${i}`} style={{ marginLeft: '2px' }}>
                        <a
                            href={targetUrl}
                            target={isExternal ? '_blank' : undefined}
                            rel={isExternal ? 'noopener noreferrer' : undefined}
                            title={`Source: ${refInfo.source_name || refInfo.source || refKey}`}
                            style={{
                                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                width: '16px', height: '16px', borderRadius: '50%',
                                background: 'rgba(255,255,255,0.1)', color: 'var(--color-blue-light)',
                                fontSize: '10px', fontWeight: 700, textDecoration: 'none'
                            }}
                        >
                            {refInfo.displayIndex}
                        </a>
                    </sup>
                );
            }
            return <span key={`${keyPrefix}-t${i}`}>{part}</span>;
        });
    };

    const renderRelationshipMap = (text, keyPrefix) => {
        const cleaned = String(text || '')
            .replace(/^GRAPH\s*:\s*/i, '')
            .replace(/^RELATIONSHIP_MAP\s*:\s*/i, '')
            .replace(/^DIAGRAM\s*:\s*/i, '')
            .trim();
        if (!cleaned) return null;

        const nodes = cleaned
            .split(/\s*(?:\n|\|\s*|\s*↓\s*|\s*->\s*|\s*→\s*)\s*/)
            .map(node => node.trim())
            .filter(Boolean);

        if (nodes.length < 2) return null;

        return (
            <div key={`${keyPrefix}-diagram`} style={{ margin: '20px 0 22px', background: 'rgba(15,23,42,0.72)', border: '1px solid rgba(148,163,184,0.18)', borderRadius: '12px', padding: '14px 12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', color: '#93c5fd', fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    <ArrowDown size={12} /> Relationship chain
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '6px' }}>
                    {nodes.map((node, idx) => (
                        <React.Fragment key={`${keyPrefix}-node-${idx}`}>
                            <div style={{
                                minWidth: '180px',
                                maxWidth: '420px',
                                width: '100%',
                                padding: '9px 12px',
                                borderRadius: '10px',
                                background: 'linear-gradient(180deg, rgba(37,99,235,0.26), rgba(30,41,59,0.72))',
                                border: '1px solid rgba(96,165,250,0.35)',
                                color: '#e2e8f0',
                                textAlign: 'center',
                                fontSize: '13px',
                                fontWeight: 600,
                                boxShadow: '0 8px 18px rgba(15,23,42,0.22)'
                            }}>
                                {renderInline(node, `${keyPrefix}-node-${idx}`)}
                            </div>
                            {idx < nodes.length - 1 && (
                                <div aria-hidden="true" style={{ color: '#93c5fd', fontSize: '16px', lineHeight: 1 }}>
                                    ↓
                                </div>
                            )}
                        </React.Fragment>
                    ))}
                </div>
            </div>
        );
    };

    // ── Paragraph renderer for a block of plain text ───────────────────────────
    const renderParagraphs = (text, sectionKey) => {
        if (!text) return null;
        return text.split('\n').map((para, pIdx) => {
            const line = para.trim();
            if (!line) return null;

            if (line.startsWith('## ')) {
                const h = line.replace('## ', '').trim();
                const isControversy = h.toLowerCase() === 'controversies';
                return (
                    <div key={`${sectionKey}-h${pIdx}`} style={{ marginTop: 24, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid var(--border-subtle)', paddingBottom: 8 }}>
                        {isControversy && <AlertTriangle size={18} color="var(--color-amber)" />}
                        <h3 style={{ fontSize: 16, fontWeight: 600, color: isControversy ? 'var(--color-amber)' : '#e2e8f0', margin: 0 }}>{h}</h3>
                    </div>
                );
            }

            if (/^(GRAPH|RELATIONSHIP_MAP|DIAGRAM)\s*:/i.test(line)) {
                return renderRelationshipMap(line, `${sectionKey}-graph-${pIdx}`);
            }

            return (
                <p key={`${sectionKey}-p${pIdx}`} style={{ marginBottom: 14, color: '#cbd5e1', lineHeight: 1.75, fontSize: 14 }}>
                    {renderInline(line, `${sectionKey}-p${pIdx}`)}
                </p>
            );
        });
    };

    // ── Sectioned format renderer ──────────────────────────────────────────────
    const renderSectioned = () => {
        const sections = Object.entries(article).filter(([key]) => key !== '_references');
        return sections.map(([title, section], idx) => {
            const isControversy = title.toLowerCase() === 'controversies';
            const updatedDate = section.last_updated
                ? new Date(section.last_updated).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                : null;

            return (
                <div key={`section-${idx}`} style={{ marginBottom: 32 }}>
                    {/* Section heading */}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border-subtle)', paddingBottom: 8, marginBottom: 14 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            {isControversy && <AlertTriangle size={16} color="var(--color-amber)" />}
                            <h3 style={{ margin: 0, fontSize: 17, fontWeight: 600, color: isControversy ? 'var(--color-amber)' : '#e2e8f0' }}>
                                {title}
                            </h3>
                        </div>
                        {updatedDate && (
                            <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                                <Clock size={11} /> {updatedDate}
                            </span>
                        )}
                    </div>
                    {/* Section content */}
                    {renderParagraphs(section.content, `s${idx}`)}
                </div>
            );
        });
    };

    // ── Legacy markdown string renderer ───────────────────────────────────────
    const renderLegacy = () => renderParagraphs(article, 'legacy');

    // ── References section (shared) ────────────────────────────────────────────
    const renderReferences = () => {
        if (!refs || refs.length === 0) return null;
        return (
            <div style={{ marginTop: 32, paddingTop: 24, borderTop: '1px solid var(--border-subtle)' }}>
                <h3 style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 16, marginTop: 0 }}>
                    References
                </h3>
                <ol style={{ paddingLeft: 20, margin: 0, color: 'var(--text-muted)', fontSize: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {refs.map((ref, i) => (
                        <li key={ref.uuid || i} id={`ref-${ref.uuid}`} style={{ paddingLeft: 8, scrollMarginTop: 80 }}>
                            <div>
                                <span style={{ fontWeight: 500, color: '#cbd5e1' }}>{ref.source_name || ref.source || 'Source'}</span>
                                {ref.publish_date && ` · ${ref.publish_date.split('T')[0]}`}
                                {ref.stance === 'CONTRADICTS' && (
                                    <span style={{ marginLeft: 8, padding: '2px 6px', background: 'var(--color-amber-alpha)', color: 'var(--color-amber)', borderRadius: 4, fontSize: 10, fontWeight: 700 }}>
                                        CONTRADICTED
                                    </span>
                                )}
                            </div>
                            <div style={{ marginTop: 4, paddingLeft: 16, borderLeft: '2px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                                {ref.article_title && <div style={{ fontStyle: 'italic', color: 'var(--text-secondary)' }}>"{ref.article_title}"</div>}
                                {ref.source_url && (
                                    <a href={ref.source_url} target="_blank" rel="noopener noreferrer"
                                        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--color-blue-light)', textDecoration: 'none', fontSize: 11 }}>
                                        <span>Original Source</span><ExternalLink size={10} />
                                    </a>
                                )}
                            </div>
                        </li>
                    ))}
                </ol>
            </div>
        );
    };

    return (
        <div style={{ paddingBottom: 32 }}>
            {isSectioned ? renderSectioned() : isLegacyString ? renderLegacy() : null}
            {renderReferences()}
        </div>
    );
}
