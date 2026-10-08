#!/usr/bin/env python3
"""アーティストノート生成（sp2obsidian）

sp2obsidian.library の fetch 結果と、調査結果（JSON。任意）から、
Vault内の保存フォルダ（既定: <Vault>/Artists/）のノートを作成・更新する。

使い方（リポジトリのルートから）:
  python -m sp2obsidian.notes create --dry-run
  python -m sp2obsidian.notes update --dry-run
  python -m sp2obsidian.notes merge-tracks --dry-run
  python -m sp2obsidian.notes candidates

  create : fetchの new_artists から新規ノートを作る（既存ノートは上書きしない）
  update : fetchの backfill_artists / updated_artists の新曲・ステータスを既存ノートに反映する
  merge-tracks : 既存ノートの保存曲のうち、曲名が同じものを1つの見出しにまとめ直す（何度実行しても同じ結果）
  ※ 同じ曲がアルバム違い・トラックID違いで保存されることがあるため、曲名が同じ曲は1つの見出しにまとめ、
     Spotifyのリンクを並べる（create / update もこのルールで書く）
  candidates : よく聴く曲ランキングから、お気に入りアーティストの候補を表示する（ノートは書き換えない）

入力（既定: <作業フォルダ>/tmp/ 配下。Git管理外）
  spotify_fetch.json    python -m sp2obsidian.library fetch の出力
  spotify_research.json 調査結果。Spotifyアーティストidをキーにした辞書:
      {"<artist id>": {"name_ja": "日本語表記（任意）", "genres": ["ジャンル", ...],
                       "summary": "概要（1〜2文）", "facts": ["補足", ...],
                       "sources": [{"title": "出典名", "url": "https://..."}]}}

共通オプション: --dry-run（書き込まずに計画だけ表示）、--vault、--artists-dir、--fetch、--research
Vault・保存フォルダの決め方は sp2obsidian.library と同じ（引数 → 環境変数 → .env → 既定値）
守ること:
  - 既存ノートの「感想メモ」など、ユーザーが書いた部分には触れない（曲の追記は「お気に入りの曲」節のみ）
  - 調査結果がないアーティストは、--allow-unresearched を付けない限りノートを作らない
"""
import argparse
import datetime
import json
import re
from pathlib import Path

from . import library as sl

DEFAULT_FETCH = sl.TMP_DIR / "spotify_fetch.json"
DEFAULT_RESEARCH = sl.TMP_DIR / "spotify_research.json"
PLACEHOLDERS = {"（まだ保存した曲はありません）", "（Spotifyの取得時に追記）"}
NO_TRACKS = "（まだ保存した曲はありません）"
SECTION_TRACKS_RE = re.compile(r"^## お気に入りの曲[ \t]*\n", re.M)


# ---------------------------------------------------------------- 整形

def yaml_scalar(value):
    """YAMLで安全な1行の値にする（特殊文字を含むときはJSON形式の引用符つきにする）。"""
    value = (value or "").strip()
    if not value:
        return ""
    if re.search(r"""[:#\[\]{}&*!|>'"%@`]|^[\s\-?]|\s$""", value):
        return json.dumps(value, ensure_ascii=False)
    return value


def kv(key, value):
    """`key: value` 行。値が空なら末尾に空白を付けず `key:` にする。"""
    return "{}: {}".format(key, value) if value else key + ":"


def safe_filename(name):
    """ファイル名・wikilinkで使えない文字を置き換える。"""
    s = re.sub(r'[\\/:*?"<>|#^\[\]]', "_", name or "")
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return s[:100].rstrip() or "artist"


def render_track(track):
    parts = [x for x in (track.get("album"), track.get("year")) if x]
    head = "### " + (track.get("name") or "（曲名不明）")
    if parts:
        head += "（{}）".format(", ".join(parts))
    return "{}\n- Spotify: {}\n- 追加: {}\n- メモ:\n".format(
        head, track["url"], (track.get("added_at") or "")[:10] or "不明")


