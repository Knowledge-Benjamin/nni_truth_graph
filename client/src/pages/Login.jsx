import React, { useState } from 'react';
import { Network } from 'lucide-react';
import { authApi } from '../api/auth';

export default function Login() {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            const result = await authApi.login(email, password);
            if (result.user?.role === 'user') {
                window.location.href = '/developer'; // Developers land on API Dashboard
            } else {
                window.location.href = '/review'; // Admins land on Review Queue
            }
        } catch (err) {
            setError(err.message || 'Login failed');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#0a0a0a', color: 'white' }}>
            <div style={{ background: '#111', padding: '40px', borderRadius: '12px', border: '1px solid #333', width: '100%', maxWidth: '400px' }}>
                <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '20px' }}>
                    <div className="app-logo">
                        <Network color="white" size={32} />
                    </div>
                </div>
                <h2 style={{ textAlign: 'center', marginBottom: '30px' }}>Admin Login</h2>

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
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '12px', color: '#888' }}>Password</label>
                        <input
                            type="password"
                            style={{ width: '100%', padding: '10px', background: '#222', border: '1px solid #444', color: 'white', borderRadius: '6px' }}
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            required
                        />
                    </div>
                    <button
                        type="submit"
                        disabled={loading}
                        style={{ marginTop: '10px', width: '100%', padding: '12px', background: 'linear-gradient(90deg, #4facfe 0%, #00f2fe 100%)', color: 'white', border: 'none', borderRadius: '6px', cursor: loading ? 'not-allowed' : 'pointer', fontWeight: 'bold' }}>
                        {loading ? 'Authenticating...' : 'Sign In'}
                    </button>
                </form>

                <div style={{ textAlign: 'center', marginTop: '25px', fontSize: '13px', color: '#888' }}>
                    Need to generate an API key? <a href="/signup" style={{ color: '#4facfe', textDecoration: 'none', fontWeight: 'bold' }}>Sign up as a Developer</a>
                </div>
            </div>
        </div>
    );
}
