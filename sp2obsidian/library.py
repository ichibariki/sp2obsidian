#!/usr/bin/env python3
"""Spotifyマイライブラリ取得（sp2obsidian）

役割:
  - Spotifyから「フォロー中アーティスト」「お気に入りの曲」「よく聴く曲ランキング」を取得する
  - Vault内のアーティストノート（保存フォルダ。既定は <Vault>/Artists/）と突き合わせ、
    「新しく増えたもの」をJSONで出力する
    （既定: <作業フォルダ>/tmp/spotify_fetch.json。--stdout で標準出力へ）
  - Vaultのノートは一切書き換えない（書き込むのはトークンキャッシュとtmp/の出力ファイルのみ）

使い方（requirements.txt を入れた仮想環境のPythonで、リポジトリのルートから実行する）:
  python -m sp2obsidian.library auth    # 初回のみ。ブラウザでSpotifyの許可操作
  python -m sp2obsidian.library fetch   # 差分JSONを tmp/spotify_fetch.json へ出力（2回目以降はこれだけ）
  python -m sp2obsidian.library index   # Vault側の索引を表示（Spotify不要・オフライン）

設定（優先順: コマンド引数 → 環境変数 → .env → 既定値）
  .env は作業フォルダ（既定: リポジトリのルート。環境変数 SP2OBSIDIAN_HOME で変更可）に置く。Git管理外。
  SPOTIFY_CLIENT_ID=（自分のSpotify Developerアプリの Client ID）
  SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback   # 省略可。Dashboardの登録と一致させる
  SP2OBSIDIAN_VAULT=/path/to/your/vault                 # --vault でも指定可
  SP2OBSIDIAN_ARTISTS_DIR=Artists                       # Vault内の保存フォルダ（相対パス）。--artists-dir でも指定可

認証: OAuth 2.0 Authorization Code with PKCE（Client Secret不要）
トークンキャッシュ: <作業フォルダ>/.spotify_cache （Git管理外）
"""
import warnings

# macOS標準Python(LibreSSL)で出るurllib3の警告は動作に影響しないため抑止する
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

import argparse
import datetime
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

# 作業フォルダ: .env・トークンキャッシュ・tmp/ の置き場（既定はリポジトリのルート）
HOME_DIR = Path(os.environ.get("SP2OBSIDIAN_HOME") or Path(__file__).resolve().parents[1]).expanduser()
ENV_PATH = HOME_DIR / ".env"
CACHE_PATH = HOME_DIR / ".spotify_cache"
TMP_DIR = HOME_DIR / "tmp"  # 作業用出力の置き場（Git管理外）
DEFAULT_ARTISTS_DIR = "Artists"
DEFAULT_OUT = TMP_DIR / "spotify_fetch.json"
SCOPES = "user-follow-read user-library-read user-top-read"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
TERMS = ("short_term", "medium_term", "long_term")

# open.spotify.com/track/<id> または spotify:track:<id> からトラックIDを拾う
TRACK_ID_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?track/|spotify:track:)([A-Za-z0-9]{22})"
)


