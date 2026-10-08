# sp2obsidian

Spotify の「フォロー中のアーティスト」と「保存した曲（お気に入りの曲）」を読み取り、Obsidian の Vault に**アーティストごとのノート**を作る・更新する Python スクリプトです。2 回目以降は、**新しく増えた分だけ**をノートに反映します。

> [!IMPORTANT]
> **sp2obsidian は Spotify と無関係の非公式ツールです。** Spotify による承認・提携・推奨を受けたものではありません。
> sp2obsidian is an unofficial tool and is not affiliated with, endorsed by, or sponsored by Spotify.

## できること

- フォロー中のアーティストと保存した曲を取得し、Vault のノートと比べて**新しく増えた分**を見つける
- 新しいアーティストのノートを作る（1 アーティスト 1 ノート。曲はノートの中に並べる）
- 既存のノートに、新しく保存した曲を追記する。**感想メモなど、自分で書いた部分は書き換えない**
- よく聴く曲のランキングから、**お気に入りアーティストの候補**を表示する（決めるのは自分です）
- 書き込む処理はすべて `--dry-run` で、先に結果を確かめられる

## ノートの例

アーティストノートは次のような形になります（データは架空のものです）。ノートの項目名と見出しは日本語です。

```markdown
---
tags:
  - artist
名前: Example Band
spotify_id: 0000000000000000000000
ステータス: フォロー中
お気に入りアーティスト: false
紹介者:
ジャンル:
created: 2026-10-08
updated: 2026-10-08T09:00:00+09:00
---
## 感想メモ

（ここは自由に書く欄です。sp2obsidian は書き換えません）

## プロフィール

- Spotify: [Example Band](https://open.spotify.com/artist/0000000000000000000000)
- （未調査）

## お気に入りの曲

### Example Song（Example Album, 2024）
- Spotify: https://open.spotify.com/track/0000000000000000000001
- 追加: 2026-10-01
- メモ:

## Changelog

- 作成：2026-10-08 — Spotifyの取得結果（フォロー中）から作成。プロフィールは未調査
```

| 項目 | 意味 |
|---|---|
| `ステータス` | `フォロー中`（フォローしている）または `曲のみ`（フォローはしていないが曲を保存している） |
| `お気に入りアーティスト` | 自分で `true` にする印。sp2obsidian は自動で変更しません |
| `別名` | Spotify での表記がノート名と違うときに入れる（照合に使います） |
| `追加spotify_id` | 同じアーティストが Spotify 上で複数の名義に分かれているとき、追加の ID をカンマ区切りで書くと 1 つのノートにまとまります |
| `紹介者`・`ジャンル` | 自由に使える欄 |

## 準備

詳しい手順は **[docs/setup.md](docs/setup.md)** にあります。概要は次のとおりです。

1. **Spotify Premium** の契約（開発者アプリの所有者に必要）
2. [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) で**自分の**アプリを作り、Client ID を取得する（Redirect URI は `http://127.0.0.1:8888/callback`）
3. Python 3.9 以上で、仮想環境を作って `pip install -r requirements.txt`
4. `.env.example` を `.env` にコピーし、Client ID と Vault の場所を書く
5. `python -m sp2obsidian.library auth` で、ブラウザから 1 回だけ許可する

## 使い方

コマンドは、`sp2obsidian` のフォルダの中で、仮想環境の Python（`.venv/bin/python`）を使って実行します。

```bash
# 1. Spotify から取得し、Vault と比べた差分を tmp/spotify_fetch.json に保存する（ノートは書き換えない）
.venv/bin/python -m sp2obsidian.library fetch

# 2. 新しいアーティストのノートを作る（まず --dry-run で確認）
.venv/bin/python -m sp2obsidian.notes create --allow-unresearched --dry-run
.venv/bin/python -m sp2obsidian.notes create --allow-unresearched

# 3. 既存のノートに、新しく保存した曲とステータスの変化を反映する
.venv/bin/python -m sp2obsidian.notes update --dry-run
.venv/bin/python -m sp2obsidian.notes update

# 4.（任意）お気に入りアーティストの候補を表示する（ノートは書き換えない）
.venv/bin/python -m sp2obsidian.notes candidates
```

月に数回、1 → 2 → 3 の順に実行する使い方を想定しています。

### コマンド一覧

| コマンド | 内容 | Vault への書き込み |
|---|---|---|
| `sp2obsidian.library auth` | 最初の認証（ブラウザで許可） | なし |
| `sp2obsidian.library fetch` | 取得して差分を JSON に保存 | なし |
| `sp2obsidian.library index` | Vault のノートの索引を表示（オフライン） | なし |
| `sp2obsidian.notes create` | 新しいアーティストのノートを作る | あり（`--dry-run` で確認可） |
| `sp2obsidian.notes update` | 既存ノートに新しい曲・ステータスを反映 | あり（`--dry-run` で確認可） |
| `sp2obsidian.notes merge-tracks` | 同じ曲名の曲を 1 つの見出しにまとめ直す | あり（`--dry-run` で確認可） |
| `sp2obsidian.notes candidates` | お気に入りアーティストの候補を表示 | なし |

