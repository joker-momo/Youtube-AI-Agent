# BUILD SPEC (Part 2 — Execution) — Long-form v2

> Đi kèm Part 1: `docs/specs/2026-06-24-long-form-v2-implementation-spec.md` (kiến
> trúc + data contracts). File này = **bảng thi công** để Claude Code làm tới hết:
> từng task có file đích, hành vi, test, lệnh verify, gate. Làm TUẦN TỰ Phase 1b→6.
> Nếu mâu thuẫn schema: **render-props.ts là chuẩn cho phía render** (xem §A).

## CÁCH LÀM (bắt buộc mỗi phase)
- Đọc trước: `CLAUDE.md`, `.claude/rules/*`, `.wolf/cerebrum.md` (Do-Not-Repeat),
  `.wolf/buglog.json`. Ưu tiên code-review-graph MCP trước Grep/Read.
- KHÔNG sửa/đụng/ import `src/video_agent/shorts/**`, `remotion/src/shorts/**`,
  `ShortVideo.tsx`. Viết code mới độc lập (module gốc: `src/video_agent/visual/`).
- Mặc định feature ở `report_only`. Chỉ `enforced` sau khi report-only được review đạt.
- Khi chưa có schedule/elena → `ChannelVideo` render y hệt hiện tại (frame-identical).
- Verify: `.venv/bin/python -m pytest -q` phải xanh. Chạy thêm test riêng của phase.
  Sau mỗi phase: cập nhật `.wolf/anatomy.md`, append `.wolf/memory.md`, log buglog
  nếu có. KHÔNG claim xong nếu thiếu bằng chứng test.
- Sau mỗi phase chạm Shorts-risk: chạy `pytest -q` các test shorts + `git status`
  xác nhận KHÔNG có file shorts nào thay đổi.

## §A. PHÁT HIỆN QUAN TRỌNG — type render đã có sẵn
`remotion/src/render-props.ts` ĐÃ định nghĩa (long-form dùng chung):
- `CompiledAssetSchedule` (schema_version: 2) với **các field bắt buộc**:
  `fps, total_duration_in_frames, scene_boundaries[], tracks[]`.
- `CompiledVisualTrack` (track_type:'background_media', `from_frame`,
  `duration_in_frames`, `end_frame_exclusive`, `trim_before_in_frames`,
  `trim_timebase_fps`, `playback_rate`, `loop_policy`, `render_media_kind`,
  `source_media_kind`, `crop_plan?`, `motion_plan?`, `visual_span_id`, `scene_ids[]`).
- `CompiledSceneBoundary` (`scene_id, from_frame, duration_in_frames, end_frame_exclusive, is_graphic?`).
- `RenderProps.visual_schedule?: CompiledAssetSchedule | null`.
- `Scene.visual_span_id?` / `visual_span_intent?` đã tồn tại.
- `timing_source` hợp lệ là `'tts_final' | 'scene_plan'` (KHÔNG phải "whisper_timestamps").

→ `src/video_agent/visual/schedule.py` PHẢI emit **đúng shape này** (đặc biệt
`scene_boundaries` + `total_duration_in_frames` + `end_frame_exclusive`). Đừng phát
minh schema mới. `ChannelVideo.tsx` hiện CHƯA tiêu thụ `visual_schedule` → việc của
Phase 2 là tiêu thụ nó.

Chưa có type cho Elena → Phase 5 phải thêm `ElenaCue`/`ElenaCuesDoc` vào render-props.ts.

---

## PHASE 1b — Stage `visual_spans` (report_only) vào pipeline sống

Mục tiêu: chạy span planner trong pipeline, ghi artifact, gắn `visual_span_id` lên
scenes. KHÔNG đổi render.

Tasks:
1. `src/video_agent/orchestrator/stages/visual_spans.py` (MỚI). Hàm
   `run_visual_spans_stage(job_dir, channel_path) -> Path`:
   - đọc `scenes.json` + channel config; gọi
     `video_agent.visual.build_visual_spans(scene_doc, channel_config, job_id=...)`;
   - ghi `jobs/<id>/json/visual_spans.json`;
   - gọi `assign_span_ids_to_scenes(scene_doc, spans)` và ghi lại scenes.json
     (CHỈ thêm field `visual_span_id`, không đổi gì khác);
   - theo pattern stage hiện có: kiểm `current_stage`, `_complete_stage`, tôn trọng
     `.stop_requested`. Đọc `render_review.py` làm mẫu cấu trúc stage.
