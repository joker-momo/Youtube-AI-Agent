import React from 'react';
import {CalculateMetadataFunction, Composition} from 'remotion';
import {LocalizedV2ChannelVideo} from './LocalizedV2ChannelVideo';
import {
  assertLocalizedV2Props,
  LocalizedV2RenderProps,
} from './types';
import './loadFonts';

const unavailable = 'localized-v2/unconfigured-media';
export const defaultLocalizedV2Props: LocalizedV2RenderProps = {
  schemaVersion: 'localized-render-props-v2/v1',
  locale: 'en-US',
  composition: 'LocalizedV2ChannelVideo',
  channel: {id: 'localized-v2-unconfigured', name: 'Unconfigured', description: ''},
  style: {
    palette: {
      background: '#F4F1EA',
      primary: '#315C52',
      secondary: '#B8754F',
      accent: '#E2B84A',
      text: '#23312D',
    },
  },
  render: {
    fps: 30,
    resolution: '1920x1080',
    duration_sec: 4,
    duration_in_frames: 120,
    subtitles: {enabled: false},
  },
  scenes: [{
    id: 'unconfigured',
    duration_sec: 1,
    narration: 'Unconfigured localized V2 composition.',
    visual_type: 'video',
    visual_prompt: 'unconfigured',
    on_screen_text: '',
    caption: '',
    motion: 'slow_push',
    asset_refs: {
      background: `${unavailable}.mp4`,
      background_media_kind: 'video',
    },
  }],
  audio: {narration: `${unavailable}.wav`, music: null},
  seo: {
    title: 'Unconfigured',
    description: 'Unconfigured',
    thumbnail_path: `${unavailable}.png`,
  },
  branding: {
    intro_video_path: `${unavailable}.mp4`,
    disclaimer_video_path: `${unavailable}.mp4`,
    outro_video_path: `${unavailable}.mp4`,
    intro_sec: 1,
    disclaimer_sec: 1,
    outro_sec: 1,
    hybrid_card_bg: `${unavailable}.mp4`,
  },
};

export const calculateLocalizedV2Metadata: CalculateMetadataFunction<
  LocalizedV2RenderProps
> = ({props}) => {
  const valid = assertLocalizedV2Props(props);
  const [widthRaw, heightRaw] = valid.render.resolution.split('x');
  const width = Number.parseInt(widthRaw ?? '', 10);
  const height = Number.parseInt(heightRaw ?? '', 10);
  if (!Number.isInteger(width) || !Number.isInteger(height)) {
    throw new Error('Localized V2 resolution is invalid');
  }
  return {
    fps: valid.render.fps,
    durationInFrames: valid.render.duration_in_frames ?? Math.max(
      1,
      Math.round(valid.render.duration_sec * valid.render.fps),
    ),
    width,
    height,
  };
};

export const LocalizedV2Root: React.FC = () => (
  <Composition
    id="LocalizedV2ChannelVideo"
    component={LocalizedV2ChannelVideo}
    durationInFrames={1}
    fps={30}
    width={1920}
    height={1080}
    defaultProps={defaultLocalizedV2Props}
    calculateMetadata={calculateLocalizedV2Metadata}
  />
);
