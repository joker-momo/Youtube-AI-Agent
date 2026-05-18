import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {mediaSrc, RenderProps, Scene} from './render-props';
import {fitHeadline, fullFrame, softShadow} from './styles';

const motionOffset = (motion: string): number => {
  if (motion === 'pan_right') {
    return -34;
  }
  if (motion === 'pan_left') {
    return 34;
  }
  return 12;
};

const SceneProgress: React.FC<{activeIndex: number; total: number; palette: RenderProps['style']['palette']}> = ({activeIndex, total, palette}) => (
  <div style={{position: 'absolute', left: 96, right: 96, top: 58, display: 'flex', gap: 12}}>
    {Array.from({length: total}).map((_, index) => (
      <div
        key={index}
        style={{
          height: 7,
          flex: 1,
          borderRadius: 7,
          backgroundColor: index <= activeIndex ? palette.accent : 'rgba(246,241,232,0.34)',
        }}
      />
    ))}
  </div>
);

const SceneView: React.FC<{
  scene: Scene;
  startFrame: number;
  sceneIndex: number;
  totalScenes: number;
  palette: RenderProps['style']['palette'];
  channelName: string;
}> = ({scene, startFrame, sceneIndex, totalScenes, palette, channelName}) => {
  const frame = useCurrentFrame() - startFrame;
  const scale = interpolate(frame, [0, 120], [1.02, scene.motion === 'slow_zoom' ? 1.1 : 1.055], {extrapolateRight: 'clamp'});
  const translate = interpolate(frame, [0, 120], [0, motionOffset(scene.motion)], {extrapolateRight: 'clamp'});
  const panelEnter = interpolate(frame, [0, 22], [28, 0], {extrapolateRight: 'clamp'});
  const panelOpacity = interpolate(frame, [0, 18], [0, 1], {extrapolateRight: 'clamp'});
  const headlineSize = fitHeadline(scene.on_screen_text, 76, 54);
  const sceneAccent = sceneIndex % 2 === 0 ? palette.secondary : palette.primary;
  return (
    <AbsoluteFill style={{...fullFrame, backgroundColor: palette.background}}>
      <Img
        src={mediaSrc(scene.asset_refs.background)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform: `scale(${scale}) translateX(${translate}px)`,
          opacity: 0.86,
        }}
      />
      <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(90deg, rgba(38,51,47,0.82), rgba(38,51,47,0.24) 55%, rgba(38,51,47,0.08))'}} />
      <div style={{position: 'absolute', inset: 0, background: `linear-gradient(180deg, ${sceneAccent}00, ${sceneAccent}52)`}} />
      <SceneProgress activeIndex={sceneIndex} total={totalScenes} palette={palette} />
      <div
        style={{
          position: 'absolute',
          left: 96,
          top: 142,
          width: 860,
          color: palette.background,
          opacity: panelOpacity,
          transform: `translateY(${panelEnter}px)`,
        }}
      >
        <div style={{display: 'flex', alignItems: 'center', gap: 18, marginBottom: 28}}>
          <div style={{width: 52, height: 52, borderRadius: 26, backgroundColor: palette.accent, color: palette.text, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 25, fontWeight: 900}}>
            {sceneIndex + 1}
          </div>
          <div style={{fontSize: 33, color: palette.accent, fontWeight: 800}}>{channelName}</div>
        </div>
        <div style={{fontSize: headlineSize, lineHeight: 1.04, fontWeight: 900, maxWidth: 830}}>{scene.on_screen_text}</div>
        <div style={{width: 180, height: 8, marginTop: 34, backgroundColor: palette.accent, borderRadius: 8}} />
      </div>
      <div
        style={{
          position: 'absolute',
          left: 96,
          right: 96,
          bottom: 72,
          padding: '24px 32px',
          backgroundColor: 'rgba(246,241,232,0.94)',
          color: palette.text,
          fontSize: 33,
          lineHeight: 1.25,
          borderLeft: `12px solid ${palette.accent}`,
          boxShadow: softShadow,
        }}
      >
        {scene.caption}
      </div>
    </AbsoluteFill>
  );
};

export const ChannelVideo: React.FC<RenderProps> = (props) => {
  const {fps} = useVideoConfig();
  let start = 0;
  return (
    <AbsoluteFill style={{backgroundColor: props.style.palette.background}}>
      {props.audio.narration ? <Audio src={mediaSrc(props.audio.narration)} /> : null}
      {props.scenes.map((scene, sceneIndex) => {
        const duration = Math.round(scene.duration_sec * fps);
        const startFrame = start;
        start += duration;
        return (
          <Sequence key={scene.id} from={startFrame} durationInFrames={duration}>
            <SceneView
              scene={scene}
              startFrame={startFrame}
              sceneIndex={sceneIndex}
              totalScenes={props.scenes.length}
              palette={props.style.palette}
              channelName={props.channel.name}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
