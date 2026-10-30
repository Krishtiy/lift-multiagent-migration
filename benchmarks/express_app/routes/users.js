// User management routes
const express = require('express');
const router = express.Router();
const { authenticate } = require('../middleware/auth');

const usersDatabase = [
    { id: 1, username: "alice", email: "alice@example.com", role: "admin" },
    { id: 2, username: "bob", email: "bob@example.com", role: "member" },
    { id: 3, username: "charlie", email: "charlie@example.com", role: "member" },
];

// GET /api/users
router.get('/', (req, res) => {
    let result = usersDatabase;
    const role = req.query.role;
    const limit = req.query.limit ? parseInt(req.query.limit, 10) : 10;

    if (role) {
        result = result.filter(u => u.role === role);
    }
    res.status(200).json({ users: result.slice(0, limit), total: result.length });
});

// GET /api/users/:id
router.get('/:id', (req, res) => {
    const userId = parseInt(req.params.id, 10);
    const user = usersDatabase.find(u => u.id === userId);

    if (!user) {
        return res.status(404).json({ error: "User not found" });
    }
    res.status(200).json(user);
});

// POST /api/users
router.post('/', (req, res) => {
    const { username, email, role } = req.body;
    if (!username || !email) {
        return res.status(400).json({ error: "username and email are required" });
    }

    const newUser = {
        id: usersDatabase.length + 1,
        username: username,
        email: email,
        role: role || "member",
    };
    usersDatabase.push(newUser);
    res.status(201).json(newUser);
});

// DELETE /api/users/:id
router.delete('/:id', authenticate, (req, res) => {
    const userId = parseInt(req.params.id, 10);
    const index = usersDatabase.findIndex(u => u.id === userId);

    if (index === -1) {
        return res.status(404).json({ error: "User not found" });
    }

    usersDatabase.splice(index, 1);
    res.status(200).json({ success: true, deletedId: userId, operator: req.user.username });
});

module.exports = router;
