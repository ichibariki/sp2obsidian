"""sp2obsidian.library のテスト（Spotifyには接続しない）。"""
import argparse
import contextlib
import io
import unittest

from sp2obsidian import library as sl
from tests.helpers import VaultTestCase, note_text, sid, track


def ns(**kw):
    return argparse.Namespace(**kw)


class NormNameTest(unittest.TestCase):
    def test_ignores_width_case_and_spaces(self):
        self.assertEqual(sl.norm_name("Ｅｘａｍｐｌｅ  Band"), sl.norm_name("exampleband"))

    def test_empty(self):
        self.assertEqual(sl.norm_name(None), "")


class ParseFrontmatterTest(unittest.TestCase):
    def test_reads_scalars_and_skips_lists(self):
        fm = sl.parse_frontmatter('---\ntags:\n  - artist\n名前: "Example"\nspotify_id: abc\n---\n本文')
        self.assertEqual(fm["名前"], "Example")
        self.assertEqual(fm["spotify_id"], "abc")
        self.assertEqual(fm["tags"], "")

    def test_no_frontmatter(self):
        self.assertEqual(sl.parse_frontmatter("本文だけ"), {})


class BuildVaultIndexTest(VaultTestCase):
    def test_collects_notes_and_known_tracks(self):
        tid = sid(1, "T")
        self.write_note("Example Band.md", note_text(
            "Example Band", sid(1), favorite="true", extra_fm="別名: EB, イービー\n追加spotify_id: {}\n".format(sid(9)),
            body_tracks="### Song（Album, 2024）\n- Spotify: https://open.spotify.com/track/{}\n".format(tid)))
        index = sl.build_vault_index(self.vault, self.adir)
        note = index["notes"][0]
        self.assertEqual(note["path"], "Artists/Example Band.md")
        self.assertEqual(note["aliases"], ["EB", "イービー"])
        self.assertEqual(note["extra_ids"], [sid(9)])
        self.assertTrue(note["favorite"])
        self.assertEqual(index["track_ids"], {tid})

    def test_missing_folder_is_empty(self):
        index = sl.build_vault_index(self.vault, self.vault / "Nothing")
        self.assertEqual(index["notes"], [])


