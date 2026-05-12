# Free Model Router

無料LLM、便利だけどすぐ429で止まる。

- 有名なモデルを試す → 429
- 別のモデルを試す → 429
- もう一回 → 429

正直めんどくさいので、ツールで解決します。

複数のプロバイダーの無料モデルを自動で順番に試し、
失敗（429 / タイムアウト）したら次のモデルへ切り替え続けます。
つまり、モデル選びやリトライを自動化できます。

すべてダメな場合は、ローカルのOllamaにフォールバックします。

ローカルで起動し、OpenAI互換APIの接続先をこれに向けるだけで使えます。

## 特徴

- **OpenAI 互換 API** (`/v1/chat/completions`)
- **マルチプロバイダー対応** — OpenRouter, Groq, Cerebras, SambaNova, ローカル Ollama
- **動的モデル検出** — 各プロバイダーから利用可能なモデルを自動取得
- **プロバイダーファクトリーパターン** — 新規プロバイダーの追加が容易（1ファイル + 1行の登録のみ）
- **優先順位ルーティング** — `qwen`, `nemotron` 等の高性能モデルを優先
- **自動フォールオーバー** — 429 / 404 / タイムアウト時に次モデルへ切替
- **存在しないモデル検出** — 存在しないモデル（404）を600秒クールダウンで自動除外
- **ローカルフォールバック** — 全クラウドモデル失敗時は Ollama へフォールバック
- **ストリーミング対応** — SSE 形式でリアルタイム応答
- **ツール呼び出し自動検証** — 新規モデル検出時に function calling の可否を自動テストし、非対応モデルを自動除外

## ディレクトリ構造

```
free-model-router/
├── main.py                   # FastAPI サーバー本体
├── config.yaml               # プロバイダー設定・タイムアウト・優先度設定
├── run.sh                    # uv sync・依存インストール・起動
├── pyproject.toml            # Python 依存パッケージとプロジェクトメタデータ
├── known_vendors.json        # 既知ベンダーリスト（自動更新）
│
├── adapters/
│   ├── __init__.py
│   ├── base.py               # 抽象アダプター
│   ├── provider_factory.py   # プロバイダーファクトリー（動的登録）
│   ├── openrouter.py         # OpenRouter アダプター
│   ├── groq.py               # Groq アダプター
│   ├── cerebras.py           # Cerebras アダプター
│   ├── sambanova.py          # SambaNova アダプター
│   └── ollama.py             # Ollama ローカルアダプター
│
├── router/
│   ├── __init__.py
│   ├── model_router.py            # モデルリスト取得・優先順位付け
│   ├── failover.py                # 429/タイムアウト検知・次モデルへ切替
│   ├── tool_verifier.py           # モデルのツール呼び出し対応を検証
│   └── tool_support_registry.py   # ツール対応モデルのキャッシュ管理
│
├── tests/                    # テストファイル群
│
└── docs/
    └── how-it-works.md       # システム動作解説（アーキテクチャ・フロー図）
```

## セットアップ

### 0. uv のインストール

```bash
# uv がインストールされていない場合
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 1. API キー設定

```bash
cp .env.example .env
# .env を編集して各プロバイダーの API キーを設定:
# - OPENROUTER_API_KEY (OpenRouter を有効化する場合は必須)
# - GROQ_API_KEY (Groq を有効化する場合は必須)
# - CEREBRAS_API_KEY (Cerebras を有効化する場合は必須)
# - SAMBANOVA_API_KEY (SambaNova を有効化する場合は必須)
```

`config.yaml` でプロバイダーの有効/無効を設定できます。

### 2. 起動

```bash
./run.sh
```

初回実行時は `uv` で依存関係を同期し、`.venv` を自動作成します。  
2回目以降は直接サーバーが起動します（デフォルト: `http://127.0.0.1:4141/v1`）。

### 3. 動作確認

```bash
curl -X POST http://127.0.0.1:4141/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

## 設定

`config.yaml` で以下を調整可能：

| 項目　　　　　　　　　　　　　 | 説明　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　                     |
| ------------------------------ | ------------------------------------------------------------------------------------------ |
| `timeout_seconds`　　　　　　  | 各モデルへのリクエストタイムアウト（秒）　　　　　　　　　　　　　　　                     |
| `model_cache_ttl_seconds`　　  | モデルリストキャッシュ有効期限（秒）　　　　　　　　　　　　　　　　　                     |
| `exclude_keywords`　　　　　　 | 除外するモデルのキーワード（日本語に弱いモデル等）　　　　　　　　　　                     |
| `priority_keywords`　　　　　  | モデル優先順位キーワード　　　　　　　　　　　　　　　　　　　　　　　                     |
| `ollama_model`　　　　　　　　 | ローカル Fallback モデル名　　　　　　　　　　　　　　　　　　　　　　                     |
| `verify_tool_support`　　　　  | 起動時に新規モデルのツール呼び出し対応を検証する（デフォルト `true`）                      |
| `verify_timeout_seconds`　　　 | 検証リクエストのタイムアウト（秒）　　　　　　　　　　　　　　　　　　                     |
| `tool_support_cache_file`　　  | 検証結果のキャッシュファイル名　　　　　　　　　　　　　　　　　　　　                     |
| `rate_limit_cooldown_seconds`  | 429 を返したモデルをスキップする秒数（デフォルト `600`、10分）                             |
| `not_found_cooldown_seconds`　 | 404/422 を返したモデルをスキップする秒数（デフォルト `3600`、1時間、存在しないモデル検出） |

### ツール呼び出し検証の動作

- 起動時、OpenRouter のモデルリスト中 **キャッシュに未記録のモデル** のみ検証します
- 簡単な function calling リクエストを送り、`tool_calls` が返るかを確認します
- 非対応と判定されたモデルは以降のリクエストから自動除外されます
- 検証に失敗（429 / タイムアウト等）した場合は判定保留とし、次回起動時に再試行します
- キャッシュファイル（`tool_support_cache.json`）は Git 管理外です
