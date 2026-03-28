const express = require('express');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');

const router = express.Router();

// Ensure JWT secret exists (we'll default to a static string for dev if .env is missing)
const JWT_SECRET = process.env.JWT_SECRET || 'fallback_super_secret_jwt_key_please_change';

// ── Authentication Middleware ────────────────────────────────────────────────
// Protect sensitive routes
const authenticateAdmin = (req, res, next) => {
    const token = req.cookies.token || req.headers.authorization?.split(' ')[1];
    if (!token) {
        return res.status(401).json({ error: 'Unauthorized: No token provided' });
    }

    try {
        const decoded = jwt.verify(token, JWT_SECRET);
        req.user = decoded;
        next();
    } catch (err) {
        return res.status(401).json({ error: 'Unauthorized: Invalid token' });
    }
};

// Stricter middleware: requires a valid JWT AND role === 'admin'
const requireAdmin = (req, res, next) => {
    authenticateAdmin(req, res, () => {
        if (req.user?.role !== 'admin') {
            return res.status(403).json({ error: 'Forbidden: Admin access required' });
        }
        next();
    });
};

// ── 1. LOGIN ────────────────────────────────────────────────────────────────
// Validates credentials against PostgreSQL and issues HttpOnly JWT
router.post('/login', async (req, res) => {
    try {
        const { email, password } = req.body;
        if (!email || !password) return res.status(400).json({ error: 'Email and password required' });

        const pgClient = await req.app.locals.pgPool.connect();
        try {
            const userRes = await pgClient.query('SELECT * FROM auth_users WHERE email = $1', [email]);
            if (userRes.rows.length === 0) {
                return res.status(401).json({ error: 'Invalid email or password' });
            }

            const user = userRes.rows[0];
            const match = await bcrypt.compare(password, user.password_hash);

            if (!match) {
                return res.status(401).json({ error: 'Invalid email or password' });
            }

            // Issue JWT
            const token = jwt.sign(
                { id: user.id, email: user.email, role: user.role },
                JWT_SECRET,
                { expiresIn: '24h' }
            );

            res.cookie('token', token, {
                httpOnly: true,
                secure: process.env.NODE_ENV === 'production',
                sameSite: process.env.NODE_ENV === 'production' ? 'none' : 'lax', // Needed for cross-domain in production
                maxAge: 24 * 60 * 60 * 1000 // 24 hours
            });

            res.json({ message: 'Logged in successfully', user: { id: user.id, email: user.email, role: user.role }, token });
        } finally {
            pgClient.release();
        }
    } catch (error) {
        console.error('Login error:', error);
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ── 2. LOGOUT ───────────────────────────────────────────────────────────────
router.post('/logout', (req, res) => {
    res.clearCookie('token');
    res.json({ message: 'Logged out successfully' });
});

// ── 3. GET CURRENT USER ─────────────────────────────────────────────────────
router.get('/me', authenticateAdmin, (req, res) => {
    res.json({ user: req.user });
});

// ── 4. ADMIN GENERATE INVITE ────────────────────────────────────────────────
// Requires authentication to create a registration token
router.post('/invite', requireAdmin, async (req, res) => {
    try {
        const pgClient = await req.app.locals.pgPool.connect();
        try {
            const token = crypto.randomBytes(32).toString('hex');
            // Valid for 7 days
            const expiresAt = new Date();
            expiresAt.setDate(expiresAt.getDate() + 7);

            await pgClient.query(
                'INSERT INTO auth_invites (token, expires_at, created_by_user_id) VALUES ($1, $2, $3)',
                [token, expiresAt, req.user.id]
            );

            // Construct the signup link based on origin
            const origin = req.headers.origin || 'http://localhost:5173';
            const inviteLink = `${origin}/signup?invite=${token}`;

            res.json({ message: 'Invite generated', token, link: inviteLink });
        } finally {
            pgClient.release();
        }
    } catch (error) {
        console.error('Invite error:', error);
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ── 5. VERIFY INVITE (to show email field on load) ──────────────────────────
router.get('/verify-invite', async (req, res) => {
    const { token } = req.query;
    if (!token) return res.status(400).json({ error: 'Token required' });

    try {
        const pgClient = await req.app.locals.pgPool.connect();
        try {
            const result = await pgClient.query('SELECT * FROM auth_invites WHERE token = $1 AND used = false AND expires_at > NOW()', [token]);
            if (result.rows.length === 0) {
                return res.status(400).json({ error: 'Invalid or expired invite token' });
            }
            res.json({ valid: true });
        } finally {
            pgClient.release();
        }
    } catch (error) {
        res.status(500).json({ error: 'Server error' });
    }
});

// ── 6. REGISTER ─────────────────────────────────────────────────────────────
// Consumes the invite token and creates the new user
router.post('/register', async (req, res) => {
    try {
        const { token, email, password } = req.body;
        if (!token || !email || !password) return res.status(400).json({ error: 'Missing fields' });

        const pgClient = await req.app.locals.pgPool.connect();
        try {
            await pgClient.query('BEGIN'); // Start transaction

            // 1. Validate invite
            const inviteRes = await pgClient.query('SELECT id FROM auth_invites WHERE token = $1 AND used = false AND expires_at > NOW() FOR UPDATE', [token]);
            if (inviteRes.rows.length === 0) {
                await pgClient.query('ROLLBACK');
                return res.status(400).json({ error: 'Invalid or expired invite' });
            }

            // 2. Hash new user's password
            const password_hash = await bcrypt.hash(password, 10);

            // 3. Create user
            const userRes = await pgClient.query(
                'INSERT INTO auth_users (email, password_hash, role) VALUES ($1, $2, $3) RETURNING id',
                [email, password_hash, 'admin']
            );

            // 4. Mark invite as used
            await pgClient.query('UPDATE auth_invites SET used = true, email = $1 WHERE id = $2', [email, inviteRes.rows[0].id]);

            await pgClient.query('COMMIT');

            // Log them in immediately
            const user_id = userRes.rows[0].id;
            const jwtToken = jwt.sign({ id: user_id, email, role: 'admin' }, JWT_SECRET, { expiresIn: '24h' });
            res.cookie('token', jwtToken, {
                httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: process.env.NODE_ENV === 'production' ? 'none' : 'lax', maxAge: 24 * 60 * 60 * 1000
            });

            res.json({ message: 'Registration successful', token: jwtToken });
        } catch (err) {
            await pgClient.query('ROLLBACK');
            throw err;
        } finally {
            pgClient.release();
        }
    } catch (error) {
        console.error('Registration error:', error);
        if (error.code === '23505') { // Postgres unique violation (email)
            return res.status(400).json({ error: 'Email already registered' });
        }
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ── 7. CHANGE PASSWORD ──────────────────────────────────────────────────────
router.post('/change-password', authenticateAdmin, async (req, res) => {
    try {
        const { currentPassword, newPassword } = req.body;
        if (!currentPassword || !newPassword) return res.status(400).json({ error: 'Missing fields' });

        const pgClient = await req.app.locals.pgPool.connect();
        try {
            const userRes = await pgClient.query('SELECT password_hash FROM auth_users WHERE id = $1', [req.user.id]);
            if (userRes.rows.length === 0) return res.status(404).json({ error: 'User not found' });

            const match = await bcrypt.compare(currentPassword, userRes.rows[0].password_hash);
            if (!match) return res.status(400).json({ error: 'Incorrect current password' });

            const newHash = await bcrypt.hash(newPassword, 10);
            await pgClient.query('UPDATE auth_users SET password_hash = $1 WHERE id = $2', [newHash, req.user.id]);

            res.json({ message: 'Password updated successfully' });
        } finally {
            pgClient.release();
        }
    } catch (error) {
        res.status(500).json({ error: 'Server error' });
    }
});

// ── 8. PUBLIC REGISTER (No invite required, assigns 'user' role) ─────────────
router.post('/register-public', async (req, res) => {
    try {
        const { email, password } = req.body;
        if (!email || !password) return res.status(400).json({ error: 'Missing fields' });

        const pgClient = await req.app.locals.pgPool.connect();
        try {
            const password_hash = await bcrypt.hash(password, 10);

            // Assign standard 'user' role natively so they don't get Admin privileges
            const userRes = await pgClient.query(
                'INSERT INTO auth_users (email, password_hash, role) VALUES ($1, $2, $3) RETURNING id',
                [email, password_hash, 'user']
            );

            const user_id = userRes.rows[0].id;
            const jwtToken = jwt.sign({ id: user_id, email, role: 'user' }, JWT_SECRET, { expiresIn: '24h' });
            
            res.cookie('token', jwtToken, {
                httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: process.env.NODE_ENV === 'production' ? 'none' : 'lax', maxAge: 24 * 60 * 60 * 1000
            });

            res.json({ message: 'Registration successful', token: jwtToken });
        } catch (err) {
            if (err.code === '23505') return res.status(400).json({ error: 'Email already registered' });
            throw err;
        } finally {
            pgClient.release();
        }
    } catch (error) {
        console.error('Public Registration error:', error);
        res.status(500).json({ error: 'Internal server error' });
    }
});

module.exports = {
    router,
    authenticateAdmin,
    requireAdmin,
    JWT_SECRET
};
