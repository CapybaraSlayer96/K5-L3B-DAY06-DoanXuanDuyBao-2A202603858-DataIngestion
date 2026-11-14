# Báo cáo cá nhân — Day 6

## Dữ liệu và pipeline
- **Corpus:** 3 RFC văn bản thuần từ rfc-editor.org (RFC 6749 OAuth 2.0, năm 2012; RFC 7519 JWT, 2015; RFC 7636 PKCE, 2015). Quyền: bản quyền IETF Trust, cho phép tái sử dụng theo IETF Trust Legal Provisions/BCP 78 (ghi trong header mỗi RFC). Truy cập 2026-11-14.
- **Trang:** trang vật lý = đoạn giữa các ký tự form-feed. 126 trang vật lý → **114 trang nội dung** (71 + 26 + 17). Loại: trang rỗng, mục lục, references/authors' addresses (`results/ingest_page_log.csv`, `data/manifest.json`).
- **Chuẩn hóa:** NFC, bỏ header/footer RFC (`[Page N]`, dòng tiêu đề lặp), bỏ thụt lề chung, nối dòng của đoạn văn, giữ nguyên khối literal (ABNF, sơ đồ). Giới hạn: sơ đồ ASCII/ví dụ HTTP vẫn tạo chunk nhiễu; không cần OCR.
- **Embedding:** Gemini `gemini-embedding-001` qua `google-genai`; client nhận `GEMINI_API_KEY` và `GEMINI_BASE_URL` từ file env của starter; 768 chiều (`output_dimensionality`), task type `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`. **Vector store:** PostgreSQL 16 + pgvector 0.8.7 (Docker), cosine, quét chính xác (không ANN).
- **Chi phí:** ≈109k token ước tính (ký tự/4) cho lần nạp đầu của A+B; truy vấn chỉ vài nghìn ký tự. Chi phí USD tính theo token nhà cung cấp; proxy không trả hóa đơn.
- **Metadata:** bắt buộc `document_id`, `source` (URL), `page`; thêm `year` (int, từ ngày header RFC), `topic` (nhãn tác giả đặt cố định), `section` (heading số của RFC, trích từ văn bản), `start/end` (offset trong trang). Filter dùng `document_id` và `year`: phân biệt 2012 vs 2015 và từng RFC. Hạn chế: `year` chỉ có 2 giá trị; filter `document_id` gần như trùng gold doc nên chỉ kiểm tra khả năng lọc, không chứng minh "tìm đúng tài liệu".

## Cấu hình và benchmark
- **A:** `fixed_size` 500 ký tự, overlap 50, không filter. **B:** `by_paragraph` gộp đoạn tối đa 1000 ký tự (đoạn quá dài cắt theo câu), không filter. **C:** cùng index với B; khác duy nhất: thêm filter SQL `document_id`/`year` khi câu hỏi có filter (câu không filter chạy như B). A–B thay chunking (kích thước + cách cắt); B–C chỉ thay filter. Cùng corpus, model, 22 câu, top-3.
- **22 câu:** 17 dev / 5 test, 4 câu filter (3 dev, 1 test: q20). Evidence được `validate_benchmark` kiểm tra tự động nằm đúng trang. Xem `benchmark/README.md`.
- **Tiêu chí chọn chiến lược:** ưu tiên Hit@3 dev, rồi Hit@1 dev, rồi coherence/grounding, rồi tài nguyên. Tiêu chí chốt ở lần chạy đầu, cùng lúc với kết quả test; không chỉnh tham số nào theo test. Benchmark, split, code và manifest được commit (313bb01) trước, sau đó xóa DB và kết quả rồi chạy lại từ đầu: số liệu và top-3 trùng lần chạy đầu. Lần chạy đầu đã cho thấy kết quả test nên test không phải holdout độc lập hoàn toàn; commit chỉ chứng minh thứ tự file.

## Kết quả (tự động, `results/summary.json`)
| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian ingest | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 9/17 | 15/17 | 511 | 36.9 s | ≈57k token |
| A | test | 1/5 | 3/5 | 511 | | |
| B | dev | 15/17 | 17/17 | 290 | 20.8 s | ≈52k token |
| B | test | 2/5 | 2/5 | 290 | | |
| C | dev | 16/17 | 17/17 | 290 (index B) | 0 thêm | 0 thêm |
| C | test | 2/5 | 2/5 | 290 (index B) | | |

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N) | Grounding 0/1/2 và TB /2 |
|---|---|---|---|---|
| A | 0.9 (n=10; 0/1/2 = 1/9/0); người chấm 2: 0.0 | 15/15 (script) | test 1/1; toàn bộ 2/4 (A không lọc) | 1/1/3 → 1.4 |
| B | 1.8 (n=10; 0/1/2 = 0/2/8); người chấm 2: 1.8 | 15/15 (script) | test 1/1; toàn bộ 2/4 (B không lọc) | 1/2/2 → 1.2 |
| C | 1.8 (cùng chunk với B) | 15/15 (script) | test 1/1; toàn bộ 4/4 | 1/2/2 → 1.2 |

- Metadata audit là **kiểm tra bằng script** (`results/metadata_audit.csv`): `source` khớp manifest, chunk nằm đúng offset trong trang nguồn, `year/topic` khớp manifest, `section` không rỗng; 45/45 bản ghi đạt.
- Nạp lại: lần chạy thứ hai `embedded_this_run=0`, số dòng DB giữ nguyên 511/290 (upsert theo `(strategy, chunk_id)` + hash nội dung; `prune()` xóa chunk không còn được tạo khi tài liệu đổi, kiểm tra bằng `starter/tests/test_pipeline.py`).
- Trace: `results/traces.jsonl` (top-3, score, metadata, nhãn hit). Mẫu coherence: 10 vị trí cách đều trong danh sách trang đã sắp xếp (quy tắc cố định, xác định trước khi chấm). Nhãn coherence/grounding: xem mục chấm độc lập bên dưới.

