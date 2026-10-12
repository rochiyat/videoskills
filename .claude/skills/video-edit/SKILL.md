---
name: video-edit
description: Edit klip talking-head mentah menjadi reel vertikal 9:16 siap upload. Memilih take terbaik dari semua klip, membuang jeda dan salah ucap, crop 9:16 mengikuti wajah, punch-in zoom di jump cut, kata besar di belakang kepala, tiga look siap pakai, insert animasi (kartu screenshot, scroll file, kartu Follow, B-roll), efek suara halus, dan cek safe zone Reels per frame. Pakai saat user minta edit video, potong klip, bikin reel/shorts/TikTok dari rekaman mentah, atau memberi catatan revisi pada reel yang sedang dikerjakan.
argument-hint: <folder klip mentah> [catatan]
---

# /video-edit

Mengubah folder berisi klip talking-head mentah menjadi `_edit/output/vN.mp4`.
File klip asli **hanya dibaca**. Semua hasil kerja disimpan di `<folder>/_edit/`.

`SKILL_DIR` di bawah adalah folder tempat file SKILL.md ini berada.
Python skill ada di `SKILL_DIR/.venv/Scripts/python.exe` (Windows) atau `SKILL_DIR/.venv/bin/python` (Mac/Linux). Selanjutnya disebut `PY`.
ffmpeg tidak perlu ada di PATH. Skrip memakai ffmpeg bawaan venv. Untuk mengambil frame, pakai `snapshot.py` (lihat langkah 4).

## 0. Setup (sekali saja)

Kalau `PY` belum ada, jalankan `python SKILL_DIR/scripts/setup.py`. Skrip ini membuat venv, memasang faster-whisper, PyAV, MediaPipe, Pillow, dan ffmpeg (versi bawaan paket, tanpa install sistem), mengunduh model wajah dan segmentasi MediaPipe (sekitar 16 MB) ke `SKILL_DIR/models`, lalu menjalankan `doctor.py`.
Kalau ada yang gagal, jalankan `PY SKILL_DIR/scripts/doctor.py` dan laporkan hasilnya ke user.

## 1. Transkrip

```
PY SKILL_DIR/scripts/transcribe.py <folder> --language id --prompt "<kosakata khusus>"
```

- `--prompt`: isi dengan nama, istilah, atau merek yang kemungkinan disebut (mis. "Claude Code, Instagram Reels"). Ini mengurangi salah dengar.
- `--model`: default `small`. Pakai `medium` kalau transkrip banyak salah dan user mau menunggu lebih lama.
- Klip yang sudah ditranskrip dan tidak berubah akan dilewati, jadi aman dijalankan ulang.
- Langkah ini juga menganalisis posisi wajah (4 sampel per detik) untuk framing, zoom, dan caption. Laporkan ke user kalau wajah jarang terdeteksi di suatu klip.

Lalu baca `<folder>/_edit/transcript.txt`. Formatnya `index:kata`, setiap klip punya id (`c1`, `c2`, ...). Kata dengan tanda `?` kemungkinan salah dengar.

## 2. Susun `plan.json`

Tulis `<folder>/_edit/plan.json`. Ini keputusan editorial, jadi kerjakan dengan teliti.

**Naskah**
- **Take berulang:** kalau satu kalimat diucapkan beberapa kali, pakai take **lengkap terakhir** yang bersih. Take terakhir biasanya yang dianggap benar oleh pembicara.
- **Buang:** salah ucap, kalimat yang terputus, filler di awal atau akhir (eh, ehm, oke), dan obrolan di luar naskah ("ulang ya", "tadi salah").
- **Urutan:** ikuti urutan rekaman kecuali naskahnya jelas meminta urutan lain.
- **Pembuka:** kalimat pertama harus langsung masuk ke inti. Jangan mulai dari basa-basi.
- Jeda di dalam rentang yang dipilih **dipotong otomatis** oleh renderer berdasarkan level audio. Rentang tidak perlu dipecah per kalimat hanya karena ada jeda.
- Kalau ada filler **di tengah** kalimat yang dipakai, pecah menjadi dua `keep` yang melompati index filler itu.

