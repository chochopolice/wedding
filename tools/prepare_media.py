#!/usr/bin/env python3
"""
写真・動画を Web 公開用に変換して media/ と media/manifest.json を作るツール

  使い方（手元で使う場合）:
    pip install pillow pillow-heif          # 初回のみ（pillow-heif は iPhone の HEIC 写真用）
    python tools/prepare_media.py           # raw/ → media/
    python tools/prepare_media.py --incremental           # 前回から変わったフォルダだけ作り直す

  GitHub Actions では Google ドライブの一覧（rclone lsjson）を --listing で渡し、
  --plan で「ダウンロードが必要なフォルダ・ファイル」だけを出力 → その分だけ取得して変換します。
  元の写真は実行のたびに必要な分だけ取得し、実行後は残しません（キャッシュにも入れません）。

  raw/ の置き方:
    raw/scenes/chapel.mp4  city.mp4  beach.mp4     … TOP の3本の動画（名前は index.html の SCENES の id）
    raw/ceremony/*.jpg *.heic *.mov …              … 挙式の写真・動画（順番は撮影日時順に自動で並びます）
    raw/groom/profile.jpg                          … プロフィール写真（ファイル名を profile にする）
    raw/city-restaurant/ など                      … index.html の GALLERIES と同じ名前のフォルダ
    raw/ogp.jpg（任意）                            … LINE などで共有したときのサムネイル元画像

  やること:
    ・写真：向きを補正し、横幅 640 / 1600px を AVIF・WebP で書き出し（--formats で JPEG も可）
           ぼかしプレビュー用の極小画像を作成、位置情報（GPS）などのメタデータは全て削除
    ・動画：H.264 / 最大1920px に圧縮、ポスター画像を作成、位置情報などのメタデータを削除
    ・media/ogp.jpg（1200×630）と media/manifest.json を作成
  動画の変換には ffmpeg が必要です（https://ffmpeg.org/）。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
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
TOOL_VERSION = 3  # 変換方法を変えたら上げる（--incremental でも全部作り直される）
GENERATED = re.compile(r"^(\d{2,4}|profile)(-\d+)?\.(jpg|webp|avif|mp4)$")


QUIET = False


def log(msg: str) -> None:
    print(msg, file=sys.stderr if PLAN_MODE else sys.stdout, flush=True)


def vlog(msg: str) -> None:
    """ファイル名を含む細かいログ（--quiet のときは出さない。公開リポジトリの Actions ログ対策）"""
    if not QUIET:
        log(msg)


PLAN_MODE = False


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




def remove_scene_files(out_dir: Path, sid: str) -> None:
    for name in (f"{sid}.mp4", f"{sid}.av1.mp4", f"{sid}.jpg"):
        (out_dir / name).unlink(missing_ok=True)


def mb(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0
    return f"{total / 1024 / 1024:.1f}MB"


# ---------------------------------------------------------------- inventory
def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory_from_raw(raw: Path) -> dict[str, list[dict]]:
    """raw/ フォルダから一覧を作る（手元で使うとき）。キー '' はトップ直下のファイル"""
    inv: dict[str, list[dict]] = {}
    for f in sorted(raw.iterdir()):
        if f.name.startswith("."):
            continue
        if f.is_file():
            inv.setdefault("", []).append({"Name": f.name, "Size": f.stat().st_size, "md5": md5_of(f)})
        elif f.is_dir():
            inv[f.name] = [{"Name": c.name, "Size": c.stat().st_size, "md5": md5_of(c)}
                           for c in sorted(f.iterdir()) if c.is_file() and not c.name.startswith(".")]
    return inv


def inventory_from_listing(path: Path) -> dict[str, list[dict]]:
    """rclone lsjson -R --files-only --hash の出力から一覧を作る（GitHub Actions で使うとき）"""
    inv: dict[str, list[dict]] = {}
    for e in json.loads(path.read_text("utf-8")):
        if e.get("IsDir"):
            continue
        parts = e["Path"].split("/")
        if any(p.startswith(".") for p in parts) or len(parts) > 2:
            continue  # 隠しファイルと、サブフォルダのさらに中は対象外
        md5 = (e.get("Hashes") or {}).get("md5") or (e.get("Hashes") or {}).get("MD5") or e.get("ModTime", "")
        inv.setdefault(parts[0] if len(parts) == 2 else "", []).append({"Name": e["Name"], "Size": e["Size"], "md5": md5})
    return inv


def entries_fp(entries: list[dict], settings: dict) -> str:
    h = hashlib.sha256(json.dumps({"v": TOOL_VERSION, **settings}, sort_keys=True).encode())
    for e in sorted(entries, key=lambda e: e["Name"]):
        h.update(f'{e["Name"]}\0{e["Size"]}\0{e["md5"]}\n'.encode())
    return h.hexdigest()[:20]


# ---------------------------------------------------------------- main
def main() -> None:
    global ROOT, QUIET, PLAN_MODE
    ap = argparse.ArgumentParser(description="写真・動画を Web 公開用に変換します")
    ap.add_argument("--raw", default="raw", help="元の写真・動画のフォルダ（既定: raw）")
    ap.add_argument("--site", default=".", help="index.html があるフォルダ（既定: カレント）")
    ap.add_argument("--sizes", default="640,1600", help="書き出す横幅（既定: 640,1600）")
    ap.add_argument("--formats", default="avif,webp",
                    help="写真の形式（既定: avif,webp。古いブラウザにも対応するなら avif,webp,jpg）")
    ap.add_argument("--av1", action="store_true", help="TOP の動画を AV1 でも書き出す（対応ブラウザでより軽くなる）")
    ap.add_argument("--no-video", action="store_true", help="動画の変換を省略する")
    ap.add_argument("--incremental", action="store_true", help="前回から変わっていないフォルダは作り直さない")
    ap.add_argument("--listing", help="rclone lsjson の出力（Google ドライブの一覧）。指定すると raw/ の代わりにこれで変更を判定する")
    ap.add_argument("--plan", action="store_true", help="ダウンロードが必要なパスを1行ずつ出力して終了する（--listing と一緒に使う）")
    ap.add_argument("--quiet", action="store_true", help="ファイル名を含む細かいログを出さない")
    a = ap.parse_args()
    QUIET, PLAN_MODE = a.quiet, a.plan

    ROOT = Path(a.site).resolve()
    raw = (ROOT / a.raw).resolve() if not Path(a.raw).is_absolute() else Path(a.raw)
    media = ROOT / "media"
    if a.listing:
        inv = inventory_from_listing(Path(a.listing))
    elif raw.is_dir():
        inv = inventory_from_raw(raw)
    else:
        sys.exit(f"{raw} がありません。README.md を見て raw/ フォルダを作ってください。")
    if a.plan and not a.listing:
        sys.exit("--plan は --listing と一緒に使ってください")

    sizes = [int(s) for s in a.sizes.split(",")]
    formats = [f.strip().lower().replace("jpeg", "jpg") for f in a.formats.split(",") if f.strip()]
    formats = [f for f in ("avif", "webp", "jpg") if f in formats] or ["webp"]
    if "avif" in formats and not features.check("avif"):
        formats.remove("avif")
        formats = formats or ["webp"]
        log("※ この Pillow は AVIF に未対応のため省略します（pip install -U pillow で対応版になります）")
    can_video = bool(shutil.which("ffmpeg") and shutil.which("ffprobe")) and not a.no_video
    if not can_video and not a.no_video:
        log("※ ffmpeg が見つからないため動画は変換しません（写真のみ処理します）")
    av1 = a.av1 and has_encoder("libsvtav1")
    if a.av1 and not av1:
        log("※ ffmpeg に AV1 エンコーダ（libsvtav1）が無いため AV1 版は作りません")

    mf_path = media / "manifest.json"
    manifest = json.loads(mf_path.read_text("utf-8")) if mf_path.exists() else {}
    for k in ("galleries", "portraits", "scenes"):
        manifest.setdefault(k, {})
    fps = manifest.setdefault("fingerprints", {})
    gal_settings = {"sizes": sizes, "formats": formats, "video": can_video, "heif": HEIF}

    # ---- 何を作り直すかを決める（ここまでは元の写真が無くても判定できる）
    galleries = sorted(k for k in inv if k and k != "scenes")
    scenes = [e for e in inv.get("scenes", []) if Path(e["Name"]).suffix.lower() in VIDEO_EXT] if can_video else []
    redo_gal = [g for g in galleries
                if not (a.incremental and fps.get(g) == entries_fp(inv[g], gal_settings)
                        and g in manifest["galleries"] and (media / "photos" / g).exists())]
    redo_scene = [e for e in scenes
                  if not (a.incremental and fps.get(f"scene:{Path(e['Name']).stem}") == entries_fp([e], {"scene": True, "av1": av1})
                          and Path(e["Name"]).stem in manifest["scenes"] and (media / "videos" / f"{Path(e['Name']).stem}.mp4").exists())]
    ogp_entry = next((e for e in inv.get("", []) if Path(e["Name"]).stem.lower() == "ogp" and Path(e["Name"]).suffix.lower() in PHOTO_EXT), None)
    ogp_fp = entries_fp([ogp_entry], {"ogp": True}) if ogp_entry else "auto"
    ogp_auto_src = "ceremony" if "ceremony" in inv else (galleries[0] if galleries else None)
    redo_ogp = not (a.incremental and fps.get("ogp") == ogp_fp and (media / "ogp.jpg").exists()
                    and (ogp_entry or ogp_auto_src not in redo_gal))

    if a.plan:
        need = list(redo_gal) + [f"scenes/{e['Name']}" for e in redo_scene]
        if redo_ogp:
            if ogp_entry:
                need.append(ogp_entry["Name"])
            elif ogp_auto_src and ogp_auto_src not in need:
                need.append(ogp_auto_src)
        print("\n".join(need))
        log(f"作り直し：ギャラリー {len(redo_gal)} / {len(galleries)}、TOP動画 {len(redo_scene)} / {len(scenes)}、OGP {'あり' if redo_ogp else 'なし'}")
        return

    # ---- ドライブ（一覧）から消えたフォルダ・動画は、media/ と manifest からも消す
    for name in list(manifest["galleries"]) + list(manifest["portraits"]):
        if name not in galleries:
            manifest["galleries"].pop(name, None)
            manifest["portraits"].pop(name, None)
            fps.pop(name, None)
            shutil.rmtree(media / "photos" / name, ignore_errors=True)
            shutil.rmtree(media / "videos" / name, ignore_errors=True)
            log(f"[{name}] 元のフォルダが無いため削除しました")
    stems = {Path(e["Name"]).stem for e in scenes}
    for sid in list(manifest["scenes"]):
        if sid not in stems and can_video:
            manifest["scenes"].pop(sid)
            fps.pop(f"scene:{sid}", None)
            remove_scene_files(media / "videos", sid)
            log(f"[scene] {sid} は元のフォルダに無いため削除しました")

    def need_raw(path: Path) -> Path:
        if not path.exists():
            sys.exit(f"{path} がありません（ダウンロードに失敗した可能性があります）")
        return path

    # ---- TOP の動画
    if redo_scene:
        out_dir = media / "videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        for e in redo_scene:
            f = need_raw(raw / "scenes" / e["Name"])
            log(f"[scene] {f.stem} を変換")
            remove_scene_files(out_dir, f.stem)
            manifest["scenes"][f.stem] = convert_scene(f, out_dir, f.stem, av1)
            fps[f"scene:{f.stem}"] = entries_fp([e], {"scene": True, "av1": av1})
    for e in scenes:
        if e not in redo_scene:
            log(f"[scene] {Path(e['Name']).stem} 変更なし（スキップ）")

    # ---- ギャラリー
    for name in galleries:
        if name not in redo_gal:
            log(f"[{name}] 変更なし（スキップ）")
            continue
        folder = need_raw(raw / name)
        files = [f for f in sorted(folder.iterdir()) if f.is_file() and not f.name.startswith(".")]
        photo_dir, video_dir = media / "photos" / name, media / "videos" / name
        log(f"[{name}] {len(files)} ファイルを変換")
        manifest["portraits"].pop(name, None)
        clean_generated(photo_dir)
        clean_generated(video_dir)
        photo_dir.mkdir(parents=True, exist_ok=True)

        portrait = next((f for f in files if f.stem.lower() == "profile" and f.suffix.lower() in PHOTO_EXT), None)
        if portrait:
            vlog(f"  profile: {portrait.name}")
            info = save_photo(open_rgb(portrait), photo_dir / "profile", [480, 960, 1440], formats)
            manifest["portraits"][name] = {"type": "photo", "base": rel(photo_dir / "profile"), **info}

        media_files = [f for f in files if f is not portrait and f.suffix.lower() in PHOTO_EXT | VIDEO_EXT]
        skipped = [f.name for f in files if f is not portrait and f not in media_files]
        if skipped:
            log(f"  対象外のファイル {len(skipped)} 件を無視")
            vlog(f"    {', '.join(skipped[:5])}{' …' if len(skipped) > 5 else ''}")
        heic = [f for f in media_files if f.suffix.lower() in (".heic", ".heif")]
        if heic and not HEIF:
            log("  ! HEIC 写真があります。pip install pillow-heif を実行してから再度お試しください")
            media_files = [f for f in media_files if f not in heic]
        media_files.sort(key=lambda f: (taken_at(f), f.name))
        digits = max(2, len(str(len(media_files))))

        items, failed = [], 0
        for i, f in enumerate(media_files, 1):
            num = str(i).zfill(digits)
            if f.suffix.lower() in VIDEO_EXT:
                if not can_video:
                    continue
                video_dir.mkdir(parents=True, exist_ok=True)
                out, poster = video_dir / f"{num}.mp4", video_dir / f"{num}.jpg"
                vlog(f"  {num}  {f.name}（動画）")
                try:
                    info = convert_video(f, out, poster)
                    items.append({"type": "video", "src": rel(out), "poster": rel(poster), **info})
                except subprocess.CalledProcessError as e:
                    failed += 1
                    vlog(f"    ! 変換に失敗しました: {e}")
            else:
                vlog(f"  {num}  {f.name}")
                try:
                    info = save_photo(open_rgb(f), photo_dir / num, sizes, formats)
                    items.append({"type": "photo", "base": rel(photo_dir / num), **info})
                except Exception as e:
                    failed += 1
                    vlog(f"    ! 読み込めませんでした: {e}")
        if failed:
            log(f"  ! {failed} 件を変換できませんでした")
        manifest["galleries"][name] = items
        fps[name] = entries_fp(inv[name], gal_settings)

    # ---- OGP 画像（1200×630）
    if redo_ogp:
        src = None
        if ogp_entry:
            src = need_raw(raw / ogp_entry["Name"])
        elif ogp_auto_src and (raw / ogp_auto_src).is_dir():
            d = raw / ogp_auto_src
            cands = sorted((f for f in d.iterdir() if f.suffix.lower() in PHOTO_EXT and f.stem.lower() != "profile"),
                           key=lambda f: (taken_at(f), f.name))
            src = cands[0] if cands else None
        if src:
            media.mkdir(exist_ok=True)
            ImageOps.fit(open_rgb(src), (1200, 630), Image.Resampling.LANCZOS).save(media / "ogp.jpg", "JPEG", quality=85, optimize=True)
            fps["ogp"] = ogp_fp
            log("[ogp] media/ogp.jpg を作成")

    manifest["version"] = 1
    manifest["generated"] = datetime.now().isoformat(timespec="seconds")
    media.mkdir(exist_ok=True)
    mf_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")

    log("")
    log(f"完了：media/ の合計 {mb(media)}")
    all_files = [f for f in media.rglob("*") if f.is_file()]
    photo_bytes = sum(f.stat().st_size for f in (media / "photos").rglob("*") if f.is_file()) if (media / "photos").exists() else 0
    video_bytes = sum(f.stat().st_size for f in (media / "videos").rglob("*") if f.is_file()) if (media / "videos").exists() else 0
    n_photos = sum(1 for g in manifest["galleries"].values() for it in g if it["type"] == "photo") + len(manifest["portraits"])
    if n_photos:
        log(f"  写真 {n_photos} 枚：{photo_bytes / 1024 / 1024:.0f}MB（1枚あたり平均 {photo_bytes / n_photos / 1024:.0f}KB）")
    log(f"  動画：{video_bytes / 1024 / 1024:.0f}MB")
    for f in all_files:
        if f.stat().st_size > 95 * 1024 * 1024:
            log(f"  ! {rel(f)} が 95MB を超えています。動画を短くするか分割してください")
    total = sum(f.stat().st_size for f in all_files)
    if total > 1000 * 1024 * 1024:
        log("  ! 合計が 1GB を超えています。GitHub Pages は 1GB までしか公開できません。")
        log("    長い動画を YouTube の限定公開に移すか、--sizes 640,1280 で写真を小さくしてください")
    elif total > 850 * 1024 * 1024:
        log("  ! 合計が 850MB を超えています。GitHub Pages の上限（1GB）に近づいています")


if __name__ == "__main__":
    main()
