# -*- coding: utf-8 -*-
"""In-memory inventory data store containing Python 2 legacy idioms."""


class InventoryStatus(object):
    """Encapsulates item availability status.
    
    Gotcha G-09: Python 2 uses __nonzero__ to evaluate bool(obj).
    In Python 3, __nonzero__ is ignored and __bool__ is used.
    Without migration, bool(InventoryStatus(count=0)) evaluates to True in Python 3!
    """

    def __init__(self, count):
        self.count = count

    def __nonzero__(self):
        return self.count > 0


class ProductCatalog(object):
    def __init__(self):
        self._items = {}

    def add_product(self, sku, name, price, stock=0):
        self._items[sku] = {
            "name": name,
            "price": price,
            "status": InventoryStatus(stock),
        }

    def has_product(self, sku):
        # Gotcha G-04: dict.has_key()
        return self._items.has_key(sku)

    def get_all_skus(self):
        # Gotcha G-07: In Py2, keys() returns a mutable list (can sort/reverse in place)
        skus = self._items.keys()
        skus.sort()
        return skus

    def get_active_items(self):
        # Gotcha G-07: dict.iteritems()
        active = []
        for sku, data in self._items.iteritems():
            if bool(data["status"]):
                active.append((sku, data["name"]))
        return active

    def filter_by_max_price(self, max_price):
        # Gotcha G-06: filter() returns a list in Py2, allowing immediate indexing or len()
        # In Py3, filter() returns a filter generator
        all_skus = self._items.keys()
        matching = filter(lambda s: self._items[s]["price"] <= max_price, all_skus)
        # In Python 2 this works because matching is a list:
        if matching:
            return matching[0]
        return None
