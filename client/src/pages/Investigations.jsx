import React, { useState, useEffect, useRef } from 'react';
import { 
    Activity, Play, Pause, Trash2, Search, Crosshair, Target, Clock, AlertTriangle, 
    ChevronRight, CheckCircle2, ChevronDown, List, FolderSearch, Plus, X, BookOpen, Network, Download
} from 'lucide-react';
import { api } from '../api';
import ArticleRenderer from '../components/ArticleRenderer';
import CytoscapeComponent from 'react-cytoscapejs';
import cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';

cytoscape.use(fcose);

export const CY_STYLE = [
    { selector: 'node[type="Entity"]', style: { width: 60, height: 60, shape: 'ellipse', 'background-color': '#3b82f6', 'border-width': 2, 'border-color': '#93c5fd', label: 'data(label)', color: '#ffffff', 'font-size': 12, 'text-valign': 'center', 'text-halign': 'center', 'text-wrap': 'wrap', 'text-max-width': 50 } },
    { selector: 'node[type="Claim"]', style: { width: 100, height: 30, shape: 'round-rectangle', 'background-color': '#10b981', label: 'data(label)', color: '#ffffff', 'font-size': 10, 'text-valign': 'center', 'text-halign': 'center', 'text-wrap': 'wrap', 'text-max-width': 90 } },
    { selector: 'node[type="Evidence"]', style: { width: 20, height: 20, shape: 'diamond', 'background-color': '#a78bfa' } },
    { selector: 'edge', style: { width: 1.5, 'line-color': '#64748b', 'target-arrow-color': '#64748b', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier' } },
    { selector: 'node:selected', style: { 'border-width': 4, 'border-color': '#ffffff' } }
];

function Investigations() {
    const [investigations, setInvestigations] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [showNewModal, setShowNewModal] = useState(false);
    
    const [selectedInvId, setSelectedInvId] = useState(null);
    const [selectedInvestigation, setSelectedInvestigation] = useState(null);
    const [selectedInvestigationLoading, setSelectedInvestigationLoading] = useState(false);
    const [leads, setLeads] = useState([]);
    const [leadsLoading, setLeadsLoading] = useState(false);

    // Tabs
    const [activeTab, setActiveTab] = useState('overview'); // overview | report | graph
    const [graphElements, setGraphElements] = useState([]);
    const [graphLoading, setGraphLoading] = useState(false);
    const cyRef = useRef(null);

    // New investigation form state
    const [target, setTarget] = useState('');
    const [goalType, setGoalType] = useState('PROFILING');
    const [maxLeads, setMaxLeads] = useState(100);

    const fetchInvestigations = async () => {
        try {
            const data = await api.listInvestigations();
            setInvestigations(data);
            if (selectedInvId) {
                const current = data.find(inv => inv.id === selectedInvId);
                if (current) {
                    setSelectedInvestigation(current);
                }
            }
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const fetchInvestigation = async (id) => {
        if (!id) return;
        setSelectedInvestigationLoading(true);
        try {
            const data = await api.getInvestigation(id);
            setSelectedInvestigation(data);
        } catch (err) {
            console.error(err);
        } finally {
            setSelectedInvestigationLoading(false);
        }
    };

    useEffect(() => {
        fetchInvestigations();
        const intv = setInterval(fetchInvestigations, 5000);
        return () => clearInterval(intv);
    }, []);

    useEffect(() => {
        if (selectedInvId) {
            fetchLeads(selectedInvId);
            fetchInvestigation(selectedInvId);
            setActiveTab('overview');
        } else {
            setLeads([]);
            setSelectedInvestigation(null);
        }
    }, [selectedInvId]);

    useEffect(() => {
        if (activeTab === 'graph' && selectedInvestigation?.target) {
            fetchGraph(selectedInvestigation.target);
        }
    }, [activeTab, selectedInvestigation?.target]);

    const fetchGraph = async (targetEntity) => {
        setGraphLoading(true);
        try {
            const res = await api.search(targetEntity, 'entity');
            setGraphElements(res.elements || []);
        } catch (err) {
            console.error('Graph fetch error', err);
        } finally {
            setGraphLoading(false);
        }
    };

    const fetchLeads = async (id) => {
        setLeadsLoading(true);
        try {
            const data = await api.getInvestigationLeads(id);
            setLeads(data);
        } catch (err) {
            console.error(err);
        } finally {
            setLeadsLoading(false);
        }
    };

    const downloadSummary = () => {
        const summary = selectedInvestigation?.report?.ch0_executive_summary?.content;
        if (!summary) return;
        const blob = new Blob([summary], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `investigation-${selectedInvestigation.id}-summary.txt`;
        anchor.click();
        URL.revokeObjectURL(url);
    };

    const buildReportText = (report) => {
        if (!report || typeof report !== 'object') return '';
        const lines = [
            'INVESTIGATION DOSSIER',
            '',
            `Investigation ID: #${selectedInvestigation?.id || 'unknown'}`,
            `Target: ${selectedInvestigation?.target || 'Unknown'}`,
            '',
        ];

        const chapterEntries = Object.entries(report)
            .filter(([key]) => !key.startsWith('_'))
            .sort(([, a], [, b]) => (a?.order ?? 999) - (b?.order ?? 999));

        for (const [key, chapter] of chapterEntries) {
            const content = chapter?.content || chapter?.text || chapter?.body || '';
            if (!content) continue;
            const title = chapter?.title || key;
            lines.push(title.toUpperCase());
            lines.push('');
            lines.push(content.trim());
            lines.push('');
        }

        const refs = Array.isArray(report._references) ? report._references : [];
        if (refs.length > 0) {
            lines.push('REFERENCES');
            lines.push('');
            refs.forEach((ref, index) => {
                const src = ref?.source_name || ref?.source || ref?.original_source || 'Unknown';
                const url = ref?.source_url || ref?.original_url || '';
                const title = ref?.article_title || '';
                const cid = ref?.claim_id || ref?.uuid || ref?.id || index + 1;
                lines.push(`- [${cid}] ${src}${title ? ` — ${title}` : ''}${url ? ` (${url})` : ''}`);
            });
        }

        return lines.join('\n').trim();
    };

    const downloadReport = () => {
        const report = selectedInvestigation?.report;
        const text = buildReportText(report);
        if (!text) return;

        const doc = window.open('', '_blank', 'width=800,height=1000');
        if (!doc) return;

        doc.document.write(`<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Investigation Report</title>
    <style>
      body { font-family: Arial, sans-serif; color: #111; line-height: 1.5; margin: 24px; }
      h1, h2, h3 { color: #0f172a; }
      h1 { font-size: 24px; margin-bottom: 8px; }
      h2 { font-size: 16px; margin-top: 24px; margin-bottom: 8px; }
      p { margin: 0 0 10px; }
      .meta { color: #475569; margin-bottom: 16px; }
      .page-break { page-break-after: always; }
    </style>
  </head>
  <body>
    <h1>Investigation Dossier</h1>
    <div class="meta">Investigation ID: #${selectedInvestigation?.id || 'unknown'}<br />Target: ${selectedInvestigation?.target || 'Unknown'}</div>
    ${text.replace(/\n/g, '<br />')}
  </body>
</html>`);
        doc.document.close();
        doc.focus();
        setTimeout(() => {
            doc.print();
        }, 250);
    };

    const renderCompletedReport = () => {
        if (selectedInvestigation?.status !== 'COMPLETED') return null;

        const report = selectedInvestigation?.report || {};
        const findings = selectedInvestigation?.findings || {};
        const summary = report?.ch0_executive_summary?.content;
        const reportFile = findings?.report_file;
        const reportLink = reportFile ? `/api/investigations/${selectedInvestigation.id}/report/file` : null;

        const findingsItems = [
            ['Goal Achieved', findings?.goal_achieved ?? 'N/A'],
            ['Termination Reason', findings?.termination_reason ?? 'N/A'],
            ['Last Harvest Summary', findings?.last_harvest_summary ?? 'N/A'],
            ['Completed At', findings?.completed_at ?? 'N/A'],
            ['Report File', reportFile ? reportFile : 'Not available'],
        ];

        return (
            <div style={{ background: '#111827', border: '1px solid #334155', borderRadius: '12px', padding: '16px', marginBottom: '16px', overflow: 'hidden' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginBottom: '12px', alignItems: 'flex-start' }}>
                    <div>
                        <div style={{ fontSize: '16px', fontWeight: '700', color: '#f8fafc' }}>Final Investigation Report</div>
                        <div style={{ marginTop: '6px', fontSize: '13px', color: '#94a3b8' }}>Sealed report content and findings for this completed investigation.</div>
                    </div>
                    <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        {reportLink && (
                            <a
                                href={reportLink}
                                target="_blank"
                                rel="noreferrer"
                                style={{
                                    padding: '10px 16px', background: '#2563eb', color: 'white', borderRadius: '10px', textDecoration: 'none', fontWeight: 700,
                                }}
                            >
                                Download Report
                            </a>
                        )}
                        {summary && (
                            <button
                                onClick={downloadSummary}
                                type="button"
                                style={{
                                    padding: '10px 16px', background: '#047857', color: 'white', borderRadius: '10px', border: 'none', fontWeight: 700, cursor: 'pointer'
                                }}
                            >
                                Export Summary
                            </button>
                        )}
                        {!reportLink && !summary && (
                            <span style={{ padding: '10px 16px', background: '#334155', color: '#94a3b8', borderRadius: '10px', fontSize: '12px' }}>
                                No downloadable report available
                            </span>
                        )}
                    </div>
                </div>

                {summary ? (
                    <div style={{ padding: '14px', background: '#0f172a', borderRadius: '10px', color: '#cbd5e1', lineHeight: '1.6', fontSize: '13px', whiteSpace: 'pre-wrap' }}>
                        {summary}
                    </div>
                ) : (
                    <div style={{ padding: '14px', background: '#0f172a', borderRadius: '10px', color: '#94a3b8', fontSize: '13px' }}>
                        Executive summary not available for this investigation.
                    </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px', marginTop: '16px' }}>
                    {findingsItems.map(([label, value]) => (
                        <div key={label} style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '10px', padding: '12px' }}>
                            <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>{label}</div>
                            <div style={{ fontSize: '14px', color: '#f8fafc', wordBreak: 'break-word' }}>{String(value)}</div>
                        </div>
                    ))}
                </div>
            </div>
        );
    };

    const handleStart = async (e) => {
        e.preventDefault();
        try {
            await api.startInvestigation(target, { goal_type: goalType, max_leads: parseInt(maxLeads) });
            setShowNewModal(false);
            setTarget('');
            fetchInvestigations();
        } catch (err) {
            alert(err.message);
        }
    };

    const handlePauseResume = async (inv) => {
        try {
            if (inv.status === 'ACTIVE') await api.pauseInvestigation(inv.id);
            else if (inv.status === 'PAUSED') await api.resumeInvestigation(inv.id);
            await fetchInvestigations();
            if (selectedInvId === inv.id) {
                await fetchInvestigation(inv.id);
            }
        } catch (err) {
            console.error(err);
        }
    };

    const handleDelete = async (id) => {
        if (!confirm('Delete this investigation? This cannot be undone.')) return;
        try {
            await api.deleteInvestigation(id);
            if (selectedInvId === id) setSelectedInvId(null);
            fetchInvestigations();
        } catch (err) {
            console.error(err);
        }
    };

    const getStatusColor = (status) => {
        switch (status) {
            case 'ACTIVE': return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
            case 'PAUSED': return 'text-amber-400 bg-amber-400/10 border-amber-400/20';
            case 'COMPLETED': return 'text-blue-400 bg-blue-400/10 border-blue-400/20';
            case 'FAILED': return 'text-red-400 bg-red-400/10 border-red-400/20';
            default: return 'text-gray-400 bg-gray-400/10 border-gray-400/20';
        }
    };

    return (
        <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto', display: 'flex', gap: '24px', height: '100%', minHeight: 0 }}>
            
            {/* Left Panel: Investigations List */}
            <div style={{ flex: '1', display: 'flex', flexDirection: 'column', gap: '16px', minWidth: '400px', minHeight: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h2 style={{ fontSize: '24px', fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <FolderSearch size={24} color="#3b82f6" /> OSINT Investigations
                    </h2>
                    <button 
                        onClick={() => setShowNewModal(true)}
                        style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 16px', background: 'var(--color-blue)', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: '600' }}
                    >
                        <Plus size={16} /> New Investigation
                    </button>
                </div>

                {error && <div style={{ padding: '12px', background: 'var(--color-red-alpha)', color: 'var(--color-red-light)', borderRadius: '8px' }}>{error}</div>}

                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', overflowY: 'auto' }}>
                    {loading ? (
                        <div style={{ color: '#94a3b8', padding: '20px' }}>Loading...</div>
                    ) : investigations.length === 0 ? (
                        <div style={{ padding: '40px', textAlign: 'center', color: '#64748b', background: '#0f172a', borderRadius: '12px', border: '1px solid #1e293b' }}>
                            <Crosshair size={48} style={{ margin: '0 auto 12px', opacity: 0.5 }} />
                            <h3>No active investigations</h3>
                            <p style={{ marginTop: '8px', fontSize: '14px' }}>Start a new OSINT investigation to begin collecting leads.</p>
                        </div>
                    ) : investigations.map(inv => (
                        <div 
                            key={inv.id} 
                            onClick={() => setSelectedInvId(inv.id)}
                            style={{ 
                                padding: '16px', background: selectedInvId === inv.id ? '#1e293b' : '#0f172a', 
                                borderRadius: '12px', border: `1px solid ${selectedInvId === inv.id ? '#3b82f6' : '#1e293b'}`,
                                cursor: 'pointer', transition: 'all 0.2s'
                            }}
                        >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                                <div>
                                    <div>
                                        <div style={{ fontWeight: 'bold', fontSize: '16px', color: '#f8fafc' }}>{inv.target}</div>
                                        <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '4px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                                            <Target size={12} /> {inv.goal_type}
                                        </div>
                                        {inv.last_summary && (
                                            <div style={{ marginTop: '8px', fontSize: '12px', color: '#cbd5e1', lineHeight: '1.4' }}>
                                                {inv.last_summary.length > 120 ? `${inv.last_summary.slice(0, 117)}...` : inv.last_summary}
                                            </div>
                                        )}
                                    </div>
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    <span className={`px-2 py-1 text-xs rounded-full border ${getStatusColor(inv.status)}`} style={{ padding: '4px 8px', borderRadius: '9999px', fontSize: '11px', fontWeight: 'bold' }}>
                                        {inv.status}
                                    </span>
                                </div>
                            </div>
                            
                            <div style={{ display: 'flex', gap: '16px', fontSize: '13px', color: '#cbd5e1' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                    <List size={14} color="#64748b" /> {inv.leads_explored} / {inv.max_leads} Explored
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                    <Activity size={14} color="#64748b" /> {inv.pending_leads} Pending
                                </div>
                            </div>

                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '12px', borderTop: '1px solid #1e293b', paddingTop: '12px' }}>
                                {(inv.status === 'ACTIVE' || inv.status === 'PAUSED') && (
                                    <button 
                                        onClick={(e) => { e.stopPropagation(); handlePauseResume(inv); }}
                                        style={{ background: 'transparent', border: '1px solid #334155', color: '#cbd5e1', padding: '6px 12px', borderRadius: '6px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px' }}
                                    >
                                        {inv.status === 'ACTIVE' ? <><Pause size={14} /> Pause</> : <><Play size={14} /> Resume</>}
                                    </button>
                                )}
                                <button 
                                    onClick={(e) => { e.stopPropagation(); handleDelete(inv.id); }}
                                    style={{ background: 'transparent', border: '1px solid #334155', color: '#ef4444', padding: '6px 12px', borderRadius: '6px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px' }}
                                >
                                    <Trash2 size={14} /> Delete
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            {/* Right Panel: Investigation Details & Leads */}
            <div style={{ flex: '2', background: '#0f172a', borderRadius: '12px', border: '1px solid #1e293b', display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
                {selectedInvId ? (
                    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1, overflow: 'hidden' }}>
                        <div style={{ padding: '20px', borderBottom: '1px solid #1e293b', background: '#1e293b' }}>
                            <h3 style={{ fontSize: '18px', fontWeight: 'bold' }}>Investigation Details</h3>
                            <p style={{ color: '#94a3b8', fontSize: '13px', marginTop: '4px' }}>Live status, summary, and lead queue for the selected investigation.</p>
                        </div>
                        <div style={{ flex: '1', display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto', padding: '20px', gap: '16px' }}>
                            <div style={{ background: '#111827', border: '1px solid #1e293b', borderRadius: '12px', padding: '16px' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', alignItems: 'flex-start' }}>
                                    <div>
                                        <div style={{ fontSize: '18px', fontWeight: '700', color: '#f8fafc' }}>
                                            {selectedInvestigation?.target || 'Investigation'}
                                        </div>
                                        <div style={{ marginTop: '8px', color: '#94a3b8', fontSize: '13px' }}>
                                            {selectedInvestigation?.goal_type} · {selectedInvestigation?.status}
                                            {selectedInvestigation?.goal_achieved === 'true' && ' · Goal Achieved'}
                                        </div>
                                    </div>
                                    <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                                        <div style={{ padding: '10px 14px', background: '#111827', border: '1px solid #334155', borderRadius: '10px', color: '#f8fafc' }}>
                                            <div style={{ fontSize: '12px', color: '#94a3b8' }}>Explored</div>
                                            <div style={{ fontSize: '16px', fontWeight: '700' }}>{selectedInvestigation?.leads_explored ?? 0}</div>
                                        </div>
                                        <div style={{ padding: '10px 14px', background: '#111827', border: '1px solid #334155', borderRadius: '10px', color: '#f8fafc' }}>
                                            <div style={{ fontSize: '12px', color: '#94a3b8' }}>Pending</div>
                                            <div style={{ fontSize: '16px', fontWeight: '700' }}>{selectedInvestigation?.pending_leads ?? 0}</div>
                                        </div>
                                        <div style={{ padding: '10px 14px', background: '#111827', border: '1px solid #334155', borderRadius: '10px', color: '#f8fafc' }}>
                                            <div style={{ fontSize: '12px', color: '#94a3b8' }}>Active Leads</div>
                                            <div style={{ fontSize: '16px', fontWeight: '700' }}>{selectedInvestigation?.active_leads ?? 0}</div>
                                        </div>
                                    </div>
                                </div>
                                {selectedInvestigation?.last_summary && (
                                    <div style={{ marginTop: '16px', color: '#cbd5e1', lineHeight: '1.6', fontSize: '13px' }}>
                                        <strong style={{ color: '#f8fafc' }}>Latest insight:</strong> {selectedInvestigation.last_summary}
                                    </div>
                                )}
                                {selectedInvestigation?.status === 'COMPLETED' && renderCompletedReport()}
                            </div>
                        </div>
                        {/* TAB BAR */}
                        <div style={{ display: 'flex', borderBottom: '1px solid #1e293b', background: '#0f172a', padding: '0 16px' }}>
                            <button
                                onClick={() => setActiveTab('overview')}
                                style={{ padding: '12px 16px', background: 'transparent', border: 'none', color: activeTab === 'overview' ? '#3b82f6' : '#94a3b8', borderBottom: activeTab === 'overview' ? '2px solid #3b82f6' : '2px solid transparent', cursor: 'pointer', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}
                            >
                                <List size={16} /> Overview & Leads
                            </button>
                            <button
                                onClick={() => setActiveTab('report')}
                                style={{ padding: '12px 16px', background: 'transparent', border: 'none', color: activeTab === 'report' ? '#3b82f6' : '#94a3b8', borderBottom: activeTab === 'report' ? '2px solid #3b82f6' : '2px solid transparent', cursor: 'pointer', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}
                            >
                                <BookOpen size={16} /> Live Report
                            </button>
                            <button
                                onClick={() => setActiveTab('graph')}
                                style={{ padding: '12px 16px', background: 'transparent', border: 'none', color: activeTab === 'graph' ? '#3b82f6' : '#94a3b8', borderBottom: activeTab === 'graph' ? '2px solid #3b82f6' : '2px solid transparent', cursor: 'pointer', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}
                            >
                                <Network size={16} /> Network Graph
                            </button>
                        </div>
                        
                        {/* TAB CONTENT */}
                        <div style={{ background: '#111827', padding: '16px', minHeight: 0, flex: 1, overflowY: 'auto' }}>
                            {activeTab === 'overview' && (
                                <>
                            {leadsLoading ? (
                                <div style={{ color: '#94a3b8' }}>Loading leads...</div>
                            ) : leads.length === 0 ? (
                                <div style={{ color: '#64748b' }}>No leads generated yet.</div>
                            ) : (
                                <div style={{ overflowX: 'auto' }}>
                                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                                        <thead>
                                            <tr style={{ color: '#94a3b8', borderBottom: '1px solid #334155', textAlign: 'left' }}>
                                                <th style={{ padding: '12px 8px' }}>Entity</th>
                                                <th style={{ padding: '12px 8px' }}>Type</th>
                                                <th style={{ padding: '12px 8px' }}>Status</th>
                                                <th style={{ padding: '12px 8px' }}>Priority</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {leads.map(lead => (
                                                <tr key={lead.id} style={{ borderBottom: '1px solid #1e293b' }}>
                                                    <td style={{ padding: '12px 8px', color: '#f8fafc', fontWeight: '500' }}>{lead.entity_name}</td>
                                                    <td style={{ padding: '12px 8px', color: '#94a3b8' }}>{lead.lead_type}</td>
                                                    <td style={{ padding: '12px 8px' }}>
                                                        <span style={{ 
                                                            fontSize: '11px', padding: '4px 8px', borderRadius: '4px',
                                                            background: lead.status === 'EXPLORED' ? 'rgba(16, 185, 129, 0.1)' : 
                                                                      lead.status === 'CLAIMED' ? 'rgba(59, 130, 246, 0.1)' : 'rgba(255, 255, 255, 0.05)',
                                                            color: lead.status === 'EXPLORED' ? '#34d399' : 
                                                                  lead.status === 'CLAIMED' ? '#60a5fa' : '#94a3b8'
                                                        }}>
                                                            {lead.status}
                                                        </span>
                                                    </td>
                                                    <td style={{ padding: '12px 8px', color: '#cbd5e1' }}>{lead.priority}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                                </>
                            )}
                            {activeTab === 'report' && (
                                <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, background: '#0f172a', borderRadius: '12px', border: '1px solid #1e293b', overflow: 'hidden' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', padding: '16px 18px', borderBottom: '1px solid #1e293b', background: 'rgba(15, 23, 42, 0.96)', position: 'sticky', top: 0, zIndex: 5 }}>
                                        <div>
                                            <div style={{ fontSize: '15px', fontWeight: 700, color: '#f8fafc' }}>Investigation Report</div>
                                            <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '2px' }}>Full chaptered report with live citations and references</div>
                                        </div>
                                        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                                            <button
                                                type="button"
                                                onClick={downloadReport}
                                                style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '8px 12px', borderRadius: '8px', border: '1px solid #334155', background: '#1d4ed8', color: '#fff', cursor: 'pointer', fontWeight: 600 }}
                                            >
                                                <Download size={14} /> Download Report
                                            </button>
                                            {selectedInvestigation?.report?.ch0_executive_summary?.content && (
                                                <button
                                                    type="button"
                                                    onClick={downloadSummary}
                                                    style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '8px 12px', borderRadius: '8px', border: '1px solid #334155', background: '#047857', color: '#fff', cursor: 'pointer', fontWeight: 600 }}
                                                >
                                                    <Download size={14} /> Export Summary
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '20px 22px 32px' }}>
                                        {selectedInvestigation?.report ? (
                                            <ArticleRenderer 
                                                articleObj={{ 
                                                    article: selectedInvestigation.report, 
                                                    references: selectedInvestigation.report._references || [] 
                                                }} 
                                            />
                                        ) : (
                                            <div style={{ color: '#64748b', textAlign: 'center', padding: '40px' }}>
                                                Report is still generating or no claims have been committed yet.
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}
                            {activeTab === 'graph' && (
                                <div style={{ height: '100%', background: '#0f172a', borderRadius: '12px', border: '1px solid #1e293b', position: 'relative' }}>
                                    {graphLoading ? (
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#94a3b8' }}>Loading Graph...</div>
                                    ) : graphElements.length === 0 ? (
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#64748b' }}>No graph data found for {selectedInvestigation?.target}</div>
                                    ) : (
                                        <CytoscapeComponent
                                            elements={CytoscapeComponent.normalizeElements(graphElements)}
                                            style={{ width: '100%', height: '100%' }}
                                            stylesheet={CY_STYLE}
                                            layout={{ name: 'fcose', animate: false, nodeRepulsion: 4500 }}
                                            cy={(cy) => { cyRef.current = cy; }}
                                        />
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                ) : (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#64748b' }}>
                        Select an investigation to view details
                    </div>
                )}
            </div>

            {/* New Investigation Modal */}
            {showNewModal && (
                <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}>
                    <div style={{ background: '#0f172a', padding: '24px', borderRadius: '12px', width: '400px', border: '1px solid #1e293b' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                            <h3 style={{ fontSize: '18px', fontWeight: 'bold' }}>New Investigation</h3>
                            <button onClick={() => setShowNewModal(false)} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}><X size={20}/></button>
                        </div>
                        <form onSubmit={handleStart} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                            <div>
                                <label style={{ display: 'block', fontSize: '13px', color: '#94a3b8', marginBottom: '6px' }}>Target (Name, Organization, IP)</label>
                                <input 
                                    type="text" 
                                    value={target} 
                                    onChange={e => setTarget(e.target.value)} 
                                    placeholder="e.g. Acme Corp" 
                                    style={{ width: '100%', padding: '10px', background: '#1e293b', border: '1px solid #334155', borderRadius: '6px', color: 'white' }} 
                                    required 
                                />
                            </div>
                            <div>
                                <label style={{ display: 'block', fontSize: '13px', color: '#94a3b8', marginBottom: '6px' }}>Goal Type</label>
                                <select 
                                    value={goalType} 
                                    onChange={e => setGoalType(e.target.value)}
                                    style={{ width: '100%', padding: '10px', background: '#1e293b', border: '1px solid #334155', borderRadius: '6px', color: 'white' }}
                                >
                                    <option value="PROFILING">General Profiling</option>
                                    <option value="EXHAUSTIVE_COLLECTION">Exhaustive Collection</option>
                                    <option value="INFRASTRUCTURE">Cyber Infrastructure</option>
                                    <option value="FINANCIAL">Financial Tracing</option>
                                </select>
                            </div>
                            <div>
                                <label style={{ display: 'block', fontSize: '13px', color: '#94a3b8', marginBottom: '6px' }}>Max Leads to Explore</label>
                                <input 
                                    type="number" 
                                    value={maxLeads} 
                                    onChange={e => setMaxLeads(e.target.value)} 
                                    style={{ width: '100%', padding: '10px', background: '#1e293b', border: '1px solid #334155', borderRadius: '6px', color: 'white' }} 
                                />
                            </div>
                            <button type="submit" style={{ padding: '12px', background: 'var(--color-blue)', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: 'bold', marginTop: '8px' }}>
                                Deploy Agents
                            </button>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
}

export default Investigations;
