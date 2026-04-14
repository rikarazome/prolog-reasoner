# prolog-reasoner 仕様書 v13

## Context

LLMは自然言語処理に強いが論理推論が弱い。Prologは逆に論理推論が厳密だが自然言語を扱えない。この補完関係を活かし、LLMが「論理の電卓」としてPrologを使えるMCPサーバー + Pythonライブラリを構築する。

既存研究(Logic-LM, ChatLogic, LoRP)は学術プロトタイプ止まりで、開発者が`pip install`して使えるツールが存在しない。この空白を狙う。

核心的な差別化: Prolog中間表現の可視化と直接実行により、AIのブラックボックス問題を「検証可能」にする。

### 2つの配布面（MCP と ライブラリ）

v13以降、このプロジェクトは2つの独立した配布面を持つ:

| 面 | 利用者 | NL→Prolog翻訳 | 必要な外部APIキー |
|----|--------|-------------|-----------------|
| **MCPサーバー** | 接続済みLLM（Claude等）がツールとして呼ぶ | **LLM側で行う**（Claude自身がPrologを書く） | なし |
| **Pythonライブラリ** | LLMをプログラムに組み込む開発者 | ライブラリ内部のLLMClientが行う | OpenAI / Anthropic |

両者は `PrologExecutor`（Prolog実行エンジン）を共有する。ライブラリはさらに `PrologTranslator` / `LLMClient` を上に重ね、NL→Prolog変換と自己修正ループを提供する。MCPサーバーは**翻訳機能を持たない** — 接続先LLMが自身の推論でProlog生成を行うため。

### 変更履歴

- v1 (2026-04-13): 初版
- v2 (2026-04-13): アーキテクチャ・セキュリティ・整合性レビューを反映した全面改訂
- v3 (2026-04-13): セキュリティ方針を「暴走防止」に再設計。過剰なサンドボックスを撤廃
- v4 (2026-04-13): 出力形式を生テキストに変更。レビュー指摘（整合性・命名・依存関係等）を全件反映
- v5 (2026-04-13): エラーハンドリング境界の明確化、Windows UTF-8対応、validate_syntax詳細、DI配線、LLMタイムアウト追加
- v6 (2026-04-13): UTF-8ヘッダーをprepend化（3層構造）、translate_with_correction戻り値をTranslationResultに統一、LLMClient.__init__修正
- v7 (2026-04-14): LLMClient.complete()タイムアウト設定反映、dead config削除、translate()エラー契約明記、main()定義追加
- v8 (2026-04-14): executor.execute()タイムアウト設定反映、3層結合の改行セパレータ明記、FastMCPインスタンス定義、エラーコードコメント修正
- v9 (2026-04-14): reasoner.translate()委譲先明記、validate_swipl()堅牢化、setup_logging()重複防止、APIキーパターン修正
- v10 (2026-04-14): REDACT_PATTERNSの到達不能パターン削除、metadata.result_count算出方法を定義
- v11 (2026-04-14): validate_swipl()のConfigurationErrorにerror_code追加、result_countの解なし時定義を明確化
- v12 (2026-04-14): suggested_queryの末尾ピリオド除去を明記（外部レビュー反映）
- v13 (2026-04-14): MCP/ライブラリの責務分離。MCPから`translate_to_prolog`削除、`llm_api_key`を省略可能化、server.pyを遅延初期化に変更

---

## 1. アーキテクチャ概要

```
                   ┌─────────────────────┐
                   │  PrologExecutor     │
                   │  (SWI-Prolog実行)   │  ← 共有コンポーネント
                   └──────────▲──────────┘
                              │
          ┌───────────────────┴──────────────────┐
          │                                      │
┌─────────┴──────────┐              ┌────────────┴─────────────┐
│  server.py         │              │  reasoner.py             │
│  (MCPサーバー)     │              │  (ライブラリAPI)         │
│                    │              │                          │
│  execute_prolog    │              │  translate() / execute() │
│  のみ公開          │              │  + PrologTranslator      │
│                    │              │  + LLMClient             │
│  APIキー不要       │              │  APIキー必要             │
└─────────▲──────────┘              └────────────▲─────────────┘
          │ stdio                                │
┌─────────┴──────────┐              ┌────────────┴─────────────┐
│  接続LLM           │              │  ユーザーPythonアプリ    │
│  (Claude等)        │              │                          │
│  Prologを自分で書く │              │  OpenAI/Anthropic経由    │
└────────────────────┘              └──────────────────────────┘
```

**責務分離の核心:** MCPサーバーは「Prolog実行エンジンへのリモート呼び出し口」にすぎない。接続先LLMが自身の推論能力でProlog生成を行うため、MCP層には翻訳機能もLLM Clientも含まれない。ライブラリは逆に「NL→Prolog→実行」の完全パイプラインを提供し、LLMをプログラムに組み込む開発者向け。両者は同一パッケージ(`prolog_reasoner`)に同居するが、**インポート依存は一方向**（server.pyはtranslatorとllm_clientをimportしない）。

**ファイルI/O（Prolog中間表現の保存）はこのシステムの責務ではない。** MCPの`execute_prolog`が受け取るprolog_codeをファイルに保存するのはMCPクライアント（LLM）側のツール（Write等）で行う。prolog-reasonerはコード文字列の入出力のみ担当する。

### 変更点と根拠

| 変更 | 根拠 |
|------|------|
| **4層→3層** (executor+correctorを統合) | 自己修正は翻訳の一部であり、独立コンポーネントにする意味がない |
| **engine.py→reasoner.py** (ファサードパターン) | ライブラリのパブリックAPI。translateとexecuteは独立操作で、呼び出し側（LLM）が組み合わせる |
| **抽象バックエンド削除** (subprocess一本化) | MVP時点でJanusは不要。LLM API呼び出し(1-3秒)に対しProlog実行(1-100ms)は誤差。YAGNI原則 |
| **LiteLLM→直接API呼び出し** | 100+プロバイダー対応はMVPに過剰。OpenAI/Anthropic SDKを直接使い、抽象化は後から追加 |
| **prompts.pyをtranslator.pyに統合** | プロンプトは翻訳器の内部実装。分離すると翻訳ロジックの理解が困難になる |
| **自己修正をTranslatorに配置** | 修正ループは「翻訳の品質向上」であり、翻訳器の責務。Executorは純粋に実行のみ |

### 設計原則

1. **MCP層はビジネスロジックを持たない** -- Core層に委譲
2. **Core層はMCPに依存しない** -- スタンドアロンのPythonライブラリとして使える
3. **stdoutに絶対にprintしない** -- JSON-RPCプロトコルが壊れる。全てstderrへ
4. **YAGNI** -- 現時点で必要ない抽象化を作らない。必要になった時にリファクタリング
5. **暴走防止はデフォルトで有効** -- タイムアウトと結果数制限で意図しない暴走を防ぐ
6. **過剰なセキュリティで機能を制限しない** -- 詳細はセキュリティ方針（§5）参照

---

## 2. MCPツール設計

### 設計判断: 1ツールに絞る（v13）

v1では`reason`, `execute_prolog`, `generate_prolog`の3ツール、v2-v12では`translate_to_prolog` + `execute_prolog`の2ツール構成だったが、v13で**`execute_prolog`のみの1ツール**に削減した。

**v13の判断根拠:**
- MCPサーバーに接続するLLM（Claude等）は自身の推論能力で自然言語→Prolog変換を行える。サーバー側で別のLLM APIを呼び出す意味がない。
- `translate_to_prolog`を残すと、MCPサーバーが外部LLM APIキーを要求することになり、利用者が「なぜAPIキーが必要？」と混乱する。一般的なMCPサーバー（playwright, filesystem, git等）はAPIキーを要求しない。
- Unix哲学（小さな道具の組み合わせ）は維持される。接続LLMが「Prologを書く→`execute_prolog`で実行→結果を解釈」のワークフローを自分で構築する。

翻訳機能自体は削除されない — ライブラリ側の `PrologReasoner.translate()` に残存する（§4.2, §4.3）。プログラムにLLMを組み込みたい開発者向け。

