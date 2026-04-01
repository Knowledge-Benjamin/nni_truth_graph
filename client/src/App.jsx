import React, { useState, useEffect } from 'react';
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { Network, ShieldAlert, GitMerge, Search, Settings, LogIn, User, BookOpen, Terminal, Menu, X, Camera } from 'lucide-react';
import { api } from './api';
import { authApi } from './api/auth';

// Page Components
import ExplorerPane from './pages/ExplorerPane';
import ContradictionsPanel from './pages/ContradictionsPanel';
import HumanReviewQueue from './pages/HumanReviewQueue';
import Login from './pages/Login';
import Signup from './pages/Signup';
import Account from './pages/Account';
import ArticleDashboard from './pages/ArticleDashboard';
import ProtectedRoute from './components/ProtectedRoute';
import DeveloperDashboard from './pages/DeveloperDashboard';
import ApiDocs from './pages/ApiDocs';
import MediaPortal from './pages/MediaPortal';

function App() {
    const formatSocialCount = (count) => {
        if (!count) return '0';
        if (count >= 1000000) return (count / 1000000).toFixed(1).replace(/\.0$/, '') + 'm';
        if (count >= 1000) return (count / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
        return count.toString();
    };

    const navigate = useNavigate();
    const location = useLocation();

    let activeTab = 'explore';
    if (location.pathname.startsWith('/contradictions')) activeTab = 'contradictions';
    else if (location.pathname.startsWith('/review')) activeTab = 'review';
    else if (location.pathname.startsWith('/articles')) activeTab = 'articles';
    else if (location.pathname.startsWith('/login')) activeTab = 'login';
    else if (location.pathname.startsWith('/signup')) activeTab = 'signup';
    else if (location.pathname.startsWith('/account')) activeTab = 'account';
    else if (location.pathname.startsWith('/developer')) activeTab = 'developer';
    else if (location.pathname.startsWith('/docs')) activeTab = 'docs';
    else if (location.pathname.startsWith('/verify')) activeTab = 'verify';

    const [stats, setStats] = useState(null);
    const [user, setUser] = useState(null);
    const [authChecked, setAuthChecked] = useState(false);
    const [menuOpen, setMenuOpen] = useState(false);

    useEffect(() => {
        authApi.getMe()
            .then(u => setUser(u))
            .catch(() => setUser(null))
            .finally(() => setAuthChecked(true));
    }, [location.pathname]);

    useEffect(() => {
        if (activeTab === 'login' || activeTab === 'signup') return;
        api.getStats().then(setStats).catch(console.error);
        const intv = setInterval(() => api.getStats().then(setStats).catch(() => {}), 15000);
        return () => clearInterval(intv);
    }, [activeTab]);

    // Close menu on navigation
    const go = (path) => { navigate(path); setMenuOpen(false); };

    if (activeTab === 'login') return <Login />;
    if (activeTab === 'signup') return <Signup />;

    // All nav items compiled into one list for reuse in both desktop + mobile drawer
    const navItems = [
        { id: 'explore',       icon: <Search size={16} />,     label: 'Graph Explorer',   path: '/',               show: true },
        { id: 'verify',        icon: <Camera size={16} />,     label: 'Media Portal',     path: '/verify',         show: true },
        { id: 'docs',          icon: <BookOpen size={16} />,   label: 'API Docs',         path: '/docs',           show: true },
        { id: 'contradictions',icon: <GitMerge size={16} />,   label: 'Controversies',    path: '/contradictions', show: user?.role === 'admin', badge: stats?.graph?.open_controversies },
        { id: 'review',        icon: <ShieldAlert size={16} />,label: 'Human Review',     path: '/review',         show: user?.role === 'admin', badge: stats?.pipeline?.human_review_pending, danger: stats?.pipeline?.human_review_pending > 0 },
        { id: 'articles',      icon: <BookOpen size={16} />,   label: 'Article Engine',   path: '/articles',       show: user?.role === 'admin' },
        { id: 'developer',     icon: <Terminal size={16} />,   label: 'Developer Portal', path: '/developer',      show: !!user },
    ].filter(i => i.show);

    return (
        <div className="app-layout">
            {/* ── Header ── */}
            <header className="app-header" style={{ position: 'relative' }}>
                {/* Brand */}
                <div className="app-brand">
                    <div className="app-logo">
                        <Network size={20} color="white" />
                    </div>
                    <div>
                        <h1 className="app-title" style={{ backgroundImage: 'var(--grad-text-primary)', WebkitBackgroundClip: 'text', color: 'transparent' }}>
                            Truth
                        </h1>
                        <div style={{ fontSize: '12px', color: '#94a3b8', fontStyle: 'italic', marginTop: '-4px', marginBottom: '4px', letterSpacing: '0.5px' }}>
                            Truth it
                        </div>
                        <div className="app-stats">
                            <span>{stats?.graph?.active_claims ? formatSocialCount(stats.graph.active_claims) : '--'} ACTIVE CLAIMS</span>
                            <span style={{ display: 'none' }}>{stats?.pipeline?.human_review_pending ? formatSocialCount(stats.pipeline.human_review_pending) : '--'} PENDING REVIEW</span>
                        </div>
                    </div>
                </div>

                {/* Desktop Nav — hidden on mobile */}
                <nav className="app-nav desktop-nav">
                    {navItems.map(item => (
                        <NavButton
                            key={item.id}
                            active={activeTab === item.id}
                            onClick={() => go(item.path)}
                            icon={item.icon}
                            label={item.label}
                            badge={item.badge}
                            danger={item.danger}
                        />
                    ))}
                </nav>

                {/* Right side: auth + hamburger */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>
                    {/* Auth buttons — desktop only */}
                    <div className="desktop-auth">
                        {authChecked && !user && (
                            <>
                                <button onClick={() => go('/signup')} className="nav-btn" style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#38bdf8', fontWeight: 'bold' }}>
                                    <Terminal size={16} /><span>Get API Key</span>
                                </button>
                                <button onClick={() => go('/login')} className="nav-btn" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                    <LogIn size={16} /><span>Login</span>
                                </button>
                            </>
                        )}
                        {user && (
                            <>
                                <button onClick={() => go('/account')} className={`nav-btn ${activeTab === 'account' ? 'active' : ''}`} style={{ display: 'flex', alignItems: 'center', gap: 6 }} title={user.email}>
                                    <User size={16} />
                                    <span style={{ fontSize: 13, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {user.email?.split('@')[0]}
                                    </span>
                                </button>
                                <button onClick={() => go('/account')} className={`nav-btn ${activeTab === 'account' ? 'active' : ''}`} title="Settings">
                                    <Settings size={16} />
                                </button>
                            </>
                        )}
                    </div>

                    {/* Hamburger button — mobile only */}
                    <button
                        className="hamburger-btn"
                        onClick={() => setMenuOpen(o => !o)}
                        aria-label="Toggle menu"
                        style={{
                            display: 'none', // shown via CSS media query
                            alignItems: 'center', justifyContent: 'center',
                            width: 38, height: 38,
                            background: menuOpen ? 'rgba(255,255,255,0.1)' : 'transparent',
                            border: '1px solid rgba(255,255,255,0.1)',
                            borderRadius: 8, cursor: 'pointer', color: '#f8fafc',
                            transition: 'background 0.2s'
                        }}
                    >
                        {menuOpen ? <X size={20} /> : <Menu size={20} />}
                    </button>
                </div>
            </header>

            {/* ── Mobile Drawer ── */}
            {menuOpen && (
                <>
                    {/* Backdrop */}
                    <div
                        onClick={() => setMenuOpen(false)}
                        style={{
                            position: 'fixed', inset: 0, zIndex: 40,
                            background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(2px)'
                        }}
                    />
                    {/* Drawer panel */}
                    <div style={{
                        position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 50,
                        width: 260, background: '#0a0f1e',
                        borderLeft: '1px solid rgba(255,255,255,0.08)',
                        display: 'flex', flexDirection: 'column',
                        boxShadow: '-8px 0 32px rgba(0,0,0,0.4)',
                        overflowY: 'auto'
                    }}>
                        {/* Drawer header */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '18px 20px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                            <span style={{ fontWeight: 700, fontSize: 16, color: '#f8fafc' }}>Menu</span>
                            <button onClick={() => setMenuOpen(false)} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
                                <X size={20} />
                            </button>
                        </div>

                        {/* Nav links */}
                        <div style={{ flex: 1, padding: '12px 12px 0' }}>
                            {navItems.map(item => (
                                <button
                                    key={item.id}
                                    onClick={() => go(item.path)}
                                    style={{
                                        width: '100%', display: 'flex', alignItems: 'center', gap: 12,
                                        padding: '13px 14px', borderRadius: 8, border: 'none', cursor: 'pointer',
                                        textAlign: 'left', marginBottom: 4,
                                        background: activeTab === item.id ? 'rgba(255,255,255,0.1)' : 'transparent',
                                        color: activeTab === item.id ? '#f8fafc' : '#94a3b8',
                                        fontWeight: activeTab === item.id ? 600 : 400,
                                        fontSize: 15,
                                        transition: 'all 0.15s'
                                    }}
                                >
                                    {item.icon}
                                    <span style={{ flex: 1 }}>{item.label}</span>
                                    {item.badge > 0 && (
                                        <span style={{
                                            padding: '2px 7px', borderRadius: 12, fontSize: 11, fontWeight: 700,
                                            background: item.danger ? 'rgba(239,68,68,0.2)' : 'rgba(59,130,246,0.2)',
                                            color: item.danger ? '#f87171' : '#93c5fd'
                                        }}>{item.badge}</span>
                                    )}
                                </button>
                            ))}
                        </div>

                        {/* Drawer footer auth */}
                        <div style={{ padding: '16px 12px', borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                            {authChecked && !user && (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                    <button onClick={() => go('/signup')} style={{
                                        width: '100%', padding: '11px', borderRadius: 8, border: 'none', cursor: 'pointer',
                                        background: 'linear-gradient(90deg,#3b82f6,#06b6d4)', color: 'white', fontWeight: 700, fontSize: 14,
                                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8
                                    }}>
                                        <Terminal size={15} /> Get API Key
                                    </button>
                                    <button onClick={() => go('/login')} style={{
                                        width: '100%', padding: '11px', borderRadius: 8,
                                        border: '1px solid rgba(255,255,255,0.12)', background: 'transparent',
                                        color: '#cbd5e1', cursor: 'pointer', fontSize: 14,
                                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8
                                    }}>
                                        <LogIn size={15} /> Login
                                    </button>
                                </div>
                            )}
                            {user && (
                                <button onClick={() => go('/account')} style={{
                                    width: '100%', display: 'flex', alignItems: 'center', gap: 12,
                                    padding: '12px 14px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)',
                                    background: 'rgba(255,255,255,0.04)', color: '#f8fafc', cursor: 'pointer', fontSize: 14
                                }}>
                                    <User size={16} color="#94a3b8" />
                                    <div style={{ textAlign: 'left' }}>
                                        <div style={{ fontWeight: 600, fontSize: 14 }}>{user.email?.split('@')[0]}</div>
                                        <div style={{ fontSize: 11, color: '#64748b' }}>{user.email}</div>
                                    </div>
                                    <Settings size={14} color="#64748b" style={{ marginLeft: 'auto' }} />
                                </button>
                            )}
                        </div>
                    </div>
                </>
            )}

            {/* Main Content */}
            <main className="app-main">
                <Routes>
                    <Route path="/" element={<ExplorerPane />} />
                    <Route path="/entity/:slug" element={<ExplorerPane />} />
                    <Route path="/claim/:id" element={<ExplorerPane />} />
                    <Route path="/login" element={<Login />} />
                    <Route path="/signup" element={<Signup />} />
                    <Route path="/contradictions" element={<ProtectedRoute><ContradictionsPanel /></ProtectedRoute>} />
                    <Route path="/review" element={<ProtectedRoute><HumanReviewQueue /></ProtectedRoute>} />
                    <Route path="/account" element={<ProtectedRoute><Account /></ProtectedRoute>} />
                    <Route path="/articles" element={<ProtectedRoute><ArticleDashboard /></ProtectedRoute>} />
                    <Route path="/developer" element={<ProtectedRoute><DeveloperDashboard /></ProtectedRoute>} />
                    <Route path="/docs" element={<ApiDocs />} />
                    <Route path="/verify" element={<MediaPortal />} />
                </Routes>
            </main>
        </div>
    );
}

function NavButton({ active, onClick, icon, label, badge, danger }) {
    return (
        <button onClick={onClick} className={`nav-btn ${active ? 'active' : ''}`}>
            {icon}
            {label}
            {badge > 0 && (
                <span className={`nav-badge ${danger ? 'danger' : ''}`}>{badge}</span>
            )}
        </button>
    );
}

export default App;
