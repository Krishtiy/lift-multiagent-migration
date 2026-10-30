// Main entry point for Express benchmark service
const express = require('express');
const usersRouter = require('./routes/users');
const productsRouter = require('./routes/products');

const app = express();
app.use(express.json());

// Health check endpoint
app.get('/health', (req, res) => {
    res.status(200).json({ status: "healthy", timestamp: Date.now() });
});

// Mount routers
app.use('/api/users', usersRouter);
app.use('/api/products', productsRouter);

// 404 Catch-all handler
app.use((req, res) => {
    res.status(404).json({ error: "Endpoint not found" });
});

if (require.main === module) {
    const PORT = process.env.PORT || 3000;
    app.listen(PORT, () => {
        console.log(`Express benchmark app running on port ${PORT}`);
    });
}

module.exports = app;
