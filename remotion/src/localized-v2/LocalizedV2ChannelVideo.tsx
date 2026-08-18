import React from 'react';
import {AbsoluteFill, Sequence, useVideoConfig} from 'remotion';
import {Video as MediaVideo} from '@remotion/media';
import {ChannelVideo} from '../ChannelVideo';
import {mediaSrc, RenderProps} from '../render-props';
import {ensureLocaleFont, localeFontFamily} from './loadFonts';
import {
  assertLocalizedV2Props,
  LocalizedV2RenderProps,
} from './types';

export const localizedTimeline = (
  props: LocalizedV2RenderProps,
  fps: number,
) => {
  const introFrames = Math.round(props.branding.intro_sec * fps);
  const disclaimerFrames = Math.round(props.branding.disclaimer_sec * fps);
  const contentFrames = props.scenes.reduce(
    (total, scene) => total + Math.round(scene.duration_sec * fps),
    0,
  );
  const outroFrames = Math.round(props.branding.outro_sec * fps);
  return {
    introFrames,
    disclaimerFrames,
    contentFrom: introFrames + disclaimerFrames,
    contentFrames,
    outroFrom: introFrames + disclaimerFrames + contentFrames,
    outroFrames,
  };
};

export const LocalizedV2ChannelVideo: React.FC<LocalizedV2RenderProps> = (
  rawProps,
) => {
  const props = assertLocalizedV2Props(rawProps);
  ensureLocaleFont(
    props.locale,
    [
      props.channel.name,
      ...props.scenes.flatMap((scene) => [
        scene.on_screen_text,
        scene.caption,
      ]),
    ].join(' '),
  );
  const {fps} = useVideoConfig();
  const {outroFrames, outroFrom} = localizedTimeline(props, fps);
  const stableProps: RenderProps = {
    ...props,
    branding: {
      ...props.branding,
      outro_video_path: null,
      outro_sec: 0,
    },
  };
  const family = localeFontFamily(props.locale);
  return (
    <AbsoluteFill className={`localized-v2 locale-${props.locale}`}>
      <style>
        {`.localized-v2.locale-${props.locale} * { font-family: ${family} !important; }`}
      </style>
      <ChannelVideo {...stableProps} />
      <Sequence from={outroFrom} durationInFrames={outroFrames}>
        <MediaVideo
          src={mediaSrc(props.branding.outro_video_path)}
          style={{
            position: 'absolute',
            width: '100%',
            height: '100%',
            objectFit: 'cover',
          }}
        />
      </Sequence>
    </AbsoluteFill>
  );
};