### Tool: `execute_prolog`

```python
@mcp.tool()
async def execute_prolog(
    prolog_code: str,
    query: str,
    max_results: int = 100
) -> dict:
    """
    Prologコードを実行し推論結果を返す。
    接続LLMがその場で書いたコードや、ライブラリ側の`PrologReasoner.translate()`で生成したコード、手動で書いたコードのいずれも実行できる。

    Args:
        prolog_code: 実行するPrologコード（事実とルールの定義）
        query: 実行するPrologクエリ（例: "mortal(X)"）
        max_results: 返す解の最大数（無限ループ防止）
    """
```

**戻り値:**
```json
{
    "success": true,
    "output": "mortal(socrates)\n",
    "query": "mortal(X)",
    "metadata": {
        "backend": "subprocess",
        "execution_time_ms": 12,
        "result_count": 1,
        "truncated": false
    }
}
```

### 設計判断: パラメータの明確化

| v1の問題 | v2以降の解決策 | 根拠 |
|----------|-----------|------|
| `prolog_source`がコードとパスの両方を受ける | `prolog_code`（コードのみ）に限定 | コードとパスの判別が曖昧。ファイル読み込みはMCPクライアント側で行う |
| `context`の型が不一致（str vs dict） | `str`に統一 | LLMが構築しやすい。構造化が必要なら将来拡張 |
| `query`が省略可能で自動推定 | `execute_prolog`では`query`を必須化 | 自動推定アルゴリズムが未定義で曖昧。明示的指定を要求する |
| `export_path`がツールパラメータ | 削除。ファイル保存はMCPクライアントの責務 | prolog-reasonerはコード文字列の入出力のみ担当 |
| `explanation`が戻り値に含まれる | 削除。必要ならLLM自身が結果を解釈する | 説明生成のためだけにLLM呼び出しするのは過剰 |
| `results`が構造化dict | `output`（生テキスト）に変更 | パーサーの複雑さとバグリスクを排除。LLMが解釈できれば十分 |

### output のフォーマット

`execute_prolog`の`output`はSWI-Prologの出力テキストをそのまま返す。

executorがユーザーのクエリを実行するために自動生成するPrologラッパー（§4.4参照）が出力を制御するため、フォーマットは予測可能である:

```
% mortal(X) の場合 → 各解を1行ずつ、write_canonical形式で出力
mortal(socrates)
mortal(plato)

% mortal(socrates) の場合（変数なし）→ インスタンス化されたクエリ項がそのまま出力
mortal(socrates)

% 解なしの場合
false

% max_results超過時 → 最後に truncated マーカー
num(1)
num(2)
num(3)
__TRUNCATED__
```

**ルール:**
- 各解は `write_canonical/1` で1行1項ずつ出力。変数あり/なしで形式は変わらない
- 解なし = `false` の1行
- `max_results`超過時 = 最後に `__TRUNCATED__` マーカー
- LLMは「mortal(socrates)が出力された＝この述語が成立した」と文脈から理解できる

**設計判断:** LLM（一次消費者）は上記テキストを自然に読める。Pythonライブラリとして構造化データが必要になった場合は、`output`を維持したまま`results`フィールドを追加する（§11 将来の拡張参照）。

---

## 3. プロジェクト構造

```
prolog-reasoner/
├── pyproject.toml
├── README.md
├── LICENSE (MIT)
├── .gitignore
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── src/
│   └── prolog_reasoner/
│       ├── __init__.py
│       ├── server.py              # FastMCPサーバー + ツール定義
│       ├── reasoner.py            # パブリックAPI（ライブラリのエントリポイント）
│       ├── translator.py          # NL→Prolog変換 + 自己修正ループ
│       ├── executor.py            # Prolog実行（クエリラッパー生成含む）
│       ├── llm_client.py          # LLM API呼び出し抽象化
│       ├── models.py              # Pydanticデータモデル
│       ├── config.py              # 設定管理
│       ├── errors.py              # 例外階層
│       └── logger.py              # 構造化ログ（stderr専用）
├── tests/
│   ├── conftest.py                # 共通フィクスチャ
│   ├── unit/
│   │   ├── test_translator.py
│   │   ├── test_executor.py
│   │   └── test_models.py
│   ├── integration/
│   │   ├── test_reasoner.py
│   │   └── test_mcp_server.py
│   └── fixtures/
│       ├── prolog/                # テスト用.plファイル
│       └── llm_responses/         # 録画済みLLMレスポンス
└── examples/
    └── standalone_usage.py
```

### 変更点と根拠

| 変更 | 根拠 |
|------|------|
| `mcp/`サブパッケージ廃止→`server.py`をルートに | ツール定義1ファイルにサブパッケージは過剰 |
| `prolog/backends/`廃止 | 抽象バックエンド削除（subprocess一本化） |
| `core/`サブパッケージ廃止→フラット構造 | ファイル数が少ないためネストは不要 |
| `llm/`サブパッケージ廃止→`llm_client.py` | ファイル1つにサブパッケージは過剰 |
| `config/`廃止→`config.py` | 同上 |
| `sandbox.py`廃止 | セキュリティ方針変更。サンドボックスは不要（§5参照） |
| `tests/security/`廃止 | 同上。暴走防止テストはtest_executor.pyに含める |
| `tests/fixtures/llm_responses/`追加 | LLM非決定性への対策。録画再生テスト |
| `pipeline.py`→`reasoner.py` | 実態はファサード（独立した2操作のAPI）であり「パイプライン」は不正確 |
| `logging.py`→`logger.py` | Python標準ライブラリの`logging`モジュールとの名前衝突を回避 |
| `test_pipeline.py`→`test_reasoner.py` | ファイル名の統一 |

---

## 4. コアコンポーネント設計

### 4.1 データモデル (models.py)

```python
from pydantic import BaseModel, Field

class TranslationRequest(BaseModel):
    """PrologReasoner.translate() への入力（ライブラリ利用時）"""
    query: str = Field(min_length=1, description="自然言語の質問")
    context: str = Field(default="", description="追加の前提条件")
    max_corrections: int = Field(default=3, ge=0, le=10)

class ExecutionRequest(BaseModel):
    """execute_prologツールへの入力"""
    prolog_code: str = Field(min_length=1, description="Prologコード")
    query: str = Field(min_length=1, description="Prologクエリ")
    max_results: int = Field(default=100, ge=1, le=10000)

class TranslationResult(BaseModel):
    """翻訳の結果"""
    success: bool
    prolog_code: str = ""
    suggested_query: str = ""
    error: str | None = None
    metadata: dict = Field(default_factory=dict)

class ExecutionResult(BaseModel):
    """実行の結果"""
    success: bool
    output: str = ""
    query: str = ""
    error: str | None = None
    metadata: dict = Field(default_factory=dict)
```

**v1からの変更:**
- `ReasoningRequest`の全モード共用モデル廃止 → ツールごとに専用モデル
- `context`を`str`に統一（型不一致の解消）
- `query`を`ExecutionRequest`で必須化（自動推定の曖昧さ排除）
- `results: list[dict[str, str]]`を`output: str`に変更（生テキスト方式）
- `explanation`フィールド削除（LLM自身が結果を解釈する）

### 4.2 パブリックAPI (reasoner.py)

ライブラリのエントリポイント。server.pyとスタンドアロン利用の両方がこのクラスを通じてCore層にアクセスする。

```python
class PrologReasoner:
    """prolog-reasonerライブラリのパブリックAPI"""
    def __init__(self, translator: PrologTranslator, executor: PrologExecutor):
        self.translator = translator
        self.executor = executor

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """
        self.translator.translate_with_correction()に委譲。
        executor引数にはself.executorを渡す（構文検証用）。
        LLMError（インフラ障害）はそのままraiseされる。
        """

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """
        self.executor.execute()に委譲。
        BackendError（インフラ障害）はそのままraiseされる。
        """
```

**根拠:** `translate`と`execute`は独立した操作。内部で暗黙に連結しない。LLM（MCPクライアント）が明示的に組み合わせる。

**初期化（コンポーネントの組み立て）:**