2. `job_state.py`: thêm `"visual_spans"` vào `DEFAULT_STAGES` **ngay sau `scenes_qa`**.
3. `web/run_all_pipeline.py`: thêm nhánh chạy `visual_spans` (pattern `_record` +
   `_check_stop_requested`) đặt sau khối `scenes_qa`, trước `seo`.
4. `configs/*/channel.yaml`: thêm khối
   ```yaml
   visual:
     span_planning:
       enabled: true
       mode: report_only
       max_scenes_per_span: 3
       max_span_sec: 40
       groupable_layouts: [subtitle]
   ```
5. Test `tests/test_visual_spans_stage.py`: stage chạy trên scene_doc giả → tạo
   visual_spans.json hợp lệ, scenes.json được gắn visual_span_id, không phá field khác.

Verify: `pytest tests/test_long_visual_spans.py tests/test_visual_spans_stage.py -q`
+ chạy thật trên ≥3 job (hoặc resume) → kiểm visual_spans.json.
GATE 1b: artifact hợp lệ, coverage PASS, **review thủ công grouping** trên job thật.

---

## PHASE 2 — Schedule + VisualTimeline (proof "no-reset")

Mục tiêu: dựng nguồn-sự-thật timeline + render clip nền trải span, chứng minh không
reset frame. CHƯA cần asset thật mới (dùng asset hiện có per-scene gộp theo span).

Tasks:
1. `src/video_agent/visual/schedule.py` (MỚI, pure, độc lập). Tham chiếu shape
   `shorts/asset_schedule.py` nhưng **emit đúng `CompiledAssetSchedule` của
   render-props.ts** (§A). Hàm
   `compile_asset_schedule(*, scene_doc, visual_spans, fps, timing_source='tts_final') -> dict`:
   - tính `scene_boundaries` từ `duration_sec` (đã chốt bởi TTS/whisper) → frame;
   - mỗi `continuous_clip` span → 1 track `background_media` trải từ from_frame của
     scene đầu tới end của scene cuối; `asset_ref` = clip nền của span (Phase 2: lấy
     clip của scene đầu span; Phase 3 thay bằng clip liên tục thật);
   - mỗi `graphic_image` span → track `render_media_kind:'image'`;
   - `loop_policy:'forbid'`, `playback_rate:1.0`, `trim_*` hợp lý.
2. `src/video_agent/orchestrator/stages/visual_schedule.py` (MỚI): stage
   `run_visual_schedule_stage` chạy **sau `whisper_timestamps`**, đọc scenes.json
   (timing thật) + visual_spans.json → ghi
   `jobs/<id>/json/compiled_asset_schedule.json`.
3. `job_state.py`: thêm `"visual_schedule"` sau `whisper_timestamps`, trước `render`.
   `run_all_pipeline.py`: wiring tương ứng.
4. Inject vào render_props long-form: tìm writer render_props.json (đọc
   `orchestrator/stages/audio.py`, `operator.py`, `pipeline.py` — xác định cái ghi
   bản cuối) và thêm `visual_schedule` khi file tồn tại; thiếu → bỏ qua.
5. `remotion/src/ChannelVisualTimeline.tsx` (MỚI). Tham chiếu
   `remotion/src/shorts/VisualTimeline.tsx`. Render mỗi track `background_media`
   thành `<Sequence from=from_frame durationInFrames=duration_in_frames>` chứa lớp
   media (OffthreadVideo cho video / Img cho image) với trim/playback_rate/crop →
   clip chạy **liền qua ranh giới scene, KHÔNG remount**.
6. `remotion/src/ChannelVideo.tsx`: nếu `props.visual_schedule` có → dùng
   `ChannelVisualTimeline` cho tầng nền; nếu không → giữ path per-scene cũ
   (frame-identical). KHÔNG xóa path cũ.
7. Test: `tests/test_long_visual_schedule.py` (compile đúng frame, boundaries khớp
   tổng duration, mỗi span 1 track). Proof render bằng clip đánh số frame.

Verify: `pytest -q` + render proof.
GATE 2 (đo được): không reset playhead/biên frame ở ranh giới span; **job KHÔNG có
schedule render frame-identical** với hiện tại; rerender frame-identical.

