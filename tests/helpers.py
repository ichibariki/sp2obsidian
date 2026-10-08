"""テスト用の共通部品。データはすべて架空のもの（実在のアーティスト・曲は使わない）。"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sp2obsidian import library as sl

CONFIG_KEYS = ("SPOTIFY_CLIENT_ID", "SPOTIFY_REDIRECT_URI", "SP2OBSIDIAN_VAULT", "SP2OBSIDIAN_ARTISTS_DIR")


def sid(n, prefix="A"):
    """22文字の架空のSpotify IDを作る。"""
    return (prefix + str(n)).ljust(22, "0")


def track(n, name, artist_id, artist_name, album="Album", year="2024"):
    """Spotify APIの形をまねた架空のトラック。"""
    return {
        "id": sid(n, "T"),
        "name": name,
        "album": {"name": album, "release_date": year + "-01-01"},
        "artists": [{"id": artist_id, "name": artist_name}],
    }


def fetched_track(n, name, album="Album", year="2024", added="2026-01-01"):
    """fetch の出力（simplify_track の形）の架空のトラック。"""
    tid = sid(n, "T")
    return {"id": tid, "name": name, "album": album, "year": year,
            "url": "https://open.spotify.com/track/" + tid, "added_at": added + "T00:00:00Z", "artists": []}


def note_text(name, spotify_id="", status="フォロー中", favorite="false", extra_fm="", body_tracks=""):
    """既存のアーティストノート（架空）。"""
    return (
        "---\ntags:\n  - artist\n名前: {}\nspotify_id: {}\n{}ステータス: {}\nお気に入りアーティスト: {}\n"
        "updated: 2026-01-01T00:00:00+09:00\n---\n"
        "## 感想メモ\n\n自分で書いたメモ（消えてはいけない）\n\n"
        "## プロフィール\n\n- 自分で書いた説明\n\n"
        "## お気に入りの曲\n\n{}\n## Changelog\n\n- 作成：2026-01-01\n"
    ).format(name, spotify_id, extra_fm, status, favorite, body_tracks or "（まだ保存した曲はありません）\n")


class VaultTestCase(unittest.TestCase):
    """一時フォルダに架空のVaultを作り、利用者の .env や環境変数の影響を受けないようにする。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.vault = self.root / "vault"
        self.adir = self.vault / "Artists"
        self.adir.mkdir(parents=True)
        self.home = self.root / "home"
        self.home.mkdir()
        for patcher in (
            mock.patch.object(sl, "ENV_PATH", self.home / ".env"),
            mock.patch.dict(os.environ, {k: "" for k in CONFIG_KEYS}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_note(self, filename, text):
        path = self.adir / filename
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, filename, data):
        path = self.home / filename
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def run_main(self, main, argv):
        """コマンドを実行し、標準出力を返す。"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(argv)
        return out.getvalue()