server.py（MCP）とライブラリ利用では初期化経路が異なる（v13）。

**server.py (MCP) — Executorのみを遅延初期化:**

```python
# server.py
_executor: PrologExecutor | None = None

def _init() -> None:
    """Initialize settings and executor on first use."""
    global _executor
    if _executor is not None:
        return
    settings = Settings()                # 環境変数から読み込み（llm_api_key不要）
    settings.validate_swipl()            # SWI-Prolog存在確認
    setup_logging(settings.log_level)
    _executor = PrologExecutor(settings)

mcp = FastMCP("prolog-reasoner")

@mcp.tool()
async def execute_prolog(prolog_code: str, query: str, max_results: int = 100) -> dict:
    _init()
    # ... _executor.execute() に委譲 ...

def main() -> None:
    """pyproject.toml [project.scripts] から呼ばれる起動関数"""
    mcp.run()
```

**初期化が遅延である理由:**
- `from prolog_reasoner.server import mcp` をimportしただけでSWI-Prolog存在確認が走るのを避ける
- テスト環境、docsビルド、その他import-only シナリオでクラッシュしないようにするため
- 最初の`execute_prolog`呼び出し時に一度だけ初期化される

**ライブラリ利用（完全パイプライン）— PrologReasonerを自分で組み立てる:**

```python
# ユーザーコード or examples/standalone_usage.py
settings = Settings(llm_api_key="sk-...")
settings.validate_swipl()
setup_logging(settings.log_level)
llm_client = LLMClient(
    provider=settings.llm_provider,
    api_key=settings.llm_api_key,
    model=settings.llm_model,
    timeout_seconds=settings.llm_timeout_seconds,
)
reasoner = PrologReasoner(
    translator=PrologTranslator(llm_client, settings),
    executor=PrologExecutor(settings),
)
```

**重要:** `prolog_reasoner` パッケージは `PrologReasoner`, `PrologTranslator`, `PrologExecutor` 等を公開するが、**`create_reasoner()` のような「全自動組み立てヘルパー」は提供しない**。v12以前は存在したが、v13でMCP側が翻訳を使わなくなったため削除した。ライブラリ利用者は上記のようにDIを明示的に行う（Settings値を変えたい、モックLLMを挿したい等の柔軟性を優先）。

### 4.3 翻訳器 (translator.py)

**設計判断: 3フェーズ→1フェーズ + 自己修正**

v1のLogic-LM 3フェーズ（意味翻訳→構文変換→バリデーション）は学術的だが、MVPでは単一のプロンプトで十分。自己修正ループが構文エラーをカバーするため、事前の多段変換は不要。

```python
class PrologTranslator:
    """自然言語→Prolog変換 + 自己修正"""

    SYSTEM_PROMPT = """You are a Prolog code generator for SWI-Prolog.
Convert natural language facts and queries into valid Prolog code.

RULES:
- Output ONLY valid Prolog code, no markdown or explanations
- Use lowercase for atoms, uppercase for variables
- Include a comment "% Query: <query>" indicating the suggested query
- Use standard SWI-Prolog predicates
- Use CLP(FD) library (:- use_module(library(clpfd)).) for constraint problems
"""

    async def translate(
        self, query: str, context: str = ""
    ) -> tuple[str, str]:
        """
        Returns: (prolog_code, suggested_query)
        Raises: TranslationError -- LLMが空応答を返した場合（TRANSLATION_001）
        """

    async def translate_with_correction(
        self, query: str, context: str, executor: PrologExecutor, max_corrections: int
    ) -> TranslationResult:
        """
        翻訳 + 構文検証ループ:
        1. translate()でProlog生成
        2. executor.validate_syntax()で構文チェック
        3. エラーがあればエラーメッセージ付きで再翻訳
        4. max_corrections回まで繰り返し
        5. 成功 → TranslationResult(success=True, prolog_code=..., suggested_query=...)
           修正上限超過 → TranslationResult(success=False, error="...", metadata={"error_code": "TRANSLATION_002"})
        """
```

**自己修正ループの所在:** Translator内に配置する。修正ループは「翻訳の品質向上」であり、Translatorの責務。ExecutorはTranslatorから呼ばれる`validate_syntax()`を提供するのみ。

**プロンプト設計の根拠:** LLMへの指示は最小限に留め、SWI-Prologの機能を自由に活用できるようにする。ライブラリの使用（CLP(FD)等）も制限しない。

**`suggested_query`の抽出:** LLM出力から `% Query: <query>` コメントをパースする。抽出後、末尾のピリオド(`.`)と前後の空白を除去する（LLMが `% Query: mortal(socrates).` のようにピリオド付きで出力する場合があり、ラッパーに埋め込むと構文エラーになるため）。LLMがコメントを含めなかった場合は `suggested_query = ""` を返す（LLMまたはユーザーが後から指定する）。

### 4.4 実行器 (executor.py)

```python
class PrologExecutor:
    """SWI-Prologのサブプロセス実行"""

    async def execute(
        self, prolog_code: str, query: str, max_results: int = 100,
        timeout_seconds: float | None = None
    ) -> ExecutionResult:
        """
        timeout_seconds: 省略時はSettings.execution_timeout_secondsの値を使用

        1. UTF-8ヘッダーをprolog_codeの先頭にprepend（\nで結合）
        2. クエリ実行ラッパーをprolog_codeの末尾にappend（\nで結合）
        3. SWI-Prologをサブプロセスで起動
        4. stdinで結合コード（ヘッダー + \n + ユーザーコード + \n + ラッパー）を送信
        5. タイムアウト監視下で実行（タイムアウト時はプロセスをkill）
        6. stdoutをoutputとして返す
        """

    async def validate_syntax(self, prolog_code: str) -> str | None:
        """
        SWI-Prologにconsultさせて構文エラーの有無を確認。
        ディレクティブ（:- use_module(...)等）は副作用として実行されるが、
        ローカルツールのため問題ない。

        実装詳細:
        - コードの先頭にUTF-8ストリーム設定を追加（execute()と同じ、\nで結合）
        - コードの末尾に `\n:- halt(0).\n` を追加し、consult後に確実に終了させる
        - execute()と同じタイムアウト(execution_timeout_seconds)を適用
          （ディレクティブ内の無限ループ対策）
        - stderrに "ERROR:" を含む行があればエラーと判定
        - タイムアウト時はproc.kill() + proc.wait()で確実にプロセスを回収

        Returns: stderrのエラーメッセージ or None（正常）
        """
```

**SWI-Prologの起動:**
```python
proc = await asyncio.create_subprocess_exec(
    self.swipl_path,
    '-f', 'none',              # ユーザーのinit fileを読まない（再現性確保）
    '-q',                      # バナー・ヘルプメッセージ抑制
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    env={**os.environ, 'LANG': 'C.UTF-8'},  # Linux/macOSフォールバック（主たるUTF-8対策はラッパー内のset_stream）
)
```

**Prolog入力の3層構造:**

executorはユーザーのPrologコードの前後にヘッダーとラッパーを付加する。各層は `\n` で結合する（ユーザーコード末尾に改行がない場合でもPrologの項終端 `.` が正しく認識されるようにするため）。`<QUERY>`と`<MAX_RESULTS>`は文字列置換（`.replace()`）で埋め込む（`.format()`はPrologの中括弧`{}`と衝突するため使わない）。

```python
prolog_input = HEADER + "\n" + prolog_code + "\n" + WRAPPER
```

```
[1. UTF-8ヘッダー]   ← ユーザーコードの前にprepend
[    \n separator  ]
[2. ユーザーコード]   ← prolog_codeそのまま
[    \n separator  ]
[3. クエリラッパー]   ← ユーザーコードの後にappend
```

**1. UTF-8ヘッダー（prepend）:**
```prolog
:- set_stream(user_input, encoding(utf8)).
:- set_stream(user_output, encoding(utf8)).
:- set_prolog_flag(verbose, silent).
```

ユーザーコードより先に実行され、以降のstdin読み込みをUTF-8に設定する。`:- set_stream(user_input, encoding(utf8)).` 自体はASCIIなのでどのエンコーディングでも正しく読める。これにより非ASCIIアトム（日本語等）を含むユーザーコードが正しくパースされる。

