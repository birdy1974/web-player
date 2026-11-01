#!/bin/sh
# Generate the demo clips used by the mock-up (mockup/media/).
#
# The clips are synthetic ffmpeg test patterns, so nothing copyrighted is
# shipped and the seek bar really works (the dev server answers Range
# requests). Needs ffmpeg on PATH.
#
#   sh tools/make-demo-media.sh [output-dir]
set -e

OUT="${1:-mockup/media}"
mkdir -p "$OUT"

gen() { # <file> <size> <seconds> <vcodec> <crf>
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=$2:rate=25:duration=$3" \
    -f lavfi -i "sine=frequency=440:duration=$3" \
    -c:v "$4" -preset veryfast -crf "$5" -pix_fmt yuv420p -g 50 \
    -c:a aac -b:a 64k -ac 1 -shortest -movflags +faststart "$OUT/$1"
}

gen aurora-drift.mp4     1280x720 300 libx264 35
gen long-way-home.mp4   1280x720 180 libx264 35
gen nightfall.mkv        1920x1080 120 libx265 36
gen coffee-and-code.mp4   640x360  90 libx264 35

ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=180:duration=60" \
  -c:a libmp3lame -b:a 128k "$OUT/signal-test.mp3"

for f in aurora-drift.mp4 long-way-home.mp4 nightfall.mkv coffee-and-code.mp4; do
  ffmpeg -hide_banner -loglevel error -y -ss 5 -i "$OUT/$f" -frames:v 1 \
    -vf "scale=480:-2" "$OUT/poster-${f%.*}.jpg"
done

echo "demo media written to $OUT"
ls -la "$OUT"
