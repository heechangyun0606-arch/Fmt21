#!/data/data/com.termux/files/usr/bin/bash
set -e

REPO="$HOME/fmt21-sync"
PROBE="/storage/emulated/0/Download/fmt21_probe"

cd "$REPO"

echo "=== FMT21 SYNC ==="

mkdir -p scripts results logs

# 현재까지 만든 분석 스크립트 전부 보관
cp -f "$HOME"/audit_true_new_*.py scripts/ 2>/dev/null || true
cp -f "$HOME"/fast_*.py scripts/ 2>/dev/null || true

# 지금까지 생성된 분석 결과 전부 수집
if [ -d "$PROBE" ]; then
    find "$PROBE" -maxdepth 1 -type f \
      \( -name "*.json" -o -name "*.csv" -o -name "*.txt" \) \
      -exec cp -f {} results/ \;
fi

# 현재 상태 기록
{
    echo "updated=$(date -Iseconds)"
    echo "host=Termux"
    echo "probe=$PROBE"
} > results/status.txt

git add run.sh scripts results logs

if git diff --cached --quiet; then
    echo "No changes."
else
    git commit -m "Sync FMT21 reverse engineering workspace"
    git branch -M main
    git push -u origin main
fi

echo
echo "=== COMPLETE ==="
echo "GitHub sync complete."
