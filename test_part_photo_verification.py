import ast
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from part_photo_verification import photo_save_flash, save_part_photos


PHOTO_SCHEMA = '''
    CREATE TABLE part_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id INTEGER,
        photo_url TEXT,
        photo_order INTEGER DEFAULT 0,
        tenant_id INTEGER
    )
'''


class UploadedFile(io.BytesIO):
    def __init__(self, content, filename):
        super().__init__(content)
        self.filename = filename


def image_upload(filename):
    stream = io.BytesIO()
    with Image.new('RGB', (40, 30), color='orange') as image:
        image.save(stream, format='JPEG')
    return UploadedFile(stream.getvalue(), filename)


class PartPhotoVerificationTests(unittest.TestCase):
    max_bytes = 5 * 1024 * 1024

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.temp_dir.name) / 'uploads'
        self.upload_dir.mkdir()
        self.database = Path(self.temp_dir.name) / 'parts.db'
        with sqlite3.connect(self.database) as connection:
            connection.execute(PHOTO_SCHEMA)

    def tearDown(self):
        self.temp_dir.cleanup()

    def get_db(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, files, get_db=None):
        return save_part_photos(
            files=files,
            part_id=42,
            tenant_id=7,
            upload_dir=str(self.upload_dir),
            get_db=get_db or self.get_db,
            allowed_file=lambda filename: filename.lower().endswith(('.jpg', '.jpeg', '.png')),
            max_files=10,
            max_bytes=self.max_bytes,
            max_dimension=1600,
        )

    def database_photo_count(self):
        with sqlite3.connect(self.database) as connection:
            return connection.execute('SELECT COUNT(*) FROM part_photos').fetchone()[0]

    def test_both_add_routes_use_shared_photo_verification(self):
        app_source = Path(__file__).with_name('app.py').read_text()
        app_tree = ast.parse(app_source)
        route_functions = {
            node.name: node
            for node in app_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for route_name in ('parts_add', 'parts_add_wizard'):
            calls = [
                node.func.id
                for node in ast.walk(route_functions[route_name])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            ]
            self.assertIn('_save_and_report_new_part_photos', calls)

    def test_zero_selected_keeps_normal_success_message(self):
        result = self.save([])

        self.assertEqual((result.selected_count, result.saved_count), (0, 0))
        self.assertEqual(photo_save_flash(result), ('✅ Part added successfully!', 'success'))
        self.assertEqual(self.database_photo_count(), 0)

    def test_successful_verified_file_remains_on_disk_and_backup_is_required(self):
        result = self.save([image_upload('phone.JPG')])

        self.assertEqual((result.selected_count, result.saved_count), (1, 1))
        self.assertEqual(
            photo_save_flash(result),
            (
                '✅ Part added successfully — 1/1 photos saved and verified. '
                'Keep the originals until the next backup has completed.',
                'success'
            )
        )
        self.assertEqual(self.database_photo_count(), 1)
        self.assertEqual(len(list(self.upload_dir.glob('part_42_*.jpg'))), 1)

    def test_five_selected_and_five_saved_is_verified_success(self):
        result = self.save([image_upload(f'phone-{number}.jpg') for number in range(5)])

        self.assertEqual((result.selected_count, result.saved_count), (5, 5))
        message, category = photo_save_flash(result)
        self.assertEqual(category, 'success')
        self.assertIn('5/5 photos saved and verified.', message)
        self.assertIn('Keep the originals until the next backup has completed.', message)
        self.assertNotIn('Safe to delete', message)
        self.assertEqual(self.database_photo_count(), 5)

    def test_five_selected_and_four_saved_warns_not_to_delete(self):
        files = [image_upload(f'phone-{number}.jpg') for number in range(4)]
        files.append(UploadedFile(b'not an image', 'broken.jpg'))

        result = self.save(files)

        self.assertEqual((result.selected_count, result.saved_count), (5, 4))
        message, category = photo_save_flash(result)
        self.assertEqual(category, 'warning')
        self.assertIn('only 4/5 photos saved', message)
        self.assertIn('DO NOT DELETE ORIGINALS', message)
        self.assertNotIn('Safe to delete', message)
        self.assertEqual(self.database_photo_count(), 4)

    def test_rejected_oversized_and_unreadable_photos_are_not_saved(self):
        files = [
            UploadedFile(b'text', 'notes.txt'),
            UploadedFile(b'x' * (self.max_bytes + 1), 'oversized.jpg'),
            UploadedFile(b'not an image', 'unreadable.jpg'),
        ]

        result = self.save(files)

        self.assertEqual((result.selected_count, result.saved_count), (3, 0))
        self.assertEqual(
            {reason for _, reason in result.skipped},
            {'unsupported file type', 'over 5MB', 'not a valid image'}
        )
        self.assertEqual(self.database_photo_count(), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_database_failure_removes_only_new_files_and_closes_connection(self):
        class FailingConnection:
            def __init__(self):
                self.rolled_back = False
                self.closed = False

            def execute(self, _query, _parameters):
                raise sqlite3.OperationalError('simulated insert failure')

            def commit(self):
                raise AssertionError('commit should not be reached')

            def rollback(self):
                self.rolled_back = True

            def close(self):
                self.closed = True

        connection = FailingConnection()
        existing_photo = self.upload_dir / 'part_999_existing.jpg'
        existing_photo.write_bytes(b'existing photo data')
        connections = iter((connection, self.get_db()))

        result = self.save([image_upload('phone.jpg')], get_db=lambda: next(connections))

        self.assertEqual(result.saved_count, 0)
        self.assertTrue(result.database_error)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.closed)
        self.assertEqual(list(self.upload_dir.iterdir()), [existing_photo])
        self.assertEqual(existing_photo.read_bytes(), b'existing photo data')
        self.assertNotIn('Safe to delete', photo_save_flash(result)[0])

    def test_unverified_association_does_not_leave_an_orphan_file(self):
        class DroppingSecondInsertConnection:
            def __init__(self, connection):
                self.connection = connection
                self.insert_count = 0

            def execute(self, query, parameters):
                if query.startswith('INSERT INTO part_photos'):
                    self.insert_count += 1
                    if self.insert_count == 2:
                        return self.connection.execute('SELECT 1')
                return self.connection.execute(query, parameters)

            def commit(self):
                self.connection.commit()

            def rollback(self):
                self.connection.rollback()

            def close(self):
                self.connection.close()

        transaction_connection = DroppingSecondInsertConnection(sqlite3.connect(self.database))
        connections = iter((transaction_connection, self.get_db()))

        result = self.save(
            [image_upload('verified.jpg'), image_upload('unassociated.jpg')],
            get_db=lambda: next(connections)
        )

        self.assertEqual((result.selected_count, result.saved_count), (2, 1))
        self.assertEqual(self.database_photo_count(), 1)
        self.assertEqual(len(list(self.upload_dir.glob('part_42_*.jpg'))), 1)
        self.assertIn('DO NOT DELETE ORIGINALS', photo_save_flash(result)[0])


if __name__ == '__main__':
    unittest.main()
