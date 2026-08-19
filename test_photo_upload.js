const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
    calculateTargetDimensions,
    limitFiles,
    canUseOriginalFallback,
    decodedDimensions,
    shouldKeepOriginal
} = require('./static/js/photo_upload.js');

test('large landscape photos are scaled to a 1600px maximum dimension', () => {
    assert.deepEqual(calculateTargetDimensions(4000, 3000, 1600), {
        width: 1600,
        height: 1200
    });
});

test('large portrait photos retain their aspect ratio', () => {
    assert.deepEqual(calculateTargetDimensions(3024, 4032, 1600), {
        width: 1200,
        height: 1600
    });
});

test('small photos are not enlarged', () => {
    assert.deepEqual(calculateTargetDimensions(800, 600, 1600), {
        width: 800,
        height: 600
    });
});

test('only the first 10 selected photos are prepared', () => {
    const files = Array.from({ length: 12 }, (_, index) => `photo-${index + 1}`);
    assert.deepEqual(limitFiles(files, 10), {
        files: files.slice(0, 10),
        omittedCount: 2
    });
});

test('original fallback is allowed only at or below the 5 MB limit', () => {
    const fiveMegabytes = 5 * 1024 * 1024;
    assert.equal(canUseOriginalFallback({ size: fiveMegabytes }, fiveMegabytes), true);
    assert.equal(canUseOriginalFallback({ size: fiveMegabytes + 1 }, fiveMegabytes), false);
});

test('decoded dimensions support ImageBitmap and HTML image sources', () => {
    assert.deepEqual(decodedDimensions({ width: 4032, height: 3024 }), {
        width: 4032,
        height: 3024
    });
    assert.deepEqual(decodedDimensions({ naturalWidth: 3024, naturalHeight: 4032 }), {
        width: 3024,
        height: 4032
    });
    assert.throws(
        () => decodedDimensions({ width: 0, height: 3024 }),
        /invalid dimensions/
    );
});

test('an oversized original is never preferred over a compressed image', () => {
    const fiveMegabytes = 5 * 1024 * 1024;
    assert.equal(shouldKeepOriginal(2_000_000, 2_100_000, fiveMegabytes), true);
    assert.equal(shouldKeepOriginal(6_000_000, 6_100_000, fiveMegabytes), false);
    assert.equal(shouldKeepOriginal(6_000_000, 4_000_000, fiveMegabytes), false);
});

test('Edit Part wires only its new-photo form to the shared uploader', () => {
    const template = fs.readFileSync(
        path.join(__dirname, 'templates', 'parts_edit.html'),
        'utf8'
    );
    const uploadForm = template.match(
        /<form action="\/parts\/upload-photo\/\{\{ part\.id \}\}"[\s\S]*?<\/form>/
    );

    assert.ok(uploadForm, 'Edit Part should keep its dedicated new-photo upload form');
    assert.match(uploadForm[0], /data-photo-upload-form/);
    assert.match(uploadForm[0], /name="photos"[^>]*data-photo-upload-input/);
    assert.match(uploadForm[0], /data-photo-upload-status/);
    assert.doesNotMatch(uploadForm[0], /delete-photo|reorder-photo/);
    assert.match(template, /static', filename='js\/photo_upload\.js'/);
});
