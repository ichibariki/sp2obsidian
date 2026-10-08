"""sp2obsidian.notes のテスト（架空データの一時Vaultで実行する）。"""
import contextlib
import io
import unittest

from sp2obsidian import notes as sn
from tests.helpers import VaultTestCase, fetched_track, note_text, sid

URL = "https://open.spotify.com/track/"


def new_artist(n, name, tracks=(), status="フォロー中"):
    a = sid(n)
    return {"id": a, "name": name, "status": status, "url": "https://open.spotify.com/artist/" + a,
            "genres": [], "new_tracks": list(tracks)}


class FormattingTest(unittest.TestCase):
    def test_yaml_scalar_quotes_special_values(self):
        self.assertEqual(sn.yaml_scalar("Plain Name"), "Plain Name")
        self.assertEqual(sn.yaml_scalar("A: B"), '"A: B"')
        self.assertEqual(sn.yaml_scalar("#hash"), '"#hash"')
        self.assertEqual(sn.yaml_scalar(""), "")

    def test_safe_filename(self):
        self.assertEqual(sn.safe_filename('A/B:C*D?"E"'), "A_B_C_D__E_")
        self.assertEqual(sn.safe_filename("  ...  "), "artist")


class RenderNoteTest(unittest.TestCase):
    def render(self, research, has_entry):
        return sn.render_note(new_artist(1, "Example Band", [fetched_track(1, "Song")]),
                              sn.clean_research(research), "2026-10-08", "2026-10-08T09:00:00+09:00", has_entry)

    def test_unresearched(self):
        text = self.render(None, has_entry=False)
        self.assertIn("- （未調査）", text)
        self.assertIn("プロフィールは未調査", text)
        self.assertIn("\nお気に入りアーティスト: false\n", text)
        self.assertNotIn("本命", text)
        self.assertIn("### Song（Album, 2024）\n- Spotify: " + URL + sid(1, "T"), text)

    def test_researched_but_empty(self):
        text = self.render({}, has_entry=True)
        self.assertIn("- （調査しましたが、確認できる情報が見つかりませんでした）", text)
        self.assertIn("プロフィールは調査したが情報なし", text)

    def test_researched(self):
        text = self.render({"name_ja": "例のバンド", "summary": "概要。", "genres": ["ロック"],
                            "sources": [{"title": "出典", "url": "https://example.com/"},
                                        {"title": "不正", "url": "javascript:alert(1)"}]}, has_entry=True)
        self.assertIn("\n名前: 例のバンド\n別名: Example Band\n", text)
        self.assertIn("- 概要: 概要。", text)
        self.assertIn("- 出典: [出典](https://example.com/)", text)
        self.assertNotIn("javascript:", text)
        self.assertIn("ジャンル: ロック", text)
        self.assertNotIn("未調査", text)


class TrackBlocksTest(unittest.TestCase):
    def test_add_tracks_merges_same_title_and_keeps_memo(self):
        text = note_text("Band", sid(1), body_tracks=(
            "### Song（Album A, 2020）\n- Spotify: {}{}\n- 追加: 2026-03-01\n- メモ: 好き\n".format(URL, sid(1, "T"))))
        text = sn.add_tracks(text, [fetched_track(2, "SONG", album="Album B", year="2021", added="2026-01-15"),
                                    fetched_track(3, "Other")])
        self.assertIn("- Spotify: {}{}（Album B, 2021）".format(URL, sid(2, "T")), text)
        self.assertIn("- 追加: 2026-01-15", text)  # 早い方の日付
        self.assertIn("- メモ: 好き", text)
        self.assertIn("### Other（Album, 2024）", text)
        self.assertEqual(text.count("### "), 2)
        self.assertIn("自分で書いたメモ（消えてはいけない）", text)
        self.assertNotIn("（まだ保存した曲はありません）", text)

    def test_add_same_track_twice_is_noop(self):
        text = sn.add_tracks(note_text("Band", sid(1)), [fetched_track(1, "Song")])
        self.assertEqual(sn.add_tracks(text, [fetched_track(1, "Song")]), text)

    def test_consolidate_is_idempotent(self):
        body = ("### Song（A, 2020）\n- Spotify: {u}{a}\n- 追加: 2026-02-01\n- メモ: 一つ目\n\n"
                "### Song（B, 2021）\n- Spotify: {u}{b}\n- 追加: 2026-01-01\n- メモ: 二つ目\n").format(
                    u=URL, a=sid(1, "T"), b=sid(2, "T"))
        merged, before, after = sn.consolidate_text(note_text("Band", sid(1), body_tracks=body))
        self.assertEqual((before, after), (2, 1))
        self.assertIn("- メモ: 一つ目 / 二つ目", merged)
        self.assertIn("- 追加: 2026-01-01", merged)
        again, before, after = sn.consolidate_text(merged)
        self.assertEqual((again, before, after), (merged, 1, 1))