**Visual**
- **Look:** tanyakan ke user kalau belum jelas. `bold` (kapital tebal, highlight kuning, warna lebih hidup), `clean` (teks putih di kotak gelap transparan, netral), atau `editorial` (serif italic krem, warna hangat dan lembut). Satu look untuk seluruh reel. Kalau sebuah kalimat menyebut nama look atau perlu suasana berbeda, isi `keep[].look` untuk kalimat itu saja.
- **Kata di belakang kepala (`titles`):** pilih 1 sampai 3 kata kunci per reel, misalnya angka ("3 HAL"), konsep utama, atau kata yang ingin ditekankan. Pakai teks pendek, maksimal sekitar 10 karakter. `at` adalah kata saat judul muncul (`"clip:index"`), biasanya tepat ketika kata itu diucapkan. Beri jarak minimal sekitar 3 detik antar-judul.
- **Zoom:** punch-in otomatis di setiap jump cut sudah cukup untuk kebanyakan reel. Isi `keep[].zoom` hanya kalau ingin penekanan khusus, misalnya 1.6 untuk kalimat punchline.
- **Insert:** hanya kalau ada bahan dari user (screenshot, file, B-roll) atau ada CTA follow. Jangan mengarang screenshot atau angka. Insert `image`/`scroll`/`cta` berupa kartu di sepertiga atas frame yang menutupi wajah sementara, jadi pakai hanya di kalimat yang memang membahas isi kartu itu. Durasi ideal 1,5 sampai 4 detik.
- **Efek suara:** sudah otomatis (pop saat judul dan kartu muncul, whoosh saat B-roll masuk dan keluar, tick saat tombol Follow ditekan). Tambahkan `sfx.events` hanya untuk penekanan khusus, maksimal 1 sampai 2 per reel.

```json
{
  "look": "bold",
  "keep": [
    {"clip": "c1", "from": 15, "to": 22, "note": "pembuka, take 3"},
    {"clip": "c1", "from": 35, "to": 46, "note": "poin 1", "zoom": 1.6, "look": "editorial"},
    {"clip": "c2", "start": 4.2, "end": 6.0, "note": "opsional: rentang waktu bebas, mis. reaksi tanpa kata"}
  ],
  "titles": [{"text": "3 hal", "at": "c1:21"}],
  "inserts": [
    {"type": "image", "src": "D:/bahan/screenshot.png", "at": "c1:39", "until": "c1:46"},
    {"type": "scroll", "src": "D:/bahan/naskah-panjang.png", "at": "c2:5", "dur": 3},
    {"type": "video", "src": "D:/bahan/broll.mp4", "at": "c2:10", "dur": 1.5, "src_start": 2.0},
    {"type": "cta", "name": "Nama Kamu", "handle": "@akun", "avatar": "D:/bahan/foto.jpg", "at": "c2:16", "until": "c2:19"}
  ],
  "sfx": {"events": [{"at": "c2:15", "sound": "ding"}]},
  "replacements": {"cloud": "Claude"},
  "word_fix": {"c1:28": "Code", "c2:7": ""}
}
```

Hanya `keep` yang wajib. Field lain punya default yang sudah baik.

