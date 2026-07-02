export const apiBase = `${import.meta.env.VITE_API_URL ?? ''}/api`;

export const getFetchOptions = (extraHeaders = {}) => {
    return {
        credentials: 'include',
        headers: {
            'Content-Type': 'application/json',
            ...extraHeaders
        }
    };
};

export const api = {
    // Global stats
    getStats: async () => {
        const r = await fetch(`${apiBase}/stats`, getFetchOptions());
        if (!r.ok) throw new Error('Failed to fetch stats');
        return r.json();
    },

    // Natural Language Graph Chat (Conversational)
    sendChat: async (messages, context) => {
        const r = await fetch(`${apiBase}/chat`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ messages, context })
        });
        if (!r.ok) throw new Error('Chat API failed');
        return r.json();
    },

    // Main search (entities or claims)
    search: async (q, type = 'all') => {
        const r = await fetch(`${apiBase}/search?q=${encodeURIComponent(q)}&type=${type}`, getFetchOptions());
        if (!r.ok) throw new Error('Search failed');
        return r.json();
    },

    // Get full timeline and clusters for an entity
    getEntity: async (name) => {
        const r = await fetch(`${apiBase}/entity/${encodeURIComponent(name)}`, getFetchOptions());
        if (!r.ok) throw new Error('Entity fetch failed');
        return r.json();
    },

    // Get deep details on a single atomic claim
    getClaim: async (id) => {
        const r = await fetch(`${apiBase}/claim/${encodeURIComponent(id)}`, getFetchOptions());
        if (!r.ok) throw new Error('Claim fetch failed');
        return r.json();
    },

    // Get subject/predicate truth evolution timeline
    getTimeline: async (subject, predicate) => {
        const r = await fetch(`${apiBase}/timeline/${encodeURIComponent(subject)}/${encodeURIComponent(predicate)}`, getFetchOptions());
        if (!r.ok) throw new Error('Timeline fetch failed');
        return r.json();
    },

    // Get open controversies list
    getControversies: async () => {
        const r = await fetch(`${apiBase}/contradictions`, getFetchOptions());
        if (!r.ok) throw new Error('Contradictions fetch failed');
        return r.json();
    },

    // Get Human Review queue
    getHumanReview: async (page = 1, limit = 20) => {
        const r = await fetch(`${apiBase}/human-review?page=${page}&limit=${limit}`, getFetchOptions());
        if (!r.ok) throw new Error('Human review fetch failed');
        return r.json();
    },

    // Resolve a Human Review conflict
    resolveHumanReview: async (id, decision, note = '') => {
        const r = await fetch(`${apiBase}/human-review/${id}/resolve`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ decision, note })
        });
        if (!r.ok) throw new Error('Resolution failed');
        return r.json();
    },

    // Get typed graph neighborhood (Entity+Claim+Evidence nodes + all edges)
    getNeighborhood: async (name, showAll = false, limit = 60, scope = 'internal') => {
        const r = await fetch(
            `${apiBase}/graph/neighborhood/${encodeURIComponent(name)}?show_all=${showAll}&limit=${limit}&scope=${scope}`, getFetchOptions()
        );
        if (!r.ok) throw new Error(`Neighborhood fetch failed: ${r.status}`);
        return r.json();
    },

    // Get Epistemic Source Trust Rankings
    getSources: async () => {
        const r = await fetch(`${apiBase}/sources`, getFetchOptions());
        if (!r.ok) throw new Error('Source rankings fetch failed');
        return r.json();
    },

    // ── Developer API Key Management ──
    getApiKeys: async () => {
        const r = await fetch(`${apiBase}/developer/keys`, getFetchOptions());
        if (!r.ok) throw new Error('Failed to fetch API keys');
        return r.json();
    },
    
    generateApiKey: async () => {
        const r = await fetch(`${apiBase}/developer/keys/generate`, {
            ...getFetchOptions(),
            method: 'POST'
        });
        if (!r.ok) throw new Error('Failed to generate API key');
        return r.json();
    },
    
    revokeApiKey: async (id) => {
        const r = await fetch(`${apiBase}/developer/keys/${id}/revoke`, {
            ...getFetchOptions(),
            method: 'POST'
        });
        if (!r.ok) throw new Error('Failed to revoke API key');
        return r.json();
    },

    applyLicense: async (token) => {
        const r = await fetch(`${apiBase}/developer/license/apply`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ token })
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to apply license');
        }
        return r.json();
    },

    getLicenseStatus: async () => {
        const r = await fetch(`${apiBase}/developer/license/status`, getFetchOptions());
        if (!r.ok) throw new Error('Failed to fetch license status');
        return r.json();
    },

    // ── System Settings Management ──
    getSettings: async () => {
        const r = await fetch(`${apiBase}/developer/settings`, getFetchOptions());
        if (!r.ok) throw new Error('Failed to fetch settings');
        return r.json();
    },

    updateSettings: async (settings) => {
        const r = await fetch(`${apiBase}/developer/settings`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify(settings)
        });
        if (!r.ok) throw new Error('Failed to update settings');
        return r.json();
    },

    // ── Internal Data Ingestion ──
    ingestUrl: async (url, label = '', priority = 'normal') => {
        const r = await fetch(`${apiBase}/ingest/url`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ url, label, priority }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to queue URL');
        }
        return r.json();
    },

    ingestText: async (title, text, source_label = '', classification = 'INTERNAL') => {
        const r = await fetch(`${apiBase}/ingest/text`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ title, text, source_label, classification }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to inject memo');
        }
        return r.json();
    },

    ingestDocuments: async (files) => {
        const formData = new FormData();
        for (const file of files) {
            formData.append('files', file);
        }
        const r = await fetch(`${apiBase}/ingest/document`, {
            credentials: 'include', // No Content-Type header — let browser set multipart boundary
            method: 'POST',
            body: formData,
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to upload documents');
        }
        return r.json();
    },

    getIngestQueue: async (status = '', page = 1, limit = 20) => {
        const params = new URLSearchParams({ page, limit });
        if (status) params.set('status', status);
        const r = await fetch(`${apiBase}/ingest/queue?${params}`, getFetchOptions());
        if (!r.ok) throw new Error('Failed to fetch ingestion queue');
        return r.json();
    },

    deleteIngestItem: async (type, id) => {
        const r = await fetch(`${apiBase}/ingest/${type}/${id}`, {
            ...getFetchOptions(),
            method: 'DELETE',
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to delete item');
        }
        return r.json();
    },

    // ── OSINT Investigations ──
    startInvestigation: async (target, options = {}) => {
        const r = await fetch(`${apiBase}/investigations`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ target, ...options }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to start investigation');
        }
        return r.json();
    },

    listInvestigations: async () => {
        const r = await fetch(`${apiBase}/investigations`, getFetchOptions());
        if (!r.ok) throw new Error('Failed to list investigations');
        return r.json();
    },

    getInvestigation: async (id) => {
        const r = await fetch(`${apiBase}/investigations/${id}`, getFetchOptions());
        if (!r.ok) throw new Error('Failed to fetch investigation');
        return r.json();
    },

    getInvestigationLeads: async (id, page = 1, limit = 50, status = '') => {
        const params = new URLSearchParams({ page, limit });
        if (status) params.set('status', status);
        const r = await fetch(`${apiBase}/investigations/${id}/leads?${params}`, getFetchOptions());
        if (!r.ok) throw new Error('Failed to fetch leads');
        return r.json();
    },

    pauseInvestigation: async (id) => {
        const r = await fetch(`${apiBase}/investigations/${id}/pause`, {
            ...getFetchOptions(),
            method: 'POST',
        });
        if (!r.ok) throw new Error('Failed to pause investigation');
        return r.json();
    },

    resumeInvestigation: async (id) => {
        const r = await fetch(`${apiBase}/investigations/${id}/resume`, {
            ...getFetchOptions(),
            method: 'POST',
        });
        if (!r.ok) throw new Error('Failed to resume investigation');
        return r.json();
    },

    deleteInvestigation: async (id) => {
        const r = await fetch(`${apiBase}/investigations/${id}`, {
            ...getFetchOptions(),
            method: 'DELETE',
        });
        if (!r.ok) throw new Error('Failed to delete investigation');
        return r.json();
    },

};
