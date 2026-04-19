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
# ビルトイン（パターン定義不要）:
#   - 作業中リポから自 owner/repo を自動検出
#   - 自リポ参照 (owner/repo, owner/repo#N, URL) はスキャン対象から除外
#   - 同 owner 配下の他リポ参照は block（非公開リポの存在露出を防ぐ）
#
# 警告（warn-regex）は stderr に出力するが戻り値に影響しない。

_SENSITIVE_PATTERNS_FILE="$(git rev-parse --show-toplevel 2>/dev/null)/.githooks/sensitive-patterns.txt"

# 正規表現メタ文字のエスケープ（POSIX sed 向け）
_escape_regex() {
    printf '%s' "$1" | sed 's/[][\.*^$|+?(){}/]/\\&/g'
}

# 自リポジトリ情報の自動検出
#   優先1: 環境変数 GITHUB_REPOSITORY（GitHub Actions 実行時に自動セット）
#   優先2: git remote origin URL から owner/repo を抽出
# 返却: "owner/repo" 形式（取得失敗時は空文字）
_detect_self_repo() {
    if [ -n "${GITHUB_REPOSITORY:-}" ]; then
        printf '%s' "$GITHUB_REPOSITORY"
        return 0
    fi
    _url=$(git config --get remote.origin.url 2>/dev/null || true)
    [ -z "$_url" ] && return 0
    printf '%s' "$_url" | sed -E 's#^.*github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(\.git)?/?$#\1#'
}

# 入力テキストから自リポ参照（owner/name 形式と URL 形式）を placeholder にマスクして出力
#   自リポが検出できない場合は入力をそのまま返す
_mask_self_repo_refs() {
    _text="$1"
    _self_repo=$(_detect_self_repo)
    if [ -z "$_self_repo" ]; then
        printf '%s' "$_text"
        return 0
    fi
    case "$_self_repo" in
        */*) : ;;
        *) printf '%s' "$_text"; return 0 ;;
    esac
    _self_owner_re=$(_escape_regex "${_self_repo%%/*}")
    _self_name_re=$(_escape_regex "${_self_repo##*/}")
    printf '%s' "$_text" \
        | sed -E "s#(^|[^A-Za-z0-9_.-])${_self_owner_re}/${_self_name_re}([^A-Za-z0-9_.-]|\$)#\\1[SELF]\\2#g" \
        | sed -E "s#https?://github\\.com/${_self_owner_re}/${_self_name_re}([#/?][^[:space:]]*)?#[SELF_URL]#g"
}

# ビルトイン: 自 owner 配下の他リポ参照を検出（block）
#   $1: 自リポ参照マスク済みテキスト
# 戻り値: 0 = 問題なし / 1 = 他リポ参照検出
_scan_builtin_same_owner() {
    _masked="$1"
    _self_repo=$(_detect_self_repo)
    [ -z "$_self_repo" ] && return 0
    case "$_self_repo" in
        */*) : ;;
        *) return 0 ;;
    esac
    _self_owner="${_self_repo%%/*}"
    _self_owner_re=$(_escape_regex "$_self_owner")

    _hit=$(printf '%s\n' "$_masked" \
        | grep -nE "(^|[^A-Za-z0-9_.-])${_self_owner_re}/[A-Za-z0-9_.-]+|https?://github\\.com/${_self_owner_re}/[A-Za-z0-9_.-]+" 2>/dev/null \
        | head -3)

    if [ -n "$_hit" ]; then
        echo "  [block] builtin: 同 owner '$_self_owner' 配下の他リポジトリ参照は禁止" >&2
        echo "$_hit" | sed 's/^/    /' >&2
        return 1
    fi
    return 0
}

_scan_sensitive_core() {
    # $1: mode = "text" | "file"
    # $2: text content | file path
    _mode="$1"
    _target="$2"
    _found=0

    # --- 入力取得（file/text どちらも文字列として扱い、自リポ参照をマスクする） ---
    if [ "$_mode" = "file" ]; then
        if [ -f "$_target" ]; then
            _content=$(cat "$_target" 2>/dev/null)
        else
            _content=""
        fi
        _loc_suffix=" in $_target"
    else
        _content="$_target"
        _loc_suffix=""
    fi
    _masked=$(_mask_self_repo_refs "$_content")

    # --- ビルトイン: 自 owner 配下の他リポ参照検出 ---
    if [ -n "$_masked" ]; then
        if ! _scan_builtin_same_owner "$_masked"; then
            [ -n "$_loc_suffix" ] && echo "   (file:$_target)" >&2
            _found=1
        fi
    fi

    [ ! -f "$_SENSITIVE_PATTERNS_FILE" ] && return $_found

    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            ''|\#*) continue ;;
        esac
        _type="${_line%%:*}"
        _pattern="${_line#*:}"
        [ -z "$_pattern" ] && continue
        [ "$_type" = "$_line" ] && continue

        _hit=""
        case "$_type" in
            literal)
                _hit=$(printf '%s\n' "$_masked" | grep -nF -- "$_pattern" 2>/dev/null | head -3)
                ;;
            regex)
                _hit=$(printf '%s\n' "$_masked" | grep -nE -- "$_pattern" 2>/dev/null | head -3)
                ;;
            warn-regex)
                if printf '%s\n' "$_masked" | grep -qE -- "$_pattern" 2>/dev/null; then
                    echo "  [warn] pattern='$_pattern'${_loc_suffix}" >&2
                fi
                ;;
        esac

        if [ -n "$_hit" ]; then
            echo "  [block] type=$_type pattern='$_pattern'${_loc_suffix}" >&2
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
