import {staticFile} from 'remotion';

export type WordSegment = {text: string; start: number; end: number};

export type SubtitleConfig = {
  enabled?: boolean;
  mode?: 'word_highlight';
  words_per_page?: number;
  max_lines?: number;
  position?: 'bottom';
  offset_sec?: number;
  font_size?: number;
  active_scale?: number;
  background_opacity?: number;
};

export type SceneLayout =
  | 'hook' | 'subtitle' | 'checklist' | 'warning' | 'quote' | 'cta'
  | 'short_hook' | 'short_pain' | 'short_tip' | 'short_checklist'
  | 'short_myth' | 'short_quote' | 'short_cta';

export type LayoutPayload = {
  title?: string;
  subtitle?: string;
  body?: string;
  bullets?: string[];
  emphasis?: string;
  cta?: string;
  cover_text?: string;
};

export type FirstFramePlan = {
  strategy?: string;
  overlay_text?: string;
  roi_target?: string;
  must_show?: string[];
  must_avoid?: string[];
};

export type CropPlan = {
  mode?: string;
  target?: string;
  scale?: number;
  anchor?: string;
  safe_area?: string;
  reason?: string;
};

export type CompiledSceneBoundary = {
  scene_id: string;
  from_frame: number;
  duration_in_frames: number;
  end_frame_exclusive: number;
  is_graphic?: boolean;
};

export type CompiledVisualTrack = {
  track_id: string;
  track_type: 'background_media';
  visual_span_id: string;
  visual_beat_id?: string | null;
  scene_ids: string[];
  asset_ref: string;
  asset_id?: string | null;
  provider?: string | null;
  render_media_kind: 'video' | 'image';
  source_media_kind: 'native_video' | 'image_backed_video' | 'native_image' | 'generated_placeholder';
  from_frame: number;
  duration_in_frames: number;
  end_frame_exclusive: number;
  trim_before_in_frames: number;
  trim_timebase_fps: number;
  trim_end_in_frames?: number | null;
  source_duration_sec?: number | null;
  playback_rate: number;
  loop_policy: 'forbid' | 'allow_if_safe';
  crop_plan?: CropPlan;
  motion_plan?: {
    name?: string;
    apply_to_native_video?: boolean;
  };
  overlay_policy?: 'scene_controlled';
  z_index?: number;
};

export type CompiledAssetSchedule = {
  schema_version: 2;
  contract_revision?: string;
  compiler_version?: number;
  short_id?: string;
  fps: number;
  timing_source?: 'tts_final' | 'scene_plan';
  scene_version?: number;
  total_duration_in_frames: number;
  scene_boundaries: CompiledSceneBoundary[];
  tracks: CompiledVisualTrack[];
};

export type Scene = {
  id: string;
  duration_sec: number;
  narration: string;
  visual_type: string;
  visual_prompt: string;
  on_screen_text: string;
  caption: string;
  motion: string;
  // background_media_kind: 'video' = real footage; 'image' = a photo-backed
  // encode (still wrapped in an .mp4 container). The renderer uses this to
  // avoid treating static photos as living video backgrounds (bug-455).
  asset_refs: {background: string; background_media_kind?: 'video' | 'image'};
  audio_offset_sec?: number;
  word_segments?: WordSegment[];
  layout?: SceneLayout;
  layout_payload?: LayoutPayload | Record<string, unknown>;
  layout_reason?: string;
  planner_warnings?: string[];
  first_frame_plan?: FirstFramePlan;
  crop_plan?: CropPlan;
  // Visual-span grouping hints (spec v3.2.3 §8/§25). Planning input only; the
  // renderer ignores these until the compiled VisualTimeline lands (PR B).
  visual_span_id?: string;
  visual_span_intent?: string;
  // Shorts ChatGPT graphic image conversion marker. These scenes keep a
  // normal short_* layout for renderer compatibility, but the generated image
  // already contains the teaching text, so ShortVideo must not add text overlays.
  generated_image_source_layout?: string;
  background_mode?: string;
  // Long-form graphic scenes (checklist/warning/quote/cta): a ChatGPT-generated
  // image replaces the Remotion card when image_ref is set (spec long-form v2 §3.1).
  graphic?: {needed?: boolean; prompt?: string; image_ref?: string};
};

export type RenderProps = {
  channel: {id: string; name: string; description: string};
  style: {
    palette: {
      background: string;
      primary: string;
      secondary: string;
      accent: string;
      text: string;
    };
  };
  render: {fps: number; resolution: string; duration_sec: number; duration_in_frames?: number; subtitles?: SubtitleConfig};
  scenes: Scene[];
  audio: {narration: string | null; music: string | null};
  seo: {
    title: string;
    description: string;
    thumbnail_path: string;
    thumbnail_text?: string;
    title_variants?: Array<{
      title: string;
      thumbnail_text: string;
      score: number;
      score_breakdown?: Record<string, unknown>;
    }>;
  };
  branding?: {
    logo_path?: string | null;
    intro_video_path?: string | null;
    outro_video_path?: string | null;
    intro_sec?: number;
    outro_sec?: number;
    watermark_enabled?: boolean;
    // Show the channel name in the top-left corner of every scene.
    // Defaults to false to keep the opening frame clean.
    show_channel_name_overlay?: boolean;
    // Hybrid graphic cards: when set, graphic scenes render the generated card
    // shrunk & centered over this fixed brand-gradient background video (a path
    // under remotion/public/). Unset → graphic cards render full-bleed (legacy).
    hybrid_card_bg?: string;
  };
  visual_schedule?: CompiledAssetSchedule | null;
};

export const mediaSrc = (path: string): string => {
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('file://')) {
    return path;
  }
  return staticFile(path.replace(/^\/+/, ''));
};

export const defaultRenderProps: RenderProps = {
  channel: {id: 'vida-plena-45', name: 'Vida Plena 45+', description: 'Demo'},
  style: {
    palette: {
      background: '#F6F1E8',
      primary: '#2F6B57',
      secondary: '#D98C5F',
      accent: '#F2C94C',
      text: '#26332F',
    },
  },
  render: {
    fps: 30,
    resolution: '1920x1080',
    duration_sec: 54,
    subtitles: {
      enabled: true,
      mode: 'word_highlight',
      words_per_page: 10,
      max_lines: 2,
      position: 'bottom',
      offset_sec: 0,
      font_size: 54,
      active_scale: 1.08,
      background_opacity: 0.58,
    },
  },
  scenes: [
    {
      id: 'scene-01',
      duration_sec: 10,
      narration: 'Demo scene',
      visual_type: 'generated_placeholder',
      visual_prompt: 'Warm wellness scene',
      on_screen_text: 'DORMIR MEJOR',
      caption: 'Demo scene',
      motion: 'slow_push',
      asset_refs: {background: staticFile('fallback.jpg')},
      layout: 'subtitle',
      layout_payload: {title: '', body: '', bullets: [], cta: ''},
      layout_reason: '',
      planner_warnings: [],
    },
  ],
  audio: {narration: null, music: null},
  seo: {title: '5 habitos nocturnos', description: 'Demo', thumbnail_path: ''},
};
