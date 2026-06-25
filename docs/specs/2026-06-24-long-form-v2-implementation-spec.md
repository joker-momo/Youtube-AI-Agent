# IMPLEMENTATION SPEC — Long-form Video Pipeline v2 (Vida Plena 45+)

> Mục đích: spec **tự chứa, sẵn sàng để code tiếp** cho Claude Code.
> Canonical hơn các file plan kèm theo (`docs/plans/2026-06-24-long-form-*.md`);
> nếu mâu thuẫn, file này thắng.
> Ngày: 2026-06-24. Mọi quyết định trong này đã được chủ dự án chốt.

---

## 0. RÀNG BUỘC CỨNG & CÁCH LÀM (đọc trước khi code)

1. **PRIME DIRECTIVE:** mọi thay đổi để nâng chất lượng/độ hấp dẫn video cho khán
   giả 45+. Không đánh đổi chất lượng lấy tốc độ/chi phí. Nếu một thay đổi hại chất
   lượng → DỪNG, cảnh báo, chờ xác nhận.
2. **KHÔNG đụng flow Shorts.** Không sửa, không phụ thuộc `src/video_agent/shorts/**`
   hay `remotion/src/shorts/**` hay `ShortVideo.tsx`. Các file Shorts chỉ dùng làm
   **tham chiếu thiết kế**. Viết code MỚI, ĐỘC LẬP.
3. **OpenWolf:** đọc `.wolf/cerebrum.md` (Do-Not-Repeat) trước khi sinh code; đọc
   `.wolf/buglog.json` trước khi sửa bug; sau khi sửa cập nhật `.wolf/anatomy.md`,
   append `.wolf/memory.md`, log buglog nếu phát sinh. Ưu tiên code-review-graph MCP
   trước Grep/Read.
4. **Verification bắt buộc:** mỗi phase có lệnh test + điều kiện PASS. Không claim
   xong nếu chưa có bằng chứng test. Dùng `.venv/bin/python -m pytest`.
5. **Quy trình bật tính năng:** `disabled → report_only → enforced`. Mặc định mọi
   tính năng mới ở `report_only` (sinh dữ liệu nhưng KHÔNG đổi video render) cho tới
   khi qua gate review thủ công.
6. **Render an toàn:** khi chưa có dữ liệu mới (schedule/elena_cues), `ChannelVideo`
   phải render **y hệt hiện tại** (job cũ frame-identical).

---

## 1. KIẾN TRÚC 3 TẦNG

```
Tầng 3  SUBTITLE          chữ chạy; KHÔNG bao giờ nằm sau Elena
Tầng 2  SCENES + OVERLAY  graphic (ảnh ChatGPT) + Elena presenter
Tầng 1  BACKGROUND SPAN   1 video Pexels liên tục cho cả span
```

- **Span** = đơn vị HÌNH NỀN: 1 video Pexels chạy liền qua nhiều scene.
- **Scene** = đơn vị BIÊN TẬP/AUDIO bên trong span (narration, timing, subtitle).
- Background thuộc **span**, KHÔNG thuộc scene. Đây là điểm khác cốt lõi so với
  pipeline hiện tại (đang gán 1 clip / 1 scene).

Layout long-form hiện có: `hook | subtitle | checklist | warning | quote | cta`.

---

## 2. TRẠNG THÁI HIỆN TẠI (Phase 1a đã viết — cần review/giữ)

Đã tạo (độc lập, KHÔNG import shorts):
- `src/video_agent/visual/__init__.py` — re-export.
- `src/video_agent/visual/config.py` — `resolve_visual_span_config()` đọc
  `channel.visual.span_planning`; `DEFAULT_SPAN_CONFIG` (max_scenes_per_span=3,
  max_span_sec=40, groupable_layouts=("subtitle",), mode=report_only).
- `src/video_agent/visual/spans.py` — engine thuần: `build_visual_spans()`,
  `validate_and_repair_visual_spans()`, `compute_span_input_hash()`,
  `assign_span_ids_to_scenes()`. Gom subtitle liền kề; isolate hook/cta/graphic;
  graphic → `planned_mode="graphic_image"`.
- `tests/test_long_visual_spans.py` — 12 test (đã pass trên bản sao logic; cần
  chạy lại bằng `.venv/bin/python -m pytest` trong môi trường thật để xác nhận).

Bằng chứng khả thi: trên job thật 48 scene → 33 span (20 nền-Pexels + 13 graphic),
giảm 15 clip Pexels so với 1:1, coverage PASS.

**Việc còn lại = Phase 1b trở đi (mục 7).**

