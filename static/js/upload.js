/**
 * upload.js – 다중 파일 → 하나의 게시물
 */

'use strict';

const CHUNK_SIZE = 10 * 1024 * 1024;

/* ── DOM 참조 ──────────────────────────────────────────────────────── */
const dropZone      = document.getElementById('drop-zone');
const fileInput     = document.getElementById('file-input');
const fileList      = document.getElementById('file-list');
const uploadForm    = document.getElementById('upload-form');
const submitBtn     = document.getElementById('submit-btn');
const progressSec   = document.getElementById('progress-section');
const progressBar   = document.getElementById('progress-bar');
const progressLabel = document.getElementById('progress-label');
const progressPct   = document.getElementById('progress-pct');
const progressDetail= document.getElementById('progress-detail');
const uploadResult  = document.getElementById('upload-result');
const titleInput    = document.getElementById('title');
const titleHint     = document.getElementById('title-hint');

let selectedFiles = [];

/* ── 파일 선택 ─────────────────────────────────────────────────────── */
dropZone.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', () => {
  if (fileInput.files.length) handleFilesSelect(Array.from(fileInput.files));
});

dropZone.addEventListener('dragover', e => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files.length) handleFilesSelect(Array.from(e.dataTransfer.files));
});

function handleFilesSelect(files) {
  selectedFiles = files;
  renderFileList(files);
  submitBtn.disabled = false;
  hideResult();

  if (files.length === 1) {
    titleHint.textContent = '';
  } else {
    titleHint.textContent = `파일 ${files.length}개 → 게시물 1개`;
  }
}

function renderFileList(files) {
  fileList.innerHTML = '';
  files.forEach((f, i) => {
    const item = document.createElement('div');
    item.className = 'file-preview';
    item.id = `file-item-${i}`;
    item.innerHTML = `
      <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20" style="flex-shrink:0;color:#4f8ef7">
        <path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9
                 2-2V8l-6-6zm4 18H6V4h7v5h5v11z"/>
      </svg>
      <span class="file-preview-name">${escHtml(f.name)}</span>
      <span class="file-preview-size">${fmtSize(f.size)}</span>
      <span class="file-item-status" id="file-status-${i}"></span>
    `;
    fileList.appendChild(item);
  });
  fileList.classList.remove('hidden');
}

function setFileStatus(index, type, text) {
  const el = document.getElementById(`file-status-${index}`);
  if (!el) return;
  el.textContent = text;
  el.style.color = type === 'done' ? '#2ecc71' : type === 'error' ? '#e74c3c' : '#f39c12';
}

/* ── 폼 제출 ───────────────────────────────────────────────────────── */
uploadForm.addEventListener('submit', async e => {
  e.preventDefault();
  if (!selectedFiles.length) return;

  const title = titleInput.value.trim();
  const description = document.getElementById('description').value.trim();

  if (!title) {
    showResult('error', '제목을 입력하세요.');
    return;
  }

  submitBtn.disabled = true;
  hideResult();
  showProgress('업로드 준비 중...');

  const uploadedFiles = [];
  let failCount = 0;

  for (let i = 0; i < selectedFiles.length; i++) {
    const file = selectedFiles[i];
    setFileStatus(i, 'uploading', '업로드 중...');
    try {
      const result = await uploadToS3(file, i, selectedFiles.length);
      uploadedFiles.push(result);
      setFileStatus(i, 'done', '완료');
    } catch (err) {
      setFileStatus(i, 'error', `실패: ${err.message}`);
      failCount++;
    }
  }

  if (uploadedFiles.length === 0) {
    showResult('error', '모든 파일 업로드 실패');
    hideProgress();
    submitBtn.disabled = false;
    return;
  }

  setProgress(98, '게시물 저장 중...');
  try {
    await fetchJSON('/api/media', { title, description, files: uploadedFiles }, 'POST');
    window.location.href = '/';
  } catch (err) {
    showResult('error', `저장 실패: ${err.message}`);
    hideProgress();
    submitBtn.disabled = false;
  }
});

/* ── S3 업로드 (DB 저장 없음) ──────────────────────────────────────── */
async function uploadToS3(file, index, total) {
  if (file.size > MULTIPART_THRESHOLD) {
    return await multipartToS3(file, index, total);
  } else {
    return await simpleToS3(file, index, total);
  }
}

