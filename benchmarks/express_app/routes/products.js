// Product catalog routes
const express = require('express');
const router = express.Router();

const productsCatalog = [
    { sku: "SKU-001", name: "Wireless Mouse", price: 29.99, stock: 150 },
    { sku: "SKU-002", name: "Mechanical Keyboard", price: 89.99, stock: 45 },
    { sku: "SKU-003", name: "USB-C Hub", price: 39.50, stock: 0 },
];

// GET /api/products
router.get('/', (req, res) => {
    let items = productsCatalog;
    if (req.query.max_price) {
        const maxPrice = parseFloat(req.query.max_price);
        items = items.filter(p => p.price <= maxPrice);
    }
    res.status(200).json({ products: items, count: items.length });
});

// GET /api/products/:sku
router.get('/:sku', (req, res) => {
    const item = productsCatalog.find(p => p.sku === req.params.sku);
    if (!item) {
        return res.status(404).json({ error: "Product not found" });
    }
    res.status(200).json(item);
});

// POST /api/products
router.post('/', (req, res) => {
    const { sku, name, price, stock } = req.body;
    if (!sku || !name || price === undefined) {
        return res.status(400).json({ error: "Missing required product fields" });
    }

    const existing = productsCatalog.find(p => p.sku === sku);
    if (existing) {
        return res.status(409).json({ error: "Product SKU already exists" });
    }

    const newProduct = {
        sku: sku,
        name: name,
        price: parseFloat(price),
        stock: stock !== undefined ? parseInt(stock, 10) : 0,
    };
    productsCatalog.push(newProduct);
    res.status(201).json(newProduct);
});

module.exports = router;