| Field | Fungsi |
|---|---|
| `keep[].from` / `to` | index kata awal dan akhir (inklusif) dari transcript.txt |
| `keep[].look` | look lain untuk kalimat ini (caption dan color grade ikut berganti) |
| `keep[].zoom` | zoom tetap untuk satu keep (1.0 sampai 1.9). Tanpa field ini, zoom diatur otomatis |
| `look` | `bold` (default), `clean`, atau `editorial` |
| `replacements` | koreksi caption untuk semua kemunculan sebuah kata (tidak peka huruf besar/kecil, tanda baca dipertahankan) |
| `word_fix` | koreksi satu kata tertentu (`"clip:index"`). String kosong menyembunyikan kata dari caption |
| `framing` | override posisi crop per klip: `x` 0 = kiri, 0.5 = tengah, 1 = kanan (`y` sama). Default otomatis mengikuti wajah |
| `zoom` | `auto` (true): punch-in bergantian di setiap jump cut. `punch`: besar zoom (default 1.5 untuk sumber 4K, 1.3 untuk sumber 1080p). `max` (1.9) |
| `titles` | daftar `{"text", "at": "clip:index", "dur"}`: kata besar di belakang kepala. Segmen yang tertimpa judul memakai zoom yang sama supaya kepala tidak melompat |
| `title_style` | `size` (300, otomatis mengecil agar muat), `dur` (1.4), `overlap` (0.3 = kepala menutupi 30% tinggi huruf), `max_cover` (0.2), serta `font`/`color`/`uppercase`/`italic` untuk menimpa look |
| `inserts` | daftar insert. `type`: `image` (kartu; tambah `"layout": "full"` untuk layar penuh), `scroll` (gambar panjang yang di-scroll), `cta` (`name`, `handle`, `avatar` opsional, `press_at` 0.55), `video` (B-roll layar penuh, `src_start` = detik awal di file sumber, audio B-roll tidak dipakai). Waktu: `at` dan `until` (`"clip:index"`) atau `dur` (detik). `sfx` per insert: nama efek atau `null` |
| `sfx` | `enabled` (true), `volume_db` (-12 relatif terhadap suara, maksimal -3), `titles`/`inserts` (true), `cuts` (false: whoosh di setiap pergantian zoom), `events` (`[{"at", "sound": whoosh/pop/tick/ding}]`) |
| `captions` | `max_words` (3), `max_chars` (18), `position` (0.70 = 70% dari atas), `enabled`, serta `font`/`size`/`bold`/`italic`/`uppercase`/`color`/`highlight`/`outline`/`box` untuk menimpa look. Caption otomatis turun (sampai batas 80% tinggi) kalau akan menutupi wajah |
| `grade` | `contrast`, `saturation`, `warmth` untuk menimpa color grade look |
| `cut` | `max_gap`: jeda lebih panjang dari ini dibuang. `pad_before`/`pad_after`: napas di tepi potongan. `silence_db`: paksa ambang hening (default otomatis) |
| `safe_zone` | `check` (true): cek setiap frame setelah render |
| `output` | `width`/`height`/`fps` (1080x1920 @ 30) |
| `audio` | `loudnorm` (true), `target_lufs` (-14) |

### Insert custom lewat sub-agent

Kalau user minta animasi yang tidak tersedia di tipe bawaan (misalnya demo app yang di-klik, grafik yang tumbuh, atau mockup HP), buat sebagai file video lalu pakai `{"type": "video"}`. Kerjakan secara paralel: untuk setiap insert custom, jalankan satu sub-agent (tool Agent) di background dengan instruksi yang lengkap dan berdiri sendiri: isi animasi, durasi, ukuran 1080x1920 @ 30fps, path output di `<folder>/_edit/custom/`, dan pesan bahwa yang dibuat harus video mp4 yang bisa diputar. Sub-agent boleh memakai `PY` (Pillow, numpy, OpenCV, dan ffmpeg dari `imageio_ffmpeg.get_ffmpeg_exe()`). Sambil menunggu, lanjutkan plan dan render draft tanpa insert itu. Setelah sub-agent selesai, cek file-nya dengan `snapshot.py` sebelum dimasukkan ke plan.

## 3. Cek naskah, lalu render

```
PY SKILL_DIR/scripts/render.py <folder> --dry-run
```

Baca naskah hasil potongan, daftar segmen, judul, dan insert. Pastikan alurnya nyambung, tidak ada kalimat dobel, dan kata yang ditandai ragu sudah dikoreksi lewat `word_fix`/`replacements` kalau memang salah dengar. Perbaiki plan sampai benar, **baru** render:

