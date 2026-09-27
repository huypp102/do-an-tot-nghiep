"""Stage 0: xây PCG (Program Call Graph) + PSG (Program Structure Graph) từ
source Python, tính FuncRank (PageRank trên PCG), xuất context JSON cho model
(qua stage4_llm_transpile/model_backend.py) hiểu dependency giữa các hàm/file thay vì chỉ
thấy 1 hàm rời rạc.

Chỉ được dùng khi `target.mode` trong config.yaml là "file" hoặc "repo"
(stage6_benchmark/bench.py import các module trong package này theo kiểu "lazy", bên
trong nhánh xử lý mode đó) -- mode "function" mặc định KHÔNG đụng tới package
này, không yêu cầu cài tree-sitter/networkx.

Xem:
  - models.py          : dataclass FunctionNode/FileNode/ProgramGraph dùng chung
  - builder.py          : parse .py -> PCG + PSG (tree-sitter, fallback `ast`)
  - rank.py             : FuncRank = PageRank (networkx) trên PCG
  - context_export.py   : xuất toàn bộ graph ra JSON
"""