class FrontmatterAndChangelogTest(unittest.TestCase):
    def test_fm_set(self):
        text = note_text("Band", "")
        text, changed = sn.fm_set(text, "spotify_id", "X", only_if_empty=True)
        self.assertTrue(changed)
        self.assertIn("\nspotify_id: X\n", text)
        text, changed = sn.fm_set(text, "spotify_id", "Y", only_if_empty=True)
        self.assertFalse(changed)
        _, changed = sn.fm_set(text, "存在しない項目", "Z")
        self.assertFalse(changed)

    def test_append_changelog_keeps_order(self):
        text = sn.append_changelog(note_text("Band", sid(1)), "- 更新：2026-10-08 — テスト")
        self.assertTrue(text.endswith("- 作成：2026-01-01\n- 更新：2026-10-08 — テスト\n"))


class FindCandidatesTest(unittest.TestCase):
    def top(self, term, rank, n, note, saved=True, fav=False):
        return {"term": term, "rank": rank, "id": sid(n, "T"), "name": "Song {}".format(n),
                "saved": saved, "artist_note": note, "artist_favorite": fav}

    def test_rules(self):
        tracks = [
            self.top("long_term", 3, 1, "a.md"), self.top("medium_term", 1, 1, "a.md"),  # 同じ曲は1曲
            self.top("long_term", 7, 2, "a.md"),
            self.top("long_term", 2, 3, "b.md"), self.top("long_term", 4, 4, "b.md"), self.top("long_term", 5, 5, "b.md"),
            self.top("long_term", 1, 6, "c.md"),                                          # 1曲だけ
            self.top("long_term", 8, 7, "d.md", saved=False), self.top("long_term", 9, 8, "d.md", saved=False),
            self.top("long_term", 6, 9, None), self.top("long_term", 10, 10, None),       # ノートなし
            self.top("long_term", 11, 11, "e.md"), self.top("long_term", 12, 12, "e.md"),  # 取得後にお気に入りにした
        ]
        found = sn.find_candidates(tracks, {"e.md": True})
        self.assertEqual([c["note"] for c in found], ["b.md", "a.md"])  # 曲数の多い順
        self.assertEqual(found[1]["best"], {"long_term": 3, "medium_term": 1})
        self.assertEqual(len(found[1]["tracks"]), 2)
        self.assertEqual([c["note"] for c in sn.find_candidates(tracks, {}, min_tracks=3)], ["b.md"])


