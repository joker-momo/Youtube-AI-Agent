# ACCEPTANCE & VERIFICATION SPEC (Phase 7) — Long-form v2

> Code Phase 1b→6 đã viết xong (modules, stages, wiring, Remotion components, tests).
> File này = phần CÒN LẠI để tuyên bố DONE: chạy test thật, render proof, đóng các
> gate review, rồi mới bật enforced. Đi kèm:
> - Part 1: `…-implementation-spec.md`  - Part 2: `…-build-spec.md`
>
> Ràng buộc giữ nguyên: KHÔNG đụng flow Shorts; report_only trước enforced; OpenWolf;
> mọi claim phải có bằng chứng.

## Đã xác nhận (khỏi làm lại)
- Đủ file + wiring + `DEFAULT_STAGES` đúng thứ tự; git không đụng shorts.
- Test module thuần xanh: visual_spans 18, visual_schedule 13, elena 10 = 41/41.

## CÒN LẠI — làm tuần tự, mỗi mục có bằng chứng

### 7.1 Full test suite (BẮT BUỘC)
- Chạy `.venv/bin/python -m pytest -q`. Báo số pass/fail.
- Riêng các test mới phải xanh: `test_visual_spans_stage`, `test_visual_schedule_stage`,
  `test_long_visual_schedule`, `test_graphic_images_stage`, `test_elena_plan_stage`,
  `test_long_elena`, `test_long_render_continuity_qa`, `test_render_continuity_qa_stage`,
  `test_remotion_channel_timeline_continuity`.
- Lỗi nào → soi root cause, sửa, log `.wolf/buglog.json`. KHÔNG sửa/đụng file shorts.
- PASS: toàn suite xanh (hoặc chỉ còn fail đã tồn tại từ trước, phải nêu rõ baseline).

### 7.2 Shorts regression (BẮT BUỘC)
- `git status --porcelain | grep shorts` → phải rỗng.
- Chạy test shorts (`pytest -q -k short`) → xanh như trước.
- PASS: không file shorts thay đổi; test shorts không hồi quy.

### 7.3 Report-only trên ≥3 job long-form thật (Gate 1b + schedule sanity)
- Chạy pipeline (hoặc resume từng stage) ở `mode: report_only` cho ≥3 job.
- Sinh và đính kèm: `json/visual_spans.json`, `json/compiled_asset_schedule.json`,
  `json/elena_cues.json` của từng job.
- Tự kiểm + báo cáo bảng: scene_count, span_count, continuous/graphic, reduction,
  qa.verdict; với elena: visible_pct, talking/hidden, appearance_count, min_gap.
- DỪNG cho tôi review thủ công grouping + nhịp Elena trước khi qua 7.4.

### 7.4 Render proof — "no-reset" + an toàn (Gate 2/5)
- Render 1 job report_only end-to-end. Kiểm và báo cáo bằng chứng:
  (a) Background: clip nền span chạy LIỀN qua ranh giới scene, KHÔNG reset playhead
      (dùng clip đánh số frame nếu cần để chứng minh).
  (b) Job KHÔNG có `visual_schedule` (xóa/ẩn file) render **frame-identical** với
      bản hiện tại của cùng job (so checksum vài frame mốc). Rerender frame-identical.
  (c) Graphic scene hiện ẢNH ChatGPT (không còn card Remotion), đọc rõ.
  (d) Elena: chỉ talking+hidden; **video mute**; 24fps→comp 30fps không giật/đổi tốc;
      vòng tròn nhỏ / lớn 20% đúng vị trí; KHÔNG đè subtitle; không restart khi 2 cue
      cùng asset liền kề.
  (e) `render_continuity_qa` verdict PASS (không nhảy/lặp frame ở ranh giới span).
- PASS: tất cả (a)–(e) đạt; đính ảnh chụp khung hình minh họa.

### 7.5 A/B chất lượng (Gate 3)
- So 1 video bản span vs bản 1:1 (cùng script): số lần cắt nền, tỉ lệ weak-match,
  cảm nhận liền mạch.
- PASS: giảm số cắt; **KHÔNG tăng weak-match**. DỪNG cho tôi xem mắt thường.

### 7.6 Bật enforced (sau khi 7.1–7.5 đạt + tôi duyệt)
- Đổi `configs/<kênh>/channel.yaml`: `visual.span_planning.mode: enforced` (+ elena
  nếu có cờ riêng). Render lại 1 job, chạy lại 7.4(e).
- PASS: video enforced đạt chất lượng; review verdict long-form KHÔNG tụt điểm
  visual/audio so với baseline.

### 7.7 Chốt OpenWolf
- Cập nhật `.wolf/anatomy.md`, append `.wolf/memory.md`, learnings vào
  `.wolf/cerebrum.md`, bug (nếu có) vào `.wolf/buglog.json`.

## DEFINITION OF DONE
7.1–7.7 đạt; `pytest -q` xanh; shorts không đổi; 1 video enforced render đúng (span
nền liền mạch, graphic = ảnh ChatGPT, Elena đúng luật + mute + không đè subtitle,
không reset frame); `.wolf/*` cập nhật.

## QUY TẮC DỪNG
Các gate cần mắt người (7.3 grouping/Elena, 7.5 A/B, 7.6 trước khi enforced) → DỪNG,
báo cáo bằng chứng, CHỜ duyệt. Không tự bật enforced.