---

## 3. HỢP ĐỒNG DỮ LIỆU (JSON contracts)

### 3.1 scenes.json — field bổ sung (do LLM điền, §4)
Mỗi scene giữ field hiện tại (`id, layout, duration_sec, narration, on_screen_text,
caption, visual_prompt, motion, asset_refs, word_segments, audio_offset_sec`) và
THÊM:
```jsonc
{
  "visual_span_id": "vs03",            // optional planner hint; engine tự gán nếu thiếu
  "visual_span_intent": "…",           // optional
  "graphic": {                          // chỉ khi layout là graphic
    "needed": true,
    "prompt": "ChatGPT image prompt viết sẵn",
    "image_ref": "assets/graphic-scene-03.png"  // điền sau khi gen
  },
  "elena": {                            // optional, do LLM gợi ý
    "mode": "talking|hidden",
    "treatment": "circle|large",
    "variant": "talk-neutral|talk-emphasis",
    "reason": "vì sao hiện/ẩn"
  }
}
```

### 3.2 visual_spans.json (schema_version 1) — ĐÃ có từ Phase 1a
```jsonc
{
  "schema_version": 1, "job_id": "...", "generation_mode": "report_only",
  "input_hash": "sha256...",
  "spans": [{
    "id": "vs01", "scene_ids": ["scene-01","scene-02"],
    "start_scene_index": 0, "end_scene_index": 1,
    "visual_intent": "", "planned_mode": "continuous_clip|graphic_image",
    "planning_reason": "...", "source": "implicit|scene_planner", "warnings": []
  }],
  "metrics": { "scene_count","visual_span_count","continuous_clip_span_count",
    "graphic_span_count","estimated_background_clip_reduction","cap_split_count","..." },
  "qa": { "verdict": "PASS|FAIL", "errors": [], "warnings": [] }
}
```

### 3.3 compiled_asset_schedule.json (schema_version 2) — MỚI, mục 7.3
Nguồn sự thật DUY NHẤT cho timeline hình của renderer. Frame-based, compile SAU
`whisper_timestamps`. Tham chiếu shape: `shorts/asset_schedule.py::compile_asset_schedule`.
```jsonc
{
  "schema_version": 2, "fps": 30, "timing_source": "whisper_timestamps",
  "tracks": [{
    "track_id": "vt01", "track_type": "background_media", "span_id": "vs06",
    "asset_ref": "assets/scene-06.mp4",         // 1 clip cho cả span
    "render_media_kind": "video|image",
    "from_frame": 0, "duration_in_frames": 540,
    "source_start_frame": 0, "trim_before_in_frames": 0, "trim_end_in_frames": 0,
    "trim_timebase_fps": 30, "playback_rate": 1.0, "loop_policy": "forbid",
    "crop_plan": {...}, "motion_plan": {...}
  }]
}
```
Quy tắc: mỗi `continuous_clip` span → 1 track `background_media` trải toàn span. Mỗi
`graphic_image` span → 1 track media kind=image (ảnh ChatGPT). `loop_policy="forbid"`.

### 3.4 elena_cues.json — MỚI, mục 7.4
```jsonc
{
  "schema_version": 1, "job_id": "...", "generation_mode": "report_only",
  "fps": 30, "total_frames": 25200,
  "cues": [{
    "start_frame": 0, "duration_frames": 240,
    "mode": "talking|hidden", "treatment": "circle|large",
    "variant": "talk-neutral|talk-emphasis",
    "position": "bottom-right", "asset_ref": "assets/elena/ELENA_TALK_NEUTRAL.mp4",
    "source_trim_frames": 0, "reason": "..."
  }],
  "metrics": { "visible_pct","talking_pct","hidden_pct","appearance_count","min_gap_sec" },
  "qa": { "verdict": "PASS|FAIL", "warnings": [] }
}
```
Lưu ý: `mode="hidden"` thì KHÔNG cần phát asset (renderer không mount).

---

## 4. TRÁCH NHIỆM ChatGPT (script) + Gemini (QA)

LLM là bộ não kế hoạch; viết prompt từ đầu. Cần sửa prompt script/scenes long-form
(viết MỚI cho long-form, không đụng prompt shorts) để output:

**Cấp span:** tổng số span, mỗi span {nội dung, mục đích, prompt video nền Pexels}.
**Cấp scene:** điền `visual_span_id`, và với scene đồ họa điền `graphic.needed=true`
+ `graphic.prompt`; với scene cần người dẫn điền `elena{mode,treatment,variant,reason}`
theo luật §6.

