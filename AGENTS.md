> **重要**: このファイルはルート `AGENTS.md` を前提とする差分定義である。
> 共通ルール（STOPPERS・WORKING PROTOCOL・TECHNICAL SPEC・DONE等）は必ずルートを参照すること。

# AI Agents Rules (openrouter-routing)

## 🛠️ TECHNICAL SPEC (PROJECT OVERRIDE)

- **Environment**: WSL (Ubuntu) + mise
- **Working Directory**: プロジェクトルートからの相対パス（`./`基点）
- **Language**: Python 3.11+
- **Testing**: `pytest` 必須。テスト失敗状態でのコミットを禁ずる。
- **Dependencies**: `requirements.txt` でバージョン固定。追加時は理由と代替案を提示せよ。

## 📋 PROJECT SPECIFIC (openrouter-routing)

### プロジェクト概要
- **目的**: OpenRouter APIのルーティングとフェイルオーバー制御
- **構成**: アダプターパターンによるモデルルーティング

### セキュリティ規則
- **APIキー**: 環境変数 `OPENROUTER_API_KEY` から取得。ハードコード厳禁。
- **.envファイル**: `.env` は `.gitignore` に登録済み。誤ってコミットしないよう注意。

### 実装方針
- **Interface活用**: アダプターは `BaseModelAdapter` を継承すること
- **リトライ**: フェイルオーバー時は指数バックオフを使用
- **型ヒント**: すべての公開メソッドに型ヒントを付与
- **エラーハンドリング**: 外部APIエラーは例外として伝播し、呼び出し元で処理
