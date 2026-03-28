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
    getNeighborhood: async (name, showAll = false, limit = 60) => {
        const r = await fetch(
            `${apiBase}/graph/neighborhood/${encodeURIComponent(name)}?show_all=${showAll}&limit=${limit}`, getFetchOptions()
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
    }

};
