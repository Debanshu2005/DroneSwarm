#!/usr/bin/env bash
# sync_coordination.sh
# Syncs the DroneOS coordination module into DroneOS1/2/3 with namespace substitution.
#
# Usage:
#   ./scripts/sync_coordination.sh [--dry-run] [--source DroneOS] [--targets DroneOS1,DroneOS2,DroneOS3]

set -euo pipefail

DRY_RUN=0
SOURCE="DroneOS"
TARGETS="DroneOS1,DroneOS2,DroneOS3"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --source)  SOURCE="$2"; shift 2 ;;
        --targets) TARGETS="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TOTAL_WRITTEN=0
TOTAL_SKIPPED=0

IFS=',' read -ra TARGET_LIST <<< "$TARGETS"

for TARGET in "${TARGET_LIST[@]}"; do
    TARGET="${TARGET// /}"   # trim whitespace
    N="${TARGET//[^0-9]/}"   # extract digits only
    if [[ -z "$N" ]]; then
        echo "ERROR: Cannot derive instance number from '$TARGET'" >&2
        exit 1
    fi

    SRC_COORD="$ROOT/$SOURCE/core/coordination"
    DST_COORD="$ROOT/$TARGET/core/coordination"
    TARGET_ROOT="$ROOT/$TARGET"
    SRC_MAIN="$ROOT/$SOURCE/main.py"
    DST_MAIN="$ROOT/$TARGET/main.py"
    SRC_TEST_CFG="$ROOT/$SOURCE/configs/flight.test.yaml"
    DST_CFG_DIR="$ROOT/$TARGET/configs"
    DST_TEST_CFG="$DST_CFG_DIR/flight.test.yaml"

    echo ""
    echo "=== Syncing $SOURCE -> $TARGET (N=$N) ==="

    # Guard: source coordination dir must exist
    if [[ ! -d "$SRC_COORD" ]]; then
        echo "ERROR: Source coordination directory not found: $SRC_COORD" >&2
        exit 1
    fi

    # Guard: create target root if needed
    if [[ ! -d "$TARGET_ROOT" ]]; then
        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "[DRY RUN] MKDIR: $TARGET_ROOT"
        else
            mkdir -p "$TARGET_ROOT"
        fi
    fi

    # ── Copy coordination subtree ──────────────────────────────────────────
    while IFS= read -r -d '' FILE; do
        REL="${FILE#$SRC_COORD/}"
        DEST="$DST_COORD/$REL"
        DEST_DIR="$(dirname "$DEST")"

        SUBSTITUTED="$(sed \
            -e "s/from DroneOS\./from DroneOS${N}./g" \
            -e "s/import DroneOS\./import DroneOS${N}./g" \
            "$FILE")"

        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "[DRY RUN] COPY: $FILE -> $DEST"
            continue
        fi

        mkdir -p "$DEST_DIR"

        EXISTING=""
        if [[ -f "$DEST" ]]; then
            EXISTING="$(cat "$DEST")"
        fi

        if [[ "$EXISTING" == "$SUBSTITUTED" ]]; then
            echo "  SKIP (up-to-date): $REL"
            TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
        else
            printf '%s' "$SUBSTITUTED" > "$DEST"
            echo "  WRITE: $REL"
            TOTAL_WRITTEN=$((TOTAL_WRITTEN + 1))
        fi
    done < <(find "$SRC_COORD" -name "*.py" -print0)

    # ── Coordination wiring in main.py ────────────────────────────────────
    if [[ ! -f "$DST_MAIN" ]]; then
        echo "  WARN: Target main.py not found: $DST_MAIN — skipping wiring"
    elif grep -q "create_formation_update_sender" "$DST_MAIN"; then
        echo "  SKIP (wiring already present): $TARGET/main.py"
        TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
    else
        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "[DRY RUN] WIRE: $DST_MAIN (coordination block)"
        else
            # Extract and namespace-substitute the coordination block from source main.py
            BLOCK="$(awk '/^def create_formation_update_sender/,0' "$SRC_MAIN" | \
                sed -e "s/from DroneOS\./from DroneOS${N}./g" \
                    -e "s/import DroneOS\./import DroneOS${N}./g")"
            if [[ -n "$BLOCK" ]]; then
                printf '\n\n%s\n' "$BLOCK" >> "$DST_MAIN"
                echo "  WRITE (wiring): $TARGET/main.py"
                TOTAL_WRITTEN=$((TOTAL_WRITTEN + 1))
            else
                echo "  WARN: Could not extract coordination block from $SRC_MAIN — skipping"
            fi
        fi
    fi

    # ── Test config ───────────────────────────────────────────────────────
    if [[ ! -f "$SRC_TEST_CFG" ]]; then
        echo "  WARN: Source test config not found: $SRC_TEST_CFG — skipping"
    else
        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "[DRY RUN] COPY: $SRC_TEST_CFG -> $DST_TEST_CFG"
        else
            mkdir -p "$DST_CFG_DIR"
            if [[ -f "$DST_TEST_CFG" ]] && diff -q "$SRC_TEST_CFG" "$DST_TEST_CFG" >/dev/null 2>&1; then
                echo "  SKIP (up-to-date): configs/flight.test.yaml"
                TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
            else
                cp "$SRC_TEST_CFG" "$DST_TEST_CFG"
                echo "  WRITE: configs/flight.test.yaml"
                TOTAL_WRITTEN=$((TOTAL_WRITTEN + 1))
            fi
        fi
    fi
done

echo ""
echo "Sync complete. Written: $TOTAL_WRITTEN, Skipped: $TOTAL_SKIPPED"
exit 0