# 同じ曲が別のアルバム・別のトラックIDで保存されることがある（リマスター、シングルとアルバムなど）。
# 1つのノートの中では、曲名が同じものを1つの見出しにまとめ、Spotifyのリンクを並べる。
HEADING_SUFFIX_RE = re.compile(r"（([^（）]*)）$")
SPOTIFY_LINE_RE = re.compile(r"^- Spotify:\s*(\S+?)(?:（([^（）]*)）)?\s*$")
ADDED_LINE_RE = re.compile(r"^- 追加:\s*(\S*)\s*$")
MEMO_LINE_RE = re.compile(r"^- メモ:[ \t]*(.*)$")


def title_key(title):
    return sl.norm_name(title)


def split_heading(heading_line):
    """`### 曲名（アルバム, 年）` → (曲名, 'アルバム, 年')"""
    h = heading_line[4:].strip()
    m = HEADING_SUFFIX_RE.search(h)
    if m and h[:m.start()].strip():
        return h[:m.start()].strip(), m.group(1)
    return h, ""


def parse_blocks(body):
    """「お気に入りの曲」節の本文を、前置き文と曲ブロックのリストに分ける。"""
    chunks = re.split(r"(?m)^(?=### )", body)
    prelude = chunks[0] if chunks and not chunks[0].startswith("### ") else ""
    blocks = []
    for c in chunks:
        if not c.startswith("### "):
            continue
        lines = c.rstrip("\n").split("\n")
        title, album = split_heading(lines[0])
        blocks.append({"key": title_key(title), "title": title, "lines": lines, "album": album})
    return prelude, blocks


def _min_date(a, b):
    ok = [x for x in (a, b) if re.match(r"^\d{4}-\d{2}-\d{2}$", x or "")]
    return min(ok) if ok else (a or b or "不明")


def _merge_into(block, spotify_url, album_note, added, memo="", extra=()):
    """block（辞書）にSpotifyリンクを追加し、追加日は早い方、メモは連結、補足行は末尾へ足す。"""
    lines = block["lines"]
    tid = sl.TRACK_ID_RE.findall(spotify_url)
    known = set(sl.TRACK_ID_RE.findall("\n".join(lines)))
    if tid and tid[0] in known:
        return False
    last_sp = max([i for i, l in enumerate(lines) if SPOTIFY_LINE_RE.match(l)] or [0])
    line = "- Spotify: " + spotify_url + ("（{}）".format(album_note) if album_note else "")
    lines.insert(last_sp + 1, line)
    for i, l in enumerate(lines):
        m = ADDED_LINE_RE.match(l)
        if m:
            lines[i] = "- 追加: " + _min_date(m.group(1), added)
        m = MEMO_LINE_RE.match(l)
        if m and memo:
            lines[i] = "- メモ: " + (m.group(1) + " / " + memo if m.group(1) else memo)
    if extra:
        lines.extend(extra)
    return True


def add_track_to_blocks(blocks, track):
    """曲を追加する。同じ曲名のブロックがあればまとめ、なければ新しいブロックにする。"""
    key = title_key(track.get("name") or "")
    for b in blocks:
        if b["key"] == key:
            parts = [x for x in (track.get("album"), track.get("year")) if x]
            return _merge_into(b, track["url"], ", ".join(parts), (track.get("added_at") or "")[:10])
    text = render_track(track).rstrip("\n").split("\n")
    title, album = split_heading(text[0])
    blocks.append({"key": key, "title": title, "lines": text, "album": album})
    return True


def consolidate_blocks(blocks):
    """既存の曲ブロックのうち、曲名が同じものを1つにまとめる（メモは消さない）。"""
    out = []
    for b in blocks:
        first = next((o for o in out if o["key"] == b["key"]), None)
        if first is None:
            out.append(b)
            continue
        added, memo, extra, urls = "", "", [], []
        for l in b["lines"][1:]:
            if SPOTIFY_LINE_RE.match(l):
                urls.append(SPOTIFY_LINE_RE.match(l).group(1))
            elif ADDED_LINE_RE.match(l):
                added = ADDED_LINE_RE.match(l).group(1)
            elif MEMO_LINE_RE.match(l):
                memo = MEMO_LINE_RE.match(l).group(1).strip()
            elif l.strip():
                extra.append(l)
        for u in urls:
            _merge_into(first, u, b["album"], added, memo, extra)
            memo, extra = "", []  # メモと補足は最初の1回だけ移す
    return out


