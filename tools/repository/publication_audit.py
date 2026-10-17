"""Read-only history audit and exact pinned publication inventory; never pushes.

Run from this worktree: python tools/repository/publication_audit.py
Reports are local ignored artifacts under data/work/publish-audit.
The credential scan is heuristic, not a claim of exhaustive secret detection.
"""
import datetime
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/work/publish-audit'


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args]).decode('utf-8')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    branches = ['main', 'task/frontend', 'task/backend-performance', 'task/signals',
                'task/congestion-coordination', 'task/repository-setup']
    refs = {b: git('rev-parse', b).strip() for b in branches}
    commits = git('rev-list', '--reverse', '--topo-order', *refs.values()).splitlines()
    lines = ['# Proposed GitHub publication (NOT pushed)', '',
             'Generated: ' + datetime.datetime.now().astimezone().isoformat(), '',
             'Approval applies only to these commit IDs. No uncommitted files, tags,',
             'scenario branch, or future branch tips are included. Existing history is retained.', '',
             'Frontend and signals snapshots are experimental; both owners have newer local edits.',
             'Publishing these snapshots does not certify congestion/safety or authorize integration.', '',
             '## Branches', '', '| Branch | Exact tip |', '|---|---|']
    lines += [f'| {b} | {s} |' for b, s in refs.items()]
    lines += ['', '## Exact push commands (approval required; do not force)', '', '```powershell',
              "Set-Location 'D:\\SUMA test'"]
    lines += [f'git push origin {s}:refs/heads/{b}' for b, s in refs.items()]
    lines += ['```', '', '## All commits and changed files (including root commit)', '']
    for sha in commits:
        lines += ['### ' + sha, '', git('show', '-s', '--format=fuller', '--date=iso-strict', sha),
                  '```text', git('diff-tree', '--root', '--no-commit-id', '--name-status', '-r', sha), '```', '']
    lines += ['## Exact complete file tree at every published branch tip', '']
    for b, sha in refs.items():
        lines += [f'### {b}', '', '```text', git('ls-tree', '-r', '--long', sha), '```', '']
    (OUT / 'PUBLICATION_MANIFEST.md').write_text('\n'.join(lines), encoding='utf-8')
    (OUT / 'publication-refs.json').write_text(json.dumps(refs, indent=2), encoding='utf-8')

    # Read every blob reachable from the proposed snapshots, including historical versions.
    objects = git('rev-list', '--objects', *refs.values()).splitlines()
    paths = dict(x.split(' ', 1) for x in objects if ' ' in x)
    ids = [x.split(' ', 1)[0] for x in objects]
    batch = subprocess.run(['git', '-C', str(ROOT), 'cat-file', '--batch'],
                           input=('\n'.join(ids) + '\n').encode(), capture_output=True, check=True).stdout
    patterns = {
        'private-key': rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
        'github-token': rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b',
        'cloud-key': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
        'stripe-key': rb'\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b',
        'google-key': rb'\bAIza[A-Za-z0-9_-]{30,}\b',
        'assigned-secret': rb'''(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[=:]\s*["']([^"'\s]{12,})["']''',
    }
    findings, large, blobs, total = [], [], 0, 0
    offset = 0
    for oid in ids:
        end = batch.index(b'\n', offset)
        header = batch[offset:end].decode().split()
        size = int(header[2])
        content = batch[end + 1:end + 1 + size]
        offset = end + size + 2
        if header[1] != 'blob':
            continue
        blobs += 1
        total += size
        path = paths.get(oid, '(unknown path)')
        if size > 10 * 1024 * 1024:
            large.append({'blob': oid, 'path': path, 'bytes': size})
        for name, pattern in patterns.items():
            if re.search(pattern, content):
                findings.append({'blob': oid, 'path': path, 'kind': name})
    suspicious = sorted({p for p in paths.values() if re.search(
        r'(^|/)(?:node_modules|\.venv|venv|\.vercel|__pycache__)(/|$)|(^|/)\.env(?:\.|$)|\.(?:pem|key|p12|pfx)$', p)
        and not p.endswith('.example')})
    result = {'blobs_scanned': blobs, 'bytes_scanned': total, 'findings_redacted': findings,
              'suspicious_paths': suspicious, 'blobs_over_10_MiB': large,
              'limitation': 'Heuristic signatures and filenames; not exhaustive secret detection.'}
    (OUT / 'SECURITY_AUDIT.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    print('Manifest:', OUT / 'PUBLICATION_MANIFEST.md')


if __name__ == '__main__':
    main()
