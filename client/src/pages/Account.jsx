import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../api/auth';
import { Settings, Link as LinkIcon, LogOut } from 'lucide-react';

export default function Account() {
    const navigate = useNavigate();
    const [currentPassword, setCurrentPassword] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [message, setMessage] = useState({ text: '', type: '' });
    const [loading, setLoading] = useState(false);
    const [inviteLink, setInviteLink] = useState('');
    const [inviteLoading, setInviteLoading] = useState(false);

    const handleLogout = async () => {
        await authApi.logout().catch(() => {});
        navigate('/login');
    };

    const handlePasswordChange = async (e) => {
        e.preventDefault();
        setLoading(true);
        setMessage({ text: '', type: '' });
        try {
            await authApi.changePassword(currentPassword, newPassword);
            setMessage({ text: 'Password successfully updated.', type: 'success' });
            setCurrentPassword('');
            setNewPassword('');
        } catch (err) {
            setMessage({ text: err.message || 'Failed to update password.', type: 'error' });
        } finally {
            setLoading(false);
        }
    };

    const handleGenerateInvite = async () => {
        setInviteLoading(true);
        setInviteLink('');
        try {
            const data = await authApi.generateInvite();
            setInviteLink(data.link);
        } catch (err) {
            setMessage({ text: err.message || 'Failed to generate invite.', type: 'error' });
        } finally {
            setInviteLoading(false);
        }
    };

    return (
        <div style={{ padding: '40px', maxWidth: '600px', margin: '0 auto', color: 'white' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '30px' }}>
                <Settings size={28} color="#00f2fe" />
                <h1 style={{ margin: 0 }}>Admin Settings</h1>
            </div>

            {/* Change Password Panel */}
            <div style={{ background: '#111', padding: '30px', borderRadius: '12px', border: '1px solid #333', marginBottom: '30px' }}>
                <h2 style={{ fontSize: '18px', marginBottom: '20px', borderBottom: '1px solid #222', paddingBottom: '10px' }}>Change Password</h2>

                {message.text && (
                    <div style={{
                        padding: '10px', borderRadius: '6px', marginBottom: '20px', fontSize: '14px',
                        background: message.type === 'error' ? 'rgba(239, 68, 68, 0.1)' : 'rgba(34, 197, 94, 0.1)',
                        color: message.type === 'error' ? '#ef4444' : '#22c55e',
                        border: `1px solid ${message.type === 'error' ? 'rgba(239, 68, 68, 0.2)' : 'rgba(34, 197, 94, 0.2)'}`
                    }}>
                        {message.text}
                    </div>
                )}

                <form onSubmit={handlePasswordChange} style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '12px', color: '#888' }}>Current Password</label>
                        <input type="password" required value={currentPassword} onChange={e => setCurrentPassword(e.target.value)}
                            style={{ width: '100%', padding: '10px', background: '#222', border: '1px solid #444', color: 'white', borderRadius: '6px' }} />
                    </div>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '12px', color: '#888' }}>New Password</label>
                        <input type="password" required minLength={8} value={newPassword} onChange={e => setNewPassword(e.target.value)}
                            style={{ width: '100%', padding: '10px', background: '#222', border: '1px solid #444', color: 'white', borderRadius: '6px' }} />
                    </div>
                    <button type="submit" disabled={loading}
                        style={{ alignSelf: 'flex-start', padding: '10px 20px', background: '#222', color: 'white', border: '1px solid #444', borderRadius: '6px', cursor: 'pointer' }}>
                        {loading ? 'Updating...' : 'Update Password'}
                    </button>
                </form>
            </div>

            {/* Admin Invites Panel */}
            <div style={{ background: '#111', padding: '30px', borderRadius: '12px', border: '1px solid #333' }}>
                <h2 style={{ fontSize: '18px', marginBottom: '10px', borderBottom: '1px solid #222', paddingBottom: '10px' }}>Invite New Admin</h2>
                <p style={{ fontSize: '14px', color: '#888', marginBottom: '20px' }}>Generate a unique 7-day registration link for a new colleague.</p>

                <button onClick={handleGenerateInvite} disabled={inviteLoading}
                    style={{ padding: '10px 20px', background: 'linear-gradient(90deg, #4facfe 0%, #00f2fe 100%)', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold' }}>
                    {inviteLoading ? 'Generating...' : 'Generate Invite Link'}
                </button>

                {inviteLink && (
                    <div style={{ marginTop: '20px', padding: '15px', background: '#222', border: '1px dashed #555', borderRadius: '6px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <LinkIcon size={16} color="#00f2fe" />
                        <input type="text" readOnly value={inviteLink} style={{ flex: 1, background: 'transparent', border: 'none', color: '#00f2fe', outline: 'none' }} />
                        <button onClick={() => navigator.clipboard.writeText(inviteLink)} style={{ background: '#333', color: 'white', border: 'none', padding: '5px 10px', borderRadius: '4px', cursor: 'pointer', fontSize: '12px' }}>
                            Copy
                        </button>
                    </div>
                )}
            </div>
            {/* Logout */}
            <div style={{ marginTop: 30 }}>
                <button
                    onClick={handleLogout}
                    style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '11px 20px', borderRadius: 8, border: '1px solid rgba(239,68,68,0.3)',
                        background: 'rgba(239,68,68,0.08)', color: '#f87171',
                        cursor: 'pointer', fontSize: 14, fontWeight: 600, transition: 'all 0.2s'
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.18)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.08)'; }}
                >
                    <LogOut size={15} />
                    Sign Out
                </button>
            </div>
        </div>
    );
}