async function simpleToS3(file, index, total) {
  const label = total > 1 ? `(${index + 1}/${total}) ` : '';
  setProgress(10, `${label}Presigned URL 발급 중...`);

  const presignRes = await fetchJSON('/api/presign', {
    filename: file.name,
    fileType: file.type,
    fileSize: file.size,
  });

  const { presigned, key } = presignRes;
  const formData = new FormData();
  Object.entries(presigned.fields).forEach(([k, v]) => formData.append(k, v));
  formData.append('file', file);

  setProgress(20, `${label}S3 업로드 중...`);
  await xhrUpload(presigned.url, formData, (pct) => {
    setProgress(20 + pct * 0.75, `${label}업로드 중... ${Math.round(pct)}%`);
  });

  setProgress(95, `${label}완료`);
  return { key, fileType: file.type, fileSize: file.size };
}

async function multipartToS3(file, index, total) {
  const label = total > 1 ? `(${index + 1}/${total}) ` : '';
  setProgress(5, `${label}Multipart 초기화 중...`);

  const initRes = await fetchJSON('/api/multipart/init', {
    filename: file.name,
    fileType: file.type,
  });
  const { uploadId, key } = initRes;

  const totalParts = Math.ceil(file.size / CHUNK_SIZE);
  const parts = [];

  for (let i = 0; i < totalParts; i++) {
    const partNumber = i + 1;
    const start = i * CHUNK_SIZE;
    const end   = Math.min(start + CHUNK_SIZE, file.size);
    const chunk = file.slice(start, end);

    const basePct = 5 + (i / totalParts) * 85;
    setProgress(basePct, `${label}파트 ${partNumber}/${totalParts} 업로드 중...`,
      `${fmtSize(end)} / ${fmtSize(file.size)}`);

    const partRes = await fetchJSON('/api/multipart/presign-part', {
      key, uploadId, partNumber,
    });

    const etag = await xhrPutChunk(partRes.url, chunk, (pct) => {
      setProgress(basePct + (pct / 100) * (85 / totalParts),
        `${label}파트 ${partNumber}/${totalParts} ${Math.round(pct)}%`,
        `${fmtSize(end)} / ${fmtSize(file.size)}`);
    });

    parts.push({ PartNumber: partNumber, ETag: etag });
  }

  setProgress(92, `${label}완료 처리 중...`);
  await fetchJSON('/api/multipart/complete', { key, uploadId, parts });

  return { key, fileType: file.type, fileSize: file.size };
}

/* ── XHR 헬퍼 ──────────────────────────────────────────────────────── */
function xhrUpload(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) onProgress((e.loaded / e.total) * 100);
    };
    xhr.onload  = () => (xhr.status < 300 ? resolve() : reject(new Error(`HTTP ${xhr.status}`)));
    xhr.onerror = () => reject(new Error('네트워크 오류'));
    xhr.send(formData);
  });
}

function xhrPutChunk(url, chunk, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) onProgress((e.loaded / e.total) * 100);
    };
    xhr.onload = () => {
      if (xhr.status < 300) {
        const etag = xhr.getResponseHeader('ETag') || '';
        resolve(etag.replace(/"/g, ''));
      } else {
        reject(new Error(`HTTP ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error('네트워크 오류'));
    xhr.send(chunk);
  });
}

async function fetchJSON(url, body, method = 'POST') {
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ── UI 헬퍼 ───────────────────────────────────────────────────────── */
function showProgress(label, detail = '') {
  progressSec.classList.remove('hidden');
  progressLabel.textContent  = label;
  progressDetail.textContent = detail;
  progressPct.textContent    = '0%';
  progressBar.style.width    = '0%';
}

function setProgress(pct, label, detail = '') {
  progressSec.classList.remove('hidden');
  const p = Math.min(100, Math.round(pct));
  progressBar.style.width   = p + '%';
  progressPct.textContent   = p + '%';
  progressLabel.textContent = label;
  if (detail) progressDetail.textContent = detail;
}

function hideProgress() {
  progressSec.classList.add('hidden');
}

function showResult(type, html) {
  uploadResult.className = `upload-result ${type}`;
  uploadResult.innerHTML = html;
  uploadResult.classList.remove('hidden');
}

function hideResult() {
  uploadResult.classList.add('hidden');
  uploadResult.innerHTML = '';
}

function fmtSize(bytes) {
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(1)} ${units[i]}`;
}

function escHtml(str) {
  return str.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