**Gemini QA (rework loop riêng cho long-form, KHÔNG đụng `auto_qa_with_rework` của
shorts):** kiểm
- span có mục đích rõ; prompt nền không mơ hồ;
- scene graphic có prompt đủ cụ thể;
- Elena: tỉ lệ hiện hợp ngân sách (§6.4); KHÔNG đặt Elena vào scene
  evidence/checklist/label; không 2 lần talking liền kề; khoảng nghỉ ≥15s.

---

## 5. CHI TIẾT TỪNG STAGE (mục tiêu, input, output, acceptance)

### 5.1 `visual_spans` (MỚI) — sau `scenes_qa`
- Gọi `video_agent.visual.build_visual_spans(scene_doc, channel_config, mode, job_id)`.
- Ghi `jobs/<id>/json/visual_spans.json`. Gọi `assign_span_ids_to_scenes` để gắn
  `visual_span_id` lên scenes.json.
- `report_only`: không ảnh hưởng render. Wrapper đặt tại
  `src/video_agent/orchestrator/stages/` (file long-form mới, vd `visual_spans.py`).
- Acceptance: visual_spans.json tồn tại, coverage đầy đủ đúng thứ tự, verdict PASS.

### 5.2 `graphic_images` (MỚI) — sau `seo_qa`
- Với mỗi scene `graphic.needed=true`: gọi cơ chế gen ảnh ChatGPT (tham chiếu cách
  `client.generate_image` đang dùng ở `auto_thumbnail_image_stage`; viết hàm long-form
  riêng, không đụng shorts). Lưu ảnh, set `scene.graphic.image_ref`.
- **Bỏ render card Remotion** cho long-form graphic layouts (mục 6/7.5).
- Acceptance: mọi scene graphic có `image_ref` tồn tại; fail 1 scene → fallback an
  toàn (không vỡ pipeline), log rõ.

### 5.3 `visual_schedule` (MỚI) — sau `whisper_timestamps`
- Module mới `src/video_agent/visual/schedule.py::compile_asset_schedule(...)` (độc
  lập; tham chiếu shorts/asset_schedule.py). Đọc scenes.json (timing frame thật từ
  whisper) + visual_spans.json + asset refs → ghi
  `jobs/<id>/json/compiled_asset_schedule.json` (schema v2, §3.3).
- Acceptance: tổng frame của tracks khớp tổng thời lượng; mỗi span đúng 1 track;
  không chồng/ thủng frame.

### 5.4 `elena_plan` (MỚI) — sau `whisper_timestamps`
- Module mới `src/video_agent/visual/elena.py`. Đọc annotation Elena (LLM) + timing
  → sinh `elena_cues.json` frame-accurate; áp ngân sách + luật chống lặp/khoảng nghỉ
  (§6); fail-safe: nghi va chạm/nội dung evidence → `hidden`.
- Acceptance: cues hợp lệ, không cue nào đè scene evidence/checklist; metrics trong
  ngưỡng §6.4; reproducible theo job_id.

### 5.5 `render` (SỬA) — `ChannelVideo.tsx`
- Nếu có `compiled_asset_schedule.json` → `ChannelVisualTimeline` sở hữu tầng nền
  (mục 7.5). Nếu không → path per-scene hiện tại (frame-identical).
- Graphic: hiển thị ảnh ChatGPT (`scene.graphic.image_ref`) thay card.
- Elena: overlay `ChannelElenaPresenter` theo elena_cues (mục 7.6).

### 5.6 `render_continuity_qa` (MỚI) — sau `render`
- Soi nối hình tại ranh giới span (luma/seek) — tham chiếu
  `shorts/render_continuity_qa.py`, viết bản long-form độc lập.
- Acceptance: không reset/nhảy frame tại ranh giới span.

---

## 6. ELENA — RULESET ĐẦY ĐỦ

### 6.1 Danh tính
Phụ nữ TBN ~cuối 50, tóc bob muối tiêu, áo teal + cardigan slate-blue, điềm đạm
tin cậy. **KHÔNG phải bác sĩ.** Lớp hỗ trợ thương hiệu, KHÔNG phải bằng chứng.
Ưu tiên biên tập: evidence/demo > B-roll Pexels > graphic/text > Elena.

### 6.2 Kho asset (CỐ ĐỊNH — chỉ 2, không bổ sung)
- `ELENA_TALK_NEUTRAL` và `ELENA_TALK_EMPHASIS`. 1280×720, **24fps**, có audio, ~10s.
- **KHÔNG có IDLE → mode IDLE BỎ HẲN.** Elena chỉ có **talking** + **hidden**.
- Audio **luôn mute** khi render (hard-rule). 24fps trong comp 30fps → phát theo
  timestamp, **không time-stretch**.
