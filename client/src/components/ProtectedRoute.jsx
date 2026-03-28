import React, { useEffect, useState } from 'react';
import { authApi } from '../api/auth';

export default function ProtectedRoute({ children }) {
    const [status, setStatus] = useState('loading'); // loading, auth, unauth

    useEffect(() => {
        authApi.getMe()
            .then(() => setStatus('auth'))
            .catch(() => {
                setStatus('unauth');
                // Redirect using native location change for simplicity
                window.location.href = '/login';
            });
    }, []);

    if (status === 'loading') {
        return (
            <div style={{ padding: '40px', color: 'white', display: 'flex', justifyContent: 'center', height: '100vh', alignItems: 'center' }}>
                <div className="loader" style={{ border: '4px solid rgba(255,255,255,0.1)', borderLeftColor: '#00f2fe', width: 30, height: 30, borderRadius: '50%', animation: 'spin 1s linear infinite' }}></div>
                <style>{`@keyframes spin { 100% { transform: rotate(360deg); } }`}</style>
            </div>
        );
    }

    if (status === 'unauth') return null;
    return children;
}
