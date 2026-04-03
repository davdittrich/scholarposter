libs=("PyMuPDF" "pymupdf4llm" "pdfplumber" "pypdf" "marker-pdf" "docling" "sumy" "lexrank" "textrank" "vllm" "av" "moviepy" "ffmpeg-python" "Pillow" "Mastodon.py" "atproto" "httpx")
for lib in "${libs[@]}"; do
  (
    version=$(curl -s "https://pypi.org/pypi/$lib/json" | grep -oP '"version":"\K[^"]+' | head -1)
    echo "$lib: $version"
  ) &
done
wait
echo "ntfy.sh: $(curl -s -I https://ntfy.sh | grep -i "^HTTP" | head -1)"
