#!/bin/sh
# ============================================
# 機密情報・環境固有情報スキャナ（共有ライブラリ）
# ============================================
# 本ファイルは commit-msg / pre-commit / pre-push から `. ` で source される。
# パターン定義: .githooks/sensitive-patterns.txt
#
# 関数:
#   scan_sensitive_text "<text>" ... 文字列を走査。block パターン検出で 1 を返す。
#   scan_sensitive_file <path>   ... ファイル内容を走査。block パターン検出で 1 を返す。
#
# 警告（warn-regex）は stderr に出力するが戻り値に影響しない。

_SENSITIVE_PATTERNS_FILE="$(git rev-parse --show-toplevel 2>/dev/null)/.githooks/sensitive-patterns.txt"

_scan_sensitive_core() {
    # $1: mode = "text" | "file"
    # $2: text content | file path
    _mode="$1"
    _target="$2"
    _found=0

    [ ! -f "$_SENSITIVE_PATTERNS_FILE" ] && return 0

    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            ''|\#*) continue ;;
        esac
        _type="${_line%%:*}"
        _pattern="${_line#*:}"
        [ -z "$_pattern" ] && continue
        [ "$_type" = "$_line" ] && continue

        _hit=""
        if [ "$_mode" = "file" ]; then
            case "$_type" in
                literal)
                    _hit=$(grep -nF -- "$_pattern" "$_target" 2>/dev/null | head -3)
                    ;;
                regex)
                    _hit=$(grep -nE -- "$_pattern" "$_target" 2>/dev/null | head -3)
                    ;;
                warn-regex)
                    if grep -qE -- "$_pattern" "$_target" 2>/dev/null; then
                        echo "  [warn] pattern='$_pattern' in $_target" >&2
                    fi
                    ;;
            esac
        else
            case "$_type" in
                literal)
                    _hit=$(printf '%s\n' "$_target" | grep -nF -- "$_pattern" 2>/dev/null | head -3)
                    ;;
                regex)
                    _hit=$(printf '%s\n' "$_target" | grep -nE -- "$_pattern" 2>/dev/null | head -3)
                    ;;
                warn-regex)
                    if printf '%s\n' "$_target" | grep -qE -- "$_pattern" 2>/dev/null; then
                        echo "  [warn] pattern='$_pattern'" >&2
                    fi
                    ;;
            esac
        fi

        if [ -n "$_hit" ]; then
            echo "  [block] type=$_type pattern='$_pattern'" >&2
            echo "$_hit" | sed 's/^/    /' >&2
            _found=1
        fi
    done < "$_SENSITIVE_PATTERNS_FILE"

    return $_found
}

scan_sensitive_text() {
    _scan_sensitive_core text "$1"
}

scan_sensitive_file() {
    _scan_sensitive_core file "$1"
}
