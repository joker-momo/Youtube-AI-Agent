import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {mediaSrc, RenderProps, Scene} from './render-props';
import {fullFrame} from './styles';

const SceneView: React.FC<{scene: Scene; startFrame: number; palette: RenderProps['style']['palette']}> = ({scene, startFrame, palette}) => {
  const frame = useCurrentFrame() - startFrame;
  const scale = interpolate(frame, [0, 90], [1, 1.04], {extrapolateRight: 'clamp'});
  const translate = interpolate(frame, [0, 90], [0, scene.motion === 'pan_right' ? -28 : 28], {extrapolateRight: 'clamp'});
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
      <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(90deg, rgba(38,51,47,0.74), rgba(38,51,47,0.12))'}} />
      <div style={{position: 'absolute', left: 96, top: 150, width: 820, color: palette.background}}>
        <div style={{fontSize: 34, marginBottom: 26, color: palette.accent, fontWeight: 700}}>{'Vida Plena 45+'}</div>
        <div style={{fontSize: 78, lineHeight: 1.05, fontWeight: 800}}>{scene.on_screen_text}</div>
      </div>
      <div style={{position: 'absolute', left: 96, right: 96, bottom: 72, padding: '24px 32px', backgroundColor: 'rgba(246,241,232,0.92)', color: palette.text, fontSize: 34, lineHeight: 1.25}}>
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
      {props.scenes.map((scene) => {
        const duration = Math.round(scene.duration_sec * fps);
        const startFrame = start;
        start += duration;
        return (
          <Sequence key={scene.id} from={startFrame} durationInFrames={duration}>
            <SceneView scene={scene} startFrame={startFrame} palette={props.style.palette} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
