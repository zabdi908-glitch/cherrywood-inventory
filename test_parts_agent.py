import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

# parts_agent imports tenants_store, whose production module initializes its
# configured database at import time. Use a minimal stand-in so these focused
# tests only ever touch in-memory or temporary databases.
fake_tenants_store = types.ModuleType('tenants_store')
fake_tenants_store.get_default_tenant_id = lambda: 1
real_tenants_store = sys.modules.get('tenants_store')
real_connect = sqlite3.connect


def isolated_import_connect(database, *args, **kwargs):
    if str(database).endswith('/inventory.db'):
        return real_connect(':memory:', *args, **kwargs)
    return real_connect(database, *args, **kwargs)


sys.modules['tenants_store'] = fake_tenants_store
sqlite3.connect = isolated_import_connect

try:
    from parts_agent import PartsAgent
finally:
    sqlite3.connect = real_connect

if real_tenants_store is None:
    del sys.modules['tenants_store']
else:
    sys.modules['tenants_store'] = real_tenants_store


PARTS_SCHEMA = '''
    CREATE TABLE parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id TEXT UNIQUE,
        part_name TEXT,
        category TEXT,
        part_type TEXT,
        make TEXT,
        model TEXT,
        generation TEXT,
        oem_number TEXT,
        engine_code TEXT,
        condition TEXT,
        price REAL,
        stock_status TEXT,
        location TEXT,
        notes TEXT,
        slug TEXT,
        registration TEXT,
        vin TEXT,
        mileage INTEGER,
        year INTEGER,
        fuel_type TEXT,
        transmission TEXT,
        engine_size TEXT,
        colour TEXT,
        side TEXT,
        position TEXT,
        tenant_id INTEGER
    )
'''


def part_data(stock_id):
    return {
        'stock_id': stock_id,
        'part_name': 'Test part',
        'category': 'Other',
    }


class PartsAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Path(self.temp_dir.name) / 'parts.db'
        conn = sqlite3.connect(database)
        conn.execute(PARTS_SCHEMA)
        conn.commit()
        conn.close()

        self.agent = PartsAgent.__new__(PartsAgent)
        self.agent.database = str(database)
        self.agent.open_connections = []

        def tracking_get_db(agent):
            tracked_conn = sqlite3.connect(agent.database, timeout=0.1)
            tracked_conn.row_factory = sqlite3.Row
            agent.open_connections.append(tracked_conn)
            return tracked_conn

        self.agent.get_db = types.MethodType(tracking_get_db, self.agent)

    def tearDown(self):
        for conn in self.agent.open_connections:
            try:
                conn.close()
            except sqlite3.ProgrammingError:
                pass
        self.temp_dir.cleanup()

    def test_duplicate_stock_id_returns_friendly_error(self):
        self.agent.add_part(part_data('223'), tenant_id=1)
        duplicate = self.agent.add_part(part_data('223'), tenant_id=1)

        self.assertEqual(duplicate, {
            'success': False,
            'error': 'Stock ID 223 is already in use by another part. Please choose a different stock ID.'
        })

    def test_duplicate_stock_id_releases_database_lock(self):
        first = self.agent.add_part(part_data('223'), tenant_id=1)
        duplicate = self.agent.add_part(part_data('223'), tenant_id=1)

        self.assertTrue(first['success'])
        self.assertFalse(duplicate['success'])

        # A fresh writer can immediately commit after the failed INSERT.
        # Retaining the failed transaction/connection would make this raise
        # "database is locked". References to every agent connection are kept
        # so garbage collection cannot accidentally hide a leaked connection.
        conn = sqlite3.connect(self.agent.database, timeout=0.1)
        try:
            conn.execute(
                'INSERT INTO parts (stock_id, part_name, tenant_id) VALUES (?, ?, ?)',
                ('224', 'Next part', 1)
            )
            conn.commit()
        finally:
            conn.close()

    def test_next_stock_id_returns_next_numeric_value_for_tenant(self):
        conn = sqlite3.connect(self.agent.database)
        conn.executemany(
            'INSERT INTO parts (stock_id, part_name, tenant_id) VALUES (?, ?, ?)',
            [('18', 'Older', 1), ('223', 'Newest', 1), ('900', 'Other tenant', 2)]
        )
        conn.commit()
        conn.close()

        self.assertEqual(self.agent.next_stock_id(tenant_id=1), '224')

    def test_next_stock_id_ignores_non_numeric_values(self):
        conn = sqlite3.connect(self.agent.database)
        conn.executemany(
            'INSERT INTO parts (stock_id, part_name, tenant_id) VALUES (?, ?, ?)',
            [('223', 'Numeric', 1), ('CH-99999', 'Prefixed', 1), ('MANUAL', 'Label', 1)]
        )
        conn.commit()
        conn.close()

        self.assertEqual(self.agent.next_stock_id(tenant_id=1), '224')

    def test_next_stock_id_skips_id_used_globally(self):
        conn = sqlite3.connect(self.agent.database)
        conn.executemany(
            'INSERT INTO parts (stock_id, part_name, tenant_id) VALUES (?, ?, ?)',
            [('223', 'Tenant sequence', 1), ('224', 'Other tenant', 2)]
        )
        conn.commit()
        conn.close()

        self.assertEqual(self.agent.next_stock_id(tenant_id=1), '225')


if __name__ == '__main__':
    unittest.main()
