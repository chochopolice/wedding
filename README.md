# Our Wedding in Guam

グアム挙式の写真・動画を家族や友人に見てもらうためのページです。
写真・動画は Google ドライブに置き、GitHub Actions が自動で Web 用に変換して GitHub Pages に公開します。

## 全体の流れ

```
Google ドライブ「Wedding」フォルダ（元の写真・動画。ここだけに置く）
        │  1日3回＋ボタンで、Actions が読み取り専用で「変わった分だけ」取りに行く
        ▼
wedding リポジトリ（GitHub・Public）… index.html・tools/・ワークフロー
        │  変換（位置情報は削除、AVIF/WebP に圧縮）→ 元の写真はその場で破棄
        ▼
GitHub Pages → https://chochopolice.github.io/wedding/
```

## ドライブのフォルダ構成

フォルダ名は半角英小文字で、`index.html` の `GALLERIES` の名前と同じにします。フォルダの中にさらにフォルダは作りません。

| フォルダ | 内容 |
|---|---|
| `scenes/chapel.mp4` `city.mp4` `beach.mp4` | TOP の動画（中央・左・右） |
| `ceremony/` | 挙式の写真・動画 |
| `groom/`, `bride/` | 新郎・新婦の写真。`profile.jpg` はプロフィール写真 |
| `city-restaurant/` `city-boutique/` `city-cafe/` | 街のお店 |
| `beach-shore/` `beach-bar/` `beach-hotel/` | タモンビーチ |
| `ogp.jpg`（任意） | LINE で URL を送ったときのサムネイル |

## 初回の設定（詳しくは構築手順書）

1. Google Cloud で読み取り専用のサービスアカウントを作り、Google Drive API を有効にして、「Wedding」フォルダを閲覧者で共有
2. GitHub に `wedding`（Public）を作り、Secrets に `GDRIVE_SA_JSON` と `GDRIVE_FOLDER_ID` を登録
3. Settings → Pages の Source を **GitHub Actions** にする
4. Settings → Actions → General で、外部からのプルリクエストのワークフロー実行を **すべて承認制** にする
5. このフォルダを push し、Actions タブで「Run workflow」

## ふだんの使い方

- 写真を足す・消す：ドライブに入れる・消すだけ（1日3回反映。すぐなら「Run workflow」）
- 名前・キャプション：`index.html` を編集して push
- 全部作り直す：「Run workflow」で `full` にチェック

## セキュリティ（このリポジトリは公開です）

- 元の写真（位置情報入り）はリポジトリにもキャッシュにも残りません。実行のたびに必要な分だけ取得し、変換後に破棄します。
- 公開されるのは変換後の写真だけで、位置情報・撮影日時・機種などのメタデータは削除されます。
- Actions のログは誰でも見られるため、元のファイル名は出力しません。
- 鍵（JSON・秘密鍵）は絶対にこのフォルダに置かないでください。`.gitignore` で防いでいますが、Secrets 以外に保存しないのが原則です。
- ページは URL を知っている人なら誰でも見られます（検索エンジンには載りません）。

## 手元で変換する場合（任意）

ドライブの「Wedding」フォルダをダウンロードして `raw/` に置き、`python tools/prepare_media.py` を実行します（Pillow・pillow-heif・ffmpeg が必要）。