class CommandTest(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.fetch = self.write_json("fetch.json", {
            "new_artists": [new_artist(1, "Example Band", [fetched_track(1, "Song")]),
                            new_artist(2, "Second Band", status="曲のみ")],
            "backfill_artists": [], "updated_artists": []})
        self.research = self.home / "research.json"  # 既定では存在しない

    def create(self, *extra):
        return self.run_main(sn.main, ["create", "--vault", str(self.vault), "--fetch", str(self.fetch),
                                       "--research", str(self.research)] + list(extra))

    def test_create_dry_run_writes_nothing(self):
        out = self.create("--dry-run")
        self.assertIn("[dry-run] 作成: 2件", out)
        self.assertEqual(list(self.adir.iterdir()), [])

    def test_create_without_research_by_default(self):
        self.create()
        self.assertEqual(sorted(p.name for p in self.adir.iterdir()), ["Example Band.md", "Second Band.md"])
        self.assertIn("- （未調査）", (self.adir / "Example Band.md").read_text(encoding="utf-8"))

    def test_require_research(self):
        self.research.write_text('{"%s": {"summary": "概要。"}}' % sid(2), encoding="utf-8")
        out = self.create("--require-research")
        self.assertIn("調査結果がない（1件）: Example Band", out)
        self.assertEqual([p.name for p in self.adir.iterdir()], ["Second Band.md"])

    def test_create_skips_existing_and_name_collision(self):
        self.write_note("Existing.md", note_text("Existing", sid(1)))
        self.write_note("Second Band.md", note_text("Other Artist", sid(9)))
        out = self.create()
        self.assertIn("このIDのノートが既にある", out)
        self.assertTrue((self.adir / "Second Band ({}).md".format(sid(2)[:4])).exists())
        self.assertEqual(self.adir.joinpath("Existing.md").read_text(encoding="utf-8"), note_text("Existing", sid(1)))

    def test_create_into_custom_folder(self):
        self.run_main(sn.main, ["create", "--vault", str(self.vault), "--artists-dir", "Music/Artists",
                                "--fetch", str(self.fetch), "--research", str(self.research)])
        self.assertTrue((self.vault / "Music" / "Artists" / "Example Band.md").exists())

    def update(self, entries, *extra):
        fetch = self.write_json("update.json", {"backfill_artists": [], "updated_artists": entries})
        return self.run_main(sn.main, ["update", "--vault", str(self.vault), "--fetch", str(fetch)] + list(extra))

    def test_update_keeps_user_text(self):
        path = self.write_note("Band.md", note_text("Band", sid(1), status="フォロー中"))
        entry = dict(new_artist(1, "Band", [fetched_track(5, "New Song")], status="曲のみ"), note="Artists/Band.md")
        dry = self.update([entry], "--dry-run")
        self.assertIn("[dry-run]", dry)
        self.assertEqual(path.read_text(encoding="utf-8"), note_text("Band", sid(1), status="フォロー中"))
        self.update([entry])
        text = path.read_text(encoding="utf-8")
        self.assertIn("自分で書いたメモ（消えてはいけない）", text)
        self.assertIn("- 自分で書いた説明", text)
        self.assertIn("ステータス: 曲のみ", text)
        self.assertIn("### New Song", text)
        self.assertIn("ステータスを「曲のみ」に更新、お気に入りの曲を1曲追加（Spotify）", text)
        self.assertNotIn("updated: 2026-01-01T00:00:00+09:00", text)
        self.assertEqual(self.update([entry]).strip(), "更新対象: 0件")  # 2回目は何もしない

    def test_update_backfills_id(self):
        path = self.write_note("Band.md", note_text("Band", ""))
        entry = dict(new_artist(3, "Band"), note="Artists/Band.md")
        fetch = self.write_json("bf.json", {"backfill_artists": [entry], "updated_artists": []})
        self.run_main(sn.main, ["update", "--vault", str(self.vault), "--fetch", str(fetch)])
        self.assertIn("\nspotify_id: {}\n".format(sid(3)), path.read_text(encoding="utf-8"))

    def test_update_refuses_paths_outside_artists_dir(self):
        outside = self.vault / "Diary.md"
        outside.write_text(note_text("Diary", sid(1)), encoding="utf-8")
        entry = dict(new_artist(1, "Diary", [fetched_track(5, "S")]), note="Diary.md")
        out = self.update([entry])
        self.assertIn("保存フォルダの外のノートは更新しません", out)
        self.assertEqual(outside.read_text(encoding="utf-8"), note_text("Diary", sid(1)))

    def test_merge_tracks_command(self):
        body = "### Song（A, 2020）\n- Spotify: {u}{a}\n- 追加: 2026-02-01\n- メモ:\n\n" \
               "### Song（B, 2021）\n- Spotify: {u}{b}\n- 追加: 2026-01-01\n- メモ:\n".format(
                   u=URL, a=sid(1, "T"), b=sid(2, "T"))
        path = self.write_note("Band.md", note_text("Band", sid(1), body_tracks=body))
        self.assertIn("2件→1件", self.run_main(sn.main, ["merge-tracks", "--vault", str(self.vault)]))
        merged = path.read_text(encoding="utf-8")
        self.assertIn("まとめ直し: 0件", self.run_main(sn.main, ["merge-tracks", "--vault", str(self.vault)]))
        self.assertEqual(path.read_text(encoding="utf-8"), merged)

    def test_missing_fetch_explains_next_step(self):
        for cmd in ("create", "update", "candidates"):
            err = io.StringIO()
            with self.subTest(cmd=cmd), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                sn.main([cmd, "--vault", str(self.vault), "--fetch", str(self.home / "none.json")])
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("先に `python -m sp2obsidian.library fetch` を実行", err.getvalue())

    def test_candidates_uses_current_note_value(self):
        self.write_note("Band.md", note_text("Band", sid(1), favorite="true"))
        self.write_note("Other.md", note_text("Other", sid(2)))
        top = [{"term": "long_term", "rank": r, "id": sid(r, "T"), "name": "S{}".format(r), "saved": True,
                "artist_note": note, "artist_favorite": False}
               for r, note in ((1, "Artists/Band.md"), (2, "Artists/Band.md"), (3, "Artists/Other.md"), (4, "Artists/Other.md"))]
        fetch = self.write_json("top.json", {"top_terms": ["long_term"], "top_tracks": top})
        out = self.run_main(sn.main, ["candidates", "--vault", str(self.vault), "--fetch", str(fetch)])
        self.assertIn("候補: 1件", out)
        self.assertIn("- Other（2曲 / 最高: 約1年3位）", out)
        self.assertNotIn("- Band", out)


if __name__ == "__main__":
    unittest.main()