def render_blocks(prelude, blocks):
    body = prelude.strip("\n")
    chunks = ["\n".join(b["lines"]).rstrip("\n") for b in blocks]
    return ((body + "\n\n") if body and chunks else (body + "\n" if body else "")) + "\n\n".join(chunks) + ("\n" if chunks else "")


def render_tracks(tracks):
    """新規ノート用: 曲名が同じ曲を1ブロックにまとめて整形する。"""
    blocks = []
    for t in tracks:
        add_track_to_blocks(blocks, t)
    return ("\n\n".join("\n".join(b["lines"]) for b in blocks) + "\n") if blocks else ""


def clean_research(r):
    """調査結果を検証する。出典は http(s) のURLだけを通す。"""
    r = r or {}
    sources = []
    for s in r.get("sources") or []:
        url = (s.get("url") or "").strip()
        if re.match(r"^https?://\S+$", url):
            sources.append({"title": (s.get("title") or url).replace("\n", " ").strip(), "url": url})
    return {
        "name_ja": (r.get("name_ja") or "").strip(),
        "genres": [g.strip() for g in (r.get("genres") or []) if g and g.strip()],
        "summary": (r.get("summary") or "").strip(),
        "facts": [f.strip() for f in (r.get("facts") or []) if f and f.strip()],
        "sources": sources,
    }


def profile_state(research, has_entry):
    """プロフィールの状態: 調査結果あり / 調査したが情報なし / 未調査。"""
    if research["summary"] or research["facts"]:
        return "調査結果あり"
    return "調査したが情報なし" if has_entry else "未調査"


def render_profile(entry, research, has_entry):
    link_text = (entry["name"] or "").replace("[", "(").replace("]", ")")
    lines = ["- Spotify: [{}]({})".format(link_text, entry["url"])]
    if research["name_ja"] and research["name_ja"] != entry["name"]:
        lines.append("- Spotify表記: " + entry["name"])
    if research["summary"]:
        lines.append("- 概要: " + research["summary"])
    lines.extend("- " + f for f in research["facts"])
    lines.extend("- 出典: [{}]({})".format(s["title"], s["url"]) for s in research["sources"])
    state = profile_state(research, has_entry)
    if state == "未調査":
        lines.append("- （未調査）")
    elif state == "調査したが情報なし":
        lines.append("- （調査しましたが、確認できる情報が見つかりませんでした）")
    return "\n".join(lines)


def display_name(entry, research):
    """ノート名: 調査で確定した日本語名があればそれ、なければSpotify表記。"""
    return research.get("name_ja") or entry["name"]


def render_note(entry, research, day, ts, has_entry=True):
    """has_entry: 調査結果ファイルにこのアーティストの項目があるか（なければプロフィールは「未調査」）。"""
    tracks = render_tracks(entry["new_tracks"]) or NO_TRACKS + "\n"
    log = "- 作成：{} — Spotifyの取得結果（{}）から作成。プロフィールは{}\n".format(
        day, entry["status"], profile_state(research, has_entry))
    return (
        "---\ntags:\n  - artist\n"
        "{name}\n{alias}{id}\n{status}\nお気に入りアーティスト: false\n紹介者:\n"
        "{genre}\ncreated: {day}\nupdated: {ts}\n---\n"
        "## 感想メモ\n\n## プロフィール\n\n{profile}\n\n## お気に入りの曲\n\n{tracks}\n## Changelog\n\n{log}"
    ).format(
        name=kv("名前", yaml_scalar(display_name(entry, research))),
        alias=(kv("別名", yaml_scalar(entry["name"])) + "\n") if display_name(entry, research) != entry["name"] else "",
        id=kv("spotify_id", entry["id"]),
        status=kv("ステータス", entry["status"]),
        genre=kv("ジャンル", yaml_scalar(", ".join(research["genres"]))), day=day, ts=ts,
        profile=render_profile(entry, research, has_entry), tracks=tracks, log=log)


