import { apiBase, getFetchOptions } from './index';

export const authApi = {
    login: async (email, password) => {
        const r = await fetch(`${apiBase}/auth/login`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ email, password })
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'Login failed');
        return data;
    },

    register: async (token, email, password) => {
        const r = await fetch(`${apiBase}/auth/register`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ token, email, password })
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'Registration failed');
        return data;
    },

    registerPublic: async (email, password) => {
        const r = await fetch(`${apiBase}/auth/register-public`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ email, password })
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'Registration failed');
        return data;
    },

    verifyInvite: async (token) => {
        const r = await fetch(`${apiBase}/auth/verify-invite?token=${encodeURIComponent(token)}`, {
            ...getFetchOptions()
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'Invite verify failed');
        return data;
    },

    generateInvite: async () => {
        const r = await fetch(`${apiBase}/auth/invite`, {
            ...getFetchOptions(),
            method: 'POST'
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'Generate invite failed');
        return data;
    },

    changePassword: async (currentPassword, newPassword) => {
        const r = await fetch(`${apiBase}/auth/change-password`, {
            ...getFetchOptions(),
            method: 'POST',
            body: JSON.stringify({ currentPassword, newPassword })
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'Change password failed');
        return data;
    },

    getMe: async () => {
        const r = await fetch(`${apiBase}/auth/me`, getFetchOptions());
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || 'User fetch failed');
        return data.user;
    },

    logout: async () => {
        const r = await fetch(`${apiBase}/auth/logout`, {
            ...getFetchOptions(),
            method: 'POST'
        });
        return r.json();
    }
};