---

## PHASE 3 — Acquire Pexels per-span (clip liên tục thật)

Tasks:
1. `src/video_agent/stages/assets.py`: thêm đường chọn **1 clip cho cả span** (giữ
   cascade chất lượng: semantic gate, SigLIP age-gate 45+, weak-match guard). Dùng
   `assets_manifest.scenes[].source_path` (clip gốc đầy đủ) để cấp cho nhiều scene
   của span qua trim offset trong schedule. Đọc `get_scene_asset` hiện tại trước khi
   đổi; không phá đường per-scene cũ.
2. Span không có clip liên tục đạt chuẩn → **fail-closed**: tách span đó về
   per-scene legacy (mỗi scene 1 clip). KHÔNG hạ chất lượng/clip yếu.
3. `schedule.py`: cập nhật `asset_ref` + `trim_before_in_frames`/`source_duration_sec`
   theo clip liên tục thật.
4. Test: span chọn được clip liên tục → 1 track trải span; span thiếu clip →
   fallback per-scene (n track).

Verify: A/B trên cùng script (bản span vs 1:1).
GATE 3: giảm số lần cắt; **KHÔNG tăng tỉ lệ weak-match**; review cảm nhận hình.

---

## PHASE 4 — Graphic = ảnh ChatGPT (bỏ card Remotion)

Tasks:
1. `src/video_agent/orchestrator/stages/graphic_images.py` (MỚI): với mỗi scene
   layout ∈ {checklist,warning,quote,cta} và `graphic.needed`: gọi cơ chế gen ảnh
   (tham chiếu `auto_thumbnail_image_stage` dùng `client.generate_image`; viết hàm
   long-form riêng, không đụng shorts). Lưu ảnh → set `scene.graphic.image_ref`.
   Chạy sau `seo_qa`. Cache + retry; scene fail → fallback an toàn (log).
2. `job_state.py` + `run_all_pipeline.py`: thêm stage `graphic_images`.
3. `remotion/src/ChannelVideo.tsx`: bỏ nhánh render card cho layout đồ họa; thay
   bằng hiển thị `scene.graphic.image_ref` (qua schedule track image hoặc trực tiếp).
   Subtitle thường vẫn text tầng 3.
4. Prompt scenes long-form (MỚI, không đụng shorts): yêu cầu LLM điền
   `graphic.needed` + `graphic.prompt` cho scene đồ họa; Gemini QA kiểm prompt đủ cụ thể.
5. Test: scene graphic có image_ref; render hiển thị ảnh; không còn card.

Verify: `pytest -q` + render mẫu.
GATE 4: ảnh đúng nội dung, đọc tốt, layout không vỡ.

---

## PHASE 5 — Elena presenter (talking + hidden)

Data type: thêm vào `render-props.ts`:
```ts
export type ElenaCue = {
  start_frame: number; duration_frames: number;
  mode: 'talking' | 'hidden'; treatment?: 'circle' | 'large';
  variant?: 'talk-neutral' | 'talk-emphasis';
  position: 'bottom-right'; asset_ref?: string; source_trim_frames?: number; reason?: string;
};
export type ElenaCuesDoc = { schema_version: 1; fps: number; total_frames: number; cues: ElenaCue[]; };
// RenderProps thêm: elena_cues?: ElenaCuesDoc | null;
```

Tasks:
1. `src/video_agent/visual/elena.py` (MỚI, độc lập). `build_elena_cues(scene_doc,
   channel_config, fps, *, job_id) -> dict`:
   - đọc annotation Elena (LLM) trên scenes + timing → cues frame-accurate;
   - áp luật §6 Part 1: mỗi lần 5–10s; cách nhau ≥15s; KHÔNG 2 talking liền kề;
     KHÔNG lặp asset 2 lần liên tiếp; biến thiên **xác định theo job_id/scene index**;
   - **IDLE bỏ hẳn** (chỉ talking/hidden); scene evidence/checklist/label → hidden
     (fail-safe: nghi ngờ → hidden);
   - treatment: large(TALK_EMPHASIS) cho hook/quy-tắc/cảnh-báo/kết-luận; circle
     (TALK_NEUTRAL) cho VO liên tục nền đơn giản.
