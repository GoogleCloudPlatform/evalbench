import mongomock
from util import get_SessionManager
from databases import get_database
import pytest
import json
import sys
import os


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """MongoDB client backed by mongomock, seeded with e-commerce documents."""
    db_config = {
        "db_type": "mongodb",
        "database_name": "unit_test_db",
        "database_path": "",
        "max_executions_per_minute": 100,
        "connection_string": "mongodb://mock-host:27017",
    }

    from databases import mongodb

    mock_client = mongomock.MongoClient("mongodb://mock-host:27017")
    original_client = mongodb.MongoClient
    mongodb.MongoClient = lambda *args, **kwargs: mock_client

    try:
        db = get_database(db_config, "unit_test_db")

        # Seed products
        db.db["products"].insert_many([
            {
                "product_id": "prod_001",
                "name": "Laptop Stand",
                "category": "Electronics",
                "price": 45.99,
                "stock": 150,
                "attributes": {"brand": "TechBrand", "material": "Aluminum", "color": "Silver", "weight_kg": 0.8},
                "tags": ["ergonomic", "portable", "aluminum"],
                "ratings": {"average": 4.5, "count": 230},
            },
            {
                "product_id": "prod_002",
                "name": "USB-C Hub",
                "category": "Electronics",
                "price": 34.99,
                "stock": 200,
                "attributes": {"brand": "TechBrand", "material": "Plastic", "color": "Black", "weight_kg": 0.15},
                "tags": ["connectivity", "portable", "usb"],
                "ratings": {"average": 4.2, "count": 415},
            },
            {
                "product_id": "prod_003",
                "name": "Mechanical Keyboard",
                "category": "Electronics",
                "price": 129.99,
                "stock": 75,
                "attributes": {"brand": "KeyMaster", "material": "Aluminum", "color": "Black", "weight_kg": 1.2},
                "tags": ["gaming", "mechanical", "rgb", "aluminum"],
                "ratings": {"average": 4.7, "count": 892},
            },
            {
                "product_id": "prod_004",
                "name": "Running Shoes",
                "category": "Sports",
                "price": 89.99,
                "stock": 300,
                "attributes": {"brand": "SpeedFoot", "material": "Mesh", "color": "Blue", "weight_kg": 0.35},
                "tags": ["running", "lightweight", "breathable"],
                "ratings": {"average": 4.4, "count": 567},
            },
            {
                "product_id": "prod_005",
                "name": "Yoga Mat",
                "category": "Sports",
                "price": 29.99,
                "stock": 180,
                "attributes": {"brand": "ZenFit", "material": "TPE", "color": "Purple", "weight_kg": 1.0},
                "tags": ["yoga", "exercise", "portable"],
                "ratings": {"average": 4.3, "count": 328},
            },
        ])

        # Seed customers
        db.db["customers"].insert_many([
            {
                "customer_id": "cust_001",
                "name": "Alice Johnson",
                "email": "alice@example.com",
                "tier": "gold",
                "created_at": "2023-06-01",
                "addresses": [
                    {"type": "home", "street": "123 Main St", "city": "New York", "state": "NY"},
                    {"type": "work", "street": "456 Office Blvd", "city": "New York", "state": "NY"},
                ],
                "preferences": {
                    "newsletter": True,
                    "notifications": {"email": True, "sms": False},
                    "preferred_categories": ["Electronics", "Books"],
                },
            },
            {
                "customer_id": "cust_002",
                "name": "Bob Smith",
                "email": "bob@example.com",
                "tier": "silver",
                "created_at": "2024-02-01",
                "addresses": [
                    {"type": "home", "street": "789 Oak Ave", "city": "Los Angeles", "state": "CA"},
                ],
                "preferences": {
                    "newsletter": False,
                    "notifications": {"email": True, "sms": True},
                    "preferred_categories": ["Sports"],
                },
            },
            {
                "customer_id": "cust_003",
                "name": "Carol White",
                "email": "carol@example.com",
                "tier": "bronze",
                "created_at": "2024-01-10",
                "addresses": [
                    {"type": "home", "street": "321 Pine Rd", "city": "Chicago", "state": "IL"},
                ],
                "preferences": {
                    "newsletter": True,
                    "notifications": {"email": False, "sms": False},
                    "preferred_categories": ["Books"],
                },
            },
        ])

        # Seed orders
        db.db["orders"].insert_many([
            {
                "order_id": "ord_001",
                "customer_id": "cust_001",
                "status": "delivered",
                "created_at": "2024-01-15",
                "total_amount": 125.97,
                "shipping_address": {"street": "123 Main St", "city": "New York", "state": "NY"},
                "items": [
                    {"product_id": "prod_001", "name": "Laptop Stand", "category": "Electronics", "quantity": 1, "unit_price": 45.99, "discount": 0},
                    {"product_id": "prod_002", "name": "USB-C Hub", "category": "Electronics", "quantity": 1, "unit_price": 34.99, "discount": 5},
                ],
                "payment": {"method": "credit_card", "status": "paid", "transaction_id": "txn_001"},
            },
            {
                "order_id": "ord_002",
                "customer_id": "cust_002",
                "status": "pending",
                "created_at": "2024-02-20",
                "total_amount": 89.99,
                "shipping_address": {"street": "789 Oak Ave", "city": "Los Angeles", "state": "CA"},
                "items": [
                    {"product_id": "prod_004", "name": "Running Shoes", "category": "Sports", "quantity": 1, "unit_price": 89.99, "discount": 0},
                ],
                "payment": {"method": "debit_card", "status": "failed", "transaction_id": "txn_002"},
            },
            {
                "order_id": "ord_003",
                "customer_id": "cust_001",
                "status": "shipped",
                "created_at": "2024-03-10",
                "total_amount": 159.98,
                "shipping_address": {"street": "123 Main St", "city": "New York", "state": "NY"},
                "items": [
                    {"product_id": "prod_003", "name": "Mechanical Keyboard", "category": "Electronics", "quantity": 1, "unit_price": 129.99, "discount": 0},
                    {"product_id": "prod_005", "name": "Yoga Mat", "category": "Sports", "quantity": 1, "unit_price": 29.99, "discount": 0},
                    {"product_id": "prod_002", "name": "USB-C Hub", "category": "Electronics", "quantity": 1, "unit_price": 34.99, "discount": 10},
                ],
                "payment": {"method": "credit_card", "status": "paid", "transaction_id": "txn_003"},
            },
        ])

        yield db
        db.close_connections()
    finally:
        mongodb.MongoClient = original_client


