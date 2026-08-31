#!/usr/bin/env bash
# 一次性字体供应商化：从 Google Fonts 拉取 woff2 unicode-range 子集到 public/fonts/
# 产出 public/fonts/fonts.css（URL 已重写为本地相对路径）
# 用途：内网部署客户端无外网，字体必须随构建分发；重跑幂等（覆盖旧文件）
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="public/fonts"
CSS_URL='https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@500;700&family=JetBrains+Mono:wght@400;600&display=swap'
UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'

mkdir -p "$OUT"
echo "== 下载字体 CSS（Chrome UA 换取 woff2 子集）=="
curl -fsS --http1.1 --retry 3 --retry-all-errors -A "$UA" "$CSS_URL" -o /tmp/gf.css
grep -c '@font-face' /tmp/gf.css || true

echo "== 下载全部 woff2 并重写 URL =="
cp /tmp/gf.css /tmp/gf.local.css
i=0
for url in $(grep -oE 'https://fonts\.gstatic\.com/[^)]+\.woff2' /tmp/gf.css | sort -u); do
  i=$((i+1))
  fname="$(echo "$url" | md5sum | cut -c1-8).woff2"
  if [ ! -s "$OUT/$fname" ]; then
    curl -fsS --http1.1 --retry 4 --retry-all-errors --retry-delay 2 -o "$OUT/$fname" "$url" \
      || { echo "!! 下载失败：$url"; rm -f "$OUT/$fname"; exit 1; }
  fi
  # 幂等替换
  sed -i "s|$url|./$fname|g" /tmp/gf.local.css
  [ $((i % 25)) -eq 0 ] && echo "  … $i"
done
echo "共 $i 个字体文件"
mv /tmp/gf.local.css "$OUT/fonts.css"
du -sh "$OUT"
echo "== 完成：$OUT/fonts.css + woff2 =="