# ---------------------------------------------------------------- 既存ノートの部分更新

def fm_set(text, key, value, only_if_empty=True):
    """フロントマター内の `key:` 行を更新する。変更したら (新テキスト, True)。"""
    m = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.S)
    if not m:
        return text, False
    block = m.group(1)
    pat = re.compile(r"^%s:[ \t]*(.*)$" % re.escape(key), re.M)
    found = pat.search(block)
    if found is None:
        return text, False
    current = found.group(1).strip().strip('"').strip("'")
    if (only_if_empty and current) or current == value:
        return text, False
    new_block = block[:found.start()] + "{}: {}".format(key, value) + block[found.end():]
    return text[:m.start(1)] + new_block + text[m.end(1):], True


def add_tracks(text, tracks):
    """「お気に入りの曲」節に曲を追記する（同じ曲名はまとめる。プレースホルダ行は取り除く）。"""
    m = SECTION_TRACKS_RE.search(text)
    if not m:
        section = "## お気に入りの曲\n\n" + render_tracks(tracks)
        i = text.find("\n## Changelog")
        if i >= 0:
            return text[:i + 1] + section + "\n" + text[i + 1:]
        return text.rstrip("\n") + "\n\n" + section
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    body = "\n".join(l for l in text[start:end].split("\n") if l.strip() not in PLACEHOLDERS)
    prelude, blocks = parse_blocks(body)
    for t in tracks:
        add_track_to_blocks(blocks, t)
    new_body = "\n" + render_blocks(prelude, blocks)
    return text[:start] + new_body + ("\n" if nxt else "") + text[end:]


def consolidate_text(text):
    """ノート内の曲を曲名でまとめ直す。変更がなければ (text, 0, 0)。"""
    m = SECTION_TRACKS_RE.search(text)
    if not m:
        return text, 0, 0
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    prelude, blocks = parse_blocks(text[start:end])
    merged = consolidate_blocks(blocks)
    if len(merged) == len(blocks):
        return text, len(blocks), len(blocks)
    return text[:start] + "\n" + render_blocks(prelude, merged) + ("\n" if nxt else "") + text[end:], len(blocks), len(merged)


def append_changelog(text, line):
    m = re.search(r"^## Changelog[ \t]*\n", text, re.M)
    if not m:
        return text.rstrip("\n") + "\n\n## Changelog\n\n" + line + "\n"
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    body = text[start:end].rstrip("\n")
    return text[:start] + body + "\n" + line + "\n" + ("\n" + text[end:] if nxt else "")


# ---------------------------------------------------------------- コマンド

def load_json(path, required=True):
    p = Path(path).expanduser()
    if not p.exists():
        if required:
            sl.die("ファイルがありません: {}".format(p), 2)
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def now_local():
    """実行しているコンピューターの地域の時刻で (日付, ISO 8601の日時) を返す。"""
    n = datetime.datetime.now().astimezone()
    return n.strftime("%Y-%m-%d"), n.isoformat(timespec="seconds")


