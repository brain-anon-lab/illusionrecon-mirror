#!/usr/bin/env python3
"""
Download ATTENTION fMRI (.h5) for four subjects from Figshare article 13474629
and save ONLY flat files named `S*_attention.h5` under the chosen folder
(default: ./fmri/test_attention or the path set in files_attention.json).

Removes any extra files/folders in the target directory so that only:
  S1_attention.h5, S2_attention.h5, S3_attention.h5, S4_attention.h5
plus attention_manifest.json remain.

Usage
-----
# From repo root or from data/
python data/download_attention.py fmri_attention
# Optional explicit filelist path:
python data/download_attention.py --filelist data/files_attention.json fmri_attention
"""

import re, json, argparse, hashlib, shutil
from pathlib import Path
from typing import List, Dict, Optional
from urllib.request import urlopen, urlretrieve, Request

try:
    from tqdm import tqdm
except Exception:
    tqdm = None  # OK if tqdm isn't installed

FIGSHARE_API_ARTICLE = "https://api.figshare.com/v2/articles/{article_id}"

S_TO_SUB = {"S1":"sub-06","S2":"sub-07","S3":"sub-04","S4":"sub-01"}
SUB_TO_S = {v:k for k,v in S_TO_SUB.items()}

def _normalize_sub_code(s: str) -> Optional[str]:
    s = s.strip().lower()
    m = re.search(r"sub[-_]?0?([1-9]\d?)", s)
    if not m: return None
    n = int(m.group(1))
    return f"sub-{n:02d}" if n in (1,4,6,7) else None

def _md5sum(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()

def _progress_urlretrieve(url: str, dest: Path):
    if tqdm is None:
        urlretrieve(url, dest.as_posix()); return
    # Try to use Content-Length for progress
    try:
        with urlopen(Request(url, headers={"User-Agent":"Mozilla/5.0"})) as r:
            total = int(r.headers.get("Content-Length","0"))
    except Exception:
        total = 0
    pbar = tqdm(total=total, unit="B", unit_scale=True, desc=dest.name, ncols=100) if total else None
    def hook(bn, bs, ts):
        if pbar is not None:
            downloaded = bn*bs
            pbar.update(downloaded - pbar.n)
    try:
        urlretrieve(url, dest.as_posix(), hook)
    finally:
        if pbar is not None: pbar.close()

def fetch_figshare_files(article_id: int) -> List[Dict]:
    url = FIGSHARE_API_ARTICLE.format(article_id=article_id)
    with urlopen(Request(url, headers={"User-Agent":"Mozilla/5.0"})) as r:
        meta = json.load(r)
    return meta.get("files", [])

def looks_like_attention_fmri(name: str) -> bool:
    lo = name.lower()
    return lo.endswith(".h5") and ("attention" in lo) and ("vc" in lo)

def select_subjects(specs: List[str]) -> List[str]:
    if not specs: return list(SUB_TO_S.keys())
    out = set()
    for s in specs:
        if s.upper() in S_TO_SUB: out.add(S_TO_SUB[s.upper()]); continue
        sub = _normalize_sub_code(s)
        if sub: out.add(sub); continue
        raise ValueError(f"Unrecognized subject: {s}")
    return sorted(out)

def main(cfg):
    # Resolve files_attention.json: default to the same folder as this script
    here = Path(__file__).resolve().parent
    filelist_path = Path(cfg.filelist) if cfg.filelist else (here / "files_attention.json")
    if not filelist_path.exists() and (here / "files_attention.json").exists():
        filelist_path = here / "files_attention.json"

    with open(filelist_path, "r") as f:
        conf = json.load(f)
    if cfg.target not in conf:
        raise KeyError(f"Target '{cfg.target}' not in {filelist_path}. Options: {list(conf.keys())}")

    target = conf[cfg.target]
    dest_root = Path(target.get("save_in","./fmri/test_attention")).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    article_id = int(target.get("article_id", 13474629))
    wanted = set(select_subjects(target.get("subjects", [])))

    files = fetch_figshare_files(article_id)
    chosen = []
    for fi in files:
        name = fi.get("name","")
        url  = fi.get("download_url") or fi.get("url") or fi.get("supplied_url")
        if not name or not url: continue
        if not looks_like_attention_fmri(name): continue
        # pin to requested subjects
        sub = _normalize_sub_code(name)
        if sub and sub in wanted:
            fi["__sub"] = sub
            fi["__S"]   = SUB_TO_S.get(sub,"Sx")
            chosen.append(fi)

    if not chosen:
        print("No matching attention fMRI files found."); return

    manifest = []
    for fi in chosen:
        name, url, md5_remote = fi["name"], fi["download_url"], fi.get("md5")
        S = fi["__S"]

        # Save directly as flat S*_attention.h5 under dest_root
        out_path = dest_root / f"{S}_attention.h5"
        if not out_path.exists():
            print(f"Downloading {name} → {out_path}")
            _progress_urlretrieve(url, out_path)
        else:
            print(f"Exists, skipping: {out_path}")

        # Verify MD5 if provided by API
        md5_local = None
        try:
            md5_local = _md5sum(out_path)
            if md5_remote and md5_local != md5_remote:
                raise ValueError(f"MD5 mismatch for {name}\nExpected: {md5_remote}\nActual:   {md5_local}")
        except Exception as e:
            print(f"MD5 check issue for {name}: {e}")

        manifest.append({
            "api_name": name,
            "saved_as": out_path.as_posix(),
            "subject_S": S,
            "download_url": url,
            "md5_remote": md5_remote,
            "md5_local": md5_local
        })

    # Prune extras: keep only S*_attention.h5 and the manifest
    allowed = {f"{S}_attention.h5" for S in S_TO_SUB.keys()}
    allowed.add("attention_manifest.json")
    pruned = []
    for p in dest_root.iterdir():
        if p.name in allowed: continue
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
            pruned.append(p.name)
        except Exception:
            pass

    # Write manifest
    (dest_root / "attention_manifest.json").write_text(json.dumps({
        "figshare_article_id": article_id,
        "dest_root": dest_root.as_posix(),
        "S_to_sub": S_TO_SUB, "sub_to_S": SUB_TO_S,
        "downloaded": manifest,
        "pruned": pruned
    }, indent=2))

    print(f"\nSaved attention files under: {dest_root}")
    for S in sorted(S_TO_SUB.keys()):
        p = dest_root / f"{S}_attention.h5"
        print(" -", p, "OK" if p.exists() else "MISSING")
    if pruned:
        print("Removed extras:", ", ".join(pruned))
    print("Done.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--filelist", default=None, help="Path to files_attention.json (default: alongside this script)")
    ap.add_argument("target", nargs="?", default="fmri_attention")
    main(ap.parse_args())