各コマンドの細かいオプションは `--help` で確認できます。主な共通オプションは次のとおりです。

- `--vault`: Vault のフォルダ（`.env` の `SP2OBSIDIAN_VAULT` より優先）
- `--artists-dir`: ノートを置くフォルダ（`.env` の `SP2OBSIDIAN_ARTISTS_DIR` より優先。Vault の外は指定できません）
- `--dry-run`: 書き込まずに、何をするかだけ表示

### 同じ曲が複数ある場合

同じ曲でも、アルバム・シングル・リマスターごとに Spotify では別の曲として扱われます。sp2obsidian は、1 つのノートの中で**曲名が同じものを 1 つの見出しにまとめ**、Spotify のリンクを並べます。

### プロフィール欄と調査結果ファイル（任意）

ノートを作るときに、アーティストの説明をプロフィール欄に入れることができます。`tmp/spotify_research.json` に、Spotify のアーティスト ID ごとに次の形で書いておきます。

```json
{
  "0000000000000000000000": {
    "name_ja": "ノート名にしたい表記（任意）",
    "genres": ["ジャンル"],
    "summary": "1〜2 文の概要",
    "facts": ["補足"],
    "sources": [{"title": "出典の名前", "url": "https://example.com/"}]
  }
}
```

- このファイルがなくても使えます。その場合は `create` に `--allow-unresearched` を付けます。プロフィール欄は「（未調査）」になります。
- `--allow-unresearched` を付けない場合、調査結果のないアーティストのノートは作りません。
- **このファイルは自分で用意してください。** 下の「Spotify のデータと AI について」も読んでください。

## データの流れと保存されるもの

```
Spotify Web API ──(読み取りのみ)──▶ tmp/spotify_fetch.json ──▶ Vault のアーティストノート
                                      （あなたのコンピューター）
```

- sp2obsidian が通信する相手は **Spotify の API だけ**です。取得したデータを、作者を含め他のどこにも送りません。
- あなたのコンピューターに保存されるもの:

| ファイル | 内容 |
|---|---|
| `.env` | Client ID と設定 |
| `.spotify_cache` | 認証のトークン |
| `tmp/spotify_fetch.json` | 取得結果（アーティスト名と ID、曲名・アルバム名・発売年・保存した日、よく聴く曲の順位） |
| Vault のノート | 上の取得結果のうち、ノートに書く分 |

- `.env`・`.spotify_cache`・`tmp/` は Git に入らないように設定済みです。**これらのファイルを公開したり、他の人に渡したりしないでください。**
- 取得したデータは、自分の Vault で個人的に使うためのものです。Spotify のコンテンツを再配布しないでください。

## Spotify のデータと AI について

> [!WARNING]
> **Spotify から取得したデータ（アーティスト名・曲名・順位など）を、AI/ML モデルの学習や入力に使わないでください。**
> Spotify の [Developer Policy](https://developer.spotify.com/policy) は、Spotify のコンテンツを機械学習・AI モデルの学習に使うことや、モデルに取り込むことを禁止しています。
> sp2obsidian 自体は、データを AI に渡しません。取得結果やノートを AI ツールに読ませるかどうかは、利用者の責任で、Spotify のポリシーを確認したうえで判断してください。

Spotify の [Developer Terms](https://developer.spotify.com/terms) と [Developer Policy](https://developer.spotify.com/policy) は、利用者自身でも確認してください。

## 動作環境

- Python 3.9 以上（macOS + Python 3.9 / spotipy 2.26 で確認）
- 初回の認証はブラウザと `127.0.0.1` を使うため、**自分のコンピューターで実行**してください。クラウド上の環境やリモートのサーバーでは、認証できないことがあります。
- Windows・Linux は未確認です。

## ライセンス

[MIT License](LICENSE)

Spotify は Spotify AB の商標です。

---

## English (short)

**sp2obsidian** reads your Spotify followed artists and saved tracks and creates/updates one Markdown note per artist in your Obsidian vault. On later runs, only newly added artists and tracks are added. Notes use Japanese field names and headings.

- **Unofficial.** Not affiliated with, endorsed by, or sponsored by Spotify.
- **Your own app.** You need your own Spotify Developer app (Client ID) and, in Development Mode, an active Spotify Premium subscription. See [docs/setup.md](docs/setup.md).
- **Local only.** Data goes from the Spotify Web API (read-only scopes) to a local JSON file and then to your notes. Nothing is sent anywhere else. Do not share `.env`, `.spotify_cache`, or `tmp/`.
- **No AI/ML.** Do not use data obtained from Spotify to train or feed AI/ML models ([Spotify Developer Policy](https://developer.spotify.com/policy)). sp2obsidian itself does not send data to any AI.
- **Safe writes.** Every write command supports `--dry-run`, and your own text in notes (e.g. the memo section) is never overwritten.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # set SPOTIFY_CLIENT_ID and SP2OBSIDIAN_VAULT
.venv/bin/python -m sp2obsidian.library auth
.venv/bin/python -m sp2obsidian.library fetch
.venv/bin/python -m sp2obsidian.notes create --allow-unresearched --dry-run
```

License: MIT. Spotify is a trademark of Spotify AB.