def die(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- 設定

def load_env(path):
    """`.env`（KEY=VALUE形式）を読む。環境変数があればそちらを優先する。"""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_REDIRECT_URI", "SP2OBSIDIAN_VAULT", "SP2OBSIDIAN_ARTISTS_DIR"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def resolve_paths(args, env=None):
    """Vaultのルートと保存フォルダ（絶対パス）を決める。優先順: 引数 → 環境変数 → .env → 既定値。"""
    env = load_env(ENV_PATH) if env is None else env
    vault = getattr(args, "vault", None) or env.get("SP2OBSIDIAN_VAULT", "")
    if not vault:
        die("Vaultの場所が未設定です。--vault で指定するか、{} に\n  SP2OBSIDIAN_VAULT=（Vaultのフォルダ）\nと書いてください。".format(ENV_PATH), 2)
    vault = Path(vault).expanduser().resolve()
    if not vault.is_dir():
        die("Vaultのフォルダが見つかりません: {}".format(vault), 2)
    rel = getattr(args, "artists_dir", None) or env.get("SP2OBSIDIAN_ARTISTS_DIR") or DEFAULT_ARTISTS_DIR
    artists_dir = (vault / Path(rel).expanduser()).resolve()
    if vault not in artists_dir.parents:
        die("保存フォルダはVaultの中のフォルダを指定してください（指定: {}）".format(rel), 2)
    return vault, artists_dir


def add_path_args(parser):
    """--vault / --artists-dir をサブコマンドに追加する。"""
    parser.add_argument("--vault", help="Vaultのルート（省略時は SP2OBSIDIAN_VAULT）")
    parser.add_argument("--artists-dir", help="Vault内の保存フォルダ（相対パス。省略時は SP2OBSIDIAN_ARTISTS_DIR、なければ {}）".format(DEFAULT_ARTISTS_DIR))


# ---------------------------------------------------------------- Vault索引

def norm_name(name):
    """名前の照合用に正規化する（全角半角・大小文字・空白の違いを吸収）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name or "")).casefold()


def parse_frontmatter(text):
    """先頭のYAMLフロントマターから `key: value` 形式の行だけを読む（リストは無視）。"""
    m = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.S)
    data = {}
    if not m:
        return data
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t-#":
            continue
        key, sep, val = line.partition(":")
        if sep:
            data[key.strip()] = val.strip().strip('"').strip("'")
    return data


def build_vault_index(vault_root, artists_dir):
    """保存フォルダのノートを読み、Spotify ID・名前・既知のトラックIDを集める。"""
    adir = artists_dir
    notes = []
    track_ids = set()
    if adir.is_dir():
        for p in sorted(adir.glob("*.md")):
            text = p.read_text(encoding="utf-8").replace("\r\n", "\n")
            fm = parse_frontmatter(text)
            notes.append({
                "path": str(p.relative_to(vault_root)),
                "name": fm.get("名前") or p.stem,
                "aliases": [a.strip() for a in re.split(r"[,、]", fm.get("別名", "")) if a.strip()],
                "spotify_id": fm.get("spotify_id", ""),
                # 同一アーティストがSpotify上で複数名義に分かれている場合の追加ID（カンマ区切り）
                "extra_ids": [a.strip() for a in re.split(r"[,、]", fm.get("追加spotify_id", "")) if a.strip()],
                "status": fm.get("ステータス", ""),
                "favorite": fm.get("本命", "").lower() == "true",
            })
            track_ids.update(TRACK_ID_RE.findall(text))
    return {"notes": notes, "track_ids": track_ids}


# ---------------------------------------------------------------- Spotify取得

def get_auth(env):
    client_id = env.get("SPOTIFY_CLIENT_ID", "").strip()
    if not client_id:
        die("SPOTIFY_CLIENT_ID が未設定です。{} に\n  SPOTIFY_CLIENT_ID=（Client ID）\nと書いてください。".format(ENV_PATH), 2)
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyPKCE

    return SpotifyPKCE(
        client_id=client_id,
        redirect_uri=env.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        scope=SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(CACHE_PATH)),
        open_browser=True,
    )


def fetch_followed_artists(sp):
    """GET /me/following（カーソル式ページネーション）"""
    artists, after = [], None
    while True:
        res = sp.current_user_followed_artists(limit=50, after=after)["artists"]
        items = res.get("items", [])
        artists.extend(items)
        after = (res.get("cursors") or {}).get("after")
        if not items or not res.get("next") or not after:
            break
    return artists


def fetch_saved_tracks(sp):
    """GET /me/tracks（オフセット式ページネーション）"""
    items, offset = [], 0
    while True:
        res = sp.current_user_saved_tracks(limit=50, offset=offset)
        batch = res.get("items", [])
        items.extend(batch)
        if not batch or not res.get("next"):
            break
        offset += len(batch)
    return items


def fetch_top_tracks(sp, terms, limit):
    """GET /me/top/tracks（期間ごと）。戻り値は (期間, 順位, トラック) のリスト。"""
    out = []
    for term in terms:
        res = sp.current_user_top_tracks(limit=limit, offset=0, time_range=term)
        for rank, track in enumerate(res.get("items", []), 1):
            out.append((term, rank, track))
    return out


# ---------------------------------------------------------------- 差分計算（純粋関数）

def simplify_track(track, added_at=None):
    album = track.get("album") or {}
    return {
        "id": track["id"],
        "name": track.get("name"),
        "album": album.get("name"),
        "year": (album.get("release_date") or "")[:4],
        "url": "https://open.spotify.com/track/" + track["id"],
        "added_at": added_at,
        "artists": [a.get("name") for a in (track.get("artists") or [])],
    }


def compute_diff(followed, saved_items, top_items, index):
    """Spotify側のデータとVault索引を突き合わせ、新しく増えたものを返す。

    - アーティストの照合: Spotify ID → （IDが空のノートに限り）名前
    - お気に入りの曲は、先頭のアーティストのノートに紐づける
    - 曲が既知かどうかは、Artistsノート内のSpotify曲URLのIDで判定する
    """
    notes = index["notes"]
    by_id = {n["spotify_id"]: n for n in notes if n["spotify_id"]}
    for n in notes:
        for x in n.get("extra_ids", []):
            by_id.setdefault(x, n)
    by_name = {}
    for n in notes:
        if not n["spotify_id"]:
            for nm in [n["name"]] + n.get("aliases", []):
                by_name.setdefault(norm_name(nm), n)
    known_tracks = index["track_ids"]

    artists, order = {}, []

    def rec(artist_id, name):
        if artist_id not in artists:
            artists[artist_id] = {"name": name, "followed": False, "genres": [], "new_tracks": []}
            order.append(artist_id)
        return artists[artist_id]

    for a in followed:
        if not a.get("id"):
            continue
        r = rec(a["id"], a.get("name"))
        r["followed"] = True
        r["genres"] = a.get("genres") or []  # genresは非推奨のため空のことがある

    saved_ids = set()
    for item in saved_items:
        track = item.get("track") or {}
        if not track.get("id") or not track.get("artists"):
            continue  # ローカルファイル等はIDがない
        saved_ids.add(track["id"])
        primary = track["artists"][0]
        if not primary.get("id"):
            continue
        r = rec(primary["id"], primary.get("name"))
        if track["id"] not in known_tracks:
            r["new_tracks"].append(simplify_track(track, item.get("added_at")))

    new_artists, backfill, updates = [], [], []
    matched_paths = set()
    for artist_id in order:
        r = artists[artist_id]
        status = "フォロー中" if r["followed"] else "曲のみ"
        note = by_id.get(artist_id) or by_name.get(norm_name(r["name"]))
        if note:  # 複数名義のノートは、どれか1つでもフォロー中ならフォロー中
            ids = [note["spotify_id"]] + note.get("extra_ids", [])
            if any(artists.get(i, {}).get("followed") for i in ids):
                status = "フォロー中"
        entry = {
            "id": artist_id,
            "name": r["name"],
            "status": status,
            "url": "https://open.spotify.com/artist/" + artist_id,
            "genres": r["genres"],
            "new_tracks": r["new_tracks"],
        }
        if note is None:
            new_artists.append(entry)
        elif not note["spotify_id"]:
            entry["note"] = note["path"]
            matched_paths.add(note["path"])
            backfill.append(entry)
        elif r["new_tracks"] or (note["status"] and note["status"] != status):
            entry["note"] = note["path"]
            entry["current_status"] = note["status"]
            updates.append(entry)

    top = []
    for term, rank, track in top_items:
        if not track.get("id"):
            continue
        tartists = track.get("artists") or []
        primary = tartists[0] if tartists else {}
        note = by_id.get(primary.get("id")) or by_name.get(norm_name(primary.get("name")))
        top.append({
            "term": term,
            "rank": rank,
            "id": track["id"],
            "name": track.get("name"),
            "artists": [a.get("name") for a in tartists],
            "saved": track["id"] in saved_ids,
            "artist_note": note["path"] if note else None,
            "artist_favorite": bool(note and note["favorite"]),
        })

    # spotify_idが空のまま、名前でも照合できなかったノート（日本語名↔ローマ字表記など）。
    # 利用者がSpotifyの候補と見比べ、ノートの spotify_id を手動で補完する。
    unmatched = [{"path": n["path"], "name": n["name"]}
                 for n in notes if not n["spotify_id"] and n["path"] not in matched_paths]

    spotify_ids = set(artists)
    not_in_spotify = [n["path"] for n in notes if n["spotify_id"] and n["spotify_id"] not in spotify_ids
                      and not any(x in spotify_ids for x in n.get("extra_ids", []))]

    return {
        "counts": {
            "followed_artists": len(followed),
            "saved_tracks": len(saved_items),
            "vault_artist_notes": len(notes),
            "unmatched_notes": len(unmatched),
            "new_artists": len(new_artists),
            "backfill_artists": len(backfill),
            "updated_artists": len(updates),
            "new_tracks": sum(len(e["new_tracks"]) for e in new_artists + backfill + updates),
        },
        "new_artists": new_artists,
        "backfill_artists": backfill,
        "updated_artists": updates,
        "notes_not_in_spotify": not_in_spotify,
        "unmatched_notes": unmatched,
        "top_tracks": top,
    }


# ---------------------------------------------------------------- 出力

def write_output(result, out_path):
    """JSONを一時ファイルに書いてから置き換える（失敗時に壊れた出力を残さない）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    part.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(str(part), 0o600)
    except OSError:
        pass
    os.replace(str(part), str(out_path))


# ---------------------------------------------------------------- コマンド

def cmd_auth(args):
    auth = get_auth(load_env(ENV_PATH))
    auth.get_access_token()  # キャッシュがなければブラウザで許可操作→トークン保存
    try:
        os.chmod(CACHE_PATH, 0o600)
    except OSError:
        pass
    print("認証が完了しました（トークンを {} に保存）。".format(CACHE_PATH.name), file=sys.stderr)


def cmd_index(args):
    vault, artists_dir = resolve_paths(args)
    index = build_vault_index(vault, artists_dir)
    out = {"vault": str(vault), "artists_dir": str(artists_dir), "notes": index["notes"], "known_track_ids": len(index["track_ids"])}
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_fetch(args):
    import requests
    import spotipy

    env = load_env(ENV_PATH)
    vault, artists_dir = resolve_paths(args, env)
    terms = [t.strip() for t in args.terms.split(",") if t.strip()]
    bad = [t for t in terms if t not in TERMS]
    if bad:
        die("--terms は {} から選んでください（指定: {}）".format(", ".join(TERMS), ", ".join(bad)), 2)

    index = build_vault_index(vault, artists_dir)
    auth = get_auth(env)
    if not auth.get_cached_token():
        die("未認証です。先に `python -m sp2obsidian.library auth` を実行してください（ブラウザでの許可操作が必要です）。", 2)
    sp = spotipy.Spotify(auth_manager=auth, requests_timeout=15, retries=3)

    try:
        followed = fetch_followed_artists(sp)
        saved = fetch_saved_tracks(sp)
        top = fetch_top_tracks(sp, terms, args.top_limit)
    except spotipy.SpotifyException as e:
        hint = ""
        if e.http_status == 403:
            hint = "\n403: アプリ所有者のPremium契約、スコープ（auth をやり直す）、開発モードの制限を確認してください。"
        die("Spotify APIエラー（HTTP {}）: {}{}".format(e.http_status, e.msg, hint), 3)
    except requests.exceptions.RequestException as e:
        die("ネットワークエラー: {}".format(e), 3)

    result = compute_diff(followed, saved, top, index)
    result["fetched_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    result["top_terms"] = terms
    if args.stdout:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        out_path = Path(args.out).expanduser()
        write_output(result, out_path)
        print("出力: {}".format(out_path), file=sys.stderr)
    c = result["counts"]
    print("フォロー中{}件 / お気に入りの曲{}件 → 新規アーティスト{}・ID補完{}・更新{}・新規の曲{}・未照合ノート{}".format(
        c["followed_artists"], c["saved_tracks"], c["new_artists"],
        c["backfill_artists"], c["updated_artists"], c["new_tracks"], c["unmatched_notes"]), file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Spotifyマイライブラリの差分をJSONで出力する")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="初回認証（ブラウザで許可）")
    p_fetch = sub.add_parser("fetch", help="Spotifyから取得してVaultとの差分をJSONで出力")
    p_fetch.add_argument("--terms", default="long_term,medium_term",
                         help="ランキングの期間（カンマ区切り。{}）".format("/".join(TERMS)))
    p_fetch.add_argument("--top-limit", type=int, default=50, help="期間ごとのランキング件数（最大50）")
    add_path_args(p_fetch)
    p_fetch.add_argument("--out", default=str(DEFAULT_OUT), help="JSONの出力先（既定: {}）".format(DEFAULT_OUT))
    p_fetch.add_argument("--stdout", action="store_true", help="ファイルに書かず標準出力へ出す")
    p_index = sub.add_parser("index", help="Vault側の索引を表示（オフライン）")
    add_path_args(p_index)
    args = ap.parse_args(argv)
    try:
        {"auth": cmd_auth, "fetch": cmd_fetch, "index": cmd_index}[args.cmd](args)
    except ImportError as e:
        die("モジュールが見つかりません（{}）。requirements.txt を入れた仮想環境のPythonで実行してください。".format(e), 2)


if __name__ == "__main__":
    main()