### Chấm thủ công và chấm lần hai
- Người chấm 1: tác giả (có AI agent hỗ trợ soạn, đã soát) — `results/manual_labels/coherence_to_label.csv`, `grounding_to_label.csv`. Người chấm 2: một subagent không thấy nhãn người chấm 1 — `independent_coherence.csv`, `independent_grounding.csv`.
- Rubric coherence áp dụng theo thang GUIDE: 0 = không hiểu được, 1 = hiểu một phần, 2 = tự hiểu và giữ trọn ý. Chunk A bắt đầu giữa câu/giữa từ nhưng phần còn lại đọc được nên chấm 1; riêng sơ đồ ASCII cắt dở chấm 0. Người chấm 2 áp dụng quy tắc nghiêm hơn theo ranh giới (cắt ở cả hai đầu = 0) nên chấm A = 0.0. Khớp chính xác: coherence 10/20 (10 lệch đều ở chunk A), grounding 15/15. Cả hai cách chấm đều cho A < B.
- Chỉ số tự động `results/boundary_metric.json`: chunk có cả hai đầu ở ranh giới câu/đoạn tối đa 24,3% (A) so với 98,6% (B).
- Grounding: q18 chỉ A đạt 2 (hai chunk A kề nhau chứa đủ "non-guessable" và ràng buộc trạng thái xác thực); q19 cả ba chiến lược 0; q20 cả ba 1 (chỉ có "bị từ chối", không có `invalid_grant`).
- Điểm cần lưu ý: (1) C giống B ở 20/22 câu vì chỉ khác khi có filter (q15, q17 đổi top-3); (2) grounding chỉ q18 tách A và B với n=5; (3) người chấm thấy `evidence_quote` và `chunk_id` có chữ A/B nên không mù chiến lược; (4) không có câu bẫy, nhiều bước hay không đáp án, không phạt distractor (q20: top-1 của B/C là đoạn lỗi `invalid_request`); (5) rubric coherence không phạt chunk gộp qua ranh giới mục.

## Phân tích và kết luận
- **A→B:** Hit@1 dev 9→15/17, Hit@3 dev 15→17/17; chunk giảm 511→290, thời gian ingest 36.9→20.8 s. A hỏng ở q08 và q16 (đúng trang nhưng cửa sổ 500 ký tự cắt câu bằng chứng → không chứa trọn evidence). Test: Hit@3 giảm 3→2/5, không kết luận được với n=5.
- **B→C:** dev +1 Hit@1 (q15: B xếp RFC 6749 p16 đầu; C với `year=2015` đưa đúng RFC 7636 p8 lên); tuân thủ filter 2/4→4/4. Test không đổi (q20 miss cả B và C).
- **Ví dụ sai 1 — q18 (RFC 6749 p59, B/C):** top-1 là chunk CSRF cùng trang nhưng đoạn chứa "MUST contain a non-guessable value" nằm ở chunk khác; evidence bị tách → miss dù đúng trang (`page_match_top3=True`).
- **Ví dụ sai 2 — q20 (RFC 7636 p10, C):** dù lọc `document_id=rfc7636`, top-3 là p9/p18/p5, không có câu "invalid_grant … MUST be returned"; embedding ưu tiên đoạn "values not equal" ở p18. Filter không sửa lỗi xếp hạng trong cùng tài liệu. q19 (p42): cả ba chiến lược lấy các đoạn về refresh token nói chung.
- **Chọn chiến lược:** theo tiêu chí trên chọn **C** (dev Hit@1 16/17, Hit@3 17/17, lưu trữ bằng B), áp dụng khi người dùng cung cấp phạm vi (tài liệu/năm) trước truy xuất; không có filter thì C = B. Đánh đổi: chunk B/C dài (median 782 ký tự) giữ ngữ cảnh nhưng có thể gộp nhiều ý; A nhiều chunk hơn (511 vs 290) nên tốn nhiều embedding/lưu trữ hơn.
- **Hạn chế:** corpus nhỏ, một lĩnh vực; test chỉ 5 câu (1 câu = 20 điểm %); chỉ 4 câu filter; chi phí là ước tính token; nhãn thủ công do tác giả chấm (thiên lệch vì cũng viết benchmark), người chấm 2 cùng họ mô hình; không dùng LLM judge cho chỉ số chính. AI agent hỗ trợ viết code; số liệu lấy trực tiếp từ output chạy.
- **Cải tiến:** làm tập câu mới (nhiều bước, câu bẫy) rồi thử overlap/heading; chunk theo heading số của RFC, loại sơ đồ ASCII khỏi index, thêm hybrid BM25 cho từ khóa MUST/SHOULD, mở rộng test ≥20 câu.

## Tái lập
```bash
cd starter
python -m pip install -r requirements.txt
# tạo file env của starter từ .env.example, điền GEMINI_API_KEY và GEMINI_BASE_URL
docker compose -p day6_student_starter up -d --wait
python -m src.run
```
Dữ liệu thô `starter/data/raw/rfc{6749,7519,7636}.txt` lấy từ `https://www.rfc-editor.org/rfc/rfcNNNN.txt` (sha256 trong `data/manifest.json`).
