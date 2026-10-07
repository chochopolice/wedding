# Our Wedding in Guam

グアム挙式の写真・動画を家族や友人に見てもらうためのページです。
写真・動画は Google ドライブに置き、GitHub Actions が自動で Web 用に変換して GitHub Pages に公開します。

## 全体の流れ

```
Google ドライブ「Wedding」フォルダ（元の写真・動画。ここだけに置く）
        │  1日3回＋ボタンで、Actions が読み取り専用で取りに行く
        ▼
wedding-src（GitHub・非公開）… index.html・tools/・ワークフロー
        │  変わった分だけ変換（位置情報は削除、AVIF/WebP に圧縮）
        ▼
wedding（GitHub・公開）… 変換後の index.html と media/ だけ → https://<ユーザー名>.github.io/wedding/
```

## ドライブのフォルダ構成

フォルダ名は半角英小文字で、`index.html` の `GALLERIES` の名前と同じにします。フォルダの中にさらにフォルダは作りません。

| フォルダ | 内容 |
|---|---|
| `scenes/chapel.mp4` | TOP中央：式場の正面（新郎新婦入り） |
| `scenes/city.mp4` | TOP左：グアムの街中 |
| `scenes/beach.mp4` | TOP右：タモンビーチ |
| `ceremony/` | 挙式の写真・動画 |
| `groom/`, `bride/` | 新郎・新婦の写真。`profile.jpg` はプロフィール写真になります |
| `city-restaurant/` `city-boutique/` `city-cafe/` | 街のお店 |
| `beach-shore/` `beach-bar/` `beach-hotel/` | タモンビーチ |
| `ogp.jpg`（任意） | LINE で URL を送ったときのサムネイル |

並び順は撮影日時順に自動で決まります。ファイル名は何でも構いません。iPhone の HEIC もそのまま置けます。

## 初回の設定

### 1. Google 側：読み取り専用の「サービスアカウント」を作る

1. https://console.cloud.google.com/ を開き、上部のプロジェクト選択から「新しいプロジェクト」を作る（名前は `wedding` など）
2. 「API とサービス」→「ライブラリ」で **Google Drive API** を検索して「有効にする」
3. 「IAM と管理」→「サービスアカウント」→「サービスアカウントを作成」。名前を入れて作成（ロールの付与は不要）
4. 作ったサービスアカウントを開き、「鍵」タブ →「鍵を追加」→「新しい鍵を作成」→ **JSON** → ダウンロード
5. サービスアカウントのメールアドレス（`〜@〜.iam.gserviceaccount.com`）をコピー
6. Google ドライブで「Wedding」フォルダを右クリック →「共有」→ 5 のアドレスを **閲覧者** で追加
7. 「Wedding」フォルダを開いたときの URL の `folders/` の後ろ（例：`1AbCdEf…`）をコピー。これがフォルダ ID

### 2. GitHub 側：リポジトリと鍵

1. `wedding-src` を **Private**、`wedding` を **Public** で作る
2. デプロイキーを作る：`ssh-keygen -t ed25519 -C "wedding-deploy" -f gh-pages -N ""`
   - `gh-pages.pub` の中身 → `wedding` の Settings → Deploy keys（**Allow write access** にチェック）
3. `wedding-src` の Settings → Secrets and variables → Actions に3つ登録

   | 名前 | 中身 |
   |---|---|
   | `GDRIVE_SA_JSON` | 1-4 でダウンロードした JSON ファイルの中身をすべて |
   | `GDRIVE_FOLDER_ID` | 1-7 のフォルダ ID |
   | `ACTIONS_DEPLOY_KEY` | 2-2 の `gh-pages`（秘密鍵）の中身 |

   登録が終わったら、パソコンの JSON と鍵ファイルは削除して構いません。
4. このフォルダの中身を `wedding-src` に push する
5. `wedding-src` の Actions タブで「Build media & deploy」→「Run workflow」を押す
6. 緑のチェックが付いたら、`wedding` の Settings → Pages で Branch を `gh-pages` / `(root)` にして保存（初回のみ）

## ふだんの使い方

- **写真を足す・消す**：ドライブの各フォルダに入れる・消すだけ。1日3回（日本時間 6:23 / 12:23 / 18:23）自動で反映されます
- **すぐ反映したい**：`wedding-src` の Actions タブ →「Run workflow」（スマホの GitHub アプリからも押せます）
- **全部作り直す**：「Run workflow」で `full` にチェック
- **名前・キャプションを変える**：`index.html` を編集して push すると反映されます

変更が無いときは変換も公開もしないので、Actions の無料枠はほとんど使いません。

## 公開前に書き換えるところ

- `index.html` 冒頭の `<title>` と OGP（`og:title`, `og:url`, `og:image`）を、実際の名前と公開URLにする
- `SITE`（名前・日付・あいさつ文）、`PROFILES`（プロフィール）、`GALLERIES` の `captions`（写真の説明）
- ワークフローの `PAGES_REPO` を公開用リポジトリ名にする

## TOP動画のクリック範囲

- 新郎・新婦は AI（MediaPipe）が動画から人物を見つけて追いかけます。処理は見る人のブラウザ内だけで行われます。
- 建物やお店の範囲は、URL の末尾に `?edit` を付けて開き、動画の上をドラッグして測ります。コピーされた座標を `SCENES` の `hotspots` に貼り付けてください。
- 追従が安定しない場合は、Groom / Bride の行から `auto:` を消すと固定の範囲になります。

## 手元で変換する場合（任意）

ドライブの「Wedding」フォルダをダウンロードして `raw/` に置き、次を実行します（Pillow・pillow-heif・ffmpeg が必要）。

```
python tools/prepare_media.py
```

## 注意

- URL を知っている人は誰でも見られます（検索エンジンには載らない設定です）。公開されるのは変換後の写真だけで、元の写真や位置情報は公開されません。
- GitHub Pages は公開サイト全体で 1GB まで、1ファイル 100MB までです。超えそうなときは Actions が警告・停止します。
- 写真は AVIF / WebP の2形式・横幅 640 / 1600px で書き出します（数百枚でも 1GB に収まるように）。
- 長い動画（数分以上）は容量を大きく使うので、YouTube の限定公開に置くのがおすすめです。
- サービスアカウントは「Wedding」フォルダを読むことしかできません（閲覧者権限・読み取り専用）。