# ---------------------------------------------------------------------------
# Legacy tests (JSON dict format)
# ---------------------------------------------------------------------------

class TestMongoDBLegacyFormat:
    """Tests for the existing JSON dict query format."""

    def test_insert_and_find(self, client):
        """insert_data() with header-row CSV format and find query."""
        data = {
            "users": [
                ["name", "age"],
                ["Alice", 30],
                ["Bob", 25],
            ]
        }
        client.insert_data(data)

        query = json.dumps({"find": "users", "filter": {"name": "Alice"}})
        result, _, error = client.execute(query)

        assert error is None
        assert len(result) == 1
        assert result[0]["name"] == "Alice"
        assert result[0]["age"] == 30

    def test_aggregate_json_format(self, client):
        """JSON dict aggregate format still works."""
        query = json.dumps({
            "aggregate": "users",
            "pipeline": [
                {"$match": {"age": {"$gt": 20}}},
                {"$count": "total_users"},
            ],
        })

        result, _, error = client.execute(query)
        assert error is None
        assert result[0]["total_users"] == 2

    def test_count(self, client):
        """JSON dict count format."""
        query = json.dumps({"count": "users"})
        result, _, error = client.execute(query)
        assert error is None
        assert result[0]["count"] == 2

    def test_invalid_json(self, client):
        """Invalid JSON returns an error."""
        result, _, error = client.execute("{invalid_json}")
        assert error is not None
        assert "Invalid JSON" in error

    def test_metadata(self, client):
        """get_metadata() returns collection keys."""
        metadata = client.get_metadata()
        assert "users" in metadata


# ---------------------------------------------------------------------------
# Shell-style query tests
# ---------------------------------------------------------------------------

