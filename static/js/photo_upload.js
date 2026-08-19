(function (root, factory) {
    const api = factory();

    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }

    if (root) {
        root.PhotoUpload = api;
        if (root.document) {
            if (root.document.readyState === 'loading') {
                root.document.addEventListener('DOMContentLoaded', () => api.init(root.document));
            } else {
                api.init(root.document);
            }
        }
    }
})(typeof window !== 'undefined' ? window : null, function () {
    'use strict';

    const DEFAULT_MAX_DIMENSION = 1600;
    const DEFAULT_QUALITY = 0.82;
    const MAX_PHOTOS = 10;
    const MAX_ORIGINAL_FALLBACK_BYTES = 5 * 1024 * 1024;

    function calculateTargetDimensions(width, height, maxDimension) {
        if (width <= 0 || height <= 0 || maxDimension <= 0) {
            throw new Error('Image dimensions must be positive numbers.');
        }

        const scale = Math.min(1, maxDimension / Math.max(width, height));
        return {
            width: Math.max(1, Math.round(width * scale)),
            height: Math.max(1, Math.round(height * scale))
        };
    }

    function limitFiles(files, maxFiles) {
        const allFiles = Array.from(files);
        return {
            files: allFiles.slice(0, maxFiles),
            omittedCount: Math.max(0, allFiles.length - maxFiles)
        };
    }

    function canUseOriginalFallback(file, maxBytes) {
        return file.size <= maxBytes;
    }

    function loadImage(file) {
        return new Promise((resolve, reject) => {
            const objectUrl = URL.createObjectURL(file);
            const image = new Image();

            image.onload = () => resolve({ image, objectUrl });
            image.onerror = () => {
                URL.revokeObjectURL(objectUrl);
                reject(new Error(`The browser could not read ${file.name}.`));
            };
            image.src = objectUrl;
        });
    }

    function canvasToBlob(canvas, quality) {
        return new Promise((resolve, reject) => {
            canvas.toBlob((blob) => {
                if (blob) {
                    resolve(blob);
                } else {
                    reject(new Error('The browser could not create a compressed image.'));
                }
            }, 'image/jpeg', quality);
        });
    }

    function jpegFilename(filename) {
        const base = filename.replace(/\.[^.]*$/, '') || 'photo';
        return `${base}.jpg`;
    }

    async function compressImage(file, options) {
        const settings = options || {};
        const maxDimension = settings.maxDimension || DEFAULT_MAX_DIMENSION;
        const quality = settings.quality || DEFAULT_QUALITY;
        const loaded = await loadImage(file);

        try {
            const dimensions = calculateTargetDimensions(
                loaded.image.naturalWidth,
                loaded.image.naturalHeight,
                maxDimension
            );
            const canvas = document.createElement('canvas');
            canvas.width = dimensions.width;
            canvas.height = dimensions.height;

            const context = canvas.getContext('2d');
            if (!context) {
                throw new Error('Image resizing is not supported by this browser.');
            }

            context.drawImage(loaded.image, 0, 0, dimensions.width, dimensions.height);
            const blob = await canvasToBlob(canvas, quality);

            // Small images are sometimes already more compact than a new JPEG.
            // Keeping the smaller file avoids making an upload slower.
            if (blob.size >= file.size) {
                return file;
            }

            return new File([blob], jpegFilename(file.name), {
                type: 'image/jpeg',
                lastModified: file.lastModified
            });
        } finally {
            URL.revokeObjectURL(loaded.objectUrl);
        }
    }

    async function prepareFiles(files, options) {
        const prepared = [];
        const fallbackNames = [];
        const oversizedFallbackNames = [];

        // Process one at a time to avoid holding several full-size phone photos
        // in memory at once on older mobile devices.
        for (const file of files) {
            try {
                prepared.push(await compressImage(file, options));
            } catch (error) {
                console.warn(`Could not compress ${file.name}:`, error);
                prepared.push(file);
                if (canUseOriginalFallback(file, MAX_ORIGINAL_FALLBACK_BYTES)) {
                    fallbackNames.push(file.name);
                } else {
                    oversizedFallbackNames.push(file.name);
                }
            }
        }

        return { files: prepared, fallbackNames, oversizedFallbackNames };
    }

    function replaceInputFiles(input, files) {
        const transfer = new DataTransfer();
        files.forEach((file) => transfer.items.add(file));
        input.files = transfer.files;
    }

    function setStatus(element, message, state) {
        element.textContent = message;
        element.hidden = !message;
        element.classList.toggle('text-red-400', state === 'error');
        element.classList.toggle('text-yellow-400', state === 'warning');
        element.classList.toggle('text-green-400', state === 'success');
        element.classList.toggle('text-slate-300', !state || state === 'working');
    }

    function setSubmitButtonsDisabled(form, disabled) {
        form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach((button) => {
            button.disabled = disabled;
            button.classList.toggle('opacity-60', disabled);
            button.classList.toggle('cursor-wait', disabled);
        });
    }

    function bind(form) {
        if (form.dataset.photoUploadBound === 'true') {
            return;
        }

        const input = form.querySelector('[data-photo-upload-input]');
        const status = form.querySelector('[data-photo-upload-status]');
        if (!input || !status) {
            return;
        }

        form.dataset.photoUploadBound = 'true';
        let preparedForSubmit = false;
        let busy = false;

        input.addEventListener('change', () => {
            preparedForSubmit = false;
            setStatus(status, '', null);
        });

        form.addEventListener('submit', async (event) => {
            let selectedFiles = Array.from(input.files || []);
            if (!selectedFiles.length) {
                return;
            }

            if (preparedForSubmit) {
                setStatus(status, 'Uploading photos...', 'working');
                setSubmitButtonsDisabled(form, true);
                return;
            }

            event.preventDefault();
            if (busy) {
                return;
            }

            busy = true;
            setSubmitButtonsDisabled(form, true);

            try {
                const limitedSelection = limitFiles(selectedFiles, MAX_PHOTOS);
                const selectionWasTrimmed = limitedSelection.omittedCount > 0;
                selectedFiles = limitedSelection.files;

                if (selectionWasTrimmed) {
                    try {
                        replaceInputFiles(input, selectedFiles);
                    } catch (error) {
                        console.warn('This browser could not enforce the photo limit:', error);
                        setStatus(
                            status,
                            'Only 10 photos are allowed. Please choose no more than 10 photos and try again.',
                            'error'
                        );
                        return;
                    }
                }

                setStatus(
                    status,
                    selectionWasTrimmed
                        ? 'Only 10 photos are allowed. Preparing the first 10 photos...'
                        : 'Preparing photos...',
                    selectionWasTrimmed ? 'warning' : 'working'
                );

                const result = await prepareFiles(selectedFiles, {
                    maxDimension: DEFAULT_MAX_DIMENSION,
                    quality: DEFAULT_QUALITY
                });

                if (result.oversizedFallbackNames.length) {
                    const names = result.oversizedFallbackNames.join(', ');
                    setStatus(
                        status,
                        `Could not resize: ${names}. The original photo${result.oversizedFallbackNames.length === 1 ? ' is' : 's are'} over the 5 MB server limit and cannot be submitted. Choose a smaller or replacement photo.`,
                        'error'
                    );
                    return;
                }

                try {
                    replaceInputFiles(input, result.files);
                } catch (error) {
                    console.warn('This browser could not attach the compressed photos:', error);
                    const oversizedOriginalNames = selectedFiles
                        .filter((file) => !canUseOriginalFallback(file, MAX_ORIGINAL_FALLBACK_BYTES))
                        .map((file) => file.name);
                    if (oversizedOriginalNames.length) {
                        const names = oversizedOriginalNames.join(', ');
                        setStatus(
                            status,
                            `This browser could not attach the resized photos. The original file${oversizedOriginalNames.length === 1 ? '' : 's'} (${names}) ${oversizedOriginalNames.length === 1 ? 'is' : 'are'} over the 5 MB server limit and cannot be submitted. Choose a smaller or replacement photo.`,
                            'error'
                        );
                        return;
                    }
                    preparedForSubmit = true;
                    setStatus(
                        status,
                        'This browser could not attach the resized photos. The originals are ready instead. Submit again to continue.',
                        'warning'
                    );
                    return;
                }
                preparedForSubmit = true;

                if (result.fallbackNames.length || selectionWasTrimmed) {
                    const names = result.fallbackNames.join(', ');
                    const limitMessage = selectionWasTrimmed
                        ? 'Only 10 photos are allowed; the first 10 are ready. '
                        : '';
                    const fallbackMessage = result.fallbackNames.length
                        ? `Could not resize: ${names}. The original photo${result.fallbackNames.length === 1 ? ' is' : 's are'} ready instead. `
                        : '';
                    setStatus(
                        status,
                        `${limitMessage}${fallbackMessage}Submit again to continue.`,
                        'warning'
                    );
                    return;
                }

                setStatus(status, 'Uploading photos...', 'working');
                HTMLFormElement.prototype.submit.call(form);
            } catch (error) {
                console.error('Photo preparation failed:', error);
                preparedForSubmit = false;
                setStatus(status, 'Photos could not be prepared. Please choose them again and retry.', 'error');
            } finally {
                busy = false;
                if (!preparedForSubmit || status.classList.contains('text-yellow-400')) {
                    setSubmitButtonsDisabled(form, false);
                }
            }
        });
    }

    function init(scope) {
        scope.querySelectorAll('[data-photo-upload-form]').forEach(bind);
    }

    return {
        calculateTargetDimensions,
        limitFiles,
        canUseOriginalFallback,
        compressImage,
        prepareFiles,
        bind,
        init
    };
});