```
PY SKILL_DIR/scripts/render.py <folder>
```

`--draft` merender 540x960 dengan kualitas rendah untuk cek alur dengan cepat. Hasil disimpan sebagai `_edit/output/vN.mp4`, dengan `vN.plan.json` (snapshot plan), `vN.ass` (caption), dan `vN.safezone.png` di sebelahnya. Versi lama tidak pernah ditimpa.

## 4. Cek hasil sendiri, lalu serahkan ke user

Renderer mengecek setiap frame setelah render dan mencetak `Safe zone: aman`, atau daftar masalah lengkap dengan detiknya (teks masuk area header, username/caption IG, kolom tombol kanan, atau caption menutupi wajah). **Perbaiki semua masalah safe zone sebelum menyerahkan hasil.** Biasanya cukup dengan mengubah `captions.position`, memendekkan `max_chars`, atau memindahkan judul.

Lihat juga hasilnya secara visual. Buka `vN.safezone.png` (area UI Reels diarsir merah, wajah dikotak hijau), dan ambil frame di setiap judul dan insert:

```
PY SKILL_DIR/scripts/snapshot.py <folder>/_edit/output/vN.mp4 1.9 4.4 8.1 -o <folder>/_edit/output/cek.png
```

Pastikan huruf judul terbaca, tepi rambut rapi, dan kartu insert tidak terpotong.

Setelah itu:
- Laporkan path video, durasi, look, dan ringkasan keputusan editorial (take mana yang dipakai, apa yang dibuang, judul dan insert apa saja).
- Kalau tool `SendUserFile` tersedia, kirim mp4-nya supaya user bisa menonton di HP.
- Minta catatan revisi.

## 5. Revisi

Ubah `plan.json` sesuai catatan, jalankan `--dry-run`, lalu render lagi. Segmen dan insert yang tidak berubah diambil dari cache, jadi revisi biasanya selesai dalam sekitar 30 detik sampai 1 menit.

Terjemahan catatan yang umum:
- "potong bagian X" → keluarkan index kata itu dari `keep`
- "pakai take yang tadi" → ganti `from`/`to` ke take lain di transcript.txt
- "terlalu cepat atau napasnya kepotong" → naikkan `pad_after`/`pad_before`, atau naikkan `max_gap` (misalnya 0.5)
- "masih ada jeda" → turunkan `max_gap` (misalnya 0.25)
- "caption salah" → `word_fix` atau `replacements`
- "caption ketutupan" atau "terlalu bawah" → ubah `captions.position`
- "gayanya kurang cocok" → ganti `look`, atau timpa satu atribut (`captions.highlight`, `grade.saturation`, dan seterusnya)
- "wajah kepotong" atau "posisi kurang pas" → `framing.<clip>.x`/`y`
- "zoom-nya kebanyakan" → `zoom.auto: false`, atau kecilkan `zoom.punch` (misalnya 1.2)
- "judulnya ketutupan kepala" → turunkan `title_style.overlap` (misalnya 0.15)
- "judulnya kurang kerasa di belakang kepala" → naikkan `overlap` (misalnya 0.45) dan `max_cover` (misalnya 0.3)
- "efek suaranya ganggu" → turunkan `sfx.volume_db` (misalnya -18), atau `sfx.enabled: false`
- "kartunya kelamaan" → ubah `until`/`dur` insert itu

## Batasan versi ini

- Font look memakai font sistem yang ada di Windows dan Mac (Arial Black, Arial, Georgia). Untuk font lain, taruh file .ttf di `SKILL_DIR/assets/fonts/` lalu sebut namanya di `captions.font`/`title_style.font`.
- Kualitas matte bergantung pada kontras orang dengan background. Rambut tipis di depan background ramai bisa terlihat kurang rapi.
- Zona UI Reels adalah perkiraan tata letak aplikasi Instagram saat ini. Bentuk UI bisa berbeda antar-versi aplikasi.
