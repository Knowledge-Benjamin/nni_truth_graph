import React, { useState, useEffect } from 'react';
import { Network } from 'lucide-react';
import { authApi } from '../api/auth';

export default function Signup() {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [tokenError, setTokenError] = useState('');
    const [loading, setLoading] = useState(false);

    // Extract invite token from query params
    const getInviteToken = () => new URLSearchParams(window.location.search).get('invite');
    const token = getInviteToken();
    const isPublic = !token;

    useEffect(() => {
        if (isPublic) return; // Skip token verification for open public developer signups
        authApi.verifyInvite(token).catch(err => {
            setTokenError(err.message || 'Invalid or expired invite token.');
        });
    }, [token, isPublic]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            if (isPublic) {
                await authApi.registerPublic(email, password);
                window.location.href = '/developer'; // Devs go to dev portal
            } else {
                await authApi.register(token, email, password);
                window.location.href = '/review'; // Admins go to review queue
            }
        } catch (err) {
            setError(err.message || 'Registration failed');
        } finally {
            setLoading(false);
        }
    };

    if (tokenError && !isPublic) {
        return (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#0a0a0a', color: 'white' }}>
                <div style={{ background: '#111', padding: '40px', borderRadius: '12px', border: '1px solid #333', textAlign: 'center' }}>
                    <div style={{ color: '#ef4444', fontSize: '18px', marginBottom: '20px' }}>⚠️ {tokenError}</div>
                    <a href="/login" style={{ color: '#4facfe', textDecoration: 'none' }}>Return to Login</a>
                </div>
            </div>
        );
    }

    return (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#0a0a0a', color: 'white' }}>
            <div style={{ background: '#111', padding: '40px', borderRadius: '12px', border: '1px solid #333', width: '100%', maxWidth: '400px' }}>
                <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '20px' }}>
                    <div className="app-logo">
                        <Network color="white" size={32} />
                    </div>
                </div>
                <h2 style={{ textAlign: 'center', marginBottom: '5px' }}>{isPublic ? 'Developer Sign Up' : 'Accept Invite'}</h2>
                <p style={{ textAlign: 'center', color: '#888', marginBottom: '25px', fontSize: '14px' }}>{isPublic ? 'Truth Graph API Access Portal' : 'Truth Admin Registration'}</p>

                {error && <div style={{ color: '#ef4444', background: 'rgba(239, 68, 68, 0.1)', padding: '10px', borderRadius: '6px', marginBottom: '20px', fontSize: '14px', border: '1px solid rgba(239, 68, 68, 0.3)' }}>{error}</div>}

                <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '12px', color: '#888' }}>Email</label>
                        <input
                            type="email"
                            style={{ width: '100%', padding: '10px', background: '#222', border: '1px solid #444', color: 'white', borderRadius: '6px' }}
                            value={email}
                            onChange={e => setEmail(e.target.value)}
                            required
                        />
                    </div>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '12px', color: '#888' }}>Create Password</label>
                        <input
                            type="password"
                            style={{ width: '100%', padding: '10px', background: '#222', border: '1px solid #444', color: 'white', borderRadius: '6px' }}
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            required
                            minLength={8}
                        />
                    </div>
                    <button
                        type="submit"
                        disabled={loading}
                        style={{ marginTop: '10px', width: '100%', padding: '12px', background: 'linear-gradient(90deg, #4facfe 0%, #00f2fe 100%)', color: 'white', border: 'none', borderRadius: '6px', cursor: loading ? 'not-allowed' : 'pointer', fontWeight: 'bold' }}>
                        {loading ? 'Registering...' : 'Create Account'}
                    </button>
                </form>
            </div>
        </div>
    );
}
