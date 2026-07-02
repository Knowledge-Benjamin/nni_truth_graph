import React, { useState, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import CytoscapeComponent from 'react-cytoscapejs';
import cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { Search, Loader2, Eye, EyeOff, Sparkles, Link as LinkIcon, BookOpen, FileText, Clock, Bot, Zap, Globe, ChevronDown, ChevronLeft, ExternalLink, ArrowRight } from 'lucide-react';
import DOMPurify from 'dompurify';
import { api, apiBase } from '../api';
import ArticleRenderer from '../components/ArticleRenderer';

cytoscape.use(fcose);

// Formats numbers social media style (e.g. 1000 -> 1k, 1500 -> 1.5k, 1000000 -> 1m)
const formatSocialCount = (count) => {
    if (!count) return '0';
    if (count >= 1000000) {
        return (count / 1000000).toFixed(1).replace(/\.0$/, '') + 'm';
    }
    if (count >= 1000) {
        return (count / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    }
    return count.toString();
};

// ── Colour palette ────────────────────────────────────────────────────────────
const COLORS = {
    entityFocal: '#3b82f6',   // blue
    entityFocalBorder: '#93c5fd',
    entityLinked: '#10b981',   // emerald
    entityLinkedBorder: '#6ee7b7',
    claimActive: '#10b981',
    claimDisputed: '#f59e0b',
    claimSuperseded: '#6b7280',
    claimRetracted: '#ef4444',
    evidence: '#a78bfa',   // violet
    edgeDefault: '#475569',
    edgeSubject: '#64748b',
    edgeObject: '#64748b',
    edgeEvidence: '#7c3aed',
    edgeEvolves: '#3b82f6',
    edgeCorroborates: '#10b981',
    edgeContradicts: '#ef4444',
    hierarchyEdge: '#c026d3', // Fuchsia/Purple for universal ontology edges
    labelText: '#e2e8f0',
    bg: '#121216',
};

// ── Cytoscape stylesheet ──────────────────────────────────────────────────────
const CY_STYLE = [
    {
        selector: 'node[type="Entity"][role="focal"]',
        style: {
            'width': 80, 'height': 80,
            'shape': 'ellipse',
            'background-color': COLORS.entityFocal,
            'border-width': 4,
            'border-color': COLORS.entityFocalBorder,
            'label': 'data(label)',
            'color': '#ffffff',
            'font-family': 'Inter, system-ui, sans-serif',
            'font-size': 14,
            'font-weight': 700,
            'text-valign': 'center',
            'text-halign': 'center',
            'text-wrap': 'wrap',
            'text-max-width': 70,
            'z-index': 10,
        },
    },
    // ── Entity: linked ────────────────────────────────────────────────────
    {
        selector: 'node[type="Entity"][role="linked"]',
        style: {
            'width': 48, 'height': 48,
            'shape': 'ellipse',
            'background-color': COLORS.entityLinked,
            'border-width': 2,
            'border-color': COLORS.entityLinkedBorder,
            'label': 'data(label)',
            'color': '#ffffff',
            'font-family': 'Inter, system-ui, sans-serif',
            'font-size': 11,
            'font-weight': 600,
            'text-valign': 'center',
            'text-halign': 'center',
            'text-wrap': 'wrap',
            'text-max-width': 44,
        },
    },
    // ── Claim nodes ───────────────────────────────────────────────────────
    {
        selector: 'node[type="Claim"]',
        style: {
            'width': 120,
            'height': 36,
            'shape': 'round-rectangle',
            'background-color': ele => {
                const lc = ele.data('lifecycle') || 'ACTIVE';
                if (lc === 'DISPUTED') return COLORS.claimDisputed;
                if (lc === 'SUPERSEDED' || lc === 'STALE') return COLORS.claimSuperseded;
                if (lc === 'RETRACTED') return COLORS.claimRetracted;
                return COLORS.claimActive;
            },
            'background-opacity': 0.85,
            'border-width': 1,
            'border-color': 'rgba(255,255,255,0.15)',
            'label': 'data(label)',
            'color': '#ffffff',
            'font-family': 'Inter, system-ui, sans-serif',
            'font-size': 10,
            'font-weight': 600,
            'text-valign': 'center',
            'text-halign': 'center',
            'text-wrap': 'wrap',
            'text-max-width': 110,
        },
    },
    // ── Evidence nodes (diamond) ──────────────────────────────────────────
    {
        selector: 'node[type="Evidence"]',
        style: {
            'width': 22, 'height': 22,
            'shape': 'diamond',
            'background-color': COLORS.evidence,
            'border-width': 1,
            'border-color': 'rgba(255,255,255,0.2)',
            'label': '',
        },
    },
    // ── Selected state ────────────────────────────────────────────────────
    {
        selector: 'node:selected',
        style: {
            'border-width': 4,
            'border-color': '#ffffff',
        },
    },
    // ── Edges: HAS_SUBJECT / HAS_OBJECT ──────────────────────────────────
    {
        selector: 'edge[type="HAS_SUBJECT"], edge[type="HAS_OBJECT"]',
        style: {
            'width': 1.5,
            'line-color': COLORS.edgeDefault,
            'target-arrow-color': COLORS.edgeDefault,
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'opacity': 0.7,
        },
    },
    // ── Edges: SUPPORTED_BY ───────────────────────────────────────────────
    {
        selector: 'edge[type="SUPPORTED_BY"]',
        style: {
            'width': 1,
            'line-color': COLORS.edgeEvidence,
            'target-arrow-color': COLORS.edgeEvidence,
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'opacity': 0.5,
            'line-style': 'dashed',
            'line-dash-pattern': [4, 3],
        },
    },
    // ── Edges: EVOLVES ────────────────────────────────────────────────────
    {
        selector: 'edge[type="EVOLVES"]',
        style: {
            'width': 2,
            'line-color': COLORS.edgeEvolves,
            'target-arrow-color': COLORS.edgeEvolves,
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'line-style': 'dashed',
            'line-dash-pattern': [6, 3],
            'label': 'data(label)',
            'font-size': 9,
            'color': COLORS.edgeEvolves,
            'text-background-opacity': 1,
            'text-background-color': COLORS.bg,
            'text-background-padding': '2px',
        },
    },
    // ── Edges: CORROBORATED_BY ────────────────────────────────────────────
    {
        selector: 'edge[type="CORROBORATED_BY"]',
        style: {
            'width': 2,
            'line-color': COLORS.edgeCorroborates,
            'target-arrow-color': COLORS.edgeCorroborates,
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'line-style': 'dashed',
            'line-dash-pattern': [6, 3],
            'label': 'data(label)',
            'font-size': 9,
            'color': COLORS.edgeCorroborates,
            'text-background-opacity': 1,
            'text-background-color': COLORS.bg,
            'text-background-padding': '2px',
        },
    },
    // ── Edges: CONTRADICTS ────────────────────────────────────────────────
    {
        selector: 'edge[type="CONTRADICTS"]',
        style: {
            'width': 2,
            'line-color': COLORS.edgeContradicts,
            'target-arrow-color': COLORS.edgeContradicts,
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'font-size': 9,
            'color': COLORS.edgeContradicts,
            'text-background-opacity': 1,
            'text-background-color': COLORS.bg,
            'text-background-padding': '2px',
        },
    },
    // ── Ontology / Hierarchy Edges ──────────────────────────────────────────
    {
        selector: 'edge[type="CONTAINS"], edge[type="PART_OF"], edge[type="IS_A"], edge[type="SUBCLASS_OF"]',
        style: {
            'width': 2.5,
            'line-color': COLORS.hierarchyEdge,
            'target-arrow-color': COLORS.hierarchyEdge,
            'target-arrow-shape': 'vee',
            'curve-style': 'bezier',
            'label': 'data(label)',
            'font-size': 10,
            'font-weight': 700,
            'color': COLORS.hierarchyEdge,
            'text-background-opacity': 0.8,
            'text-background-color': COLORS.bg,
            'text-background-padding': '3px',
            'opacity': 0.9,
        },
    },
    // ── Hover highlight ───────────────────────────────────────────────────
    {
        selector: 'node.hovered',
        style: {
            'border-width': 3,
            'border-color': '#ffffff',
        },
    },
    // ── External Entity: focal ────────────────────────────────────────────
    {
        selector: 'node[type="Entity"][role="focal"][origin="external"]',
        style: {
            'background-color': '#0f766e',
            'border-color': '#ccfbf1',
        },
    },
    // ── External Entity: linked ───────────────────────────────────────────
    {
        selector: 'node[type="Entity"][role="linked"][origin="external"]',
        style: {
            'background-color': '#115e59',
            'border-color': '#99f6e4',
        },
    },
    // ── External Claim nodes ──────────────────────────────────────────────
    {
        selector: 'node[type="Claim"][origin="external"]',
        style: {
            'border-color': '#2dd4bf',
            'border-width': 2,
            'border-style': 'dashed',
            'background-color': 'rgba(15, 118, 110, 0.85)',
        },
    },
    // ── SAME_AS Edges ─────────────────────────────────────────────────────
    {
        selector: 'edge[type="SAME_AS"]',
        style: {
            'width': 3,
            'line-color': '#f59e0b',
            'target-arrow-shape': 'none',
            'curve-style': 'bezier',
            'line-style': 'dashed',
            'label': 'data(label)',
            'font-size': 10,
            'color': '#f59e0b',
            'text-background-opacity': 1,
            'text-background-color': COLORS.bg,
        },
    },
];

// ── Cytoscape layout options ──────────────────────────────────────────────────
const LAYOUT = {
    name: 'fcose',
    animate: true,
    animationDuration: 600,
    randomize: true, // Randomize initial positions slightly to help the algorithm
    fit: true,
    padding: 60,
    nodeDimensionsIncludeLabels: true,
    nodeRepulsion: 9000, // Increased to push nodes further apart
    idealEdgeLength: 180, // Increased to make edges longer
    edgeElasticity: 0.45,
    nestingFactor: 1.2,
    nodeSeparation: 100, // Explicitly request more space between nodes
    gravity: 0.25, // Lower gravity lets the graph expand more
    gravityRange: 3.8,
    numIter: 3000, // More iterations for a more stable layout
};

// ── Build Cytoscape elements from neighborhood payload ────────────────────────
function buildElements(data) {
    const elements = [];
    for (const node of data.nodes) {
        elements.push({ data: node });
    }
    for (const edge of data.edges) {
        elements.push({ data: edge });
    }
    return elements;
}

// ── Score bar helper ──────────────────────────────────────────────────────────
function ScoreBar({ score }) {
    const pct = Math.round((score || 0) * 100);
    const color = pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444';
    return (
        <div style={{ marginTop: 4 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#94a3b8', marginBottom: 3 }}>
                <span>Epistemic Score</span><span style={{ color }}>{pct}%</span>
            </div>
            <div style={{ height: 4, borderRadius: 2, background: '#1e293b', overflow: 'hidden' }}>
                <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.4s' }} />
            </div>
        </div>
    );
}

// ── useIsMobile hook ───────────────────────────────────────────────
function useIsMobile() {
    const [mobile, setMobile] = React.useState(() => window.innerWidth < 768);
    React.useEffect(() => {
        const handler = () => setMobile(window.innerWidth < 768);
        window.addEventListener('resize', handler);
        return () => window.removeEventListener('resize', handler);
    }, []);
    return mobile;
}

// ── Sources Dropdown (citation panel below AI messages) ─────────────────────
function SourcesDropdown({ refs, focalEntity, onOpenArticle }) {
    const [open, setOpen] = React.useState(false);
    return (
        <div style={{ marginTop: 6, maxWidth: '92%' }}>
            <button
                onClick={() => setOpen(o => !o)}
                style={{
                    background: 'rgba(100,116,139,0.12)', border: '1px solid rgba(100,116,139,0.2)',
                    color: '#94a3b8', borderRadius: 8, padding: '3px 10px', fontSize: 11,
                    cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5
                }}>
                <ExternalLink size={10} />
                {refs.length} Source{refs.length !== 1 ? 's' : ''}
                <ChevronDown size={10} style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />
            </button>
            {open && (
                <div style={{
                    marginTop: 6, padding: '8px 10px',
                    background: 'rgba(0,0,0,0.3)', borderRadius: 8,
                    border: '1px solid rgba(255,255,255,0.06)'
                }}>
                    {refs.map((ref, i) => (
                        <div key={i} style={{ marginBottom: i < refs.length - 1 ? 8 : 0 }}>
                            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 2 }}>
                                [{i + 1}] {ref.source || ref.article_title || 'Source'}
                            </div>
                            {ref.url ? (
                                <a href={ref.url} target="_blank" rel="noopener noreferrer"
                                    style={{ fontSize: 11, color: '#60a5fa', textDecoration: 'none', wordBreak: 'break-all' }}>
                                    {ref.url}
                                </a>
                            ) : (
                                <span style={{ fontSize: 11, color: '#475569' }}>{ref.title || 'No URL available'}</span>
                            )}
                        </div>
                    ))}
                    {focalEntity && (
                        <button onClick={onOpenArticle} style={{
                            marginTop: 8, background: 'none', border: 'none', color: '#8b5cf6',
                            cursor: 'pointer', fontSize: 11, padding: 0, display: 'flex', alignItems: 'center', gap: 4
                        }}>
                            <BookOpen size={10} /> Open full article →
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function ExplorerPane() {
    const { slug, id } = useParams();
    const navigate = useNavigate();

    const [query, setQuery] = useState('');
    const [loading, setLoading] = useState(false);
    const [elements, setElements] = useState([]);
    const [selectedNode, setSelectedNode] = useState(null);
    const [hoveredNode, setHoveredNode] = useState(null);
    const [showAll, setShowAll] = useState(false);
    const [scope, setScope] = useState('internal'); // 'internal', 'external', 'both'

    // Agentic Chat State
    const [hasStarted, setHasStarted] = useState(false);
    const [messages, setMessages] = useState([]);
    const [isAsking, setIsAsking] = useState(false);
    const [layoutName, setLayoutName] = useState('cose'); // fcose, grid, circle, concentric

    // Agent Mode: 'search' | 'agent'
    const [agentMode, setAgentMode] = useState(() => {
        try { return localStorage.getItem('tg_agent_mode') || 'search'; } catch { return 'search'; }
    });
    const setAndPersistMode = (mode) => {
        setAgentMode(mode);
        try { localStorage.setItem('tg_agent_mode', mode); } catch { /* ignore */ }
    };

    // Mobile
    const isMobile = useIsMobile();
    const [mobileResult, setMobileResult] = useState(null); // { type: 'article'|'snippets'|'answer'|'empty', data: {} }
    const [mobileArticleExpanded, setMobileArticleExpanded] = useState(false);
    const [showMobileGraph, setShowMobileGraph] = useState(false);

    // Living Article State
    const [entityArticle, setEntityArticle] = useState(null);
    const [inspectorTab, setInspectorTab] = useState('article'); // 'article', 'facts', 'timeline'

    // Raw Facts & Timeline data
    const [entityFacts, setEntityFacts] = useState(null);
    const [factsLoading, setFactsLoading] = useState(false);

    const cyRef = useRef(null);
    const chatEndRef = useRef(null);
    const searchInputRef = useRef(null);

    // Auto-scroll chat to bottom
    React.useEffect(() => {
        chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, isAsking]);

    // Re-run the layout explicitly when elements change
    React.useEffect(() => {
        if (cyRef.current && elements.length > 0) {
            const timer = setTimeout(() => {
                cyRef.current.layout(LAYOUT).run();
            }, 100);
            return () => clearTimeout(timer);
        }
    }, [elements]);

    // ── URL Deep-Linking Hook ────────────────────────────────────────────────
    const loadedSlugRef = useRef(null);
    const loadedIdRef = useRef(null);

    React.useEffect(() => {
        if (slug && slug !== loadedSlugRef.current) {
            loadedSlugRef.current = slug;
            setHasStarted(true);
            const decoded = decodeURIComponent(slug);
            if (isMobile && !showMobileGraph) {
                setQuery(decoded);
                loadMobileResult(decoded);
            } else {
                loadNeighborhood(decoded).then(() => {
                    // Auto-select the focal entity so the article panel opens
                    setSelectedNode({ type: 'Entity', role: 'focal', label: decoded });
                });
            }
        } else if (id && id !== loadedIdRef.current) {
            loadedIdRef.current = id;
            setHasStarted(true);
            api.getClaim(id).then(res => {
                if (res.subject?.name) {
                    loadedSlugRef.current = encodeURIComponent(res.subject.name);
                    loadNeighborhood(res.subject.name).then(() => {
                        setSelectedNode({ type: 'Claim', ...res.claim });
                    });
                }
            }).catch(e => console.error("Fact link broken:", e));
        }
    }, [slug, id /* loadNeighborhood omitted safely */]);

    // ── Load a neighborhood ───────────────────────────────────────────────────
    const loadNeighborhood = useCallback(async (entityName, forceShowAll = showAll, currentScope = scope) => {
        if (!entityName || !entityName.trim()) return;
        setLoading(true);
        setSelectedNode(null);
        setHoveredNode(null);

        try {
            let data;
            try {
                data = await api.getNeighborhood(entityName, forceShowAll, 60, currentScope);
            } catch {
                const searchRes = await api.search(entityName, 'entity');
                if (!searchRes.entities?.length) throw new Error(`No entity matching "${entityName}"`);
                data = await api.getNeighborhood(searchRes.entities[0].name, forceShowAll, 60, currentScope);
            }
            setElements(buildElements(data));
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [showAll, scope]);

    // ── Handle Article Fetching ───────────────────────────────────────────────
    
    // Automatically fetch the article when an Entity is selected in the inspector
    React.useEffect(() => {
        if (selectedNode && selectedNode.type === 'Entity' && selectedNode.label) {
            fetchEntityArticle(selectedNode.label);
        } else {
            setEntityArticle(null);
            setEntityFacts(null);
        }
    }, [selectedNode?.label, selectedNode?.type]);

    // Fetch raw facts when user switches to facts or timeline tab
    React.useEffect(() => {
        if (!selectedNode || selectedNode.type !== 'Entity' || !selectedNode.label) return;
        if (inspectorTab !== 'facts' && inspectorTab !== 'timeline') return;
        if (entityFacts?.entity === selectedNode.label) return; // already loaded
        setFactsLoading(true);
        api.getEntity(selectedNode.label)
            .then(data => setEntityFacts(data))
            .catch(console.error)
            .finally(() => setFactsLoading(false));
    }, [inspectorTab, selectedNode?.label]);

    const fetchEntityArticle = async (entityName) => {
        try {
            setEntityArticle({ status: 'generating' }); // Show loading skeleton
            const response = await fetch(`${apiBase}/entity/${encodeURIComponent(entityName)}/article`);
            if (response.ok) {
                const data = await response.json();
                setEntityArticle(data);
            } else if (response.status === 202) {
                setEntityArticle({ status: 'generating' });
            } else {
                setEntityArticle(null);
            }
        } catch (error) {
            console.error('Failed to fetch article:', error);
            setEntityArticle(null);
        }
    };

    // ── Mobile fast-path: article → snippets → AI ─────────────────────────────
    const loadMobileResult = async (text) => {
        setMobileResult(null);
        setMobileArticleExpanded(false);
        setIsAsking(true);

        // Always pre-load the graph in parallel so "Explore Graph" works immediately
        loadNeighborhood(text);

        try {
            // 1. Try article fast-path (no LLM needed)
            const artRes = await fetch(`${apiBase}/entity/${encodeURIComponent(text)}/article`, {
                headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` }
            });
            if (artRes.ok) {
                const art = await artRes.json();
                if (art.article) {
                    setMobileResult({ type: 'article', data: { ...art, entityName: text } });
                    return;
                }
                // Entity exists but no article yet → show snippet cards
                const entRes = await fetch(`${apiBase}/entity/${encodeURIComponent(text)}`, {
                    headers: { Authorization: `Bearer ${localStorage.getItem('token') || ''}` }
                });
                if (entRes.ok) {
                    const ent = await entRes.json();
                    setMobileResult({ type: 'snippets', data: { entityName: text, claims: ent.claims || [] } });
                    return;
                }
            }

            // 2. NLP fallback — use AI ReAct loop
            const nlpMessages = [{ role: 'user', content: text }];
            const res = await api.sendChat(nlpMessages, { current_entity: null, viewport_entities: [] });
            if (res.focal_entity && res.focal_entity !== text) {
                // AI resolved to a different entity — update graph with the correct one
                loadNeighborhood(res.focal_entity);
            }
            setMobileResult({
                type: 'answer',
                data: {
                    answer: res.answer,
                    focal_entity: res.focal_entity,
                    references: res.references || []
                }
            });
        } catch (err) {
            console.error('[Mobile] loadMobileResult error:', err);
            setMobileResult({ type: 'empty', data: { query: text } });
        } finally {
            setIsAsking(false);
        }
    };


    // ── Slash Command Preprocessor ──────────────────────────────────────────
    // Returns true if the command was handled locally (skip API call)
    const handleSlashCommand = async (text) => {
        const lower = text.toLowerCase().trim();

        const match = (prefix) => {
            if (lower.startsWith(prefix)) return text.slice(prefix.length).trim();
            return null;
        };

        let entity;
        if ((entity = match('/explore ')) !== null) {
            await loadNeighborhood(entity);
            setMessages(prev => [...prev,
                { role: 'user', content: text },
                { role: 'assistant', content: `Loaded the graph for <strong>${entity}</strong>.` }
            ]);
            return true;
        }
        if ((entity = match('/article ')) !== null) {
            await loadNeighborhood(entity);
            setInspectorTab('article');
            setMessages(prev => [...prev,
                { role: 'user', content: text },
                { role: 'assistant', content: `Opening the article for <strong>${entity}</strong>.` }
            ]);
            return true;
        }
        if ((entity = match('/facts ')) !== null) {
            await loadNeighborhood(entity);
            setInspectorTab('facts');
            setMessages(prev => [...prev,
                { role: 'user', content: text },
                { role: 'assistant', content: `Showing raw claims for <strong>${entity}</strong>.` }
            ]);
            return true;
        }
        if (lower === '/close') {
            setSelectedNode(null);
            setMessages(prev => [...prev,
                { role: 'user', content: text },
                { role: 'assistant', content: 'Inspector panel closed.' }
            ]);
            return true;
        }
        if ((entity = match('/search ')) !== null) {
            await loadNeighborhood(entity);
            setMessages(prev => [...prev,
                { role: 'user', content: text },
                { role: 'assistant', content: `Searching for <strong>${entity}</strong> in the graph.` }
            ]);
            return true;
        }
        // Not a slash command
        return false;
    };

    // ── Action Executor (runs AI-returned actions against the UI) ─────────
    const executeActions = useCallback(async (actions) => {
        if (!Array.isArray(actions)) return;
        for (const action of actions) {
            if (action.type === 'LOAD_ENTITY' && action.entity) {
                await loadNeighborhood(action.entity);
            } else if (action.type === 'OPEN_INSPECTOR' && action.entity) {
                // Find the matching node from current elements and open inspector
                setSelectedNode({ type: 'Entity', role: 'focal', label: action.entity });
                if (action.tab) setInspectorTab(action.tab);
            } else if (action.type === 'SET_TAB' && action.tab) {
                setInspectorTab(action.tab);
            } else if (action.type === 'CLOSE_INSPECTOR') {
                setSelectedNode(null);
            }
        }
    }, [loadNeighborhood]);

    // ── Main Chat / Command Submit Handler ─────────────────────────────
    // optionalText: bypass the query state (used by I'm Feeling Curious to avoid render race)
    const handleChatSubmit = async (e, optionalText) => {
        if (e) e.preventDefault();
        const rawText = (optionalText ?? query).trim();
        const text = DOMPurify.sanitize(rawText);
        if (!text || isAsking) return;

        setQuery('');
        setHasStarted(true);

        if (isMobile) {
            setShowMobileGraph(false);
            await loadMobileResult(text);
            return;
        }

        setIsAsking(true);

        // ─ 1. Check for slash commands first (client-side only, no API call) ─
        if (text.startsWith('/')) {
            const handled = await handleSlashCommand(text);
            if (handled) { setIsAsking(false); return; }
        }

        // ─ 2. Search mode: direct graph search, no AI ─
        if (agentMode === 'search') {
            setMessages(prev => [...prev, { role: 'user', content: text }]);
            try {
                await loadNeighborhood(text);
                setMessages(prev => [...prev, {
                    role: 'assistant',
                    content: `Loaded graph results for <strong>${text}</strong>. Click any node to explore.`
                }]);
            } catch {
                setMessages(prev => [...prev, { role: 'assistant', content: `No results found for "${text}".` }]);
            }
            setIsAsking(false);
            return;
        }

        // ─ 3. Agent mode: send to AI ReAct loop with session context ─
        const newMessages = [...messages, { role: 'user', content: text }];
        setMessages(newMessages);

        // Build session context from current graph state
        const focalNodes = elements.filter(el => el.data?.role === 'focal');
        const context = {
            current_entity: focalNodes[0]?.data?.label || null,
            viewport_entities: elements
                .filter(el => el.data?.type === 'Entity')
                .map(el => el.data?.label)
                .filter(Boolean)
                .slice(0, 10)
        };

        try {
            const res = await api.sendChat(newMessages, context);

            // Append AI message with references attached
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: res.answer,
                references: res.references || [],
                focal_entity: res.focal_entity
            }]);

            // Execute UI actions returned by the agent
            if (res.actions && res.actions.length > 0) {
                await executeActions(res.actions);
            } else if (res.focal_entity) {
                // Fallback: at minimum load the focal entity
                await loadNeighborhood(res.focal_entity);
            }
        } catch (err) {
            console.error('Chat API failed:', err);
            setMessages(prev => [...prev, { role: 'assistant', content: "Sorry, I couldn't reach the AI engine right now. Please try again." }]);
        } finally {
            setIsAsking(false);
        }
    };

    // Reload with toggled show_all
    const handleToggleShowAll = async () => {
        // Find current top focal node
        const focalNodes = elements.filter(el => el.data.role === 'focal');
        const focalName = focalNodes.length > 0 ? focalNodes[0].data.id : null;

        const next = !showAll;
        setShowAll(next);

        if (focalName) {
            const actualName = focalName.startsWith('e:') ? focalName.substring(2) : focalName;
            await loadNeighborhood(actualName, next, scope);
        }
    };

    const handleCycleScope = async () => {
        const focalNodes = elements.filter(el => el.data.role === 'focal');
        const focalName = focalNodes.length > 0 ? focalNodes[0].data.id : null;

        const nextScope = scope === 'internal' ? 'external' : scope === 'external' ? 'both' : 'internal';
        setScope(nextScope);

        if (focalName) {
            const actualName = focalName.startsWith('e:') ? focalName.substring(2) : focalName;
            await loadNeighborhood(actualName, showAll, nextScope);
        }
    };

    // ── Attach Cytoscape events ───────────────────────────────────────────────
    const attachCyEvents = useCallback((cy) => {
        cyRef.current = cy;
        cy.off('mouseover', 'node');
        cy.off('mouseout', 'node');
        cy.off('tap', 'node');
        cy.off('tap');

        cy.on('mouseover', 'node', (e) => {
            const n = e.target;
            n.addClass('hovered');
            const pos = n.renderedPosition();
            setHoveredNode({ x: pos.x, y: pos.y, data: n.data() });
        });

        cy.on('mouseout', 'node', (e) => {
            e.target.removeClass('hovered');
            setHoveredNode(null);
        });

        cy.on('tap', 'node', (e) => {
            const d = e.target.data();
            if (d.type === 'Entity' && d.role === 'linked') {
                navigate(`/entity/${encodeURIComponent(d.label)}`);
                return;
            }
            if (d.type === 'Claim' || d.type === 'Evidence' || (d.type === 'Entity' && d.role === 'focal')) {
                setSelectedNode(d);
            }
        });

        cy.on('tap', (e) => {
            if (e.target === cy) setSelectedNode(null);
        });
    }, [loadNeighborhood]);

    // ── Initial Clean Screen (Google Style) ──────────────────────────────────
    if (!hasStarted) {
        return (
            <div style={{
                height: '100%', width: '100%', display: 'flex', flexDirection: 'column',
                background: COLORS.bg
            }}>
                {/* Navbar area */}
                <div style={{ padding: '20px 30px', display: 'flex', justifyContent: 'flex-end', gap: 16 }}>
                    <a href="#" style={{ color: '#e2e8f0', textDecoration: 'none', fontSize: 14 }}>About</a>
                    <a href="#" style={{ color: '#e2e8f0', textDecoration: 'none', fontSize: 14 }}>Docs</a>
                </div>

                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', paddingBottom: '10vh' }}>

                    {/* Branding Logo Area */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 36 }}>
                        <div style={{
                            width: 60, height: 60, borderRadius: '50%',
                            background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            boxShadow: '0 8px 32px rgba(59, 130, 246, 0.4)'
                        }}>
                            <Search color="white" size={30} strokeWidth={2.5} />
                        </div>
                        <h1 style={{
                            fontSize: 48, fontWeight: 800, margin: 0, letterSpacing: '-1px',
                            background: 'linear-gradient(90deg, #60a5fa, #c084fc)',
                            WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent'
                        }}>
                            Truth
                        </h1>
                    </div>

                    {/* Search Bar Container */}
                    <form onSubmit={handleChatSubmit} style={{ width: '100%', maxWidth: 640, position: 'relative', marginBottom: 32 }}>
                        <div className="glass-panel" style={{
                            display: 'flex', alignItems: 'center', padding: '12px 20px',
                            borderRadius: 30, background: 'rgba(255,255,255,0.03)',
                            border: '1px solid rgba(255,255,255,0.1)',
                            boxShadow: '0 4px 20px rgba(0,0,0,0.2)',
                            transition: 'all 0.3s ease'
                        }}
                            onMouseEnter={(e) => {
                                e.currentTarget.style.background = 'rgba(255,255,255,0.06)';
                                e.currentTarget.style.boxShadow = '0 6px 24px rgba(0,0,0,0.3)';
                            }}
                            onMouseLeave={(e) => {
                                e.currentTarget.style.background = 'rgba(255,255,255,0.03)';
                                e.currentTarget.style.boxShadow = '0 4px 20px rgba(0,0,0,0.2)';
                            }}>
                            {/* Search icon LEFT — only shown when bar is empty */}
                            {!query && (
                                <button
                                    type="button"
                                    onClick={() => searchInputRef.current?.focus()}
                                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center', color: '#94a3b8', flexShrink: 0 }}
                                    aria-label="Focus search"
                                >
                                    <Search size={20} />
                                </button>
                            )}
                            <input
                                id="search-query"
                                name="query"
                                ref={searchInputRef}
                                autoFocus
                                type="text"
                                value={query}
                                onChange={e => setQuery(e.target.value)}
                                placeholder="Search the graph or ask a question..."
                                style={{
                                    flex: 1, background: 'transparent', border: 'none', color: '#fff',
                                    padding: query ? '6px 12px 6px 0' : '6px 0 6px 16px', fontSize: 17, outline: 'none'
                                }}
                            />
                            {/* Search icon RIGHT — shown when bar has text, clicking submits */}
                            {query && (
                                <button
                                    type="submit"
                                    style={{
                                        background: 'linear-gradient(135deg,#3b82f6,#8b5cf6)', border: 'none', borderRadius: '50%',
                                        width: 36, height: 36, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                        cursor: 'pointer', flexShrink: 0, transition: 'transform 0.15s, box-shadow 0.15s',
                                        boxShadow: '0 2px 12px rgba(59,130,246,0.4)'
                                    }}
                                    onMouseEnter={e => { e.currentTarget.style.transform = 'scale(1.1)'; e.currentTarget.style.boxShadow = '0 4px 20px rgba(59,130,246,0.6)'; }}
                                    onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)'; e.currentTarget.style.boxShadow = '0 2px 12px rgba(59,130,246,0.4)'; }}
                                    aria-label="Search"
                                >
                                    <Search size={16} color="white" />
                                </button>
                            )}
                        </div>
                    </form>

                    {/* Action Buttons */}
                    <div style={{ display: 'flex', gap: 12 }}>
                        <button
                            onClick={handleChatSubmit}
                            disabled={!query.trim()}
                            style={{
                                background: 'rgba(255,255,255,0.05)', color: '#e2e8f0', border: '1px solid transparent',
                                padding: '10px 24px', borderRadius: 4, fontSize: 14, cursor: query.trim() ? 'pointer' : 'default',
                                transition: 'all 0.2s', fontFamily: 'Inter, system-ui, sans-serif'
                            }}
                            onMouseEnter={(e) => {
                                if (query.trim()) {
                                    e.currentTarget.style.background = 'rgba(255,255,255,0.1)';
                                    e.currentTarget.style.border = '1px solid rgba(255,255,255,0.1)';
                                }
                            }}
                            onMouseLeave={(e) => {
                                e.currentTarget.style.background = 'rgba(255,255,255,0.05)';
                                e.currentTarget.style.border = '1px solid transparent';
                            }}
                        >
                            Graph Search
                        </button>
                        <button
                            type="button"
                            onClick={() => {
                                const curious = "Show me a random disputed claim.";
                                setQuery(curious);
                                // Pass text directly — avoids the React state render race
                                handleChatSubmit(null, curious);
                            }}
                            style={{
                                background: 'rgba(255,255,255,0.05)', color: '#e2e8f0', border: '1px solid transparent',
                                padding: '10px 24px', borderRadius: 4, fontSize: 14, cursor: 'pointer',
                                transition: 'all 0.2s', fontFamily: 'Inter, system-ui, sans-serif'
                            }}
                            onMouseEnter={(e) => {
                                e.currentTarget.style.background = 'rgba(255,255,255,0.1)';
                                e.currentTarget.style.border = '1px solid rgba(255,255,255,0.1)';
                            }}
                            onMouseLeave={(e) => {
                                e.currentTarget.style.background = 'rgba(255,255,255,0.05)';
                                e.currentTarget.style.border = '1px solid transparent';
                            }}
                        >
                            I'm Feeling Curious
                        </button>
                    </div>

                    <p style={{ marginTop: 28, fontSize: 13, color: '#64748b' }}>
                        Graph AI offered in: <a href="#" style={{ color: '#8b5cf6', textDecoration: 'none' }}>English</a>
                    </p>
                </div>
            </div>
        );
    }

    // ── Mobile Article Text Renderer ──────────────────────────────────────────
    const renderMobileArticle = (rawArticle, maxLength = 0) => {
        if (!rawArticle) return null;

        // Parse JSON if the new sectioned format is stored as a string
        let article = rawArticle;
        if (typeof rawArticle === 'string') {
            try {
                const parsed = JSON.parse(rawArticle);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    article = parsed;
                }
            } catch { /* stay as string */ }
        }

        // ── NEW FORMAT: sectioned object ──────────────────────────────────────
        if (typeof article === 'object' && !Array.isArray(article)) {
            const sections = Object.entries(article).filter(([k]) => k !== '_references');
            // For collapsed view, only show first section
            const visibleSections = maxLength > 0 ? sections.slice(0, 1) : sections;
            return visibleSections.map(([title, section], idx) => (
                <div key={idx} style={{ marginBottom: 20 }}>
                    <h3 style={{
                        fontSize: 15, fontWeight: 600, color: '#f1f5f9',
                        margin: '0 0 8px 0', paddingBottom: 6,
                        borderBottom: '1px solid rgba(255,255,255,0.06)'
                    }}>{title}</h3>
                    {renderMobileParagraph(section.content)}
                </div>
            ));
        }

        // ── LEGACY FORMAT: plain markdown string ───────────────────────────────
        let content = article;
        if (maxLength > 0 && content.length > maxLength) {
            content = content.slice(0, maxLength) + '...';
        }
        const paragraphs = content.split('\n');
        return paragraphs.map((para, pIdx) => {
            const pText = para.trim();
            if (!pText) return null;
            if (pText.startsWith('## ')) {
                return <h3 key={pIdx} style={{ fontSize: 15, marginTop: 16, marginBottom: 8, color: '#f1f5f9' }}>{pText.replace('## ', '').trim()}</h3>;
            }
            return <p key={pIdx} style={{ marginBottom: 12, lineHeight: 1.6 }}>{renderMobileParagraph(pText)}</p>;
        });
    };

    // Inline entity link + [REF:] renderer for mobile paragraphs
    const renderMobileParagraph = (text) => {
        if (!text) return null;
        const parts = text.split(/(\[\[.*?\]\]|\[REF:[a-zA-Z0-9\-]+\])/g);
        return parts.map((part, i) => {
            if (part.startsWith('[[') && part.endsWith(']]')) {
                const entityName = part.slice(2, -2);
                return (
                    <button key={i} onClick={() => navigate(`/entity/${encodeURIComponent(entityName)}`)} style={{
                        color: '#60a5fa', fontWeight: 500, textDecoration: 'underline',
                        textDecorationStyle: 'dashed', textDecorationColor: 'rgba(59,130,246,0.3)',
                        textUnderlineOffset: 4, background: 'transparent', border: 'none', cursor: 'pointer', padding: 0, fontSize: 'inherit'
                    }}>{entityName}</button>
                );
            }
            if (part.startsWith('[REF:') && part.endsWith(']')) {
                return <sup key={i} style={{ color: '#94a3b8', fontSize: 10, marginLeft: 2 }}>[ref]</sup>;
            }
            return <span key={i}>{part}</span>;
        });
    };

    // ── Mobile Rendering Path ─────────────────────────────────────────────────
    if (isMobile && !showMobileGraph) {
        return (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', overflow: 'hidden', background: COLORS.bg }}>
                {/* Mobile Sticky Header */}
                <div style={{ padding: '16px', borderBottom: '1px solid rgba(255,255,255,0.05)', background: 'rgba(15, 23, 42, 0.8)', backdropFilter: 'blur(10px)', zIndex: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, fontSize: 16, color: '#f8fafc' }}>
                            <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#10b981', boxShadow: '0 0 8px #10b981' }} />
                            Truth
                        </div>
                    </div>
                    {/* Mobile Search Input */}
                    <form onSubmit={handleChatSubmit} style={{ margin: 0 }}>
                        <div style={{
                            display: 'flex', alignItems: 'center', padding: '8px 12px',
                            borderRadius: 24, background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)'
                        }}>
                            <Search size={16} color="#64748b" style={{ marginRight: 8 }} />
                            <input
                                type="text"
                                value={query}
                                onChange={e => setQuery(e.target.value)}
                                placeholder="Search entity or ask a question..."
                                disabled={isAsking}
                                style={{ flex: 1, background: 'transparent', border: 'none', color: '#fff', fontSize: 14, outline: 'none' }}
                            />
                            {isAsking && <Loader2 size={16} className="spin" color="#8b5cf6" />}
                        </div>
                    </form>
                </div>

                {/* Mobile Scrollable Results */}
                <div style={{ flex: 1, overflowY: 'auto', padding: '20px 16px' }}>
                    {!hasStarted ? (
                        <div style={{ textAlign: 'center', marginTop: 40, color: '#64748b' }}>
                            <Search size={40} style={{ opacity: 0.5, marginBottom: 16 }} />
                            <p style={{ fontSize: 14, lineHeight: 1.6 }}>Search Truth to explore entities, read verified articles, and discover connections.</p>
                        </div>
                    ) : mobileResult ? (
                        <div className="glass-panel" style={{ padding: 20, borderRadius: 16, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.05)' }}>
                            {mobileResult.type === 'article' && (
                                <>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#3b82f6' }} />
                                        <h2 style={{ margin: 0, fontSize: 18, color: '#f8fafc' }}>{mobileResult.data.entityName}</h2>
                                        <span style={{ fontSize: 10, padding: '2px 6px', background: 'rgba(59,130,246,0.1)', color: '#60a5fa', borderRadius: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>Entity</span>
                                    </div>
                                    <div style={{ fontSize: 14, color: '#e2e8f0', lineHeight: 1.6, marginBottom: 16 }}>
                                        {mobileArticleExpanded 
                                            ? renderMobileArticle(mobileResult.data.article)
                                            : renderMobileArticle(mobileResult.data.article, 300)
                                        }
                                    </div>
                                    {!mobileArticleExpanded && (() => {
                                        const art = mobileResult.data.article;
                                        const isLong = typeof art === 'string' ? art.length > 300 : Object.keys(art).filter(k => k !== '_references').length > 1;
                                        return isLong ? (
                                            <button onClick={() => setMobileArticleExpanded(true)} style={{ background: 'none', border: 'none', color: '#8b5cf6', fontSize: 13, cursor: 'pointer', padding: 0, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 4 }}>
                                                Read More <ChevronDown size={14} />
                                            </button>
                                        ) : null;
                                    })()}
                                    {mobileArticleExpanded && (() => {
                                        const art = mobileResult.data.article;
                                        const isLong = typeof art === 'string' ? art.length > 300 : Object.keys(art).filter(k => k !== '_references').length > 1;
                                        return isLong ? (
                                            <button onClick={() => setMobileArticleExpanded(false)} style={{ background: 'none', border: 'none', color: '#8b5cf6', fontSize: 13, cursor: 'pointer', padding: 0, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 4 }}>
                                                Show Less <ChevronDown size={14} style={{ transform: 'rotate(180deg)' }} />
                                            </button>
                                        ) : null;
                                    })()}
                                    <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 16, display: 'flex', gap: 10 }}>
                                        <button onClick={() => { setShowMobileGraph(true); navigate(`/entity/${encodeURIComponent(mobileResult.data.entityName)}`); }} style={{ flex: 1, padding: '10px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                                            Explore Graph <ArrowRight size={14} />
                                        </button>
                                    </div>
                                </>
                            )}
                            
                            {mobileResult.type === 'snippets' && (
                                <>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
                                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#f59e0b' }} />
                                        <h2 style={{ margin: 0, fontSize: 18, color: '#f8fafc' }}>{mobileResult.data.entityName}</h2>
                                        <span style={{ fontSize: 10, padding: '2px 6px', background: 'rgba(245,158,11,0.1)', color: '#fbbf24', borderRadius: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>Generating</span>
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
                                        {mobileResult.data.claims.slice(0, 5).map((claim, i) => (
                                            <div key={i} style={{ padding: 12, background: 'rgba(0,0,0,0.2)', borderRadius: 8, border: '1px solid rgba(255,255,255,0.04)' }}>
                                                <div style={{ fontSize: 13, color: '#e2e8f0' }}>👉 {mobileResult.data.entityName} <strong style={{ color: '#94a3b8' }}>{claim.predicate}</strong> {claim.target_entity}</div>
                                            </div>
                                        ))}
                                    </div>
                                    <button onClick={() => { setShowMobileGraph(true); navigate(`/entity/${encodeURIComponent(mobileResult.data.entityName)}`); }} style={{ width: '100%', padding: '10px', background: 'rgba(59,130,246,0.1)', color: '#60a5fa', border: '1px solid rgba(59,130,246,0.2)', borderRadius: 8, fontSize: 13, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                                        Explore Graph <ArrowRight size={14} />
                                    </button>
                                </>
                            )}

                            {mobileResult.type === 'answer' && (
                                <>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                                        <Zap size={16} color="#8b5cf6" />
                                        <h2 style={{ margin: 0, fontSize: 15, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: 0.5 }}>Verified Answer</h2>
                                    </div>
                                    <div style={{ fontSize: 14, color: '#f1f5f9', lineHeight: 1.6, marginBottom: 12 }} dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(mobileResult.data.answer) }} />
                                    {mobileResult.data.references && mobileResult.data.references.length > 0 && (
                                        <SourcesDropdown refs={mobileResult.data.references} />
                                    )}
                                    {mobileResult.data.focal_entity && (
                                        <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 16, marginTop: 16 }}>
                                            <button onClick={() => { setShowMobileGraph(true); navigate(`/entity/${encodeURIComponent(mobileResult.data.focal_entity)}`); }} style={{ width: '100%', padding: '10px', background: 'rgba(139,92,246,0.1)', color: '#a78bfa', border: '1px solid rgba(139,92,246,0.2)', borderRadius: 8, fontSize: 13, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                                                Explore {mobileResult.data.focal_entity} <ArrowRight size={14} />
                                            </button>
                                        </div>
                                    )}
                                </>
                            )}

                            {mobileResult.type === 'empty' && (
                                <div style={{ textAlign: 'center', padding: '20px 0', color: '#64748b' }}>
                                    <p style={{ fontSize: 14 }}>Nothing found in Truth for "{mobileResult.data.query}".</p>
                                </div>
                            )}
                        </div>
                    ) : null}
                </div>
            </div>
        );
    }

    // ── 3-Column Agentic Layout (Desktop or Mobile Canvas Mode)
    return (
        <div style={{ display: 'flex', height: '100%', width: '100%', overflow: 'hidden', background: COLORS.bg }}>

            {/* ── Left Column: Chat / Command Panel ────────────────────── */}
            {!isMobile && (
                <div className="glass-panel" style={{
                    width: 380, height: '100%', display: 'flex', flexDirection: 'column',
                    borderRight: '1px solid rgba(255,255,255,0.05)', backgroundColor: 'rgba(15, 23, 42, 0.6)'
                }}>
                {/* Header with Mode Toggle */}
                <div style={{ padding: '16px 16px 12px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: agentMode === 'agent' ? '#8b5cf6' : '#10b981', boxShadow: `0 0 8px ${agentMode === 'agent' ? '#8b5cf6' : '#10b981'}` }} />
                        <span style={{ fontWeight: 600, fontSize: 14, color: '#e2e8f0' }}>
                            {agentMode === 'agent' ? 'AI Agent' : 'Graph Search'}
                        </span>
                    </div>
                    {/* Mode Toggle Pill */}
                    <div style={{
                        display: 'flex', background: 'rgba(0,0,0,0.3)', borderRadius: 20,
                        padding: 3, border: '1px solid rgba(255,255,255,0.08)'
                    }}>
                        <button
                            onClick={() => setAndPersistMode('search')}
                            style={{
                                padding: '4px 12px', borderRadius: 16, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                                background: agentMode === 'search' ? '#10b981' : 'transparent',
                                color: agentMode === 'search' ? 'white' : '#64748b',
                                transition: 'all 0.2s', display: 'flex', alignItems: 'center', gap: 4
                            }}>
                            <Search size={11} /> Search
                        </button>
                        <button
                            onClick={() => setAndPersistMode('agent')}
                            style={{
                                padding: '4px 12px', borderRadius: 16, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                                background: agentMode === 'agent' ? '#8b5cf6' : 'transparent',
                                color: agentMode === 'agent' ? 'white' : '#64748b',
                                transition: 'all 0.2s', display: 'flex', alignItems: 'center', gap: 4
                            }}>
                            <Bot size={11} /> AI Agent
                        </button>
                    </div>
                </div>

                {/* Slash command hint when in agent mode */}
                {agentMode === 'agent' && messages.length === 0 && (
                    <div style={{ padding: '10px 16px', background: 'rgba(139,92,246,0.05)', borderBottom: '1px solid rgba(139,92,246,0.1)' }}>
                        <div style={{ fontSize: 11, color: '#7c3aed', fontWeight: 600, marginBottom: 4 }}>QUICK COMMANDS</div>
                        <div style={{ fontSize: 11, color: '#64748b', lineHeight: 1.8 }}>
                            <code style={{ color: '#a78bfa' }}>/explore NASA</code> · <code style={{ color: '#a78bfa' }}>/article SpaceX</code> · <code style={{ color: '#a78bfa' }}>/facts Einstein</code> · <code style={{ color: '#a78bfa' }}>/close</code>
                        </div>
                    </div>
                )}

                {/* Chat History */}
                <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
                    {messages.length === 0 && (
                        <div style={{ textAlign: 'center', padding: '40px 20px', color: '#475569' }}>
                            <Search size={32} color="#334155" style={{ marginBottom: 12 }} />
                            <div style={{ fontSize: 13 }}>
                                {agentMode === 'agent'
                                    ? 'Ask Truth anything. The AI will navigate the facts for you.'
                                    : 'Truth it by typing an entity name or topic.'}
                            </div>
                        </div>
                    )}
                    {messages.map((msg, i) => {
                        const isUser = msg.role === 'user';
                        const refs = msg.references || [];
                        return (
                            <div key={i} style={{ marginBottom: 20, display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start' }}>
                                <div style={{ fontSize: 10, color: '#475569', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.8, display: 'flex', alignItems: 'center', gap: 4 }}>
                                    {isUser ? 'You' : (<><Bot size={10} /> Truth Agent</>)}
                                </div>
                                <div style={{
                                    padding: '11px 14px', borderRadius: isUser ? '16px 16px 4px 16px' : '4px 16px 16px 16px',
                                    maxWidth: '92%', fontSize: 13.5, lineHeight: 1.65,
                                    background: isUser ? 'linear-gradient(135deg, #3b82f6, #8b5cf6)' : 'rgba(255,255,255,0.07)',
                                    border: isUser ? 'none' : '1px solid rgba(255,255,255,0.06)',
                                    color: '#f1f5f9'
                                }}
                                    dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(msg.content) }}
                                />
                                {/* Citation Sources Section */}
                                {!isUser && refs.length > 0 && (
                                    <SourcesDropdown refs={refs} focalEntity={msg.focal_entity} onOpenArticle={() => {
                                        if (msg.focal_entity) {
                                            setSelectedNode({ type: 'Entity', role: 'focal', label: msg.focal_entity });
                                            setInspectorTab('article');
                                        }
                                    }} />
                                )}
                                {/* Quick action buttons for agent response */}
                                {!isUser && msg.focal_entity && (
                                    <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                                        <button
                                            onClick={() => {
                                                setSelectedNode({ type: 'Entity', role: 'focal', label: msg.focal_entity });
                                                setInspectorTab('article');
                                            }}
                                            style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)', color: '#60a5fa', borderRadius: 8, padding: '4px 10px', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                                            <BookOpen size={10} /> Read Article
                                        </button>
                                        <button
                                            onClick={() => {
                                                setSelectedNode({ type: 'Entity', role: 'focal', label: msg.focal_entity });
                                                setInspectorTab('facts');
                                            }}
                                            style={{ background: 'rgba(139,92,246,0.1)', border: '1px solid rgba(139,92,246,0.2)', color: '#a78bfa', borderRadius: 8, padding: '4px 10px', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                                            <FileText size={10} /> Raw Facts
                                        </button>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                    {isAsking && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#8b5cf6', fontSize: 13 }}>
                            <Loader2 size={16} className="spin" /> Analyzing graph evidence...
                        </div>
                    )}
                    <div ref={chatEndRef} />
                </div>

                {/* Chat Input Container */}
                <div style={{ padding: 20, borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                    <form onSubmit={handleChatSubmit} style={{ position: 'relative' }}>
                        <div className="glass-panel" style={{
                            display: 'flex', alignItems: 'center', padding: '6px 12px',
                            borderRadius: 24, background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)'
                        }}>
                            <input
                                type="text"
                                value={query}
                                onChange={e => setQuery(e.target.value)}
                                placeholder="Ask a follow up..."
                                disabled={isAsking}
                                style={{
                                    flex: 1, background: 'transparent', border: 'none', color: '#fff',
                                    padding: '8px', fontSize: 14, outline: 'none'
                                }}
                            />
                            <button type="submit" disabled={isAsking || !query.trim()} style={{
                                background: query.trim() ? '#8b5cf6' : 'transparent',
                                color: query.trim() ? 'white' : '#64748b',
                                border: 'none', padding: '6px', borderRadius: '50%',
                                cursor: query.trim() ? 'pointer' : 'default',
                                display: 'flex', alignItems: 'center', justifyContent: 'center'
                            }}>
                                {isAsking ? <Loader2 size={16} className="spin" /> : <Search size={16} />}
                            </button>
                        </div>
                    </form>
                </div>
            </div>
            )}

            {/* ── Center Column: Graph Canvas ──────────────────────────────── */}
            <div style={{ flex: 1, position: 'relative' }}>

                {/* Mobile Back Button */}
                {isMobile && (
                    <button onClick={() => setShowMobileGraph(false)} className="glass-panel" style={{
                        position: 'absolute', top: 20, left: 20, zIndex: 10,
                        display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px',
                        borderRadius: 20, border: 'none', cursor: 'pointer', color: '#fff',
                        background: 'rgba(15,23,42,0.8)'
                    }}>
                        <ChevronLeft size={16} /> <span style={{fontSize: 13, fontWeight: 500}}>Back to Results</span>
                    </button>
                )}

                {/* Controls (Top Right of graph) */}
                <div style={{ position: 'absolute', top: 20, right: 20, zIndex: 10, display: 'flex', gap: 10 }}>
                    <button onClick={handleCycleScope} className="glass-panel" style={{
                        display: 'flex', alignItems: 'center', gap: 8, padding: '8px 16px',
                        borderRadius: 20, border: 'none', cursor: 'pointer', color: '#fff',
                        background: scope === 'both' ? '#0f766e' : scope === 'external' ? '#0369a1' : 'rgba(255,255,255,0.1)'
                    }}>
                        <Globe size={16} />
                        <span style={{ fontSize: 13, fontWeight: 500 }}>
                            Scope: {scope === 'internal' ? 'Internal VPC' : scope === 'external' ? 'External Master' : 'Hybrid (Both)'}
                        </span>
                    </button>

                    <button onClick={handleToggleShowAll} className="glass-panel" style={{
                        display: 'flex', alignItems: 'center', gap: 8, padding: '8px 16px',
                        borderRadius: 20, border: 'none', cursor: 'pointer', color: '#fff',
                        background: showAll ? '#7c3aed' : 'rgba(255,255,255,0.1)'
                    }}>
                        {showAll ? <Eye size={16} /> : <EyeOff size={16} />}
                        <span style={{ fontSize: 13, fontWeight: 500 }}>
                            {showAll ? 'Hide History' : 'Show History'}
                        </span>
                    </button>
                </div>

                {elements.length === 0 && !loading && !isAsking && (
                    <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#64748b' }}>
                        No entity context matching the conversation found.
                    </div>
                )}

                {elements.length > 0 && (
                    <CytoscapeComponent
                        elements={elements}
                        style={{ width: '100%', height: '100%' }}
                        stylesheet={CY_STYLE}
                        layout={LAYOUT}
                        cy={attachCyEvents}
                        wheelSensitivity={1}
                    />
                )}

                {/* Hover tooltip */}
                {hoveredNode && !selectedNode && (
                    <div className="hover-tooltip" style={{ left: hoveredNode.x + 18, top: hoveredNode.y - 36, position: 'absolute', zIndex: 50 }}>
                        <div className="tooltip-title" style={{ fontSize: 12, fontWeight: 600, color: '#f8fafc', marginBottom: 2 }}>
                            {hoveredNode.data.type === 'Evidence' ? 'Evidence' : hoveredNode.data.type === 'Entity' ? 'Entity' : `${hoveredNode.data.predicate}`}
                        </div>
                        {hoveredNode.data.type === 'Claim' && (
                            <div className="tooltip-text" style={{ fontSize: 11, color: '#cbd5e1' }}>
                                {hoveredNode.data.subject} → {hoveredNode.data.object}
                            </div>
                        )}
                        {hoveredNode.data.type === 'Evidence' && hoveredNode.data.raw_text && (
                            <div className="tooltip-text" style={{ fontSize: 10, color: '#94a3b8', fontStyle: 'italic' }}>
                                "{hoveredNode.data.raw_text.slice(0, 80)}…"
                            </div>
                        )}
                        {hoveredNode.data.score != null && hoveredNode.data.type === 'Claim' && (
                            <div className="tooltip-score" style={{ marginTop: 4, fontSize: 10, color: '#10b981' }}>Score: {Number(hoveredNode.data.score).toFixed(2)}</div>
                        )}
                    </div>
                )}

                {/* Legend (Now Bottom-Left) */}
                <div className="legend glass-panel" style={{
                    position: 'absolute', bottom: 20, left: 20, zIndex: 10,
                    padding: 16, borderRadius: 12, fontSize: 11, color: '#cbd5e1'
                }}>
                    <div style={{ fontWeight: 600, color: '#f8fafc', marginBottom: 12, fontSize: 12, letterSpacing: 0.5, textTransform: 'uppercase' }}>Graph Legend</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                                <div style={{ width: 12, height: 12, borderRadius: '50%', background: COLORS.entityFocal }} /> Focal Entity
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                                <div style={{ width: 10, height: 10, borderRadius: '50%', background: COLORS.entityLinked }} /> Linked Entity
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <div style={{ width: 10, height: 10, transform: 'rotate(45deg)', background: COLORS.evidence }} /> Evidence
                            </div>
                        </div>
                        <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                                <div style={{ width: 14, height: 6, borderRadius: 2, background: COLORS.claimActive }} /> Active Claim
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                                <div style={{ width: 14, height: 6, borderRadius: 2, background: COLORS.claimDisputed }} /> Disputed
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <div style={{ width: 14, height: 6, borderRadius: 2, background: COLORS.claimSuperseded }} /> Superseded
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* ── Right Column: Inspector Sidebar ──────────────────────────── */}
            {selectedNode && (
                <div className="inspector-sidebar glass-panel" style={{
                    position: 'fixed',
                    top: 56,           /* below the app header */
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
                        <button onClick={() => setSelectedNode(null)} style={{
                            background: 'transparent', border: 'none', color: '#94a3b8',
                            fontSize: 20, cursor: 'pointer', padding: 0
                        }}>×</button>
                    </div>

                    <div className="inspector-body" style={{ padding: 20, overflowY: 'auto', height: 'calc(100% - 65px)' }}>

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
                                    <button onClick={() => {
                                        let mdHtml = `<div style="font-family: system-ui, sans-serif; padding: 40px; max-width: 800px; margin: 0 auto; color: #1e293b;">`;
                                        mdHtml += `<h1 style="border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; color: #0f172a;">Research Report: ${selectedNode.label}</h1>`;
                                        mdHtml += `<h2 style="color: #334155; margin-top: 30px;">Overview</h2>`;
                                        
                                        if (entityArticle?.article) {
                                            if (typeof entityArticle.article === 'string') {
                                                mdHtml += `<p style="line-height: 1.6;">${entityArticle.article}</p>`;
                                            } else {
                                                Object.entries(entityArticle.article).forEach(([k,v]) => {
                                                    if (k !== '_references') mdHtml += `<h3 style="color: #475569;">${k}</h3><p style="line-height: 1.6;">${v.content}</p>`;
                                                });
                                            }
                                        } else {
                                            mdHtml += `<p style="line-height: 1.6; font-style: italic;">No synthesized article available.</p>`;
                                        }
                                        
                                        mdHtml += `<h2 style="color: #334155; margin-top: 30px; border-bottom: 1px solid #e2e8f0; padding-bottom: 5px;">Verified Facts & Citations</h2><ul style="line-height: 1.6;">`;
                                        if (entityFacts?.claims) {
                                            const active = entityFacts.claims.filter(c => c.is_current);
                                            if (active.length > 0) {
                                                active.forEach(c => {
                                                    const pred = (c.predicate || '').replace(/_/g, ' ').toLowerCase();
                                                    mdHtml += `<li style="margin-bottom: 8px;"><strong>${c.subject}</strong> ${pred} <strong>${c.object}</strong>. `;
                                                    mdHtml += `<span style="color: #64748b; font-size: 0.9em;">(Source: <em>${c.source_name || 'Unknown'}</em>${c.publish_date ? `, ${new Date(c.publish_date).toLocaleDateString()}` : ''})</span></li>`;
                                                });
                                            } else {
                                                mdHtml += `<li>No active facts found.</li>`;
                                            }
                                        }
                                        mdHtml += `</ul>`;
                                        
                                        mdHtml += `<h2 style="color: #334155; margin-top: 30px; border-bottom: 1px solid #e2e8f0; padding-bottom: 5px;">Fact Evolution Timeline</h2><ul style="line-height: 1.6;">`;
                                        if (entityFacts?.claims) {
                                            const superseded = entityFacts.claims.filter(c => !c.is_current);
                                            if (superseded.length > 0) {
                                                superseded.forEach(c => {
                                                    const pred = (c.predicate || '').replace(/_/g, ' ').toLowerCase();
                                                    mdHtml += `<li style="margin-bottom: 8px;"><span style="background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; font-weight: bold; color: #64748b; margin-right: 8px;">SUPERSEDED</span>`;
                                                    mdHtml += `<strong>${c.subject}</strong> ${pred} <strong>${c.object}</strong>. <span style="color: #64748b; font-size: 0.9em;">(Valid: ${c.valid_from ? new Date(c.valid_from).toLocaleDateString() : 'Unknown'} &mdash; ${c.valid_until ? new Date(c.valid_until).toLocaleDateString() : 'Unknown'})</span></li>`;
                                                });
                                            } else {
                                                mdHtml += `<li style="color: #64748b; font-style: italic;">No historical fact evolution found.</li>`;
                                            }
                                        }
                                        mdHtml += `</ul></div>`;

                                        const printWindow = window.open('', '_blank');
                                        printWindow.document.write(`
                                            <!DOCTYPE html>
                                            <html>
                                                <head>
                                                    <title>Research Report - ${selectedNode.label}</title>
                                                </head>
                                                <body>${mdHtml}</body>
                                            </html>
                                        `);
                                        printWindow.document.close();
                                        // Wait for resources to load before triggering print
                                        setTimeout(() => {
                                            printWindow.print();
                                        }, 250);
                                    }} style={{
                                        background: 'rgba(16, 185, 129, 0.2)', border: '1px solid #10b981', color: '#34d399',
                                        borderRadius: 6, padding: '4px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 500
                                    }}>
                                        <FileText size={14} /> Export Research
                                    </button>
                                </div>

                                {/* Tab Navigation */}
                                <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: 8 }}>
                                    <button
                                        onClick={() => setInspectorTab('article')}
                                        style={{
                                            background: inspectorTab === 'article' ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                                            color: inspectorTab === 'article' ? '#60a5fa' : '#94a3b8',
                                            border: 'none', padding: '6px 12px', borderRadius: 6, cursor: 'pointer',
                                            display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 500,
                                            transition: 'all 0.2s'
                                        }}>
                                        <BookOpen size={14} /> Article
                                    </button>
                                    <button
                                        onClick={() => setInspectorTab('facts')}
                                        style={{
                                            background: inspectorTab === 'facts' ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                                            color: inspectorTab === 'facts' ? '#60a5fa' : '#94a3b8',
                                            border: 'none', padding: '6px 12px', borderRadius: 6, cursor: 'pointer',
                                            display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 500,
                                            transition: 'all 0.2s'
                                        }}>
                                        <FileText size={14} /> Raw Facts
                                    </button>
                                    <button
                                        onClick={() => setInspectorTab('timeline')}
                                        style={{
                                            background: inspectorTab === 'timeline' ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                                            color: inspectorTab === 'timeline' ? '#60a5fa' : '#94a3b8',
                                            border: 'none', padding: '6px 12px', borderRadius: 6, cursor: 'pointer',
                                            display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 500,
                                            transition: 'all 0.2s'
                                        }}>
                                        <Clock size={14} /> Timeline
                                    </button>
                                </div>

                                {/* Tab Content */}
                                {inspectorTab === 'article' && (
                                    <ArticleRenderer 
                                        articleObj={entityArticle} 
                                        onEntityClick={async (entityName) => {
                                            if (entityName !== selectedNode.label) {
                                                await loadNeighborhood(entityName);
                                            }
                                        }} 
                                    />
                                )}

                                {inspectorTab === 'facts' && (
                                    <div>
                                        {factsLoading && (
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#64748b', fontSize: 13, padding: '20px 0' }}>
                                                <Loader2 size={14} className="spin" /> Loading facts…
                                            </div>
                                        )}
                                        {!factsLoading && entityFacts && (() => {
                                            const active = (entityFacts.claims || []).filter(c => c && c.id);
                                            if (active.length === 0) return (
                                                <div style={{ color: '#64748b', fontSize: 13, padding: '20px 0' }}>No claims found for this entity yet.</div>
                                            );
                                            return (
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                                                    {active.map((claim, i) => {
                                                        const rawId = claim.id || '';
                                                        const predicateWords = (claim.predicate || '').replace(/_/g, ' ').toLowerCase();
                                                        const citation = `${claim.subject} ${predicateWords} ${claim.object}.${claim.source_name ? ` ${claim.source_name}` : ''}${claim.publish_date ? `, ${new Date(claim.publish_date).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}` : ''}. ${window.location.origin}/claim/${rawId}`;
                                                        const lc = claim.lifecycle || 'ACTIVE';
                                                        const lcColor = lc === 'ACTIVE' ? '#10b981' : lc === 'DISPUTED' ? '#f59e0b' : '#64748b';
                                                        const lcBg = lc === 'ACTIVE' ? 'rgba(16,185,129,0.12)' : lc === 'DISPUTED' ? 'rgba(245,158,11,0.12)' : 'rgba(100,116,139,0.12)';
                                                        return (
                                                            <div key={rawId || i} style={{ padding: '12px 14px', background: 'rgba(0,0,0,0.25)', borderRadius: 10, border: '1px solid rgba(255,255,255,0.06)' }}>
                                                                <div style={{ fontSize: 13, color: '#f1f5f9', lineHeight: 1.5, marginBottom: 8 }}>
                                                                    <strong>{claim.subject}</strong>{' '}
                                                                    <span style={{ color: '#8b5cf6' }}>{predicateWords}</span>{' '}
                                                                    <strong>{claim.object}</strong>
                                                                </div>
                                                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                                                    <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 10, fontWeight: 600, background: lcBg, color: lcColor }}>{lc}</span>
                                                                    {claim.source_name && <span style={{ fontSize: 11, color: '#64748b' }}>{claim.source_name}</span>}
                                                                    {claim.publish_date && <span style={{ fontSize: 11, color: '#475569' }}>{new Date(claim.publish_date).toLocaleDateString()}</span>}
                                                                    <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                                                                        <button
                                                                            onClick={() => navigator.clipboard.writeText(citation)}
                                                                            title="Copy APA-style citation"
                                                                            style={{ background: 'rgba(139,92,246,0.1)', border: '1px solid rgba(139,92,246,0.2)', color: '#a78bfa', borderRadius: 6, padding: '3px 9px', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}
                                                                        >
                                                                            <LinkIcon size={10} /> Cite
                                                                        </button>
                                                                        {claim.source_url && (
                                                                            <a href={claim.source_url} target="_blank" rel="noreferrer" style={{ fontSize: 11, color: '#60a5fa', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 3, padding: '3px 9px', background: 'rgba(59,130,246,0.08)', borderRadius: 6, border: '1px solid rgba(59,130,246,0.2)' }}>
                                                                                <ExternalLink size={10} /> Source
                                                                            </a>
                                                                        )}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            );
                                        })()}
                                        {!factsLoading && !entityFacts && (
                                            <div style={{ color: '#64748b', fontSize: 13, padding: '20px 0' }}>Switch to this tab after selecting an entity.</div>
                                        )}
                                    </div>
                                )}
                                
                                {inspectorTab === 'timeline' && (
                                    <div>
                                        {factsLoading && (
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#64748b', fontSize: 13, padding: '20px 0' }}>
                                                <Loader2 size={14} className="spin" /> Loading timeline…
                                            </div>
                                        )}
                                        {!factsLoading && entityFacts && (() => {
                                            const all = (entityFacts.claims || []).filter(c => c && c.id);
                                            if (all.length === 0) return (
                                                <div style={{ color: '#64748b', fontSize: 13, padding: '20px 0' }}>No fact history found for this entity.</div>
                                            );
                                            // Group by predicate, sort each group oldest→newest
                                            const byPred = {};
                                            for (const c of all) {
                                                const k = c.predicate || 'unknown';
                                                if (!byPred[k]) byPred[k] = [];
                                                byPred[k].push(c);
                                            }
                                            for (const k of Object.keys(byPred)) {
                                                byPred[k].sort((a, b) => new Date(a.valid_from || 0) - new Date(b.valid_from || 0));
                                            }
                                            const groups = Object.entries(byPred);
                                            return (
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                                                    {groups.map(([pred, versions]) => (
                                                        <div key={pred}>
                                                            <div style={{ fontSize: 11, fontWeight: 700, color: '#7c3aed', textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
                                                                {pred.replace(/_/g, ' ')}
                                                                <span style={{ fontWeight: 400, color: '#334155' }}>· {versions.length} version{versions.length > 1 ? 's' : ''}</span>
                                                            </div>
                                                            <div style={{ paddingLeft: 16, borderLeft: '2px solid rgba(124,58,237,0.25)' }}>
                                                                {versions.map((v, vi) => (
                                                                    <div key={v.id || vi} style={{ marginBottom: vi < versions.length - 1 ? 14 : 0, position: 'relative' }}>
                                                                        <div style={{ position: 'absolute', left: -21, top: 5, width: 8, height: 8, borderRadius: '50%', background: v.is_current ? '#8b5cf6' : '#1e293b', border: `2px solid ${v.is_current ? '#8b5cf6' : '#334155'}` }} />
                                                                        <div style={{ fontSize: 13, color: v.is_current ? '#f1f5f9' : '#64748b', lineHeight: 1.5 }}>
                                                                            <strong style={{ color: v.is_current ? '#f1f5f9' : '#475569' }}>{v.object}</strong>
                                                                        </div>
                                                                        <div style={{ display: 'flex', gap: 8, marginTop: 3, alignItems: 'center', flexWrap: 'wrap' }}>
                                                                            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, fontWeight: 600, background: v.is_current ? 'rgba(139,92,246,0.15)' : 'rgba(30,41,59,0.8)', color: v.is_current ? '#a78bfa' : '#475569' }}>
                                                                                {v.is_current ? '● CURRENT' : v.lifecycle || 'SUPERSEDED'}
                                                                            </span>
                                                                            {v.valid_from && <span style={{ fontSize: 10, color: '#475569' }}>{new Date(v.valid_from).toLocaleDateString()}</span>}
                                                                            {v.source_name && <span style={{ fontSize: 10, color: '#334155' }}>{v.source_name}</span>}
                                                                            {v.source_url && <a href={v.source_url} target="_blank" rel="noreferrer" style={{ fontSize: 10, color: '#3b82f6', textDecoration: 'none' }}>↗</a>}
                                                                        </div>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            );
                                        })()}
                                        {!factsLoading && !entityFacts && (
                                            <div style={{ color: '#64748b', fontSize: 13, padding: '20px 0' }}>Select an entity to view its fact evolution.</div>
                                        )}
                                    </div>
                                )}
                            </div>
                        )}

                        {selectedNode.type === 'Claim' && (
                            <>
                                <div style={{ marginBottom: 24 }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                                        <h3 style={{ fontSize: 12, color: '#94a3b8', textTransform: 'uppercase', margin: 0 }}>Full Statement</h3>
                                        <button onClick={() => {
                                            const rawId = (selectedNode.id || '').replace(/^c:/, '');
                                            const pred = (selectedNode.predicate || '').replace(/_/g, ' ').toLowerCase();
                                            const citation = `${selectedNode.subject} ${pred} ${selectedNode.object}.${selectedNode.source_name ? ` ${selectedNode.source_name}` : ''}${selectedNode.publish_date ? `, ${new Date(selectedNode.publish_date).toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}` : ''}. ${window.location.origin}/claim/${rawId}`;
                                            navigator.clipboard.writeText(citation);
                                            alert('Fact Citation copied to clipboard!');
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

                                {selectedNode.media_items && selectedNode.media_items.length > 0 && (
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
                                                        <span style={{ fontSize: 11, color: '#94a3b8' }}>
                                                            {Math.round(m.synthetic_probability * 100)}% Synthetic
                                                        </span>
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
                                        <div style={{
                                            fontSize: 13, lineHeight: 1.6, color: '#cbd5e1', fontStyle: 'italic',
                                            borderLeft: '2px solid #3b82f6', paddingLeft: 12
                                        }}>
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
            )}
        </div>
    );
}
