import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, Sequence, useCurrentFrame, useVideoConfig, Video} from 'remotion';
import {mediaSrc, RenderProps, Scene} from './render-props';
import {fitHeadline, fullFrame} from './styles';

const FADE_IN = 18; // 0.6 s fade-in per scene

const motionTransform = (motion: string, progress: number) => {
  const s = motion === 'slow_zoom'
    ? interpolate(progress, [0, 1], [1.0, 1.12], {extrapolateRight: 'clamp'})
    : interpolate(progress, [0, 1], [1.02, 1.07], {extrapolateRight: 'clamp'});
  const x = motion === 'pan_right'
    ? interpolate(progress, [0, 1], [0, -28], {extrapolateRight: 'clamp'})
    : motion === 'pan_left'
    ? interpolate(progress, [0, 1], [0, 28], {extrapolateRight: 'clamp'})
    : 0;
  return `scale(${s}) translateX(${x}px)`;
};

const ProgressBar: React.FC<{active: number; total: number; accent: string}> = ({active, total, accent}) => (
  <div style={{position: 'absolute', left: 0, right: 0, top: 0, display: 'flex', height: 4}}>
    {Array.from({length: total}).map((_, i) => (
      <div key={i} style={{flex: 1, height: 4, margin: '0 1.5px', backgroundColor: i <= active ? accent : 'rgba(255,255,255,0.22)'}} />
    ))}
  </div>
);

const SceneView: React.FC<{
  scene: Scene;
  totalFrames: number;
  sceneIndex: number;
  totalScenes: number;
  palette: RenderProps['style']['palette'];
  channelName: string;
}> = ({scene, totalFrames, sceneIndex, totalScenes, palette, channelName}) => {
  const frame = useCurrentFrame();
  const progress = frame / Math.max(totalFrames - 1, 1);

  const opacity       = interpolate(frame, [0, FADE_IN], [0, 1], {extrapolateRight: 'clamp'});
  const headlineY     = interpolate(frame, [4, FADE_IN + 8], [20, 0], {extrapolateRight: 'clamp'});
  const headlineAlpha = interpolate(frame, [4, FADE_IN + 8], [0, 1], {extrapolateRight: 'clamp'});
  const captionAlpha  = interpolate(frame, [FADE_IN, FADE_IN + 14], [0, 1], {extrapolateRight: 'clamp'});

  const headlineSize = fitHeadline(scene.on_screen_text, 84, 52);

  return (
    <AbsoluteFill style={{...fullFrame, opacity}}>

      {/* Full-bleed background — video clip or photo */}
      {scene.asset_refs.background.endsWith('.mp4') ? (
        <Video
          src={mediaSrc(scene.asset_refs.background)}
          muted
          style={{
            position: 'absolute', width: '100%', height: '100%',
            objectFit: 'cover',
          }}
        />
      ) : (
        <Img
          src={mediaSrc(scene.asset_refs.background)}
          style={{
            position: 'absolute', width: '100%', height: '100%',
            objectFit: 'cover',
            transform: motionTransform(scene.motion, progress),
          }}
        />
      )}

      {/* Bottom gradient — only bottom 40% darkens, top stays natural */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(to top, rgba(10,16,13,0.88) 0%, rgba(10,16,13,0.5) 30%, transparent 58%)',
      }} />

      {/* Left vignette — subtle, helps text contrast */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(to right, rgba(10,16,13,0.42) 0%, transparent 52%)',
      }} />

      {/* Progress bar */}
      <ProgressBar active={sceneIndex} total={totalScenes} accent={palette.accent} />

      {/* Channel name — small, top-left */}
      <div style={{
        position: 'absolute', top: 24, left: 56,
        fontSize: 21, fontWeight: 800, letterSpacing: 2.2, textTransform: 'uppercase',
        color: palette.accent,
        textShadow: '0 1px 8px rgba(0,0,0,0.7)',
        opacity: headlineAlpha,
      }}>
        {channelName}
      </div>

      {/* Headline block — bottom-left, above caption */}
      <div style={{
        position: 'absolute', left: 56, bottom: 190, width: 920,
        opacity: headlineAlpha, transform: `translateY(${headlineY}px)`,
      }}>
        <div style={{width: 48, height: 5, backgroundColor: palette.accent, borderRadius: 3, marginBottom: 18}} />
        <div style={{
          fontSize: headlineSize, fontWeight: 900, lineHeight: 1.06, color: '#FFFFFF',
          textShadow: '0 2px 24px rgba(0,0,0,0.85), 0 1px 6px rgba(0,0,0,0.95)',
          letterSpacing: -0.5,
        }}>
          {scene.on_screen_text}
        </div>
      </div>

      {/* Caption — lower-third, no box, text + left accent bar */}
      <div style={{
        position: 'absolute', left: 56, right: 80, bottom: 58,
        display: 'flex', alignItems: 'flex-start', gap: 18,
        opacity: captionAlpha,
      }}>
        <div style={{width: 5, minHeight: 44, backgroundColor: palette.accent, borderRadius: 3, flexShrink: 0, marginTop: 4}} />
        <div style={{
          fontSize: 31, fontWeight: 500, lineHeight: 1.35, color: 'rgba(255,255,255,0.93)',
          textShadow: '0 1px 14px rgba(0,0,0,0.95)',
          maxWidth: 1080,
        }}>
          {scene.caption}
        </div>
      </div>

    </AbsoluteFill>
  );
};

export const ChannelVideo: React.FC<RenderProps> = (props) => {
  const {fps} = useVideoConfig();
  let start = 0;
  return (
    <AbsoluteFill style={{backgroundColor: '#0C100D'}}>
      {props.audio.narration ? <Audio src={mediaSrc(props.audio.narration)} /> : null}
      {props.scenes.map((scene, i) => {
        const totalFrames = Math.round(scene.duration_sec * fps);
        const from = start;
        start += totalFrames;
        return (
          <Sequence key={scene.id} from={from} durationInFrames={totalFrames}>
            <SceneView
              scene={scene}
              totalFrames={totalFrames}
              sceneIndex={i}
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