def cmd_create(args):
    vault, adir = sl.resolve_paths(args)
    fetch = load_json(args.fetch)
    research = load_json(args.research, required=False)
    index = sl.build_vault_index(vault, adir)
    known_ids = {n["spotify_id"] for n in index["notes"] if n["spotify_id"]}
    for n in index["notes"]:
        known_ids.update(n.get("extra_ids", []))
    noid_names = {sl.norm_name(n["name"]) for n in index["notes"] if not n["spotify_id"]}
    used = {p.name.casefold() for p in adir.glob("*.md")} if adir.is_dir() else set()
    only = set(x for x in (args.only or "").split(",") if x)
    day, ts = now_local()
    created, skipped = [], []
    for e in fetch.get("new_artists", []):
        if only and e["id"] not in only:
            continue
        if e["id"] in known_ids:
            skipped.append((e["name"], "このIDのノートが既にある"))
            continue
        if sl.norm_name(e["name"]) in noid_names:
            skipped.append((e["name"], "同名でID未設定のノートがある（手動でIDを補完する）"))
            continue
        if e["id"] not in research and not args.allow_unresearched:
            skipped.append((e["name"], "調査結果がない"))
            continue
        r = clean_research(research.get(e["id"]))
        shown = display_name(e, r)
        fname = safe_filename(shown) + ".md"
        if fname.casefold() in used:
            fname = "{} ({}).md".format(safe_filename(shown), e["id"][:4])
        used.add(fname.casefold())
        text = render_note(e, r, day, ts, has_entry=e["id"] in research)
        if not args.dry_run:
            adir.mkdir(parents=True, exist_ok=True)
            (adir / fname).write_text(text, encoding="utf-8")
        created.append((fname, len(e["new_tracks"])))
    print("{}作成: {}件".format("[dry-run] " if args.dry_run else "", len(created)))
    for fname, n in created:
        print("  + {}（曲{}）".format(fname, n))
    print("スキップ: {}件".format(len(skipped)))
    reasons = {}
    for name, why in skipped:
        reasons.setdefault(why, []).append(name)
    for why, names in reasons.items():
        shown = "、".join(names[:5]) + ("…" if len(names) > 5 else "")
        print("  - {}（{}件）: {}".format(why, len(names), shown))


def cmd_update(args):
    vault, adir = sl.resolve_paths(args)
    fetch = load_json(args.fetch)
    day, ts = now_local()
    done = 0
    for e in fetch.get("backfill_artists", []) + fetch.get("updated_artists", []):
        path = (vault / e["note"]).resolve()
        if adir not in path.parents:  # 保存フォルダの外は書き換えない
            print("  ! 保存フォルダの外のノートは更新しません: {}".format(e["note"]))
            continue
        if not path.exists():
            print("  ! ノートが見つかりません: {}".format(e["note"]))
            continue
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        known = set(sl.TRACK_ID_RE.findall(text))
        new = [t for t in e.get("new_tracks", []) if t["id"] not in known]
        notes = []
        text, ch = fm_set(text, "spotify_id", e["id"], only_if_empty=True)
        if ch:
            notes.append("spotify_idを補完")
        text, ch = fm_set(text, "ステータス", e["status"], only_if_empty=False)
        if ch:
            notes.append("ステータスを「{}」に更新".format(e["status"]))
        if new:
            text = add_tracks(text, new)
            notes.append("お気に入りの曲を{}曲追加".format(len(new)))
        if not notes:
            continue
        text, _ = fm_set(text, "updated", ts, only_if_empty=False)
        text = append_changelog(text, "- 更新：{} — {}（Spotify）".format(day, "、".join(notes)))
        if not args.dry_run:
            path.write_text(text, encoding="utf-8")
        print("{}更新: {} … {}".format("[dry-run] " if args.dry_run else "", e["note"].split("/")[-1], "、".join(notes)))
        done += 1
    print("更新対象: {}件".format(done))


def cmd_merge_tracks(args):
    _, adir = sl.resolve_paths(args)
    day, ts = now_local()
    changed = 0
    for p in sorted(adir.glob("*.md")):
        text = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        new, before, after = consolidate_text(text)
        if after == before:
            continue
        new, _ = fm_set(new, "updated", ts, only_if_empty=False)
        new = append_changelog(new, "- 更新：{} — 曲名が同じ保存曲をまとめた（{}件→{}件）".format(day, before, after))
        if not args.dry_run:
            p.write_text(new, encoding="utf-8")
        print("{}{} … {}件→{}件".format("[dry-run] " if args.dry_run else "", p.name, before, after))
        changed += 1
    print("まとめ直し: {}件".format(changed))


TERM_LABELS = {"short_term": "約4週間", "medium_term": "約6か月", "long_term": "約1年"}


