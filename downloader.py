#!/usr/bin/env python3
"""Import the approved manifest into Git, preserving the source URL paths."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = 'ebooks.iskcondesiretree.com'
MAX_FILE = 100 * 1024 * 1024 - 1
MAX_TOTAL = 8 * 1024**3
EXPECTED_COUNT = 3201
EXPECTED_SHA = '5552cb245abf5542235f9d7a12c4e25c51ce95f60f3c7178486cb04b0c0ad033'
ROOT = Path.cwd().resolve()
REPORT = ROOT / 'reports/status.json'
START = time.monotonic()

class SizeLimit(Exception):
    pass

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname != HOST:
            raise ValueError('Redirect outside the approved HTTPS source')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def target(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != HOST or parsed.query or parsed.fragment:
        raise ValueError('Manifest URL is outside the approved source')
    decoded = urllib.parse.unquote(parsed.path)
    parts = decoded.lstrip('/').split('/')
    if (not decoded.startswith('/pdf/') or '\\' in decoded
            or any(p in ('', '.', '..', '.git') for p in parts)
            or any(ord(c) < 32 for c in decoded)):
        raise ValueError('Unsafe destination path')
    path = ROOT.joinpath(*parts)
    if ROOT not in path.resolve().parents:
        raise ValueError('Destination escapes the repository')
    return path

def manifest():
    source = ROOT / 'urls.txt'
    if not source.exists():
        source = ROOT / 'idt.txt'
    if not source.exists():
        raise FileNotFoundError('Upload the original idt.txt to the repository root')
    urls = list(dict.fromkeys(line.strip() for line in
                source.read_text(encoding='utf-8-sig').splitlines() if line.strip()))
    canonical = ('\n'.join(urls) + '\n').encode()
    if len(urls) != EXPECTED_COUNT or hashlib.sha256(canonical).hexdigest() != EXPECTED_SHA:
        raise ValueError('Manifest does not match the approved 3,201-URL input')
    paths = [target(u) for u in urls]
    if len(set(paths)) != len(paths):
        raise ValueError('Two URLs map to the same destination')
    (ROOT / 'urls.txt').write_bytes(canonical)
    return urls

def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def download(url):
    path = target(url)
    temp = path.with_name(path.name + '.part')
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            opener = urllib.request.build_opener(SafeRedirect())
            request = urllib.request.Request(url, headers={
                'User-Agent': 'IDT-Ebooks-Mirror/1.0', 'Accept-Encoding': 'identity'})
            with opener.open(request, timeout=60) as response:
                declared = response.headers.get('Content-Length')
                declared = int(declared) if declared else None
                if declared is not None and declared > MAX_FILE:
                    raise SizeLimit(f'File exceeds GitHub 100 MiB limit ({declared} bytes)')
                size, head, digest = 0, b'', hashlib.sha256()
                deadline = time.monotonic() + 600
                with temp.open('wb') as output:
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError('Download exceeded 10 minutes')
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > MAX_FILE:
                            raise SizeLimit('File exceeds GitHub 100 MiB limit')
                        if not head:
                            head = block[:1024]
                        output.write(block)
                        digest.update(block)
                if not size or (declared is not None and size != declared):
                    raise ValueError('Empty or incomplete download')
                if path.suffix.lower() == '.pdf' and b'%PDF-' not in head:
                    raise ValueError('Server returned a non-PDF response')
                if path.suffix.lower() == '.zip' and not head.startswith(b'PK'):
                    raise ValueError('Server returned a non-ZIP response')
                temp.replace(path)
                return {'status': 'downloaded', 'bytes': size,
                        'sha256': digest.hexdigest(), 'attempts': attempt + 1}
        except SizeLimit as exc:
            temp.unlink(missing_ok=True)
            return {'status': 'oversized', 'error': str(exc)}
        except Exception as exc:
            temp.unlink(missing_ok=True)
            error = f'{type(exc).__name__}: {exc}'
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (404, 410):
                break
            if attempt < 2:
                time.sleep(3 * 2**attempt)
    return {'status': 'failed', 'error': error, 'attempts': attempt + 1}

def git(*args):
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True)

def checkpoint(urls, entries, stopped=None):
    counts = {}
    for url in urls:
        state = entries.get(url, {}).get('status', 'pending')
        counts[state] = counts.get(state, 0) + 1
    data = {'total': len(urls), 'counts': counts,
            'complete': counts.get('downloaded', 0) == len(urls),
            'stopped': stopped, 'files': entries}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    git('add', '--', 'pdf', 'reports/status.json', 'urls.txt')
    if git('diff', '--cached', '--name-only').stdout.strip():
        git('commit', '-m', f"Import ebooks: {counts.get('downloaded', 0)}/{len(urls)}")
        for attempt in range(3):
            try:
                git('push', 'origin', 'HEAD:main')
                break
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
    print(json.dumps({k: v for k, v in data.items() if k != 'files'}), flush=True)
    return data

def main():
    urls = manifest()
    (ROOT / 'pdf').mkdir(exist_ok=True)
    entries = json.loads(REPORT.read_text()).get('files', {}) if REPORT.exists() else {}
    pending, total_bytes = [], 0
    for url in urls:
        path, entry = target(url), entries.get(url, {})
        if (entry.get('status') == 'downloaded' and path.is_file()
                and path.stat().st_size == entry.get('bytes')
                and digest_file(path) == entry.get('sha256')):
            total_bytes += path.stat().st_size
        else:
            pending.append(url)
    stopped = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for offset in range(0, len(pending), 12):
            if time.monotonic() - START > 240 * 60:
                stopped = 'Time safety limit reached; rerun the Action to resume'
                break
            if total_bytes + min(12, len(pending) - offset) * MAX_FILE > MAX_TOTAL:
                stopped = '8 GiB repository safety budget reached; no files silently omitted'
                break
            if shutil.disk_usage(ROOT).free < 3 * 1024**3:
                stopped = 'Runner disk safety limit reached; rerun to resume'
                break
            batch = pending[offset:offset + 12]
            for url, result in zip(batch, pool.map(download, batch)):
                entries[url] = result
                if result['status'] == 'downloaded':
                    total_bytes += result['bytes']
                print(f"{result['status']}: {target(url).relative_to(ROOT)}", flush=True)
            checkpoint(urls, entries)
    data = checkpoint(urls, entries, stopped)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as file:
            file.write(f'# Ebook import\n\nTotal: {len(urls)}\n\n')
            for state, count in data['counts'].items():
                file.write(f'- {state}: {count}\n')
            if stopped:
                file.write(f'\nStopped: {stopped}\n')
            file.write('\nSee `reports/status.json` for every URL and any error. '
                       'Rerun this Action to resume or retry.\n')
    return 0 if data['complete'] else 1

if __name__ == '__main__':
    sys.exit(main())