class ComputeDiffTest(VaultTestCase):
    def diff(self, followed, saved, top=()):
        return sl.compute_diff(followed, saved, list(top), sl.build_vault_index(self.vault, self.adir))

    def test_new_artist_from_follow_and_saved_track(self):
        a = sid(1)
        result = self.diff([{"id": a, "name": "New Band", "genres": []}],
                           [{"track": track(1, "Song", a, "New Band"), "added_at": "2026-02-01T00:00:00Z"}])
        self.assertEqual(result["counts"]["new_artists"], 1)
        entry = result["new_artists"][0]
        self.assertEqual(entry["status"], "フォロー中")
        self.assertEqual([t["name"] for t in entry["new_tracks"]], ["Song"])
        self.assertEqual(entry["new_tracks"][0]["year"], "2024")

    def test_saved_only_artist_is_song_only(self):
        a = sid(2)
        result = self.diff([], [{"track": track(2, "Song", a, "Solo")}])
        self.assertEqual(result["new_artists"][0]["status"], "曲のみ")

    def test_known_track_is_not_new(self):
        a = sid(1)
        known = sid(1, "T")
        self.write_note("Band.md", note_text("Band", a, body_tracks=(
            "### Song（Album, 2024）\n- Spotify: https://open.spotify.com/track/{}\n".format(known))))
        result = self.diff([{"id": a, "name": "Band"}],
                           [{"track": track(1, "Song", a, "Band")}, {"track": track(2, "Next", a, "Band")}])
        self.assertEqual(result["counts"]["new_artists"], 0)
        self.assertEqual([t["name"] for t in result["updated_artists"][0]["new_tracks"]], ["Next"])

    def test_status_change_is_update(self):
        a = sid(1)
        self.write_note("Band.md", note_text("Band", a, status="フォロー中"))
        result = self.diff([], [])
        self.assertEqual(result["updated_artists"], [])  # Spotifyに出てこないものは更新しない
        self.assertEqual(result["notes_not_in_spotify"], ["Artists/Band.md"])
        result = self.diff([], [{"track": track(5, "S", a, "Band")}])
        self.assertEqual(result["updated_artists"][0]["status"], "曲のみ")
        self.assertEqual(result["updated_artists"][0]["current_status"], "フォロー中")

    def test_note_without_id_is_backfilled_by_alias(self):
        self.write_note("例のバンド.md", note_text("例のバンド", "", extra_fm="別名: Example Band\n"))
        result = self.diff([{"id": sid(3), "name": "EXAMPLE band"}], [])
        self.assertEqual(result["counts"]["new_artists"], 0)
        self.assertEqual(result["backfill_artists"][0]["note"], "Artists/例のバンド.md")
        self.assertEqual(result["unmatched_notes"], [])

    def test_unmatched_note_without_id(self):
        self.write_note("Unknown.md", note_text("Unknown", ""))
        result = self.diff([{"id": sid(4), "name": "Other"}], [])
        self.assertEqual(result["unmatched_notes"], [{"path": "Artists/Unknown.md", "name": "Unknown"}])

    def test_extra_id_merges_aliases_and_follow_status(self):
        main_id, alt_id = sid(1), sid(2)
        self.write_note("Band.md", note_text("Band", main_id, status="曲のみ",
                                             extra_fm="追加spotify_id: {}\n".format(alt_id)))
        result = self.diff([{"id": alt_id, "name": "Band Alt"}], [{"track": track(1, "S", main_id, "Band")}])
        self.assertEqual(result["counts"]["new_artists"], 0)
        statuses = {e["id"]: e["status"] for e in result["updated_artists"]}
        self.assertEqual(statuses[main_id], "フォロー中")
        self.assertEqual(result["notes_not_in_spotify"], [])

    def test_top_tracks_are_annotated(self):
        a = sid(1)
        self.write_note("Band.md", note_text("Band", a, favorite="true"))
        saved = track(1, "Saved", a, "Band")
        top = [("long_term", 1, saved), ("long_term", 2, track(2, "Not saved", sid(9), "Stranger"))]
        result = self.diff([], [{"track": saved}], top)
        first, second = result["top_tracks"]
        self.assertTrue(first["saved"])
        self.assertEqual(first["artist_note"], "Artists/Band.md")
        self.assertTrue(first["artist_favorite"])
        self.assertFalse(second["saved"])
        self.assertIsNone(second["artist_note"])

    def test_local_files_without_id_are_ignored(self):
        result = self.diff([], [{"track": {"id": None, "name": "Local", "artists": []}}])
        self.assertEqual(result["counts"]["new_artists"], 0)


class ResolvePathsTest(VaultTestCase):
    def test_default_artists_dir(self):
        vault, adir = sl.resolve_paths(ns(vault=str(self.vault), artists_dir=None), env={})
        self.assertEqual((vault, adir), (self.vault, self.vault / "Artists"))

    def test_env_then_argument_precedence(self):
        env = {"SP2OBSIDIAN_VAULT": str(self.vault), "SP2OBSIDIAN_ARTISTS_DIR": "Music/Artists"}
        _, adir = sl.resolve_paths(ns(vault=None, artists_dir=None), env=env)
        self.assertEqual(adir, self.vault / "Music" / "Artists")
        _, adir = sl.resolve_paths(ns(vault=None, artists_dir="Other"), env=env)
        self.assertEqual(adir, self.vault / "Other")

    def test_reads_env_file(self):
        (self.home / ".env").write_text("# comment\nSP2OBSIDIAN_VAULT='{}'\n".format(self.vault), encoding="utf-8")
        vault, _ = sl.resolve_paths(ns(vault=None, artists_dir=None))
        self.assertEqual(vault, self.vault)

    def assertExits(self, args, env):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            sl.resolve_paths(args, env=env)
        self.assertEqual(cm.exception.code, 2)

    def test_rejects_folder_outside_vault(self):
        for rel in ("..", "../elsewhere", str(self.root), "."):
            with self.subTest(rel=rel):
                self.assertExits(ns(vault=str(self.vault), artists_dir=rel), {})

    def test_requires_existing_vault(self):
        self.assertExits(ns(vault=None, artists_dir=None), {})
        self.assertExits(ns(vault=str(self.root / "missing"), artists_dir=None), {})


class WriteOutputTest(VaultTestCase):
    def test_writes_json_atomically(self):
        out = self.home / "tmp" / "fetch.json"
        sl.write_output({"名前": "例"}, out)
        self.assertEqual(out.read_text(encoding="utf-8"), '{\n  "名前": "例"\n}\n')
        self.assertFalse(out.with_name("fetch.json.part").exists())


if __name__ == "__main__":
    unittest.main()
