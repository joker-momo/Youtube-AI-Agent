import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, OffthreadVideo, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {WordSegment} from './render-props';
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
  const {fps} = useVideoConfig();
  const progress = frame / Math.max(totalFrames - 1, 1);

  // Animated caption: find active word segment by local scene time.
  // word_segments are rebased to scene-local time (start=0), so compare
  // against frame/fps directly — NOT audio_offset_sec + frame/fps.
  const localTimeSec = frame / fps;
  const segments = scene.word_segments ?? [];
  const activeSegmentIdx = segments.findIndex(s => localTimeSec >= s.start && localTimeSec < s.end);
  const activeSegment: WordSegment | null = activeSegmentIdx >= 0 ? segments[activeSegmentIdx] : null;

  // Between chunks: show last-passed segment text instead of falling back to
  // scene.caption to prevent abrupt text flicker on gap frames.
  const prevSegment: WordSegment | null = activeSegment
    ? null
    : ([...segments].reverse().find(s => localTimeSec >= s.end) ?? null);
  const displaySegment = activeSegment ?? prevSegment;
  const captionText = displaySegment ? displaySegment.text : scene.caption;

  // Caption chunk fade: fade in at chunk start, hold, fade out at chunk end.
  const CHUNK_FADE = 5; // frames
  let captionChunkAlpha = 1;
  if (activeSegment) {
    const chunkStartFrame = Math.round(activeSegment.start * fps);
    const chunkEndFrame   = Math.round(activeSegment.end   * fps);
    captionChunkAlpha = interpolate(
      frame,
      [chunkStartFrame, chunkStartFrame + CHUNK_FADE, chunkEndFrame - CHUNK_FADE, chunkEndFrame],
      [0, 1, 1, 0],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
    );
  } else if (prevSegment) {
    // Between chunks: dim slightly to signal "gap" without jarring text switch.
    captionChunkAlpha = 0.65;
  }

  const opacity       = interpolate(frame, [0, FADE_IN], [0, 1], {extrapolateRight: 'clamp'});
  const headlineY     = interpolate(frame, [4, FADE_IN + 8], [20, 0], {extrapolateRight: 'clamp'});
  const headlineAlpha = interpolate(frame, [4, FADE_IN + 8], [0, 1], {extrapolateRight: 'clamp'});
  const captionAlpha  = interpolate(frame, [FADE_IN, FADE_IN + 14], [0, 1], {extrapolateRight: 'clamp'});

  const headlineSize = fitHeadline(scene.on_screen_text, 84, 52);

  return (
    <AbsoluteFill style={{...fullFrame, opacity}}>

      {/* Full-bleed background — video clip or photo */}
      {scene.asset_refs.background.endsWith('.mp4') ? (
        // OffthreadVideo is preferred over Video for server-side rendering (faster, frame-accurate).
        // loop: short Pexels clips (3-30s) repeat to fill the full scene duration.
        // No pan/zoom transform — the video itself has natural motion.
        <OffthreadVideo
          src={mediaSrc(scene.asset_refs.background)}
          muted
          loop
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
        opacity: captionAlpha * (displaySegment ? captionChunkAlpha : 1),
      }}>
        <div style={{width: 5, minHeight: 44, backgroundColor: palette.accent, borderRadius: 3, flexShrink: 0, marginTop: 4}} />
        <div style={{
          fontSize: 31, fontWeight: 500, lineHeight: 1.35, color: 'rgba(255,255,255,0.93)',
          textShadow: '0 1px 14px rgba(0,0,0,0.95)',
          maxWidth: 1080,
        }}>
          {captionText}
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
