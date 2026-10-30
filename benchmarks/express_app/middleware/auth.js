// Authentication middleware for Express benchmark service

function authenticate(req, res, next) {
    const authHeader = req.headers['authorization'];
    if (!authHeader) {
        return res.status(401).json({ error: "Missing authorization header" });
    }

    const parts = authHeader.split(" ");
    if (parts.length !== 2 || parts[0] !== "Bearer") {
        return res.status(401).json({ error: "Invalid token format" });
    }

    const token = parts[1];
    if (token !== "secret-token-123") {
        return res.status(401).json({ error: "Invalid or expired token" });
    }

    // Attach user payload to request
    req.user = { id: 101, username: "admin", role: "superuser" };
    next();
}

module.exports = {
    authenticate
};