2. `src/video_agent/orchestrator/stages/elena_plan.py` (MỚI): stage sau
   `whisper_timestamps` (cùng nhóm với visual_schedule), ghi `elena_cues.json`;
   inject vào render_props.
3. `job_state.py` + `run_all_pipeline.py`: thêm stage `elena_plan`.
4. `remotion/src/ChannelElenaPresenter.tsx` (MỚI). Props từ ElenaCue. Yêu cầu:
   `mode==='hidden'` → null, KHÔNG mount; **luôn mute**; mask tròn(circle)/bo
   góc(large); size 240px(circle)/~384px(large); góc dưới-phải, lề phải ~72/dưới
   ~120; crop ôm mặt+ngực, mặt 65–75%; viền 4–6px + bóng; giữ nền gốc; tôn trọng
   safe-area; KHÔNG đè subtitle; **không restart** khi 2 cue liền kề cùng asset;
   overlay debug ở dev. 24fps asset trong comp 30fps → phát theo timestamp, KHÔNG
   time-stretch.
5. `ChannelVideo.tsx`: render `ChannelElenaPresenter` theo `props.elena_cues` (tầng
   trên B-roll/graphic, dưới hoặc không đè subtitle).
6. Asset: đặt 2 clip `ELENA_TALK_NEUTRAL`, `ELENA_TALK_EMPHASIS` (1280×720,24fps,
   ~10s) vào nơi cố định (vd `assets/elena/` chung kênh); tài liệu hóa đường dẫn.
7. Prompt scenes long-form: LLM điền `elena{mode,treatment,variant,reason}` theo §6.
8. Test: cues hợp lệ; không cue đè scene evidence; metrics ngân sách; reproducible
   theo job_id; component mute + không restart (test logic chọn cue/asset).

Verify: `pytest -q` + render có Elena.
GATE 5: mute OK; 24→30fps không giật; không đè subtitle; nhịp/khoảng nghỉ đúng;
reproducible; không lặp asset liền kề.

---

## PHASE 6 — Continuity QA + bật enforced

Tasks:
1. `src/video_agent/orchestrator/stages/render_continuity_qa.py` (MỚI, long-form;
   tham chiếu `shorts/render_continuity_qa.py`): soi luma/seek tại ranh giới span,
   báo lỗi nếu reset/nhảy frame. Stage sau `render`.
2. `job_state.py` + `run_all_pipeline.py`: thêm stage.
3. Chuyển `visual.span_planning.mode` + elena sang `enforced` cho kênh đã PASS các
   gate trước (qua channel.yaml).
4. Cập nhật `.wolf/anatomy.md`, `memory.md`, `cerebrum.md` (learnings), buglog.

Verify: `.venv/bin/python -m pytest -q` xanh toàn bộ; review verdict long-form
không tụt điểm visual/audio.
GATE 6 = DEFINITION OF DONE.

---

## DEFINITION OF DONE (toàn dự án)
- Pipeline long-form: nền theo span (ít cắt vụn), graphic là ảnh ChatGPT, Elena
  đúng luật (talking+hidden, mute, không đè subtitle), KHÔNG reset frame.
- Mọi stage mới: report_only → review đạt → enforced.
- `.venv/bin/python -m pytest -q` xanh toàn bộ.
- Flow Shorts KHÔNG đổi: `git status` không có file shorts thay đổi; test shorts xanh.
- `.wolf/*` đã cập nhật.

## THỨ TỰ STAGE CUỐI (job_state.DEFAULT_STAGES long-form)
```
idea_research, script, script_promote, script_qa,
scenes, scenes_promote, scenes_qa,
visual_spans,                      # 1b
seo, seo_promote, seo_qa,
graphic_images,                    # 4
thumbnail_image,
whisper_timestamps,
visual_schedule,                   # 2
elena_plan,                        # 5
render,
render_continuity_qa,              # 6
review
```

## NIT cần dọn (Phase 1b, tiện tay)
`_classify_span` trong `visual/spans.py`: nhánh `if _is_cta(only): return
"continuous_clip","cta isolated"` là **code chết** (cta đã thuộc GRAPHIC_LAYOUTS nên
bị `all(_is_graphic)` bắt trước → graphic_image). Xóa nhánh đó + sửa docstring
dòng ~108 cho khớp (cta → graphic_image). Có test `test_cta_tagged_graphic_image`
bảo vệ hành vi đúng.
