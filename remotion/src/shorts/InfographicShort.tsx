import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

export type InfographicShortProps = {
  poster: string;
  audio: string;
  music?: string;
  channelName?: string;
  durationInFrames: number;
  kenBurns?: boolean;
  kenBurnsScaleMax?: number;
  showSubscribeCue?: boolean;
};

export const InfographicShort: React.FC<InfographicShortProps> = ({
  poster, audio, music, channelName, durationInFrames,
  kenBurns = true, kenBurnsScaleMax = 1.02, showSubscribeCue = false,
}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  // Hard cap the zoom at 1.02: baked-in poster text must never be cropped.
  const maxScale = Math.min(kenBurnsScaleMax, 1.02);
  const scale = kenBurns ? interpolate(frame, [0, durationInFrames], [1, maxScale], {extrapolateRight: 'clamp'}) : 1;
  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
        <Img src={staticFile(poster)} style={{width, height, objectFit: 'contain', transform: `scale(${scale})`}} />
      </AbsoluteFill>
      {/* Overlays are OFF by default (safe area): the poster already carries all text.
          They render only when explicitly enabled via showSubscribeCue. */}
      {showSubscribeCue && (
        <AbsoluteFill style={{alignItems: 'center', top: 24, height: 60}}>
          <div style={{color: '#fff', fontWeight: 800, fontSize: 40, background: '#E11D2A', padding: '6px 22px', borderRadius: 10}}>
            SUSCRÍBETE
          </div>
        </AbsoluteFill>
      )}
      {showSubscribeCue && channelName && (
        <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', bottom: 24}}>
          <div style={{color: '#fff', fontWeight: 700, fontSize: 30, textShadow: '0 2px 6px #000'}}>{channelName}</div>
        </AbsoluteFill>
      )}
      <Audio src={staticFile(audio)} />
      {music ? <Audio src={staticFile(`music/${music}.mp3`)} volume={0.12} /> : null}
    </AbsoluteFill>
  );
};