**2. クエリラッパー（append）:**
```prolog
:- nb_setval('__pr_count', 0).
:- ( <QUERY>,
     nb_getval('__pr_count', N),
     ( N >= <MAX_RESULTS>
     -> (write('__TRUNCATED__'), nl, !)
     ;  (N1 is N + 1,
         nb_setval('__pr_count', N1),
         write_canonical(<QUERY>), nl,
         fail)
     )
   ; true
   ),
   nb_getval('__pr_count', Final),
   ( Final =:= 0 -> write(false), nl ; true ),
   halt(0).
:- halt(1).
```

**ラッパーの設計根拠:**
- `write_canonical/1`で各解を1行1項ずつ出力（Prolog標準形式、LLMが読める）
- `fail`で明示的にバックトラックを駆動し、次の解を探索する
- `nb_setval`/`nb_getval`でカウンタ管理（`findall`はメモリ枯渇の原因になるため使わない）
- カウンタが`max_results`に達したら`__TRUNCATED__`を出力し、`!`（カット）で探索を停止
- `; true` で全解の列挙完了後を捕捉
- 解が0件の場合は`false`を出力
- 変数なしクエリ（例: `mortal(socrates)`）でも`write_canonical`でインスタンス化された項を出力（全クエリで形式統一）
- `halt(1)`はラッパー自体の構文エラー時のフォールバック
- `forall/2`は使わない（内部で二重否定を使うため`!`が外側の探索を停止しない）
- `set_stream/2`はUTF-8ヘッダー内でユーザーコードより先に実行される（WindowsのCP932問題をProlog側で解決。`LANG`環境変数はLinux/macOS用フォールバック）
- カウンタ変数名は `'__pr_count'`（クォート付きアトム）でユーザーコードとの衝突を回避
- `metadata.truncated`: executorが出力末尾の `__TRUNCATED__\n` を検出して設定する
- `metadata.result_count`: executorがoutputの非空行数から算出する（`__TRUNCATED__`行および`false`行は除外。解なし時は0）

**成功/失敗の判定:**

| 条件 | `success` | `error` | `output` |
|------|-----------|---------|----------|
| exit code = 0, stderr にPrologエラーなし | `True` | `None` | stdoutの内容 |
| exit code = 0, stderr にPrologエラーあり | `False` | stderrの内容 | stdoutの内容（部分結果がある場合） |
| exit code != 0 | `False` | stderrの内容 | `""` |
| タイムアウト | `False` | `"Prolog execution timed out..."` | `""` |

**注:** Prologの推論結果が「解なし」の場合は `success=True`, `output="false\n"` である。これは正常な推論結果であり失敗ではない。`success=False` は構文エラー、タイムアウト、プロセス異常終了など実際の障害のみを示す。

**stderrの取り扱い:**
- `validate_syntax()`: stderrをパースしてエラーメッセージを返す
- `execute()`: stderrにPrologエラーがあれば`error`フィールドに格納。正常時のstderr（警告等）は`metadata.prolog_warnings`に含める

**タイムアウト時のプロセスクリーンアップ:**
```python
try:
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(input=prolog_input.encode('utf-8')),
        timeout=timeout_seconds
    )
except asyncio.TimeoutError:
    proc.kill()               # SIGKILLでプロセスを強制終了
    await proc.wait()         # 終了を待つ（ゾンビプロセス防止）
    return ExecutionResult(
        success=False,
        output="",
        error=f"Prolog execution timed out after {timeout_seconds}s",
        metadata={"error_code": "EXEC_002"}
    )
```

**stdout/stderrのデコード:** `proc.communicate()`が返すbytesは`stdout.decode('utf-8')`でデコードする。UTF-8ヘッダーにより出力は常にUTF-8でエンコードされるため、デコード方式は固定。

**サブプロセス呼び出しの規則（コード品質）:**
- `shell=True`を使わない → バグ防止（引数のエスケープ漏れを避ける）
- Prologコードはstdin経由で渡す（コマンドライン引数に入れない。引数長制限とエスケープ問題を回避）
- リクエストごとに独立プロセス → 状態の混在防止

**並行性モデル:**
- 各リクエストに独立したSWI-Prologプロセスを起動する
- プロセス間で状態を共有しない
- これによりassert/retractのレース条件を根本的に排除
- MCPのstdio通信は単一クライアントのため、同時実行数は実質的に制限される。将来HTTP等で多重化する場合は`asyncio.Semaphore`でプロセス数を制御する

### 4.5 暴走防止

暴走防止は以下の3つで実現する:

**1. タイムアウト（executor.py内）**
- `asyncio.wait_for`でサブプロセス全体にタイムアウトを強制
- タイムアウト時は`proc.kill()`でプロセスを確実に停止

**2. 結果数制限（クエリラッパー内）**
- `nb_setval`/`nb_getval`によるカウンタ制御（`findall`は使わない）
- `findall`は全解をメモリに集めるため、大量解でOOM（Out of Memory）になるリスクがある
- カウンタ方式は1件ずつ出力し、上限に達したら即座に停止する

**3. LLM自己修正回数の上限**
- `max_corrections`パラメータで制御（デフォルト3、最大10）

これにより、SWI-Prologの全機能（ライブラリ、モジュール、ファイルI/O、CLP等）を制限なく利用できる。

### 4.6 LLMクライアント (llm_client.py)

```python
class LLMClient:
    """LLM API呼び出しの薄い抽象化"""

    def __init__(self, provider: str, api_key: str, model: str, timeout_seconds: float = 30.0):
        """
        provider: "openai" | "anthropic"
        timeout_seconds: complete()のデフォルトタイムアウト（呼び出し時にオーバーライド可能）
        """

    async def complete(
        self, system_prompt: str, user_prompt: str,
        temperature: float = 0.0,
        timeout_seconds: float | None = None
    ) -> str:
        """テキスト補完を実行。タイムアウト時はLLMErrorを送出。戻り値はLLMの応答テキスト。
        timeout_seconds: 省略時はコンストラクタで設定した値（self.timeout_seconds）を使用。"""
```

**設計判断: LiteLLM→直接API**

v1ではLiteLLM（100+プロバイダー対応）だったが、以下の理由で変更:
- MVPユーザーの95%はOpenAIかAnthropicを使う
- LiteLLMは巨大な依存ツリーを持ち込む
- 2プロバイダーの直接実装は100行未満
- 将来LiteLLMに切り替える場合もこの薄い抽象化層の差し替えで済む

**APIキーの取り扱い:**
- 環境変数から読み取り（Pydantic Settingsの標準的アプローチ）
- ログ出力時のAPIキー自動マスキング（logger.pyで実装）

**プロバイダーSDKの遅延import:**
- `openai`/`anthropic`パッケージはoptional extrasとしてインストール（§6参照）
- `LLMClient.__init__`で使用するプロバイダーのSDKをimportし、未インストールの場合は明確なエラーメッセージを出す

### 4.7 設定 (config.py)

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROLOG_REASONER_",
        env_file=".env",
    )

    # LLM（ライブラリ利用時のみ必要。MCPサーバーは使わない）
    llm_provider: str = "openai"           # "openai" | "anthropic"
    llm_api_key: str = ""                  # v13以降オプション。空文字の場合LLMClientは使えない
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 30.0     # LLM API呼び出しのタイムアウト

    # Prolog
    swipl_path: str = "swipl"             # SWI-Prologの実行パス
    execution_timeout_seconds: float = 10.0

    # ログ
    log_level: str = "INFO"
