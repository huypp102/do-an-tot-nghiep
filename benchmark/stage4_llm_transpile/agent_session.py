"""Phiên hội thoại RIÊNG cho từng agent -- tách vai trò, chống lẫn ngữ cảnh.

VẤN ĐỀ cần tránh (POLO và RepoTransAgent đều nhấn mạnh): nếu Generator Agent
và Decision Agent dùng CHUNG một dòng hội thoại, Decision Agent sẽ nhìn thấy
toàn bộ quá trình "vật lộn" sinh code của Generator (kể cả các bản sai đã bị
loại), còn Generator thì thấy luôn các nhận xét đánh giá. Hai vai trò nhiễu
lẫn nhau, và lịch sử phình to vô ích.

CÁCH LÀM Ở ĐÂY:

    ModelBackend  (1 instance, dùng chung)   <- chỉ là ĐƯỜNG DÂY tới model,
         |                                       KHÔNG giữ state hội thoại
         +---- AgentSession("generator")  -> messages RIÊNG + system RIÊNG
         +---- AgentSession("decision")   -> messages RIÊNG + system RIÊNG

Hai agent có thể cùng gọi 1 model local qua cùng base_url, nhưng KHÔNG bao
giờ thấy lịch sử của nhau. Dữ liệu cần trao đổi giữa 2 agent (code Rust, kết
quả đo, chiến lược trước đó) được truyền TƯỜNG MINH qua tham số hàm, chứ
không rò rỉ qua shared conversation history.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("benchmark.stage4_llm_transpile.agent_session")


class AgentSession:
    """Giữ lịch sử hội thoại của ĐÚNG MỘT agent.

    role_name: nhãn để log ("generator" | "decision"), giúp đọc log biết
        lượt gọi thuộc agent nào.
    system_prompt: mô tả VAI TRÒ của agent này, gửi kèm mọi lượt gọi.
    backend: ModelBackend dùng chung với agent khác -- chỉ là kết nối, không
        mang state hội thoại.
    max_history_turns: cắt bớt lượt cũ để prompt không phình vô hạn qua
        nhiều vòng retry (giữ system + các lượt gần nhất).
    """

    def __init__(
        self,
        role_name: str,
        system_prompt: str,
        backend: Any,
        model: str | None = None,
        num_ctx: int | None = None,
        think: bool | None = None,
        max_history_turns: int = 12,
    ) -> None:
        self.role_name = role_name
        self.system_prompt = system_prompt
        self.backend = backend
        # TÊN MODEL RIÊNG của vai trò này. Hai agent dùng chung `backend`
        # (1 kết nối) nhưng mỗi bên gọi model của mình -- vd generator chạy
        # devstral:24b, decision chạy qwen3:8b. None = dùng model mặc định
        # của backend.
        self.model = model
        # Cửa sổ ngữ cảnh riêng cho vai trò này: generator cần lớn hơn vì
        # prompt chứa code + context; decision chỉ cần số liệu đo nên nhỏ hơn,
        # tiết kiệm VRAM cho KV cache.
        self.num_ctx = num_ctx
        # think=False để model reasoning (vd qwen3) không sinh khối <think>.
        self.think = think
        self.max_history_turns = max_history_turns
        self._messages: list[dict[str, str]] = []
        # Số liệu của lượt gọi GẦN NHẤT (PHẦN 2.5). Giữ trên session chứ không
        # đọc trực tiếp từ backend vì hai vai trò dùng chung một backend, giá
        # trị ở đó sẽ bị vai trò kia ghi đè trước khi ta đọc.
        self.last_prompt_tokens: int | None = None
        self.last_truncated: bool = False

    @property
    def messages(self) -> list[dict[str, str]]:
        """Bản SAO lịch sử -- trả bản sao để không ai ngoài session này sửa
        được lịch sử (tránh 1 agent chèn nhầm vào lịch sử agent kia)."""
        return list(self._messages)

    def reset(self) -> None:
        """Xoá lịch sử, bắt đầu hội thoại mới (vd sang hotspot khác) nhưng
        giữ nguyên vai trò (system prompt)."""
        self._messages.clear()
        logger.debug("[%s] đã reset lịch sử hội thoại.", self.role_name)

    def _trim(self) -> None:
        if len(self._messages) > self.max_history_turns:
            dropped = len(self._messages) - self.max_history_turns
            self._messages = self._messages[dropped:]
            logger.debug(
                "[%s] cắt bớt %d lượt cũ (giữ %d lượt gần nhất).",
                self.role_name, dropped, self.max_history_turns,
            )

    def send(
        self,
        content: str,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> str:
        """Gửi 1 lượt người dùng, nhận trả lời, LƯU cả hai vào lịch sử RIÊNG
        của agent này. Raise ModelBackendError nếu gọi model thất bại (lúc
        đó lượt hỏng KHÔNG được ghi vào lịch sử, tránh làm bẩn ngữ cảnh).

        `temperature`/`seed`: dùng cho ablation (PHẦN 2.3). Sau lời gọi,
        `last_prompt_tokens` và `last_truncated` mang số liệu của lượt vừa rồi
        -- tầng trên đọc để gắn CONFOUNDED cho hotspot bị cắt prompt."""
        pending = self._messages + [{"role": "user", "content": content}]
        logger.info(
            "[%s] gửi lượt thứ %d tới model '%s' (%d ký tự).",
            self.role_name, len(pending) // 2 + 1,
            self.model or "(mặc định của backend)", len(content),
        )
        reply = self.backend.chat(
            pending,
            system=self.system_prompt,
            model=self.model,
            num_ctx=self.num_ctx,
            think=self.think,
            temperature=temperature,
            seed=seed,
        )
        # Sao lại số liệu của lời gọi vừa rồi: backend dùng chung cho nhiều
        # session nên giá trị trên backend sẽ bị session khác ghi đè.
        self.last_prompt_tokens = getattr(self.backend, "last_prompt_tokens", None)
        self.last_truncated = bool(getattr(self.backend, "last_truncated", False))

        # Chỉ ghi vào lịch sử KHI gọi thành công.
        self._messages = pending + [{"role": "assistant", "content": reply}]
        self._trim()
        return reply
