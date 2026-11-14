# Benchmark

`questions.json`: 22 câu, 17 dev (q01–q17) / 5 test (q18–q22). Chia và viết trước khi chạy retrieval lần đầu (file tạo trước `results/`).
- Chia theo câu, phủ cả 3 tài liệu ở cả dev và test; không chia theo tài liệu → nguy cơ rò rỉ chủ đề (cùng tài liệu xuất hiện ở dev và test).
- 4 câu filter: q10 (`document_id=rfc7519`), q15 (`year=2015`), q17 (`year=2012`) ở dev; q20 (`document_id=rfc7636`) ở test. Giá trị filter là input đưa trước, nằm trong câu hỏi ("Within the JWT specification…").
- `evidence_quote` được `src/corpus.py::validate_benchmark` kiểm tra nằm đúng trang sau chuẩn hóa; q07 có `alt_gold` (trang 58).
- Hit = chunk cùng source+page và chứa trọn `evidence_quote` (đã squash khoảng trắng).