- Vị trí asset đề xuất: `assets/elena/` trong job, hoặc thư mục chung kênh; Claude
  Code chọn nơi đặt và tài liệu hóa.

### 6.3 Treatment hiển thị
| Treatment | Khi nào | Kích thước (1920×1080) | Thời lượng | Asset |
|---|---|---|---|---|
| `circle` (tròn nhỏ, mặc định) | VO liên tục + nền Pexels đơn giản | đường kính 240px (220–260), góc dưới-phải, lề phải ~72 / dưới ~120 | 6–10s | TALK_NEUTRAL |
| `large` (~20%) | câu cần nhớ: hook chính, quy tắc cốt lõi, cảnh báo, kết luận | ~20% chiều rộng (~384px), bên phải | 5–8s | TALK_EMPHASIS |
| HIDDEN | nhãn/checklist/số liệu/so sánh/full-body exercise/evidence/khung dày | — | — | — |

Quy tắc 1 dòng: *Pexels đơn giản → circle; ý cần nhớ → large; evidence → hidden.*

### 6.4 Ngân sách & nhịp (guideline)
Vì mất IDLE, **thực tế Elena hiện THẤP hơn mốc 20–30%** (chủ yếu là các lần talking
ngắn). Áp: mỗi lần 5–10s; **cách nhau ≥15s**; không 2 talking liền kề; không lặp
cùng asset 2 lần liên tiếp; biến thiên **xác định theo job_id/scene index**
(reproducible). HIDDEN là mặc định.

### 6.5 Crop/mask (component)
Crop tròn/bo góc ôm mặt+ngực trên, mặt chiếm 65–75% khung; viền 4–6px; bóng mềm;
**giữ nền gốc, không tách nền**; tôn trọng safe-area; không đè subtitle.

---

## 7. REMOTION + WIRING

### 7.1 Pipeline order (DEFAULT_STAGES trong `job_state.py`)
Chèn stage mới (giữ nguyên tên/ngữ nghĩa stage cũ):
```
idea_research
script  script_promote  script_qa
scenes  scenes_promote  scenes_qa
visual_spans                 # MỚI (report_only)
seo  seo_promote  seo_qa
graphic_images               # MỚI
thumbnail_image
whisper_timestamps
visual_schedule              # MỚI
elena_plan                   # MỚI
render
render_continuity_qa         # MỚI
review
```
> Vị trí acquire Pexels (hiện trong `prepare_assets` lúc render) cần chuyển sang
> **per-span**; xác minh wiring thực tế trong `stages/assets.py` + `pipeline.py`
> trước khi sửa. Có thể tạo bước acquire trước `visual_schedule`.

### 7.2 Wiring `run_all_pipeline.py`
Thêm các nhánh stage tương tự pattern hiện có (`_record`, `_check_stop_requested`,
gate approval). Mọi stage mới phải tôn trọng `.stop_requested` và ghi job state.

### 7.3 `src/video_agent/visual/schedule.py` (MỚI, độc lập)
Compile schema-v2 (§3.3). Pure, deterministic, không acquire provider.

### 7.4 `src/video_agent/visual/elena.py` (MỚI, độc lập)
Sinh elena_cues (§3.4) từ annotation + timing; áp luật §6.

### 7.5 `remotion/src/ChannelVisualTimeline.tsx` (MỚI)
Tham chiếu `remotion/src/shorts/VisualTimeline.tsx`. Render track `background_media`
thành `<Sequence from=from_frame durationInFrames=duration_in_frames>` + lớp media
(OffthreadVideo cho video / Img cho ảnh) với trim/playback_rate/crop → clip chạy
**liền qua ranh giới scene, KHÔNG remount/reset**. `ChannelVideo.tsx` dùng nó khi
có `props.visual_schedule`.

### 7.6 `remotion/src/ChannelElenaPresenter.tsx` (MỚI)
Props: `mode, treatment, variant, size, position, src, trim`. Yêu cầu:
- `mode==="hidden"` → render null, **không mount**.
- **Luôn mute** video.
- Mask tròn (circle) / bo góc (large); crop nhất quán; viền + bóng ở cấp component.
- **Không restart** clip khi 2 cue liền kề cùng asset.
- Tôn trọng safe-area; không đè subtitle.
- Overlay debug safe-area/collision ở dev mode.
- Độc lập subtitle & B-roll component.

