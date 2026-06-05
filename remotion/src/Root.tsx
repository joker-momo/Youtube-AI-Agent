import React from 'react';
import {Composition} from 'remotion';
import {ChannelVideo} from './ChannelVideo';
import {Thumbnail} from './Thumbnail';
import {ShortVideo} from './ShortVideo';
import {ShortCover} from './ShortCover';
import {defaultRenderProps, RenderProps} from './render-props';
import graphicMvpShortPropsJson from '../test-props/graphic-mvp-short.json';
import graphicKitPhase15PropsJson from '../test-props/graphic-kit-phase15-short.json';
// Side-effect import: registers webfonts via delayRender so Shorts
// compositions render with the correct typeface from frame 0.
import './shorts/loadFonts';

const graphicMvpShortProps = graphicMvpShortPropsJson as unknown as RenderProps;
const graphicKitPhase15Props = graphicKitPhase15PropsJson as unknown as RenderProps;

const calculateVideoMetadata = ({props}: {props: RenderProps}) => {
  const fps = props.render?.fps ?? defaultRenderProps.render.fps;
  const duration_sec = props.render?.duration_sec ?? defaultRenderProps.render.duration_sec;
  const resStr = props.render?.resolution || defaultRenderProps.render.resolution;
  const parts = resStr.split('x');
  const width = parseInt(parts[0] || '1920', 10);
  const height = parseInt(parts[1] || '1080', 10);
  return {
    fps,
    durationInFrames: Math.max(1, Math.round(duration_sec * fps)),
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
        calculateMetadata={calculateVideoMetadata}
      />
      <Composition
        id="ThumbnailStandard"
        component={Thumbnail}
        durationInFrames={1}
        fps={fps}
        width={1280}
        height={720}
        defaultProps={defaultRenderProps}
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
        id="ShortCover"
        component={ShortCover}
        durationInFrames={1}
        fps={fps}
        width={1080}
        height={1920}
        defaultProps={defaultRenderProps}
      />
      {/* Spec v7 §15 — preview for the Shorts graphic MVP (stock + 3 graphics).
          Reuses the repo's render-props-based metadata; duration comes from
          test-props render.duration_sec, not a hardcoded frame count. */}
      <Composition
        id="GraphicMvpPreview"
        component={ShortVideo}
        durationInFrames={Math.round(
          graphicMvpShortProps.render.duration_sec * graphicMvpShortProps.render.fps,
        )}
        fps={graphicMvpShortProps.render.fps}
        width={1080}
        height={1920}
        defaultProps={graphicMvpShortProps}
        calculateMetadata={calculateVideoMetadata}
      />
      <Composition
        id="GraphicKitPhase15Preview"
        component={ShortVideo}
        durationInFrames={Math.round(
          graphicKitPhase15Props.render.duration_sec * graphicKitPhase15Props.render.fps,
        )}
        fps={graphicKitPhase15Props.render.fps}
        width={1080}
        height={1920}
        defaultProps={graphicKitPhase15Props}
        calculateMetadata={calculateVideoMetadata}
      />
    </>
  );
};
