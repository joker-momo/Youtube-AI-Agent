import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {EndEngagementCue} from './EndEngagementCue';

export type InfographicShortProps = {
  poster: string;
  audio: string;
  music?: string;
  channelName?: string;
  durationInFrames: number;
  kenBurns?: boolean;
  kenBurnsScaleMax?: number;
  /** Legacy alias: previously a full-video banner; now enables the end cue. */
  showSubscribeCue?: boolean;
  showEngagementCue?: boolean;
  engagementCueDurationSec?: number;
};

/** Pure timing helper: first frame of the final engagement cue. */
export const engagementCueStartFrame = (durationInFrames: number, cueFrames: number): number =>
  Math.max(0, durationInFrames - cueFrames);

export const InfographicShort: React.FC<InfographicShortProps> = ({
  poster, audio, music, channelName, durationInFrames,
  kenBurns = true, kenBurnsScaleMax = 1.02,
  showSubscribeCue = false, showEngagementCue, engagementCueDurationSec = 4.0,
}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  // Hard cap the zoom at 1.02: baked-in poster text must never be cropped.
  const maxScale = Math.min(kenBurnsScaleMax, 1.02);
  const scale = kenBurns ? interpolate(frame, [0, durationInFrames], [1, maxScale], {extrapolateRight: 'clamp'}) : 1;

  // Final-3s Like/Subscribe cue. showSubscribeCue is a legacy alias for the end
  // cue — it must never render a whole-video banner again.
  const cueEnabled = showEngagementCue ?? showSubscribeCue;
  const cueFrames = Math.min(
    durationInFrames,
    Math.max(0, Math.round(engagementCueDurationSec * fps)),
  );
  const cueStart = engagementCueStartFrame(durationInFrames, cueFrames);

  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
        <Img src={staticFile(poster)} style={{width, height, objectFit: 'contain', transform: `scale(${scale})`}} />
      </AbsoluteFill>
      {/* The cue exists ONLY in the final seconds: the poster carries all text,
          so nothing may cover it earlier (safe-area rule). */}
      {cueEnabled && cueFrames > 0 && (
        <Sequence from={cueStart} durationInFrames={cueFrames} name="EndEngagementCue">
          <EndEngagementCue channelName={channelName} />
        </Sequence>
      )}
      <Audio src={staticFile(audio)} />
      {music ? <Audio src={staticFile(`music/${music}.mp3`)} volume={0.12} /> : null}
    </AbsoluteFill>
  );
};
