#!/usr/bin/env python3
"""
写真・動画を Web 公開用に変換して media/ と media/manifest.json を作るツール

  使い方:
    pip install pillow pillow-heif          # 初回のみ（pillow-heif は iPhone の HEIC 写真用）
    python tools/prepare_media.py           # raw/ → media/
    python tools/prepare_media.py --only ceremony bride   # 一部のフォルダだけ作り直す

  raw/ の置き方:
    raw/scenes/chapel.mp4  city.mp4  beach.mp4     … TOP の3本の動画（名前は index.html の SCENES の id）
    raw/ceremony/*.jpg *.heic *.mov …              … 挙式の写真・動画（順番は撮影日時順に自動で並びます）
    raw/groom/profile.jpg                          … プロフィール写真（ファイル名を profile にする）
    raw/groom/*.jpg …                              … 新郎の写真
    raw/city-restaurant/ など                      … index.html の GALLERIES と同じ名前のフォルダ
    raw/ogp.jpg（任意）                            … LINE などで共有したときのサムネイル元画像

  やること:
    ・写真：向きを補正し、640 / 1280 / 2048px の3サイズを AVIF・WebP・JPEG で書き出し
           ぼかしプレビュー用の極小画像を作成、位置情報（GPS）などのメタデータは全て削除
    ・動画：H.264 / 最大1920px に圧縮、ポスター画像を作成、位置情報などのメタデータを削除
           TOP の動画は音声なし・ループ向け。--av1 を付けると AV1 版も作ります（より軽い）
    ・media/ogp.jpg（1200×630）と media/manifest.json を作成
  動画の変換には ffmpeg が必要です（https://ffmpeg.org/）。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageOps, features
except ImportError:
    sys.exit("Pillow が必要です:  pip install pillow pillow-heif")

try:
    from pillow_heif import register_heif_opener  # iPhone の HEIC/HEIF 写真
    register_heif_opener()
    HEIF = True
except ImportError:
    HEIF = False

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp", ".mts"}
GENERATED = re.compile(r"^(\d{2,4}|profile)(-\d+)?\.(jpg|webp|avif|mp4)$")


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- helpers
def taken_at(path: Path) -> float:
    """撮影日時（写真は EXIF、動画は ffprobe）。無ければ更新日時。"""
    try:
        if path.suffix.lower() in PHOTO_EXT:
            with Image.open(path) as im:
                exif = im.getexif()
                raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)
                if raw:
                    return datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
        elif shutil.which("ffprobe"):
            out = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format_tags=creation_time", "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=30).stdout.strip()
            if out:
                return datetime.fromisoformat(out.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    return path.stat().st_mtime


def open_rgb(path: Path) -> Image.Image:
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def lqip(im: Image.Image) -> str:
    small = im.copy()
    small.thumbnail((24, 24))
    buf = io.BytesIO()
    small.save(buf, "WEBP", quality=35)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def save_photo(im: Image.Image, base: Path, sizes: list[int], formats: list[str]) -> dict:
    w, h = im.size
    widths = sorted({min(s, w) for s in sizes})
    used = []
    for fmt in formats:
        ok = True
        for tw in widths:
            th = round(h * tw / w)
            r = im if tw == w else im.resize((tw, th), Image.Resampling.LANCZOS)
            out = base.parent / f"{base.name}-{tw}.{fmt}"
            try:
                if fmt == "jpg":
                    r.save(out, "JPEG", quality=82, progressive=True, optimize=True)
                elif fmt == "webp":
                    r.save(out, "WEBP", quality=80, method=6)
                elif fmt == "avif":
                    r.save(out, "AVIF", quality=60, speed=6)
            except Exception as e:  # AVIF 非対応の環境など
                log(f"    ! {fmt} を書き出せませんでした（{e}）。この形式は省略します")
                ok = False
                break
        if ok:
            used.append(fmt)
    return {"w": w, "h": h, "widths": widths, "formats": used, "lqip": lqip(im)}


def ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip().split(",")
    return int(out[0]), int(out[1])


def has_encoder(name: str) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return name in out


SCALE = "scale='min(1920,iw)':-2"


def convert_video(src: Path, out: Path, poster: Path) -> dict:
    ffmpeg(["-i", str(src), "-map_metadata", "-1", "-vf", SCALE, "-c:v", "libx264", "-preset", "slow", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
    ffmpeg(["-ss", "1", "-i", str(out), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(poster)])
    w, h = probe_size(out)
    return {"w": w, "h": h}


def convert_scene(src: Path, out_dir: Path, sid: str, av1: bool) -> dict:
    mp4 = out_dir / f"{sid}.mp4"
    ffmpeg(["-i", str(src), "-map_metadata", "-1", "-an", "-vf", SCALE, "-c:v", "libx264", "-preset", "slow", "-crf", "24",
            "-g", "60", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)])
    poster = out_dir / f"{sid}.jpg"
    ffmpeg(["-ss", "0.5", "-i", str(mp4), "-frames:v", "1", "-q:v", "3", str(poster)])
    sources = [{"src": rel(mp4), "type": "video/mp4"}]
    if av1:
        av1_out = out_dir / f"{sid}.av1.mp4"
        ffmpeg(["-i", str(src), "-map_metadata", "-1", "-an", "-vf", SCALE, "-c:v", "libsvtav1", "-crf", "36", "-preset", "6",
                "-g", "60", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(av1_out)])
        sources.insert(0, {"src": rel(av1_out), "type": 'video/mp4; codecs="av01.0.08M.08"'})
    return {"sources": sources, "poster": rel(poster)}


ROOT = Path(".")


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def clean_generated(folder: Path) -> None:
    if folder.exists():
        for f in folder.iterdir():
            if f.is_file() and GENERATED.match(f.name):
                f.unlink()


def mb(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0
    return f"{total / 1024 / 1024:.1f}MB"


# ---------------------------------------------------------------- main
def main() -> None:
    global ROOT
    ap = argparse.ArgumentParser(description="写真・動画を Web 公開用に変換します")
    ap.add_argument("--raw", default="raw", help="元の写真・動画のフォルダ（既定: raw）")
    ap.add_argument("--site", default=".", help="index.html があるフォルダ（既定: カレント）")
    ap.add_argument("--sizes", default="640,1280,2048", help="書き出す横幅（既定: 640,1280,2048）")
    ap.add_argument("--only", nargs="*", help="指定したフォルダだけ作り直す（例: --only ceremony scenes）")
    ap.add_argument("--av1", action="store_true", help="TOP の動画を AV1 でも書き出す（対応ブラウザでより軽くなる）")
    ap.add_argument("--no-video", action="store_true", help="動画の変換を省略する")
    a = ap.parse_args()

    ROOT = Path(a.site).resolve()
    raw = (ROOT / a.raw).resolve() if not Path(a.raw).is_absolute() else Path(a.raw)
    media = ROOT / "media"
    if not raw.is_dir():
        sys.exit(f"{raw} がありません。README.md を見て raw/ フォルダを作ってください。")
    sizes = [int(s) for s in a.sizes.split(",")]
    formats = ["avif", "webp", "jpg"] if features.check("avif") else ["webp", "jpg"]
    if "avif" not in formats:
        log("※ この Pillow は AVIF に未対応のため WebP/JPEG のみ作ります（pip install -U pillow で対応版になります）")
    can_video = bool(shutil.which("ffmpeg") and shutil.which("ffprobe")) and not a.no_video
    if not can_video and not a.no_video:
        log("※ ffmpeg が見つからないため動画は変換しません（写真のみ処理します）")
    av1 = a.av1 and has_encoder("libsvtav1")
    if a.av1 and not av1:
        log("※ ffmpeg に AV1 エンコーダ（libsvtav1）が無いため AV1 版は作りません")

    mf_path = media / "manifest.json"
    manifest = json.loads(mf_path.read_text("utf-8")) if mf_path.exists() else {}
    manifest.setdefault("galleries", {})
    manifest.setdefault("portraits", {})
    manifest.setdefault("scenes", {})

    folders = sorted(p for p in raw.iterdir() if p.is_dir() and not p.name.startswith("."))
    if a.only:
        folders = [p for p in folders if p.name in a.only]

    for folder in folders:
        name = folder.name
        files = [f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")]

        # ---- TOP の動画
        if name == "scenes":
            if not can_video:
                continue
            out_dir = media / "videos"
            out_dir.mkdir(parents=True, exist_ok=True)
            for f in sorted(files):
                if f.suffix.lower() not in VIDEO_EXT:
                    continue
                log(f"[scene] {f.name} → media/videos/{f.stem}.mp4")
                manifest["scenes"][f.stem] = convert_scene(f, out_dir, f.stem, av1)
            continue

        # ---- ギャラリー
        log(f"[{name}]")
        photo_dir, video_dir = media / "photos" / name, media / "videos" / name
        clean_generated(photo_dir)
        clean_generated(video_dir)
        photo_dir.mkdir(parents=True, exist_ok=True)

        portrait = next((f for f in files if f.stem.lower() == "profile" and f.suffix.lower() in PHOTO_EXT), None)
        if portrait:
            log(f"  profile: {portrait.name}")
            info = save_photo(open_rgb(portrait), photo_dir / "profile", [480, 960, 1440], formats)
            manifest["portraits"][name] = {"type": "photo", "base": rel(photo_dir / "profile"), **info}

        media_files = [f for f in files if f is not portrait and f.suffix.lower() in PHOTO_EXT | VIDEO_EXT]
        skipped = [f.name for f in files if f is not portrait and f not in media_files]
        if skipped:
            log(f"  対象外のファイルを無視: {', '.join(skipped[:5])}{' …' if len(skipped) > 5 else ''}")
        heic = [f for f in media_files if f.suffix.lower() in (".heic", ".heif")]
        if heic and not HEIF:
            log("  ! HEIC 写真があります。pip install pillow-heif を実行してから再度お試しください")
            media_files = [f for f in media_files if f not in heic]
        media_files.sort(key=lambda f: (taken_at(f), f.name))
        digits = max(2, len(str(len(media_files))))

        items = []
        for i, f in enumerate(media_files, 1):
            num = str(i).zfill(digits)
            if f.suffix.lower() in VIDEO_EXT:
                if not can_video:
                    continue
                video_dir.mkdir(parents=True, exist_ok=True)
                out, poster = video_dir / f"{num}.mp4", video_dir / f"{num}.jpg"
                log(f"  {num}  {f.name}（動画）")
                try:
                    info = convert_video(f, out, poster)
                    items.append({"type": "video", "src": rel(out), "poster": rel(poster), **info})
                except subprocess.CalledProcessError as e:
                    log(f"    ! 変換に失敗しました: {e}")
            else:
                log(f"  {num}  {f.name}")
                try:
                    info = save_photo(open_rgb(f), photo_dir / num, sizes, formats)
                    items.append({"type": "photo", "base": rel(photo_dir / num), **info})
                except Exception as e:
                    log(f"    ! 読み込めませんでした: {e}")
        manifest["galleries"][name] = items

    # ---- OGP 画像（1200×630）
    ogp_src = next((p for p in raw.glob("ogp.*") if p.suffix.lower() in PHOTO_EXT), None)
    if not ogp_src:
        for g in ["ceremony", *[p.name for p in folders]]:
            d = raw / g
            if d.is_dir():
                cands = sorted((f for f in d.iterdir() if f.suffix.lower() in PHOTO_EXT and f.stem.lower() != "profile"), key=lambda f: (taken_at(f), f.name))
                if cands:
                    ogp_src = cands[0]
                    break
    if ogp_src and (not a.only or "ogp" in a.only or not (media / "ogp.jpg").exists()):
        media.mkdir(exist_ok=True)
        ImageOps.fit(open_rgb(ogp_src), (1200, 630), Image.Resampling.LANCZOS).save(media / "ogp.jpg", "JPEG", quality=85, optimize=True)
        log(f"[ogp] {ogp_src.name} → media/ogp.jpg")

    manifest["version"] = 1
    manifest["generated"] = datetime.now().isoformat(timespec="seconds")
    media.mkdir(exist_ok=True)
    mf_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")

    log("")
    log(f"完了：media/ の合計 {mb(media)}")
    all_files = [f for f in media.rglob("*") if f.is_file()]
    for f in all_files:
        if f.stat().st_size > 95 * 1024 * 1024:
            log(f"  ! {rel(f)} が 95MB を超えています。GitHub は1ファイル100MBまでです。動画を短くするか分割してください")
    if sum(f.stat().st_size for f in all_files) > 900 * 1024 * 1024:
        log("  ! 合計が 900MB を超えています。GitHub Pages の推奨上限（1GB）に近いので、動画の見直しをおすすめします")
    log("index.html と media/ を GitHub に置いてください（raw/ は置かないでください）。")


if __name__ == "__main__":
    main()
