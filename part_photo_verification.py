import os
import uuid
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class PartPhotoSaveResult:
    selected_count: int
    saved_count: int
    skipped: tuple
    truncated_count: int = 0
    database_error: bool = False


def photo_save_flash(result):
    if result.selected_count == 0:
        return '✅ Part added successfully!', 'success'

    if result.saved_count == result.selected_count:
        return (
            f'✅ Part added successfully — {result.saved_count}/{result.selected_count} '
            'photos saved and verified. Keep the originals until the next backup has completed.',
            'success'
        )

    return (
        f'⚠️ Part added, but only {result.saved_count}/{result.selected_count} photos saved. '
        'DO NOT DELETE ORIGINALS. Please retry the missing photo.',
        'warning'
    )


def _remove_new_file(filepath, source_name):
    if filepath and os.path.lexists(filepath):
        try:
            os.remove(filepath)
        except OSError as error:
            print(
                f"⚠️ Failed to clean up new photo '{source_name}' at '{filepath}': "
                f'{type(error).__name__}: {error}',
                flush=True
            )


def _verified_file_exists(filepath):
    try:
        return os.path.isfile(filepath) and os.path.getsize(filepath) > 0
    except OSError:
        return False


def _row_photo_url(row):
    try:
        return row['photo_url']
    except (IndexError, KeyError, TypeError):
        return row[0]


def save_part_photos(
    files,
    part_id,
    tenant_id,
    upload_dir,
    get_db,
    allowed_file,
    max_files,
    max_bytes,
    max_dimension,
):
    """Write and associate new-part photos, returning conservative verified counts."""
    submitted_files = [file for file in files if file and file.filename]
    selected_count = len(submitted_files)
    truncated_count = max(0, selected_count - max_files)
    candidates = submitted_files[:max_files]
    skipped = [
        (file.filename, f'maximum {max_files} photos per upload')
        for file in submitted_files[max_files:]
    ]
    written_photos = []

    # Decode, resize and write without holding a database connection or lock.
    for file in candidates:
        if not allowed_file(file.filename):
            skipped.append((file.filename, 'unsupported file type'))
            continue

        try:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)
        except Exception as error:
            print(f"⚠️ Failed to read '{file.filename}': {type(error).__name__}: {error}", flush=True)
            skipped.append((file.filename, 'unreadable file'))
            continue

        if size > max_bytes:
            skipped.append((file.filename, 'over 5MB'))
            continue

        filepath = None
        filepath_is_new = False
        image = None
        try:
            with Image.open(file) as source_image:
                source_image.load()
                image = source_image.copy() if source_image.mode == 'RGB' else source_image.convert('RGB')

            if image.width > max_dimension or image.height > max_dimension:
                image.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

            filename = f'part_{part_id}_{uuid.uuid4().hex}.jpg'
            filepath = os.path.join(upload_dir, filename)
            with open(filepath, 'xb') as output_file:
                filepath_is_new = True
                image.save(output_file, format='JPEG', quality=85)

            if not _verified_file_exists(filepath):
                raise OSError('saved image file could not be verified')
        except Exception as error:
            print(f"⚠️ Failed to process '{file.filename}': {type(error).__name__}: {error}", flush=True)
            if filepath_is_new:
                _remove_new_file(filepath, file.filename)
            skipped.append((file.filename, 'not a valid image'))
            continue
        finally:
            if image is not None:
                image.close()

        written_photos.append({
            'source_name': file.filename,
            'filepath': filepath,
            'web_url': f'/uploads/parts/{filename}',
        })

    if not written_photos:
        return PartPhotoSaveResult(
            selected_count=selected_count,
            saved_count=0,
            skipped=tuple(skipped),
            truncated_count=truncated_count,
        )

    # Associate all disk-written files in one short transaction, then read the
    # rows back. A photo counts as saved only when both its path and row verify.
    transaction_db = None
    associated_urls = set()
    database_error = False
    try:
        transaction_db = get_db()
        for photo_order, photo in enumerate(written_photos, start=1):
            transaction_db.execute(
                'INSERT INTO part_photos (part_id, photo_url, photo_order, tenant_id) VALUES (?, ?, ?, ?)',
                (part_id, photo['web_url'], photo_order, tenant_id)
            )
        transaction_db.commit()
    except Exception as error:
        database_error = True
        print(f"❌ part_photos transaction failed for part {part_id}: {type(error).__name__}: {error}", flush=True)
        if transaction_db is not None:
            try:
                transaction_db.rollback()
            except Exception as rollback_error:
                print(
                    f"❌ part_photos rollback failed for part {part_id}: "
                    f'{type(rollback_error).__name__}: {rollback_error}',
                    flush=True
                )
    finally:
        if transaction_db is not None:
            try:
                transaction_db.close()
            except Exception as error:
                database_error = True
                print(f"❌ part_photos connection close failed for part {part_id}: {type(error).__name__}: {error}", flush=True)

    # Verify with a fresh connection after the write connection is closed. This
    # sees only durable rows, including the outcome of an ambiguous commit error.
    verification_completed = False
    verification_db = None
    try:
        verification_db = get_db()
        placeholders = ','.join('?' for _ in written_photos)
        expected_urls = [photo['web_url'] for photo in written_photos]
        rows = verification_db.execute(
            f'SELECT photo_url FROM part_photos '
            f'WHERE part_id = ? AND tenant_id = ? AND photo_url IN ({placeholders})',
            (part_id, tenant_id, *expected_urls)
        ).fetchall()
        associated_urls = {_row_photo_url(row) for row in rows}
        verification_completed = True
    except Exception as error:
        database_error = True
        associated_urls.clear()
        print(f"❌ part_photos verification failed for part {part_id}: {type(error).__name__}: {error}", flush=True)
    finally:
        if verification_db is not None:
            try:
                verification_db.close()
            except Exception as error:
                database_error = True
                print(f"❌ part_photos verification close failed for part {part_id}: {type(error).__name__}: {error}", flush=True)

    saved_count = 0
    for photo in written_photos:
        association_verified = verification_completed and photo['web_url'] in associated_urls
        if _verified_file_exists(photo['filepath']) and association_verified:
            saved_count += 1
        else:
            # Every path here was confirmed absent before this request wrote it.
            # Delete only when a fresh DB read proves that exact URL is absent.
            if verification_completed and not association_verified:
                _remove_new_file(photo['filepath'], photo['source_name'])
            skipped.append((photo['source_name'], 'photo record/path verification failed'))

    return PartPhotoSaveResult(
        selected_count=selected_count,
        saved_count=saved_count,
        skipped=tuple(skipped),
        truncated_count=truncated_count,
        database_error=database_error,
    )
