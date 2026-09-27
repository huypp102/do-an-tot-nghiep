"""Stage 4 -- Backend gọi LLM (interface chung cho API và local).

Cả 2 backend implement CÙNG 1 interface: `generate(prompt: str) -> str`.
Nơi gọi (generator_agent.py, decision_agent.py) không cần biết đang chạy
Anthropic API hay 1 server local.

  - `ApiModelBackend`   : gọi Anthropic API thật qua package `anthropic`.
                          API key LUÔN đọc từ biến môi trường
                          ANTHROPIC_API_KEY -- KHÔNG bao giờ hard-code, không
                          bao giờ đọc từ config.yaml (config chỉ chứa TÊN
                          model).
  - `LocalModelBackend` : gọi server local tương thích OpenAI-style API qua
                          `POST {base_url}/v1/chat/completions`. Mặc định trỏ
                          tới Ollama (http://localhost:11434), cũng chạy được
                          với vLLM / LM Studio / llama.cpp server vì tất cả
                          đều expose endpoint OpenAI-compatible này. Dùng
                          `urllib` của stdlib, KHÔNG cần cài `requests`.

Mọi lỗi (thiếu API key, chưa cài package, server local không chạy, HTTP lỗi)
đều gom vào `ModelBackendError` với thông điệp rõ ràng. Caller (run_pipeline.py)
bắt lỗi này, log rồi bỏ qua phần LLM -- phần còn lại của pipeline (benchmark
các implementation đã có sẵn trong versions/) vẫn chạy bình thường.

Cấu hình đọc từ mục `llm:` trong config.yaml (KHÔNG phải mục `model_backend:`
cũ -- mục đó là legacy, giữ lại cho tương thích ngược).
"""
from __future__ import annotations

import abc
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("benchmark.stage4_llm_transpile.model_backend")

API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SEC = 180


class ModelBackendError(RuntimeError):
    """Không gọi được LLM (thiếu key, thiếu package, server lỗi, ...)."""


