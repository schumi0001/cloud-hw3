const apiKey = (window.PHOTO_ALBUM_CONFIG || {}).apiKey;
const apiClient = apigClientFactory.newClient({ apiKey: apiKey || '' });

const searchForm = document.getElementById('search-form');
const uploadForm = document.getElementById('upload-form');
const searchButton = document.getElementById('search-button');
const uploadButton = document.getElementById('upload-button');
const searchStatus = document.getElementById('search-status');
const uploadStatus = document.getElementById('upload-status');
const results = document.getElementById('results');

function showStatus(element, message, isError = false) {
  element.textContent = message;
  element.className = isError ? 'status error' : 'status';
}

function errorMessage(error) {
  const data = error.response && error.response.data;
  if (data && data.message) return data.message;
  return error.message || 'Request failed. Please try again.';
}

searchForm.addEventListener('submit', async function (event) {
  event.preventDefault();
  if (searchButton.disabled) return;

  const query = document.getElementById('query').value.trim();
  if (!query) return;

  searchButton.disabled = true;
  results.replaceChildren();
  showStatus(searchStatus, 'Searching...');

  try {
    if (!apiKey) throw new Error('Website configuration is missing.');
    const response = await apiClient.searchGet({ q: query }, null, {});
    const data = typeof response.data === 'string'
      ? JSON.parse(response.data) : response.data;

    if (!data || !Array.isArray(data.results)) {
      throw new Error('Unexpected search response.');
    }

    for (const photo of data.results) {
      let url;
      try {
        url = new URL(photo.url);
      } catch (_) {
        continue;
      }
      if (url.protocol !== 'https:') continue;

      const labels = Array.isArray(photo.labels)
        ? photo.labels.filter(label => typeof label === 'string') : [];
      const card = document.createElement('figure');
      const link = document.createElement('a');
      const image = document.createElement('img');
      const caption = document.createElement('figcaption');

      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      image.src = url.href;
      image.alt = labels.join(', ') || 'Photo';
      image.loading = 'lazy';
      caption.textContent = 'Labels: ' + labels.join(', ');

      link.appendChild(image);
      card.append(link, caption);
      results.appendChild(card);
    }

    const count = results.children.length;
    showStatus(searchStatus, count ? count + ' photo(s) found.' : 'No photos found.');
  } catch (error) {
    showStatus(searchStatus, errorMessage(error), true);
  } finally {
    searchButton.disabled = false;
  }
});

uploadForm.addEventListener('submit', async function (event) {
  event.preventDefault();
  if (uploadButton.disabled) return;

  const fileInput = document.getElementById('photo-file');
  const labelsInput = document.getElementById('custom-labels');
  const file = fileInput.files[0];

  if (!file || !['image/jpeg', 'image/png'].includes(file.type)) {
    showStatus(uploadStatus, 'Choose a JPG or PNG image.', true);
    return;
  }
  if (!file.size || file.size > 10000000) {
    showStatus(uploadStatus, 'The image must be between 1 byte and 10 MB.', true);
    return;
  }

  const labels = [...new Set(
    labelsInput.value.split(',').map(label => label.trim()).filter(Boolean)
  )].join(', ');
  if (/[^\x20-\x7e]/.test(labels)) {
    showStatus(uploadStatus, 'Use English letters, numbers and punctuation for labels.', true);
    return;
  }

  // Add a prefix so uploading the same filename does not replace another photo.
  const filename = Date.now() + '-' + Math.random().toString(36).slice(2, 8) + '-' + file.name;
  if (new TextEncoder().encode(filename).length > 1024) {
    showStatus(uploadStatus, 'The filename is too long. Rename the file first.', true);
    return;
  }

  uploadButton.disabled = true;
  fileInput.disabled = true;
  labelsInput.disabled = true;
  showStatus(uploadStatus, 'Uploading...');

  try {
    if (!apiKey) throw new Error('Website configuration is missing.');
    await apiClient.photosPut({
      filename: filename,
      'Content-Type': file.type,
      'x-amz-meta-customLabels': labels
    }, file, {});

    uploadForm.reset();
    showStatus(uploadStatus, 'Upload complete. Wait a few seconds, then search for a label.');
  } catch (error) {
    showStatus(uploadStatus, errorMessage(error), true);
  } finally {
    uploadButton.disabled = false;
    fileInput.disabled = false;
    labelsInput.disabled = false;
  }
});