def find_candidates(top_tracks, favorites, min_tracks=2):
    """お気に入りアーティストの候補を選ぶ（純粋関数）。

    よく聴く曲ランキングのうち、保存済みの曲で、ノートがあり、まだお気に入りアーティストでない
    アーティストを数え、ランキングに入っている曲が min_tracks 曲以上のものを返す。
    favorites: ノートのパス → お気に入りアーティストか（実行時点のノートの値）
    並び順: 曲数の多い順 → 最高順位の高い順。
    """
    by_note = {}
    for t in top_tracks:
        note = t.get("artist_note")
        if not (t.get("saved") and note) or favorites.get(note, t.get("artist_favorite")):
            continue
        c = by_note.setdefault(note, {"note": note, "tracks": {}, "best": {}})
        c["tracks"].setdefault(t["id"], t.get("name"))
        term, rank = t.get("term"), t.get("rank")
        if term and rank and (term not in c["best"] or rank < c["best"][term]):
            c["best"][term] = rank
    out = [c for c in by_note.values() if len(c["tracks"]) >= min_tracks]
    out.sort(key=lambda c: (-len(c["tracks"]), min(c["best"].values() or [10 ** 6]), c["note"]))
    return out


def cmd_candidates(args):
    vault, adir = sl.resolve_paths(args)
    fetch = load_json(args.fetch)
    index = sl.build_vault_index(vault, adir)
    favorites = {n["path"]: n["favorite"] for n in index["notes"]}
    found = find_candidates(fetch.get("top_tracks", []), favorites, args.min_tracks)
    terms = "・".join(TERM_LABELS.get(t, t) for t in fetch.get("top_terms", []))
    print("お気に入りアーティストの候補: {}件（ランキング: {}、保存済みの曲が{}曲以上）".format(
        len(found), terms or "不明", args.min_tracks))
    for c in found[:args.limit]:
        best = "、".join("{}{}位".format(TERM_LABELS.get(t, t), r) for t, r in sorted(c["best"].items(), key=lambda x: x[1]))
        print("  - {}（{}曲 / 最高: {}）".format(Path(c["note"]).stem, len(c["tracks"]), best))
        for name in list(c["tracks"].values())[:args.show_tracks]:
            print("      ・{}".format(name))
    if found:
        print("お気に入りにするかはご自身で決めてください。ノートの `お気に入りアーティスト: true` で設定します（このコマンドは書き換えません）。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Spotifyの取得結果と調査結果からアーティストノートを作成・更新する")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("create", "新規アーティストのノートを作成"), ("update", "既存ノートに新曲・ステータスを反映")):
        p = sub.add_parser(name, help=helptext)
        sl.add_path_args(p)
        p.add_argument("--fetch", default=str(DEFAULT_FETCH), help="fetchの出力JSON")
        p.add_argument("--dry-run", action="store_true", help="書き込まずに計画だけ表示")
        if name == "create":
            p.add_argument("--research", default=str(DEFAULT_RESEARCH), help="調査結果JSON")
            p.add_argument("--only", help="対象のSpotifyアーティストID（カンマ区切り）")
            p.add_argument("--allow-unresearched", action="store_true", help="調査結果がなくてもノートを作る")
    p = sub.add_parser("merge-tracks", help="既存ノートの保存曲を曲名でまとめ直す")
    sl.add_path_args(p)
    p.add_argument("--dry-run", action="store_true", help="書き込まずに計画だけ表示")
    p = sub.add_parser("candidates", help="お気に入りアーティストの候補を表示（ノートは書き換えない）")
    sl.add_path_args(p)
    p.add_argument("--fetch", default=str(DEFAULT_FETCH), help="fetchの出力JSON")
    p.add_argument("--min-tracks", type=int, default=2, help="ランキングに入っている保存済みの曲が何曲以上で候補にするか（既定: 2）")
    p.add_argument("--limit", type=int, default=20, help="表示する候補の最大数（既定: 20）")
    p.add_argument("--show-tracks", type=int, default=3, help="候補ごとに表示する曲の数（既定: 3。0で非表示）")
    args = ap.parse_args(argv)
    {"create": cmd_create, "update": cmd_update, "merge-tracks": cmd_merge_tracks,
     "candidates": cmd_candidates}[args.cmd](args)


if __name__ == "__main__":
    main()