```

**SWI-Prolog未インストール時の挙動:**
```python
def validate_swipl(self) -> None:
    """起動時にSWI-Prologの存在と正常動作を確認"""
    try:
        result = subprocess.run(
            [self.swipl_path, '--version'],
            capture_output=True, timeout=5
        )
        if result.returncode != 0:
            raise ConfigurationError(
                f"SWI-Prolog returned exit code {result.returncode}.\n"
                f"stderr: {result.stderr.decode(errors='replace')}\n"
                f"Path: {self.swipl_path}",
                error_code="CONFIG_001"
            )
    except (FileNotFoundError, PermissionError):
        raise ConfigurationError(
            "SWI-Prolog not found. Install from: https://www.swi-prolog.org/download/stable\n"
            f"Searched path: {self.swipl_path}\n"
            "Or set PROLOG_REASONER_SWIPL_PATH to the correct location.",
            error_code="CONFIG_001"
        )
    except subprocess.TimeoutExpired:
        raise ConfigurationError(
            f"SWI-Prolog did not respond within 5 seconds.\n"
            f"Path: {self.swipl_path}",
            error_code="CONFIG_001"
        )
```

### 4.8 エラーハンドリング (errors.py)

```python
class PrologReasonerError(Exception):
    """基底例外。全てのエラーはこれを継承"""
    def __init__(self, message: str, error_code: str, retryable: bool = False):
        self.error_code = error_code
        self.retryable = retryable
        super().__init__(message)

class TranslationError(PrologReasonerError):
    """NL→Prolog変換失敗（内部使用。公開APIではTranslationResult(success=False)として返される）"""
    # error_code: "TRANSLATION_001" (空応答)
    # ※ TRANSLATION_002(修正上限超過)はTranslationResult.metadata["error_code"]として直接返される

class ExecutionError(PrologReasonerError):
    """Prolog実行時エラー（内部使用。公開APIではExecutionResult(success=False)として返される）"""
    # error_code: "EXEC_001" (構文エラー), "EXEC_002" (タイムアウト), "EXEC_003" (プロセス異常終了)
    # ※ これらはexecutor内部でcatchされ、ExecutionResult.metadata["error_code"]として返される

class BackendError(PrologReasonerError):
    """SWI-Prologが利用不可"""
    # error_code: "BACKEND_001"

class LLMError(PrologReasonerError):
    """LLM API呼び出し失敗"""
    # error_code: "LLM_001" (API通信エラー), "LLM_002" (認証エラー),
    #             "LLM_003" (レート制限)
    # retryable: True (LLM_001, LLM_003の場合)

class ConfigurationError(PrologReasonerError):
    """設定不正"""
    # error_code: "CONFIG_001"
```

**エラーの使い分けルール（公開API境界）:**

| レイヤー | 正常 | 業務エラー（予期される失敗） | インフラエラー（予期しない障害） |
|----------|------|---------------------------|-------------------------------|
| **executor** | `ExecutionResult(success=True)` | `ExecutionResult(success=False)` — タイムアウト、構文エラー、異常終了 | `BackendError` raise — SWI-Prolog起動不可 |
| **translator** | `TranslationResult(success=True)` | `TranslationResult(success=False)` — 最大修正回数超過、翻訳不可能な入力 | `LLMError` raise — API通信障害、認証エラー |
| **server.py** | resultをdictに変換 | resultをdictに変換 | 例外を捕捉→MCPエラーレスポンス |

- **result.success=True, output="false\n"**: Prolog推論が解なし。エラーではなく正常な推論結果
- **業務エラーの詳細**: `result.error`にメッセージ、`result.metadata["error_code"]`（例: `"EXEC_002"`）でプログラム的な判別が可能
- **例外はインフラ障害のみ**: executor/translatorは業務エラーを例外として投げない。常にresultオブジェクトで返す
- **APIキーのマスキング**: server.pyが例外をMCPエラーに変換する際、logger.pyで自動マスキング

### 4.9 ログ (logger.py)

```python
import sys
import logging

def setup_logging(level: str = "INFO") -> None:
    """stderr専用の構造化ログ設定。複数回呼ばれても安全（ハンドラ重複防止）。"""
    if logging.root.handlers:
        logging.root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)  # 絶対にstdoutは使わない
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    handler.setFormatter(formatter)
    logging.root.addHandler(handler)
    logging.root.setLevel(level)

class SecureLogger:
    """APIキー等の機密情報を自動マスキングするロガーラッパー"""
    REDACT_PATTERNS = [
        re.compile(r'sk-[a-zA-Z0-9_-]{20,}'),     # OpenAI / Anthropic共通（sk-proj-..., sk-ant-... 両方にマッチ）
    ]

    def info(self, msg: str, **kwargs): ...
    def error(self, msg: str, **kwargs): ...
```

---

## 5. セキュリティ方針

### 5.1 脅威モデル: 攻撃者は存在しない

このツールはローカル環境で動作するライブラリ/MCPサーバーである。

```
利用者   = 開発者本人（信頼できる）
実行環境 = ユーザー自身のPC
通信     = MCP stdio（ネットワーク非公開）
外部接続 = LLM APIへのHTTPS（OpenAI/Anthropic）のみ
```

外部からのアクセス経路が存在しないため、従来型の攻撃（インジェクション、パストラバーサル等）は脅威にならない。ユーザーは自分のPCで自分の権限でPrologを実行するだけであり、`shell/1`等の述語をブロックする理由がない。

**したがって、Prologの機能制限（サンドボックス、ホワイトリスト）は設けない。**

### 5.2 実際のリスクと対策

攻撃者がいない前提で、それでも対処すべきリスク:

| リスク | 原因 | 影響 | 対策 |
|--------|------|------|------|
| **意図しない暴走** | LLMが無限ループや指数爆発するPrologを生成 | CPU/メモリ占有、PC応答なし | タイムアウト + 結果数制限 |
| **推論結果の誤信用** | NL→Prolog変換の意味的誤り | 間違った結論を「検証済み」と思い込む | **Prolog中間表現の可視化**（核心機能） |
| **APIキーのログ出力** | デバッグログにキーが含まれる | 意図しない露出 | ログでのAPIキーマスキング |
| **LLM API課金の暴走** | 自己修正ループの過剰実行 | 予想外の課金 | max_corrections上限（デフォルト3、最大10） |

### 5.3 暴走防止（唯一のセキュリティ機構）

| 保護 | 制限値 | 変更可能 | 設定方法 |
|------|--------|---------|---------|
| Prolog実行タイムアウト | 10秒 | Yes | `execution_timeout_seconds` |
| 結果数上限 | 100 | Yes | `max_results`パラメータ |
| LLM自己修正回数 | 3（デフォルト）、10（上限） | Yes | `max_corrections`パラメータ |

全ての制限値はユーザーが変更可能。ユーザーの意図的な操作を制限しない。

### 5.4 この方針の根拠

**サンドボックスを設けない理由:**
- ユーザーは自分のPC上で任意のコードを実行できる立場にある。ライブラリが制限をかけても意味がない
- ホワイトリストは不完全な正規表現パースに依存し、偽陽性（正当なコードのブロック）を生む
- SWI-Prologのライブラリ（CLP(FD)、DCG、ファイル処理等）を自由に使えることが推論能力の幅に直結する
- 過剰なセキュリティは開発者体験を損ない、ツールの採用を妨げる

**暴走防止のみを設ける理由:**
- LLMが生成するコードは予測不能で、意図せず無限ループになることが現実的に起こり得る
- タイムアウトはユーザー体験の問題（PCがフリーズしないようにする）であり、セキュリティではない
- この制限はユーザーの機能を奪わない（必要なら延長できる）

**将来、Webサービスに組み込まれた場合:**
- サービス化する側がDocker/コンテナ分離等を追加すればよい
- ライブラリ側がその責務を負う必要はない

### 5.5 サブプロセス呼び出しの実装規則

セキュリティ目的ではなく、**コード品質として**以下を守る:

- `shell=True`を使わない → バグ防止（引数のエスケープ漏れを避ける）
- Prologコードはstdin経由 → 引数長制限の回避
- リクエストごとに独立プロセス → 状態の混在防止（レース条件ではなくバグ防止）

---

## 6. 依存パッケージ

### 必須依存

| パッケージ | 用途 | バージョン |
|-----------|------|-----------|
| `fastmcp` | MCPサーバー | `^3.0` |
| `pydantic` | データバリデーション | `^2.0` |
| `pydantic-settings` | 設定管理 | `^2.0` |

### Optional extras（LLMプロバイダー）

```toml
[project.optional-dependencies]
openai = ["openai>=1.0"]
anthropic = ["anthropic>=0.40"]
all = ["openai>=1.0", "anthropic>=0.40"]
```

```bash
pip install prolog-reasoner[openai]      # OpenAIのみ
pip install prolog-reasoner[anthropic]   # Anthropicのみ
pip install prolog-reasoner[all]         # 両方
```

**根拠:** 使わないプロバイダーのSDKをインストールする必要がない。`llm_client.py`で遅延importし、未インストールの場合は明確なエラーメッセージを出す。

### ランタイム要件

| 要件 | バージョン | 備考 |
|------|-----------|------|
| **Python** | 3.10以上 | `str \| None` 構文（PEP 604）、asyncioの安定性 |
| **SWI-Prolog** | 9.0以上 | ユーザーが別途インストール（pip依存ではない） |

- SWI-Prolog: Docker使用時はDockerfile内でバージョン固定（例: 9.2.7）

---

## 7. テスト戦略

### 7.1 ユニットテスト（LLM不要）

| テスト | 対象 | 方法 |
|--------|------|------|
| `test_models.py` | バリデーション | 不正入力の拒否確認 |
| `test_executor.py` | Prolog実行 + 暴走防止 | 固定コードの実行結果確認、タイムアウト確認、プロセスkill確認 |
| `test_translator.py` | 翻訳 | モックLLM（録画再生）で確認 |

### 7.2 統合テスト（LLM API必要）

| テスト | シナリオ |
|--------|---------|
| ソクラテス問題 | 演繹推論の基本 |
| 家族関係推論 | 推移的関係の多段推論 |
| 制約充足 | スケジューリング問題 |
| 自己修正 | 意図的に難しい入力で修正ループ確認 |
| MCPサーバー | インメモリクライアントでツール呼び出し |

### 7.3 LLM非決定性への対策

LLMの応答は非決定的なため、テストの再現性が問題になる。

**対策:** `tests/fixtures/llm_responses/`にLLM応答を録画保存。通常のテストでは録画を再生。定期的に（月1回）実際のLLM APIで録画を更新し、モデルのドリフトを検出。

```python
# conftest.py
@pytest.fixture
def mock_llm(request):
    """録画済みLLM応答を再生するフィクスチャ"""
    recording_path = f"tests/fixtures/llm_responses/{request.node.name}.json"
    if os.path.exists(recording_path):
        return RecordedLLMClient(recording_path)
    else:
        # 録画がなければ実際のAPIを呼び、録画を保存
        return RecordingLLMClient(real_client, recording_path)
