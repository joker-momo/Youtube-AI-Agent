import {staticFile} from 'remotion';
import type {GraphicAnyPayload} from './graphics/graphic-payloads';

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

export type GraphicLayout =
  | 'graphic_plate_ratio'
  | 'graphic_checklist'
  | 'graphic_step_list'
  | 'graphic_label_callout'
  | 'graphic_comparison'
  | 'graphic_routine_split';

export type SceneLayout =
  | 'hook' | 'subtitle' | 'checklist' | 'warning' | 'quote' | 'cta'
  | 'short_hook' | 'short_pain' | 'short_tip' | 'short_checklist'
  | 'short_myth' | 'short_quote' | 'short_cta'
  // MVP graphic layouts (spec v7 §3.2) — appended, existing values unchanged.
  | GraphicLayout;

export type LayoutPayload = {
  title?: string;
  subtitle?: string;
  body?: string;
  bullets?: string[];
  emphasis?: string;
  cta?: string;
  cover_text?: string;
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
  asset_refs: {background: string};
  audio_offset_sec?: number;
  word_segments?: WordSegment[];
  layout?: SceneLayout;
  // Stock layouts use LayoutPayload; graphic_* layouts carry a graphic payload.
  layout_payload?: LayoutPayload | GraphicAnyPayload;
  layout_reason?: string;
  planner_warnings?: string[];
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
  render: {fps: number; resolution: string; duration_sec: number; subtitles?: SubtitleConfig};
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
  };
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