class ModelBackend(abc.ABC):
    """Interface chung cho mọi backend LLM ở Stage 4.

    QUAN TRỌNG -- backend này KHÔNG giữ state hội thoại. Nó chỉ là "đường
    dây kết nối" tới model. Lịch sử hội thoại (`messages`) và vai trò
    (`system`) do CALLER truyền vào từng lời gọi.

    Nhờ vậy Generator Agent và Decision Agent có thể DÙNG CHUNG 1 instance
    backend (1 kết nối tới cùng model local) mà vẫn giữ ngữ cảnh RIÊNG
    BIỆT -- mỗi agent tự quản lý list messages của mình trong
    `agent_session.AgentSession`. Đây là "role separation to avoid context
    entanglement" mà POLO và RepoTransAgent đều làm.
    """

    name: str = "base"

    @abc.abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Gửi cả lịch sử hội thoại `messages` (list {"role", "content"}) kèm
        `system` prompt (vai trò của agent), trả về text trả lời.

        `model` là TÊN MODEL CHO RIÊNG LỜI GỌI NÀY. Nhờ vậy Generator Agent và
        Decision Agent dùng CHUNG 1 instance backend (1 kết nối tới server)
        nhưng vẫn chạy trên 2 MODEL KHÁC NHAU -- vd generator dùng model
        chuyên code (devstral:24b), decision dùng model nhỏ hơn thiên về suy
        luận (qwen3:8b). `None` thì dùng model mặc định của backend.

        Raise `ModelBackendError` nếu không gọi được."""
        raise NotImplementedError

    def generate(self, prompt: str, model: str | None = None) -> str:
        """Lời gọi MỘT LƯỢT, không lịch sử, không vai trò -- tiện cho smoke
        test và cho code cũ. Agent thật nên dùng `AgentSession` để giữ lịch
        sử riêng, đừng dùng hàm này."""
        return self.chat([{"role": "user", "content": prompt}], model=model)


class ApiModelBackend(ModelBackend):
    """Anthropic API. Cần `pip install anthropic` và biến môi trường
    ANTHROPIC_API_KEY."""

    name = "api"

    def __init__(self, model: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        self.model = model
        self.max_tokens = max_tokens

        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise ModelBackendError(
                f"Chưa set biến môi trường {API_KEY_ENV} -- không gọi được "
                "Anthropic API. Cách set (PowerShell):\n"
                f'    $env:{API_KEY_ENV} = "sk-ant-..."\n'
                "Hoặc đặt llm.enabled: false trong config.yaml để chạy "
                "pipeline không cần LLM."
            )

        try:
            import anthropic  # noqa: PLC0415 -- import trễ để không bắt buộc cài khi llm.enabled=false
        except ImportError as exc:
            raise ModelBackendError(
                "Chưa cài package `anthropic` (pip install anthropic) -- "
                "không dùng được llm.backend=api. Đổi sang llm.backend=local "
                "hoặc đặt llm.enabled: false nếu chưa cần LLM."
            ) from exc

        self._client = anthropic.Anthropic(api_key=api_key)
        logger.info("ApiModelBackend sẵn sàng (model=%s).", self.model)

    def chat(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        model_name = model or self.model
        kwargs: dict = {
            "model": model_name,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        # Anthropic API nhận system prompt qua tham số RIÊNG, không nhét vào
        # messages như OpenAI-style.
        if system:
            kwargs["system"] = system
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:  # SDK có nhiều loại lỗi riêng -- gom hết về 1 mối
            raise ModelBackendError(
                f"Gọi Anthropic API thất bại (model={model_name}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        chunks = [
            block.text for block in getattr(resp, "content", [])
            if getattr(block, "type", None) == "text"
        ]
        if not chunks:
            raise ModelBackendError(
                f"Anthropic API trả về response không có nội dung text "
                f"(model={model_name})."
            )
        return "\n".join(chunks)


class LocalModelBackend(ModelBackend):
    """Server local tương thích OpenAI-style API (Ollama/vLLM/LM Studio/...)."""

    name = "local"

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        self.max_tokens = max_tokens
        self.timeout = timeout
        if not self.base_url:
            raise ModelBackendError(
                "llm.local.base_url rỗng -- cần điền endpoint server local "
                "(vd http://localhost:11434) trong config.yaml."
            )
        logger.info(
            "LocalModelBackend sẵn sàng (model=%s, base_url=%s).", self.model, self.base_url
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        model_name = model or self.model
        url = f"{self.base_url}/v1/chat/completions"
        # OpenAI-style: system prompt là MESSAGE ĐẦU TIÊN với role="system"
        # (khác Anthropic vốn nhận qua tham số riêng).
        full_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)
        logger.info("Gọi model local '%s' (%d message).", model_name, len(full_messages))
        payload = json.dumps({
            "model": model_name,
            "messages": full_messages,
            "max_tokens": self.max_tokens,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
            raise ModelBackendError(
                f"Server local trả HTTP {exc.code} tại {url}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelBackendError(
                f"Không kết nối được server local tại {url}: {exc}. Kiểm tra "
                "server đã chạy chưa (vd `ollama serve`) và llm.local.base_url "
                "trong config.yaml."
            ) from exc

        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelBackendError(
                f"Response từ server local không đúng định dạng OpenAI-style "
                f"(url={url}): {body[:300]}"
            ) from exc


GENERATOR_ROLE = "generator"
DECISION_ROLE = "decision"


def resolve_model_for_role(cfg: dict[str, Any], role: str) -> str:
    """Tên model dùng cho VAI TRÒ `role` ("generator" | "decision").

    Đọc `llm.<backend>.<role>_model`. Nếu chưa khai báo thì rơi về
    `llm.<backend>.model` (khoá CŨ, dùng chung 1 model cho cả 2 vai trò) để
    config cũ vẫn chạy được.

    Nhờ tách theo vai trò, Generator Agent có thể chạy model chuyên sinh code
    (vd devstral:24b) còn Decision Agent chạy model nhỏ hơn thiên về suy luận
    (vd qwen3:8b), trên CÙNG 1 server local.
    """
    llm_cfg = cfg.get("llm") or {}
    backend = (llm_cfg.get("backend") or "api").strip().lower()
    section = llm_cfg.get(backend) or {}

    model = section.get(f"{role}_model") or section.get("model")
    if not model:
        raise ModelBackendError(
            f"Thiếu tên model cho vai trò '{role}': cần khai báo "
            f"llm.{backend}.{role}_model (hoặc llm.{backend}.model dùng chung) "
            "trong config.yaml."
        )
    return str(model)


def get_model_backend(cfg: dict[str, Any]) -> ModelBackend:
    """Factory: đọc mục `llm:` trong config.yaml, trả về backend tương ứng.

    Backend chỉ là ĐƯỜNG DÂY kết nối, model mặc định của nó lấy theo vai trò
    generator. Tên model THẬT SỰ dùng cho mỗi lời gọi do AgentSession truyền
    vào (xem `resolve_model_for_role` và `ModelBackend.chat(..., model=...)`),
    nên 2 agent có thể chạy 2 model khác nhau qua cùng instance này.

    Raise `ModelBackendError` nếu cấu hình sai hoặc môi trường chưa sẵn sàng
    (thiếu key/package/server) -- caller bắt và xử lý, KHÔNG để crash pipeline.
    """
    llm_cfg = cfg.get("llm") or {}
    backend = (llm_cfg.get("backend") or "api").strip().lower()
    default_model = resolve_model_for_role(cfg, GENERATOR_ROLE)

    if backend == "api":
        return ApiModelBackend(model=default_model)

    if backend == "local":
        local_cfg = llm_cfg.get("local") or {}
        return LocalModelBackend(
            model=default_model,
            base_url=local_cfg.get("base_url", "http://localhost:11434"),
        )

    raise ModelBackendError(f"llm.backend không hỗ trợ: {backend!r} (api | local)")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(BENCHMARK_ROOT))
    from config_loader import ensure_utf8_stdio, load_config

    ensure_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

    cfg = load_config()
    try:
        be = get_model_backend(cfg)
        print(f"Backend OK: {be.name}")
        print(be.generate("Trả lời đúng 1 từ: ping"))
    except ModelBackendError as exc:
        print(f"LỖI BACKEND (đúng như thiết kế, không crash): {exc}")
        sys.exit(1)