### 7.7 Bỏ card Remotion cho graphic
Trong `ChannelVideo.tsx`, bỏ nhánh render card cho layout `checklist/warning/quote/
cta`; thay bằng hiển thị `scene.graphic.image_ref` (ảnh ChatGPT). Subtitle thường
vẫn là text tầng 3.

### 7.8 render-props
Cập nhật `remotion/src/render-props.ts` thêm `visual_schedule` + `elena_cues`. Phía
Python (`render_props.json` cho long-form) bơm 2 trường này khi có; thiếu → bỏ qua
(fallback path cũ).

---

## 8. ROLLOUT THEO PHASE + GATE (acceptance cụ thể)

| Phase | Deliverable | Lệnh verify | PASS khi |
|---|---|---|---|
| 1a (DONE) | module `visual/` + test | `pytest tests/test_long_visual_spans.py -q` | toàn bộ test xanh |
| 1b | stage `visual_spans` (report_only) + wiring + assign ids | pytest stage + chạy trên ≥3 job thật | visual_spans.json hợp lệ, coverage PASS, **review thủ công grouping** |
| 2 | `schedule.py` + `visual_schedule` + `ChannelVisualTimeline` | clip đánh số frame; so khớp frame | không reset/thủng frame; **job cũ frame-identical**; rerender frame-identical |
| 3 | acquire Pexels per-span + fallback per-scene | A/B vs 1:1 | giảm số cắt, **KHÔNG tăng weak-match** |
| 4 | `graphic_images` + bỏ card + hiển thị ảnh | render scene graphic | ảnh đúng, đọc tốt, không vỡ layout |
| 5 | `elena.py` + `elena_plan` + `ChannelElenaPresenter` | render có Elena | mute OK; 24→30fps không giật; không đè subtitle; ngân sách §6.4; reproducible; không lặp asset liền kề |
| 6 | `render_continuity_qa` + chuyển enforced | `pytest -q` full | suite xanh; review verdict không tụt điểm visual/audio; cập nhật `.wolf/*` |

Mỗi phase: chỉ enforced sau khi report_only đã review đạt.

---

## 9. RỦI RO & GIẢM THIỂU
- Phá Shorts → code độc lập, không import/đụng shorts.
- Reset/định frame sai → Gate Phase 2 (proof frame-number), fail-closed.
- Elena giả nhép (thiếu IDLE) → chỉ talking ngắn + nhiều hidden.
- Cạn clip Pexels đủ dài cho span → fallback per-scene; max_span_sec=40 giới hạn.
- Va chạm Elena với chủ thể quan trọng → nghi ngờ thì hidden (fail-safe).
- Gen ảnh ChatGPT chậm/lỗi → cache + retry; scene fail → fallback an toàn.

---

## 10. FILES (tạo/sửa) — KHÔNG file nào thuộc shorts

**Đã tạo (Phase 1a):** `src/video_agent/visual/{__init__,config,spans}.py`,
`tests/test_long_visual_spans.py`.

**Cần tạo:**
- `src/video_agent/visual/schedule.py`, `src/video_agent/visual/elena.py`
- `src/video_agent/orchestrator/stages/` wrappers long-form: visual_spans,
  graphic_images, visual_schedule, elena_plan, render_continuity_qa
- `remotion/src/ChannelVisualTimeline.tsx`, `remotion/src/ChannelElenaPresenter.tsx`
- tests cho từng module/stage mới

**Cần sửa:**
- `src/video_agent/orchestrator/job_state.py` (DEFAULT_STAGES long-form)
- `src/video_agent/web/run_all_pipeline.py` (wiring + gate + stop-check)
- `src/video_agent/stages/assets.py` (acquire per-span)
- prompt script/scenes long-form + QA long-form (mới, không đụng shorts)
- `remotion/src/ChannelVideo.tsx`, `remotion/src/render-props.ts`
- `configs/*/channel.yaml` (`visual.span_planning`, `elena`)

---

## 11. DEFINITION OF DONE
- Tất cả stage mới chạy report_only → đã review đạt → enforced cho kênh đã PASS.
- `.venv/bin/python -m pytest -q` xanh toàn bộ.
- Video long-form: nền theo span (ít cắt vụn), graphic là ảnh ChatGPT, Elena xuất
  hiện đúng luật (talking+hidden, mute, không đè subtitle), không reset frame.
- `.wolf/anatomy.md`/`memory.md`/`cerebrum.md`/`buglog.json` được cập nhật.
- Flow Shorts KHÔNG đổi (kiểm bằng pytest shorts vẫn xanh + không diff file shorts).