```

---

## 8. 配布戦略

### pyproject.toml エントリポイント

```toml
[project.scripts]
prolog-reasoner = "prolog_reasoner.server:main"
```

`main()`はFastMCPサーバーの起動関数。これにより`pip install`後に`prolog-reasoner`コマンドが使える。

### PyPI
```bash
pip install prolog-reasoner[openai]   # ライブラリとして（OpenAI使用）
prolog-reasoner                       # MCPサーバーとして起動
uvx prolog-reasoner                   # インストール不要で即実行
```

### Docker（SWI-Prolog同梱）

```dockerfile
FROM python:3.12-slim

# SWI-Prologインストール（バージョン固定）
RUN apt-get update && \
    apt-get install -y swi-prolog=9.2.* && \
    rm -rf /var/lib/apt/lists/*

# 非rootユーザー
RUN useradd -m -u 1000 reasoner
USER reasoner

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[all]"

ENTRYPOINT ["prolog-reasoner"]
```

### MCP設定例（v13）

MCPサーバーはLLM APIを呼ばないため**APIキーの設定は不要**:

```json
{
  "mcpServers": {
    "prolog-reasoner": {
      "command": "uvx",
      "args": ["prolog-reasoner"]
    }
  }
}
```

Docker経由（SWI-Prolog未インストール環境向け）:

```json
{
  "mcpServers": {
    "prolog-reasoner": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "prolog-reasoner"]
    }
  }
}
```

---

## 9. 実装順序

### フェーズ1: 動くプロトタイプ
1. `pyproject.toml` + プロジェクト構造 + `.gitignore`
2. `models.py` -- データモデル（Pydanticバリデーション含む）
3. `config.py` -- 設定管理 + SWI-Prolog存在確認
4. `errors.py` -- 例外階層
5. `logger.py` -- stderr専用ログ
6. `executor.py` -- SWI-Prologサブプロセス実行（クエリラッパー + タイムアウト + 結果数制限）
7. `llm_client.py` -- OpenAI/Anthropic直接呼び出し（遅延import）
8. `translator.py` -- 1フェーズ翻訳 + 自己修正ループ
9. `reasoner.py` -- パブリックAPI
10. `server.py` -- MCPサーバー（2ツール）
11. 基本テスト（実行 + 翻訳 + 暴走防止）

### フェーズ2: 品質強化
- 統合テスト（LLM API使用）
- LLM応答録画の仕組み
- エラーハンドリング強化
- Unicode/多言語対応テスト

### フェーズ3: 配布準備
- Docker対応
- README
- PyPI公開
- サンプルコード

---

## 10. 検証方法

### 基本動作確認（MCP）

```bash
# 1. MCPサーバー起動（エラーなし、APIキー不要）
prolog-reasoner

# 2. execute_prolog（接続LLMがPrologを書いてツール呼び出し）
# 入力: prolog_code="human(socrates). mortal(X) :- human(X)." + query="mortal(socrates)"
# 期待: output="mortal(socrates)\n"

# 3. execute_prolog（変数ありクエリ）
# 入力: 同じprolog_code + query="mortal(X)"
# 期待: output="mortal(socrates)\n"

# 4. ルール修正の効果確認
# prolog_codeを意図的に変更 → execute_prolog → outputが変わることを確認
```

### ライブラリパイプライン動作確認（LLM APIキー必要）

```python
# 1. translate: NL→Prolog（内部でLLM API呼び出し + 自己修正）
# 入力: "ソクラテスは人間。人間は死すべきもの。ソクラテスは死すべきものか？"
# 期待: success=true, prolog_codeにhuman(socrates)とmortal/1のルール

# 2. execute: 1で得たprolog_code + suggested_query
# 期待: output="mortal(socrates)\n"
```

### 暴走防止の検証

```bash
# タイムアウト: 無限ループの停止
execute_prolog(prolog_code="loop :- loop. :- loop.", query="true")
# → error: "Prolog execution timed out after 10.0s"

# リソース制限: 大量結果の切り捨て
execute_prolog(prolog_code="num(X) :- between(1,999999,X).", query="num(X)", max_results=10)
# → output: 10行の結果 + "__TRUNCATED__\n", metadata.truncated=true
```

---

## 11. 将来の拡張

MVP後に検討する機能。現時点では実装しない。

| 拡張 | 概要 | 追加条件 |
|------|------|---------|
| **構造化出力パーサー** | `output`テキストをパースし`results: list[dict[str, str]]`を生成するユーティリティ | Pythonライブラリとしてのプログラム的利用の需要が確認された場合 |
| **Janusバックエンド** | SWI-Prolog公式Pythonブリッジ。subprocess不要で~1μs | パフォーマンス要件がある場合。抽象バックエンド層を追加 |
| **LiteLLM統合** | 100+プロバイダー対応 | 3プロバイダー以上のサポート要望がある場合 |
| **追加LLMプロバイダー** | Google Gemini、Ollama等 | ユーザーからの要望に応じて |
| **MCPリソース** | 生成済みPrologコードの一覧や再利用 | MCP Resources仕様の成熟を待って |
| **Webサービス化** | Docker + API Gateway + 認証 | フェーズCのドメイン特化時に検討 |

---

## 付録A: 変更の全一覧

### v1→v2 変更

| 項目 | v1 | v2 | 根拠 |
|------|----|----|------|
| MCPツール数 | 3 (reason, execute, generate) | 2 (translate, execute) | Unix哲学。組み合わせ可能な小さなツール |
| アーキテクチャ | 4層 + 抽象バックエンド | 3層 + subprocess直接 | YAGNI。MVPに不要な抽象化を排除 |
| オーケストレーション | engine.py (命令的) | pipeline.py (パイプライン) | 拡張性。ステップの追加・除去が容易 |
| LLM連携 | LiteLLM (100+プロバイダー) | 直接API (OpenAI/Anthropic) | 依存削減。95%のユースケースをカバー |
| 翻訳方式 | 3フェーズ (Logic-LM) | 1フェーズ + 自己修正 | MVP向け簡素化。自己修正が構文エラーをカバー |
| `context`型 | str (ツール) vs dict (モデル) | str統一 | 型不一致の解消 |
| `prolog_source` | コードとパス兼用 | `prolog_code`のみ（コード限定） | パストラバーサル防止 |
| `query` (execute) | Optional (自動推定) | 必須 | 曖昧さ排除。自動推定アルゴリズム未定義だった |
| `explanation` | 戻り値に含む | 削除 | LLM自身が結果を解釈すればよい。追加LLM呼び出しは過剰 |
| `results`形式 | 未定義 | 明確に定義（変数バインディングdict） | 曖昧さ排除 |
| 並行性 | 未定義 | リクエストごと独立プロセス | レース条件の根本排除 |

### v2→v3 変更

| 項目 | v2 | v3 | 根拠 |
|------|----|----|------|
| セキュリティ方針 | ホワイトリスト型サンドボックス | 暴走防止のみ（§5参照） | ローカルツールに攻撃者は存在しない |
| `sandbox.py` | PrologSandbox（正規表現ホワイトリスト） | 廃止 | サンドボックスは不要 |
| `SandboxViolationError` | 例外階層に存在 | 廃止 | 対応する機構がないため |
| プロンプトセキュリティ制約 | SYSTEM_PROMPTに制限事項を記載 | 制約なし | LLMの生成能力を制限しない |

### v3→v4 変更

| 項目 | v3 | v4 | 根拠 |
|------|----|----|------|
| 実行結果形式 | `results: list[dict[str, str]]` | `output: str`（生テキスト） | パーサーの複雑さとバグリスクを排除。LLMが解釈できれば十分。構造化は将来追加可能 |
| クエリ実行方式 | 未定義 | クエリラッパー自動生成（§4.4） | 結果出力フォーマットとmax_results制御の仕組みを明確化 |
| クエリラッパー実装 | forall/2 + cut | fail-driven loop + nb_setval カウンタ | forall/2は内部で二重否定を使うためcutが効かない。fail-loopならcutがトップレベルで動作 |
| 結果数制限 | `findall + length`（曖昧） | `nb_setval`カウンタ方式 | `findall`はOOMリスク。カウンタ方式は1件ずつ処理し上限で即停止 |
| タイムアウト処理 | `asyncio.wait_for`のみ | + `proc.kill()` + `proc.wait()` | タイムアウト後のゾンビプロセス防止 |
| 自己修正の所在 | アーキテクチャ図でExecutor内 | Translator内（図と実装を統一） | 修正は翻訳品質向上であり、Translatorの責務 |
| ツール説明 | 「実行はしない」 | 「クエリの推論実行は行わない」 | 構文検証でSWI-Prologを使う事実を正確に記述 |
| `pipeline.py` | パイプラインパターン | `reasoner.py`（ファサード） | 実態はファサード。名称を実態に合わせる |
| `logging.py` | 標準ライブラリと同名 | `logger.py` | import時の名前衝突を回避 |
| LLM SDK依存 | `Yes (択一)` | optional extras | 使わないプロバイダーをインストール不要にする |
| `error_code`/`retryable` | 付録で「簡素化」と記載 | 維持（付録の記述を修正） | プログラム的エラーハンドリングに有用 |
| ファイルI/O | 「MCP層で別途管理」（未定義） | MCPクライアントの責務と明記 | prolog-reasonerはコード文字列の入出力のみ担当 |
| 同時実行 | 未言及 | stdio単一クライアント + 将来のSemaphore設計ノート | 現実的な制約と将来の拡張方針を明記 |
| `validate_syntax` | 「構文チェックのみ。実行はしない」 | consult方式（ディレクティブ実行あり）と明記 | ローカルツールのため副作用は問題ない |
| SWI-Prolog起動引数 | 未定義 | `-f none -q` + `LANG=C.UTF-8` | `-f none`で初期化ファイル無効化、`-q`でバナー抑制、UTF-8で多言語対応 |
| 成功/失敗判定 | 未定義 | exit code + stderr内容による4パターン判定表 | 曖昧だった判定基準を網羅的に定義 |
| stderr処理 | 未定義 | WARNING以上をログ出力、Prolog errorでsuccess=False | stdoutとstderrの役割を明確に分離 |
| `suggested_query` | 未定義 | `% Query:` コメントからの抽出 + 空文字フォールバック | LLMがコメントを含めない場合の安全なデフォルト |
| 「解なし」の扱い | success=Falseに含めていた | success=True, output="false\n" | 解なしはエラーではなく正常な推論結果 |

### v4→v5 変更

| 項目 | v4 | v5 | 根拠 |
|------|----|----|------|
| エラーハンドリング境界 | タイムアウトで`raise ExecutionError` | `return ExecutionResult(success=False)` | 例外はインフラ障害のみ。業務エラーはresultで返す。§4.8のルールと§4.4のコードが矛盾していた |
| UTF-8対応 | `LANG=C.UTF-8` 環境変数のみ | + `set_stream(user_input/output, encoding(utf8))` | WindowsではLANG環境変数が効かない。Prolog側でストリームエンコーディングを明示設定 |
| `validate_syntax`詳細 | 「consultさせて確認」のみ | 末尾`:- halt(0).`、タイムアウト適用、`ERROR:`判定 | プロセス終了方法・タイムアウト・エラー判定基準が未定義だった |
| コンポーネント初期化 | 未定義 | `create_reasoner()`でDI配線を明示 | server.pyからPrologReasonerへの接続方法が不明だった |
| LLM呼び出しタイムアウト | 未定義 | `llm_timeout_seconds: float = 30.0` | ネットワーク障害時にtranslate呼び出しが無期限ブロックする問題 |
| Python バージョン | 未記載 | 3.10以上 | `str \| None` 構文等の言語機能を使用 |
| `pyproject.toml`エントリポイント | 未定義 | `[project.scripts]`を明記 | `prolog-reasoner`コマンドの起動方法が不明だった |
| `nb_setval`変数名 | `result_count` | `'__pr_count'` | ユーザーPrologコードとの変数名衝突を回避 |
| `metadata.truncated`判定 | 未定義 | 出力末尾の`__TRUNCATED__\n`を検出 | truncatedフラグの設定方法が不明だった |

### v5→v6 変更

| 項目 | v5 | v6 | 根拠 |
|------|----|----|------|
| Prolog入力構造 | ユーザーコード末尾にラッパー追加（set_streamはラッパー内） | 3層構造: UTF-8ヘッダー(prepend) + ユーザーコード + クエリラッパー(append) | set_streamがユーザーコードの後に実行されると非ASCIIアトムのパースに間に合わない |
| `translate_with_correction`戻り値 | `tuple[str, str]` | `TranslationResult` | tupleでは失敗（修正上限超過）を表現できない。§4.8のエラールール「業務エラーはresultで返す」と矛盾していた |
| `LLMClient.__init__` | `(provider, api_key, model)` | + `timeout_seconds: float = 30.0` | create_reasoner()がtimeout_secondsを渡すがコンストラクタに受け口がなかった |
| `TranslationError`/`ExecutionError`コメント | error_code: "EXEC_001"等 | metadata["error_code"]: "EXEC_001"等 + 「内部使用。公開APIではresultとして返される」 | v5で例外→result方針に変更したが、クラスコメントが旧設計のままだった |
| 文字列埋め込み方式 | 「Pythonの文字列フォーマット」 | 「文字列置換（.replace()）」 | .format()はPrologの中括弧{}（DCG記法等）と衝突する |

### v6→v7 変更

| 項目 | v6 | v7 | 根拠 |
|------|----|----|------|
| `LLMClient.complete()`タイムアウト | `timeout_seconds: float = 30.0` | `timeout_seconds: float \| None = None`（省略時は`self.timeout_seconds`） | パラメータデフォルト30.0がコンストラクタ経由の設定値を上書きし、`llm_timeout_seconds`環境変数が無効になっていた |
| `Settings.max_results_default` | `max_results_default: int = 100` | 削除 | ExecutionRequestモデル（`default=100`）とツール定義（`max_results=100`）と三重定義。Settings値は誰も参照しないdead config |
| `Settings.max_results_limit` | `max_results_limit: int = 10000` | 削除 | ExecutionRequestモデル（`le=10000`）と二重定義。バリデーションはモデルに一本化 |
| `translate()`エラー契約 | 戻り値docstringのみ | + `Raises: TranslationError` | LLM空応答（TRANSLATION_001）時のエラー伝播パスが未記載。translate_with_correction()がcatchしてTranslationResult(success=False)に変換する流れが不明だった |
| `server.py` main() | 未定義（pyproject.tomlのみ参照） | 関数定義を追記: `mcp.run()` | エントリポイントの実装が不明だった |
| stdout/stderrデコード | 未記載 | `stdout.decode('utf-8')` | UTF-8ヘッダーとの対応。bytes→strの変換方式が未定義だった |

### v7→v8 変更

| 項目 | v7 | v8 | 根拠 |
|------|----|----|------|
| `executor.execute()`タイムアウト | `timeout_seconds: float = 10.0` | `timeout_seconds: float \| None = None`（省略時は`Settings.execution_timeout_seconds`） | v7でLLMClient.complete()を修正したが、executor.execute()に同じパターン（パラメータデフォルトが設定値を上書き）が残っていた |
| 3層Prolog入力の結合 | 各層の結合方法が未記載 | `HEADER + "\n" + prolog_code + "\n" + WRAPPER` | ユーザーコード末尾に改行がない場合、Prologの項終端`.`の後に空白がなくなり次のディレクティブと連結して構文エラーになる |
| validate_syntax結合 | 結合方法が未記載 | `\n`で結合を明記 | execute()と同じ改行セパレータ問題がvalidate_syntax()にも存在 |
| FastMCPインスタンス | 未定義（`@mcp.tool()`のみ使用） | `mcp = FastMCP("prolog-reasoner")` を明記 | mcpオブジェクトの生成元が不明だった |
| `TranslationError`コメント | `metadata["error_code"]: "TRANSLATION_001", "TRANSLATION_002"` | `error_code: "TRANSLATION_001"`のみ。TRANSLATION_002はResultのmetadataで直接返される旨を注記 | 例外クラスの属性は`self.error_code`であり`metadata`ではない。TRANSLATION_002はraiseされずresultで返される |
| `ExecutionError`コメント | `metadata["error_code"]: "EXEC_001"〜"EXEC_003"` | `error_code: "EXEC_001"〜"EXEC_003"` + executor内部でcatchされresultのmetadataとして返される旨を注記 | TranslationErrorと同じ不正確さの修正 |

### v8→v9 変更

| 項目 | v8 | v9 | 根拠 |
|------|----|----|------|
| `reasoner.translate()`docstring | 「LLMでNL→Prolog変換 + 構文検証」 | `self.translator.translate_with_correction()`に委譲、executor引数に`self.executor`を渡すことを明記 | 実装者がreasonerとtranslatorの接続方法を推測しなければならなかった |
| `reasoner.execute()`docstring | 「Prolog実行 + 出力テキスト」 | `self.executor.execute()`に委譲を明記 | translate()と同様、委譲先の明示 |
| `validate_swipl()`例外処理 | `FileNotFoundError`のみcatch | + `PermissionError`, `subprocess.TimeoutExpired`, `returncode != 0` チェック | パスが存在するが壊れている場合、権限不足、hangする場合にConfigurationError以外の例外が伝播していた |
| `setup_logging()`重複防止 | ガードなし（呼び出しごとにハンドラ追加） | `logging.root.handlers`チェックで重複防止 | テスト等で複数回呼ばれるとログが重複出力される |
| `SecureLogger.REDACT_PATTERNS` | `sk-[a-zA-Z0-9]{20,}` | `sk-[a-zA-Z0-9_-]{20,}` | OpenAI `sk-proj-...` 形式のキーにハイフン`-`が含まれるためマッチしなかった |

### v9→v10 変更

| 項目 | v9 | v10 | 根拠 |
|------|----|----|------|
| `SecureLogger.REDACT_PATTERNS` | 2パターン（`sk-`汎用 + `sk-ant-`Anthropic専用） | 1パターン（`sk-`汎用のみ） | パターン1が`sk-ant-`にも先にマッチするため、パターン2は到達不能だった |
| `metadata.result_count` | 戻り値例に含まれるが算出方法が未定義 | outputの非空行数から算出（`__TRUNCATED__`行は除外）と明記 | 実装者がresult_countの算出方法を推測しなければならなかった |

### v10→v11 変更

| 項目 | v10 | v11 | 根拠 |
|------|----|----|------|
| `validate_swipl()` ConfigurationError | `error_code`引数なし（3箇所） | 全箇所に`error_code="CONFIG_001"`を追加 | PrologReasonerError.__init__の必須引数`error_code`が欠落しており、実装通りに書くとTypeErrorで即死する |
| `metadata.result_count`定義 | 「非空行数（`__TRUNCATED__`行は除外）」 | 「非空行数（`__TRUNCATED__`行および`false`行は除外。解なし時は0）」 | output="false\n"時にresult_count=1となり、「解の数」を暗示する名前と矛盾していた |

### v11→v12 変更

| 項目 | v11 | v12 | 根拠 |
|------|----|----|------|
| `suggested_query`抽出 | `% Query: <query>`コメントからパース | + 末尾ピリオドと前後空白を除去 | LLMが`% Query: mortal(socrates).`のようにピリオド付きで出力した場合、ラッパーの`<QUERY>`置換で構文エラーになる |

### v12→v13 変更

v13は**アーキテクチャ上の責務再分離**を行うメジャー変更。MCPサーバーとライブラリが共有していたLLM翻訳機能を、ライブラリ側のみに限定した。

| 項目 | v12 | v13 | 根拠 |
|------|----|----|------|
| MCPツール数 | 2 (`translate_to_prolog` + `execute_prolog`) | 1 (`execute_prolog`) | MCPに接続するLLM（Claude等）は自身の推論でProlog生成できる。サーバー側で別LLM APIを呼ぶのは冗長。一般的なMCPサーバーはAPIキーを要求しない |
| `Settings.llm_api_key` | `str`（必須、デフォルトなし） | `str = ""`（空文字デフォルト） | MCPサーバー利用時はLLM APIを使わないため、APIキー未設定でも起動できる必要がある |
| `server.py`の初期化 | モジュールロード時に`create_reasoner()`で`PrologReasoner`全体を生成 | `_init()`で`PrologExecutor`のみを遅延生成 | Translator/LLMClientはMCPで使わないので持たない。Import-only シナリオでの即死を防ぐため遅延化 |
| `create_reasoner()`ヘルパー | 公開API（server.pyとライブラリ両方で使用） | 削除 | MCPはExecutorのみ、ライブラリは用途ごとに自前DI。共通ヘルパーの役割が消失 |
| MCP設定の`env.PROLOG_REASONER_LLM_API_KEY` | 必須 | 不要 | MCPサーバーはLLM APIを呼ばない |
| アーキテクチャ図 | 単一スタック（MCP→Reasoner→Translator+Executor） | 分岐スタック（MCP→Executor、Library→Reasoner→Translator+Executor） | 責務分離を図にも反映 |
| `translate_to_prolog`ツールのテスト | `tests/integration/test_mcp_server.py`に存在 | 削除 + `test_no_translate_tool`を追加（ツールが公開されていないことを保証） | 誤って再公開することを防ぐリグレッションテスト |
| 翻訳機能自体 | MCPとライブラリで共有 | ライブラリのみ（`PrologReasoner.translate()`として残存） | 機能自体は消していない。配布面を分けただけ |
