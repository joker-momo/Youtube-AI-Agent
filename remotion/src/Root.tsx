import React from 'react';
import {Composition} from 'remotion';
import {ChannelVideo} from './ChannelVideo';
import {Thumbnail} from './Thumbnail';
import {defaultRenderProps} from './render-props';

export const Root: React.FC = () => {
  const fps = defaultRenderProps.render.fps;
  const durationInFrames = defaultRenderProps.render.duration_sec * fps;
  return (
    <>
      <Composition
        id="ChannelVideoStandard"
        component={ChannelVideo}
        durationInFrames={durationInFrames}
        fps={fps}
        width={1920}
        height={1080}
        defaultProps={defaultRenderProps}
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
    </>
  );
};