class TestMongoDBShellFormat:
    """Tests for MongoDB shell syntax: db.<collection>.aggregate([...])"""

    def test_shell_aggregate_count(self, client):
        """db.orders.aggregate([{$count}]) returns total order count."""
        query = 'db.orders.aggregate([{"$count": "total_orders"}])'
        result, _, error = client.execute(query)
        assert error is None
        assert len(result) == 1
        assert result[0]["total_orders"] == 3

    def test_shell_aggregate_match_project(self, client):
        """$match + $project filters and shapes output correctly."""
        query = (
            'db.orders.aggregate(['
            '{"$match": {"status": "delivered"}}, '
            '{"$project": {"order_id": 1, "status": 1, "_id": 0}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        assert len(result) == 1
        assert result[0]["order_id"] == "ord_001"
        assert result[0]["status"] == "delivered"
        assert "_id" not in result[0]

    def test_shell_aggregate_group(self, client):
        """$group aggregation counts orders by status."""
        query = (
            'db.orders.aggregate(['
            '{"$group": {"_id": "$status", "count": {"$sum": 1}}}, '
            '{"$sort": {"count": -1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        statuses = {r["_id"]: r["count"] for r in result}
        assert statuses["delivered"] == 1
        assert statuses["shipped"] == 1
        assert statuses["pending"] == 1

    def test_shell_aggregate_invalid_json_args(self, client):
        """Malformed JSON in shell aggregate args returns an error."""
        query = "db.orders.aggregate([{invalid}])"
        result, _, error = client.execute(query)
        assert error is not None

    def test_shell_unsupported_method(self, client):
        """Unrecognised shell method returns an error."""
        query = "db.orders.mapReduce({})"
        result, _, error = client.execute(query)
        assert error is not None


# ---------------------------------------------------------------------------
# Nested document query tests
# ---------------------------------------------------------------------------

class TestNestedDocumentQueries:
    """Tests for queries that access nested map fields via dot notation."""

    def test_filter_on_nested_shipping_city(self, client):
        """Filter by shipping_address.city returns only matching orders."""
        query = (
            'db.orders.aggregate(['
            '{"$match": {"shipping_address.city": "New York"}}, '
            '{"$project": {"order_id": 1, "_id": 0}}, '
            '{"$sort": {"order_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        order_ids = [r["order_id"] for r in result]
        assert order_ids == ["ord_001", "ord_003"]

    def test_filter_on_nested_payment_method(self, client):
        """Filter by payment.method returns correct orders."""
        query = (
            'db.orders.aggregate(['
            '{"$match": {"payment.method": "credit_card"}}, '
            '{"$project": {"order_id": 1, "_id": 0}}, '
            '{"$sort": {"order_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        order_ids = [r["order_id"] for r in result]
        assert order_ids == ["ord_001", "ord_003"]

    def test_filter_on_payment_status_failed(self, client):
        """Filter by payment.status=failed returns the failed order."""
        query = (
            'db.orders.aggregate(['
            '{"$match": {"payment.status": "failed"}}, '
            '{"$project": {"order_id": 1, "_id": 0}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        assert len(result) == 1
        assert result[0]["order_id"] == "ord_002"

    def test_filter_on_attributes_brand(self, client):
        """Filter by attributes.brand returns only TechBrand products."""
        query = (
            'db.products.aggregate(['
            '{"$match": {"attributes.brand": "TechBrand"}}, '
            '{"$project": {"product_id": 1, "_id": 0}}, '
            '{"$sort": {"product_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        product_ids = [r["product_id"] for r in result]
        assert product_ids == ["prod_001", "prod_002"]

    def test_deep_nested_notifications_email(self, client):
        """Filter by preferences.notifications.email (depth=3) works."""
        query = (
            'db.customers.aggregate(['
            '{"$match": {"preferences.notifications.email": true}}, '
            '{"$project": {"customer_id": 1, "_id": 0}}, '
            '{"$sort": {"customer_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        ids = [r["customer_id"] for r in result]
        assert "cust_001" in ids
        assert "cust_002" in ids
        assert "cust_003" not in ids

    def test_nested_ratings_average(self, client):
        """Filter by ratings.average threshold returns correct products."""
        query = (
            'db.products.aggregate(['
            '{"$match": {"ratings.average": {"$gte": 4.5}}}, '
            '{"$project": {"product_id": 1, "_id": 0}}, '
            '{"$sort": {"product_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        ids = [r["product_id"] for r in result]
        assert "prod_001" in ids  # 4.5
        assert "prod_003" in ids  # 4.7
        assert "prod_002" not in ids  # 4.2

    def test_aggregate_on_nested_field(self, client):
        """$group by category with $avg on ratings.average."""
        query = (
            'db.products.aggregate(['
            '{"$group": {"_id": "$category", "avg_rating": {"$avg": "$ratings.average"}}}, '
            '{"$project": {"category": "$_id", "avg_rating": 1, "_id": 0}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        by_cat = {r["category"]: r["avg_rating"] for r in result}
        assert "Electronics" in by_cat
        assert "Sports" in by_cat


# ---------------------------------------------------------------------------
# Array field query tests
# ---------------------------------------------------------------------------

class TestArrayFieldQueries:
    """Tests for queries on array fields (tags, items, addresses, preferred_categories)."""

    def test_filter_by_tag(self, client):
        """Querying tags array returns products containing the tag."""
        query = (
            'db.products.aggregate(['
            '{"$match": {"tags": "portable"}}, '
            '{"$project": {"product_id": 1, "_id": 0}}, '
            '{"$sort": {"product_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        ids = [r["product_id"] for r in result]
        assert "prod_001" in ids
        assert "prod_002" in ids
        assert "prod_005" in ids
        assert "prod_004" not in ids  # not tagged portable

    def test_filter_by_items_category(self, client):
        """Filter orders by items.category matches array elements."""
        query = (
            'db.orders.aggregate(['
            '{"$match": {"items.category": "Electronics"}}, '
            '{"$project": {"order_id": 1, "_id": 0}}, '
            '{"$sort": {"order_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        order_ids = [r["order_id"] for r in result]
        assert "ord_001" in order_ids
        assert "ord_003" in order_ids
        assert "ord_002" not in order_ids  # only Sports items

    def test_filter_by_items_discount(self, client):
        """Filter orders where any item has discount > 0."""
        query = (
            'db.orders.aggregate(['
            '{"$match": {"items.discount": {"$gt": 0}}}, '
            '{"$project": {"order_id": 1, "_id": 0}}, '
            '{"$sort": {"order_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        order_ids = [r["order_id"] for r in result]
        assert "ord_001" in order_ids  # USB-C Hub has discount=5
        assert "ord_003" in order_ids  # USB-C Hub has discount=10
        assert "ord_002" not in order_ids

    def test_unwind_and_group_tags(self, client):
        """$unwind on tags then $group counts occurrences per tag."""
        query = (
            'db.products.aggregate(['
            '{"$unwind": "$tags"}, '
            '{"$group": {"_id": "$tags", "count": {"$sum": 1}}}, '
            '{"$match": {"_id": "portable"}}, '
            '{"$project": {"tag": "$_id", "count": 1, "_id": 0}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        assert len(result) == 1
        assert result[0]["tag"] == "portable"
        assert result[0]["count"] == 3  # prod_001, prod_002, prod_005

    def test_unwind_and_sum_quantities(self, client):
        """$unwind items then $sum quantities per product."""
        query = (
            'db.orders.aggregate(['
            '{"$unwind": "$items"}, '
            '{"$group": {"_id": "$items.product_id", "total_qty": {"$sum": "$items.quantity"}}}, '
            '{"$match": {"_id": "prod_002"}}, '
            '{"$project": {"product_id": "$_id", "total_qty": 1, "_id": 0}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        assert len(result) == 1
        assert result[0]["product_id"] == "prod_002"
        assert result[0]["total_qty"] == 2  # ord_001(1) + ord_003(1)

    def test_array_size_filter(self, client):
        """$expr + $size filters orders with more than 2 items."""
        query = (
            'db.orders.aggregate(['
            '{"$match": {"$expr": {"$gt": [{"$size": "$items"}, 2]}}}, '
            '{"$project": {"order_id": 1, "_id": 0}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        order_ids = [r["order_id"] for r in result]
        assert "ord_003" in order_ids  # 3 items
        assert "ord_001" not in order_ids  # 2 items

    def test_preferred_categories_array(self, client):
        """Filter customers whose preferred_categories contains Books."""
        query = (
            'db.customers.aggregate(['
            '{"$match": {"preferences.preferred_categories": "Books"}}, '
            '{"$project": {"customer_id": 1, "_id": 0}}, '
            '{"$sort": {"customer_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        ids = [r["customer_id"] for r in result]
        assert "cust_001" in ids
        assert "cust_003" in ids
        assert "cust_002" not in ids

    def test_both_home_and_work_address(self, client):
        """$and on addresses.type finds customers with both home and work."""
        query = (
            'db.customers.aggregate(['
            '{"$match": {"$and": [{"addresses.type": "home"}, {"addresses.type": "work"}]}}, '
            '{"$project": {"customer_id": 1, "_id": 0}}, '
            '{"$sort": {"customer_id": 1}}'
            '])'
        )
        result, _, error = client.execute(query)
        assert error is None
        ids = [r["customer_id"] for r in result]
        assert ids == ["cust_001"]
        assert "cust_002" not in ids  # only home
        assert "cust_003" not in ids  # only home


# ---------------------------------------------------------------------------
# insertMany command tests
# ---------------------------------------------------------------------------

class TestInsertManyCommand:
    """Tests for the insertMany JSON dict command used in setup.json seeding."""

    def test_insert_many_and_query(self, client):
        """insertMany command seeds documents that are then queryable."""
        cmd = json.dumps({
            "insertMany": "temp_products",
            "documents": [
                {"sku": "t001", "name": "Widget A", "price": 9.99},
                {"sku": "t002", "name": "Widget B", "price": 19.99},
            ]
        })
        result, _, error = client.execute(cmd)
        assert error is None

        query = json.dumps({"count": "temp_products"})
        result, _, error = client.execute(query)
        assert error is None
        assert result[0]["count"] == 2

    def test_insert_many_array_of_commands(self, client):
        """A JSON array of insertMany commands is executed in sequence."""
        commands = json.dumps([
            {"insertMany": "bulk_a", "documents": [{"x": 1}, {"x": 2}]},
            {"insertMany": "bulk_b", "documents": [{"y": 10}]},
        ])
        _, _, error = client.execute(commands)
        assert error is None

        r_a, _, err_a = client.execute(json.dumps({"count": "bulk_a"}))
        r_b, _, err_b = client.execute(json.dumps({"count": "bulk_b"}))
        assert err_a is None and r_a[0]["count"] == 2
        assert err_b is None and r_b[0]["count"] == 1


# ---------------------------------------------------------------------------
# get_metadata recursive schema tests
# ---------------------------------------------------------------------------

class TestGetMetadataRecursive:
    """Tests for the improved recursive get_metadata / _extract_fields."""

    def test_nested_fields_appear_as_dotted_paths(self, client):
        """Nested object fields are returned with dot-notation names."""
        metadata = client.get_metadata()
        assert "orders" in metadata
        order_field_names = [f["name"] for f in metadata["orders"]]
        # Top-level nested objects
        assert "shipping_address" in order_field_names
        assert "payment" in order_field_names
        # Dot-notation nested fields
        assert "shipping_address.city" in order_field_names
        assert "payment.method" in order_field_names

    def test_array_of_objects_uses_bracket_notation(self, client):
        """Array-of-object fields are described with [] notation for sub-fields."""
        metadata = client.get_metadata()
        order_field_names = [f["name"] for f in metadata["orders"]]
        assert "items" in order_field_names
        assert "items[].product_id" in order_field_names
        assert "items[].unit_price" in order_field_names

    def test_array_of_scalars_shows_correct_type(self, client):
        """Array of scalars (tags) shows array<str> type."""
        metadata = client.get_metadata()
        product_fields = {f["name"]: f["type"] for f in metadata["products"]}
        assert "tags" in product_fields
        assert "array" in product_fields["tags"]

    def test_deep_nested_fields(self, client):
        """preferences.notifications.email appears as a dotted path."""
        metadata = client.get_metadata()
        customer_field_names = [f["name"] for f in metadata["customers"]]
        assert "preferences.notifications" in customer_field_names
        assert "preferences.notifications.email" in customer_field_names
        assert "preferences.notifications.sms" in customer_field_names
