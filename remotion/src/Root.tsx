import React from 'react';
import {Composition} from 'remotion';
import {ChannelVideo} from './ChannelVideo';
import {ShortVideo} from './ShortVideo';
import {ShortCover} from './ShortCover';
import {InfographicShort} from './shorts/InfographicShort';
import {defaultRenderProps, RenderProps} from './render-props';
// Side-effect import: registers webfonts via delayRender so Shorts
// compositions render with the correct typeface from frame 0.
import './shorts/loadFonts';

// Shorts metadata: the scene layer starts at frame 0, so the compiled schedule
// total IS the whole composition length (preferred). Unchanged contract.
const calculateVideoMetadata = ({props}: {props: RenderProps}) => {
  const fps = props.render?.fps ?? defaultRenderProps.render.fps;
  const duration_sec = props.render?.duration_sec ?? defaultRenderProps.render.duration_sec;
  const durationInFrames =
    props.visual_schedule?.total_duration_in_frames ??
    props.render?.duration_in_frames ??
    Math.max(1, Math.round(duration_sec * fps));
  const resStr = props.render?.resolution || defaultRenderProps.render.resolution;
  const parts = resStr.split('x');
  const width = parseInt(parts[0] || '1920', 10);
  const height = parseInt(parts[1] || '1080', 10);
  return {
    fps,
    durationInFrames,
    width,
    height,
  };
};

// Long-form metadata: ChannelVideo shifts the scene layer by introFrames and
// appends an outro, so the composition length is intro + scenes + outro. The
// pipeline pins that as render.duration_in_frames; the scenes-only
// visual_schedule.total_duration_in_frames must NOT be used here (it would cut
// the intro shift + the entire outro).
const calculateChannelMetadata = ({props}: {props: RenderProps}) => {
  const fps = props.render?.fps ?? defaultRenderProps.render.fps;
  const duration_sec = props.render?.duration_sec ?? defaultRenderProps.render.duration_sec;
  const durationInFrames =
    props.render?.duration_in_frames ??
    Math.max(1, Math.round(duration_sec * fps));
  const resStr = props.render?.resolution || defaultRenderProps.render.resolution;
  const parts = resStr.split('x');
  const width = parseInt(parts[0] || '1920', 10);
  const height = parseInt(parts[1] || '1080', 10);
  return {
    fps,
    durationInFrames,
    width,
    height,
  };
};

export const Root: React.FC = () => {
  const fps = defaultRenderProps.render.fps;
  return (
    <>
      <Composition
        id="ChannelVideoStandard"
        component={ChannelVideo}
        durationInFrames={Math.round(defaultRenderProps.render.duration_sec * fps)}
        fps={fps}
        width={1920}
        height={1080}
        defaultProps={defaultRenderProps}
        calculateMetadata={calculateChannelMetadata}
      />
      <Composition
        id="ShortVideoStandard"
        component={ShortVideo}
        durationInFrames={Math.round(defaultRenderProps.render.duration_sec * fps)}
        fps={fps}
        width={1080}
        height={1920}
        defaultProps={defaultRenderProps}
        calculateMetadata={calculateVideoMetadata}
      />
      <Composition
        id="InfographicShort"
        component={InfographicShort}
        durationInFrames={900}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{poster: '', audio: '', durationInFrames: 900}}
        calculateMetadata={({props}) => ({
          durationInFrames: Math.max(1, Math.round(props.durationInFrames || 900)),
        })}
      />
      <Composition
        id="ShortCover"
        component={ShortCover}
        durationInFrames={1}
        fps={fps}
        width={1080}
        height={1920}
        defaultProps={defaultRenderProps}
      />
    </>
  );
};
