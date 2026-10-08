# セットアップ手順 / Setup

初めて使うときの準備です。全体で 15〜20 分ほどかかります。
Spotify の画面は変わることがあります。見当たらない項目があれば、[Spotify for Developers の公式ドキュメント](https://developer.spotify.com/documentation/web-api) を確認してください。

> sp2obsidian は Spotify と無関係の非公式ツールです。
> sp2obsidian is an unofficial tool and is not affiliated with Spotify.

## 0. 必要なもの

- **Spotify Premium** の契約（開発者アプリの所有者に必要です。下の「開発モードの制限」参照）
- **Python 3.9 以上**（macOS + Python 3.9 で動作を確認しています）
- **Obsidian の Vault**（ノートを置くフォルダ）
- ブラウザ（初回の認証で使います）

## 1. Spotify Developer アプリを作る

このツールは、**利用者が自分で作った Spotify Developer アプリ**（Client ID）で動きます。作者や他の人の Client ID は使えません。

1. [Spotify for Developers の Dashboard](https://developer.spotify.com/dashboard) に、自分の Spotify アカウントでログインします。
2. 開発者向けの利用規約が表示されたら、内容を確認して同意します。
3. **Create app** を押し、次のように入力します。
   - **App name / App description**: 自分で分かる名前（例: `my-library-notes`）。**「Spot」で始まる名前や、Spotify 公式と誤解される名前は付けないでください**（Spotify のポリシーで禁止されています）。
   - **Redirect URI**: `http://127.0.0.1:8888/callback` と入力して **Add** を押します。
     - `localhost` は使えません。必ず `127.0.0.1` と書いてください。
     - 別のポート番号を使う場合は、あとで `.env` の `SPOTIFY_REDIRECT_URI` も同じ値にします。
   - **Which API/SDKs are you planning to use?**: **Web API** を選びます。
4. 規約への同意にチェックを入れて **Save** を押します。
5. 作ったアプリの **Settings** を開き、**Client ID** をコピーします。
   - **Client Secret は使いません**。このツールは PKCE という方式で認証するため、Client Secret は不要です。どこにも書かないでください。

### 開発モードの制限

新しく作ったアプリは「開発モード（Development Mode）」で動きます。2026 年 2 月の Spotify の変更により、次の制限があります。

- アプリの**所有者が Spotify Premium を契約し続けている**必要があります。契約が切れるとアプリが止まります。
- アプリを使えるのは**最大 5 人**まで、Dashboard の **User Management** で追加した人だけです（自分 1 人で使うなら追加は不要です）。
- 1 人の開発者が持てる Client ID には上限があります。
- アーティストの `genres`（ジャンル）などの一部の項目は、空で返ることがあります。

最新の条件は Spotify の [Web API の変更点](https://developer.spotify.com/documentation/web-api/references/changes/february-2026) を確認してください。

## 2. sp2obsidian を入れる

```bash
git clone https://github.com/ichibariki/sp2obsidian.git
cd sp2obsidian
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

以降のコマンドは、`sp2obsidian` のフォルダの中で `.venv/bin/python` を使って実行します。

> Vault を iCloud などで同期している場合も、`sp2obsidian` のフォルダと `.venv` は**同期対象の外**に置くことをおすすめします。

## 3. 設定ファイル（.env）を作る

```bash
cp .env.example .env
```

`.env` をテキストエディタで開き、次の値を書き込みます。

| 項目 | 内容 |
|---|---|
| `SPOTIFY_CLIENT_ID` | 手順 1 でコピーした Client ID |
| `SPOTIFY_REDIRECT_URI` | Dashboard に登録した Redirect URI（既定のままなら変更不要） |
| `SP2OBSIDIAN_VAULT` | Obsidian の Vault のフォルダ（絶対パス。例: `/Users/you/Documents/MyVault`） |
| `SP2OBSIDIAN_ARTISTS_DIR` | Vault の中でアーティストノートを置くフォルダ（既定: `Artists`） |

- `.env` は Git に入らないように設定済みです。**Client ID を他人に教えたり、公開の場所に書いたりしないでください。**
- `.env`・トークン・取得結果の置き場所を変えたいときは、環境変数 `SP2OBSIDIAN_HOME` にフォルダを指定します（このときは `.env` もそのフォルダに置きます）。

## 4. 最初の認証

```bash
.venv/bin/python -m sp2obsidian.library auth
```

1. ブラウザが開き、Spotify の許可画面が表示されます。
2. 内容（フォロー中のアーティスト、保存した曲、よく聴く曲の読み取り）を確認して **同意する** を押します。
3. 「認証が完了しました」と表示されれば完了です。

- 認証の結果（トークン）は `.spotify_cache` に保存され、2 回目以降は自動で使われます。このファイルも Git に入りません。
- 依頼する権限は、読み取りだけの 3 つです: `user-follow-read`、`user-library-read`、`user-top-read`。ライブラリやプレイリストを書き換えることはありません。

## 5. 動作確認

```bash
.venv/bin/python -m sp2obsidian.library fetch
.venv/bin/python -m sp2obsidian.notes create --dry-run
```

1 つ目で `tmp/spotify_fetch.json` に取得結果が保存され、件数が表示されます。2 つ目で、作られる予定のノートが表示されます（`--dry-run` なので、まだ何も書き込みません）。
使い方の続きは [README](../README.md#使い方) を見てください。

## うまくいかないとき

| 症状 | 確認すること |
|---|---|
| `INVALID_CLIENT: Invalid redirect URI` | Dashboard の Redirect URI と `.env` の `SPOTIFY_REDIRECT_URI` が完全に同じか。`localhost` ではなく `127.0.0.1` か |
| `HTTP 403` | アプリ所有者の Premium 契約が有効か。User Management に自分が入っているか（所有者以外の場合）。`auth` をやり直す |
| `SPOTIFY_CLIENT_ID が未設定です` | `.env` が `sp2obsidian` のフォルダ（または `SP2OBSIDIAN_HOME`）にあるか |
| `Vaultの場所が未設定です` | `.env` の `SP2OBSIDIAN_VAULT`、または `--vault` を指定したか |
| `未認証です` | 先に `auth` を実行する |
| `モジュールが見つかりません` | `.venv/bin/python` で実行しているか。`pip install -r requirements.txt` をしたか |

---

## English (short)

1. Create **your own** app at the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard). Do not start the app name with "Spot" or imply it is an official Spotify app.
   Redirect URI: `http://127.0.0.1:8888/callback` (`localhost` is not allowed). API: Web API. Copy the **Client ID** (no Client Secret needed — PKCE is used).
2. Development Mode: the app owner needs an active **Spotify Premium** subscription; up to 5 users (added via User Management).
3. `git clone`, `python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt`.
4. `cp .env.example .env` and set `SPOTIFY_CLIENT_ID` and `SP2OBSIDIAN_VAULT` (and optionally `SP2OBSIDIAN_ARTISTS_DIR`).
5. `.venv/bin/python -m sp2obsidian.library auth` — approve in the browser (read-only scopes: `user-follow-read`, `user-library-read`, `user-top-read`).
