# Our Wedding in Guam

グアム挙式の写真・動画を家族や友人に見てもらうためのページです。GitHub Pages で公開します。

## フォルダ構成

```
wedding/
├─ index.html              ページ本体（名前・プロフィール・キャプションはここで編集）
├─ tools/prepare_media.py  写真・動画を Web 用に変換するツール
├─ raw/                    元の写真・動画を置く場所（GitHub には上げない）
└─ media/                  変換後の写真・動画（ツールが作る。これを GitHub に上げる）
```

## 写真・動画の入れ方

1. Googleフォトでアルバムを開き、「すべてダウンロード」で ZIP を保存して展開します。
2. 写真・動画を `raw/` の中のフォルダに振り分けます。フォルダ名は `index.html` の `GALLERIES` の名前と同じにします。

   | フォルダ | 内容 |
   |---|---|
   | `raw/scenes/chapel.mp4` | TOP中央：式場の正面（新郎新婦入り） |
   | `raw/scenes/city.mp4` | TOP左：グアムの街中 |
   | `raw/scenes/beach.mp4` | TOP右：タモンビーチ |
   | `raw/ceremony/` | 挙式の写真・動画 |
   | `raw/groom/`, `raw/bride/` | 新郎・新婦の写真。`profile.jpg` はプロフィール写真になります |
   | `raw/city-restaurant/` ほか | お店・ビーチなどの写真 |
   | `raw/ogp.jpg`（任意） | LINE で URL を送ったときのサムネイル |

   並び順は撮影日時順に自動で決まります。ファイル名は何でも構いません。

3. ツールを実行します（初回のみ `pip install pillow pillow-heif` と ffmpeg のインストールが必要）。

   ```
   python tools/prepare_media.py
   python tools/prepare_media.py --av1          # TOPの動画をさらに軽くしたい場合
   python tools/prepare_media.py --only bride   # 一部のフォルダだけ作り直す場合
   ```

   写真は AVIF / WebP / JPEG の3サイズに、動画は Web 向けに圧縮されます。**位置情報（GPS）などのメタデータは削除されます。**

4. `index.html` と `media/` を GitHub リポジトリに上げ、Settings → Pages で公開します。
   `.gitignore` に `raw/` を書いておくと、元データを誤って上げずに済みます。

## 公開前に書き換えるところ

- `index.html` 冒頭の `<title>` と OGP（`og:title`, `og:url`, `og:image`）を、実際の名前と公開URLにする
- `SITE`（名前・日付・あいさつ文）、`PROFILES`（プロフィール）、`GALLERIES` の `captions`（写真の説明）

## TOP動画のクリック範囲

- 新郎・新婦は AI（MediaPipe）が動画から人物を見つけて自動で追いかけます。白いドレスの明るさで新婦を見分けます。
  処理は見る人のブラウザ内だけで行われ、動画が外部に送られることはありません。
- 建物やお店の範囲は、URL の末尾に `?edit` を付けて開き、動画の上をドラッグして測ります。コピーされた座標を `SCENES` の `hotspots` に貼り付けてください。
- 人物の自動追従がうまくいかない場合は、`?edit` で時間ごとの位置を測り、`track` に書くこともできます。

## 注意

- URL を知っている人は誰でも見られます（検索エンジンには載らない設定です）。
- 公開リポジトリにすると、写真ファイルも GitHub 上で誰でも閲覧できます。
- GitHub は1ファイル100MBまで、Pages 全体は1GB程度が目安です。ツールが超過を警告します。
