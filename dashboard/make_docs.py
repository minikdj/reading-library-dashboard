#!/usr/bin/env python3
"""Write the dashboard's store documents (datasets/*, files/index.html, dash/meta) as JSON files
under dashboard/docs/, ready for an ArtifactData batch write. Run after `pipeline/bundle.py`
and after uploading any changed data/out/ds/*.json as artifact assets (update the blob ids in
dashboard/datasets.json first).

Usage: python3 dashboard/make_docs.py [--updated-by NAME]
"""
import json, os, sys, datetime
cfg = json.load(open('dashboard/datasets.json'))
by = sys.argv[sys.argv.index('--updated-by') + 1] if '--updated-by' in sys.argv else 'Claude'
now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
os.makedirs('dashboard/docs', exist_ok=True)
entries = [{"op": "set", "collection": "dash", "doc_id": "meta", "file_path": os.path.abspath('dashboard/docs/dash__meta.json')}]
json.dump({"title": "Reading library"}, open('dashboard/docs/dash__meta.json', 'w'))
for k, d in cfg['datasets'].items():
    doc = {"title": d['title'], "description": d['description'],
           "source": {"kind": "file", "url": f"/_blob/{d['blob']}", "name": f"{k}.json"},
           "updated": {"at": now, "by": by}}
    json.dump(doc, open(f'dashboard/docs/datasets__{k}.json', 'w'), ensure_ascii=False)
    entries.append({"op": "set", "collection": "datasets", "doc_id": k, "file_path": os.path.abspath(f'dashboard/docs/datasets__{k}.json')})
json.dump({"text": open('dashboard/index.html').read()}, open('dashboard/docs/files__index.html.json', 'w'), ensure_ascii=False)
entries.append({"op": "set", "collection": "files", "doc_id": "index.html", "file_path": os.path.abspath('dashboard/docs/files__index.html.json')})
json.dump(entries, open('dashboard/docs/batch.json', 'w'), indent=1)
print(f'{len(entries)} documents written to dashboard/docs/; batch entries in dashboard/docs/batch.json')
print('Existing documents need if_version pins on each batch entry (read them first).')
