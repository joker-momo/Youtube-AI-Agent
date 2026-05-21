import {staticFile} from 'remotion';

export type WordSegment = {text: string; start: number; end: number};

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
  render: {fps: number; resolution: string; duration_sec: number};
  scenes: Scene[];
  audio: {narration: string | null; music: string | null};
  seo: {title: string; description: string; thumbnail_path: string};
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
  render: {fps: 30, resolution: '1920x1080', duration_sec: 54},
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
    },
  ],
  audio: {narration: null, music: null},
  seo: {title: '5 habitos nocturnos', description: 'Demo', thumbnail_path: ''},
};
