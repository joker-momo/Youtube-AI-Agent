import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, OffthreadVideo, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {WordSegment} from './render-props';
import {mediaSrc, RenderProps, Scene} from './render-props';
import {fitHeadline, fullFrame} from './styles';

const FADE_IN = 18; // 0.6 s fade-in per scene
const BRIDGE_FRAMES = 18; // 0.6 s bridge transition between intro/main/outro

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

const LogoWatermark: React.FC<{logoPath: string}> = ({logoPath}) => (
  <div style={{position: 'absolute', top: 24, right: 28, zIndex: 20}}>
    <Img
      src={mediaSrc(logoPath)}
      style={{
        height: 78,
        width: 'auto',
        objectFit: 'contain',
        filter: 'drop-shadow(0 8px 18px rgba(0,0,0,0.55))',
        opacity: 0.95,
      }}
    />
  </div>
);

const BrandCard: React.FC<{
  logoPath: string;
  title: string;
  subtitle: string;
  palette: RenderProps['style']['palette'];
}> = ({logoPath, title, subtitle, palette}) => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, 18, 48], [0.92, 1.0, 1.03], {extrapolateRight: 'clamp'});
  const alpha = interpolate(frame, [0, 10], [0, 1], {extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{...fullFrame, background: `radial-gradient(circle at 50% 38%, ${palette.primary} 0%, #0C100D 78%)`}}>
      <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(135deg, rgba(255,255,255,0.05), transparent 45%)'}} />
      <div
        style={{
          margin: 'auto',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 24,
          transform: `scale(${scale})`,
          opacity: alpha,
        }}
      >
        <Img src={mediaSrc(logoPath)} style={{height: 280, width: 'auto', objectFit: 'contain', filter: 'drop-shadow(0 10px 30px rgba(0,0,0,0.6))'}} />
        <div style={{color: '#fff', fontSize: 64, fontWeight: 900, letterSpacing: 1, textAlign: 'center'}}>{title}</div>
        <div style={{color: palette.accent, fontSize: 34, fontWeight: 700, textAlign: 'center'}}>{subtitle}</div>
      </div>
    </AbsoluteFill>
  );
};

const BridgeFade: React.FC<{mode: 'out' | 'in'}> = ({mode}) => {
  const frame = useCurrentFrame();
  const alpha = mode === 'out'
    ? interpolate(frame, [0, BRIDGE_FRAMES - 1], [0, 1], {extrapolateRight: 'clamp'})
    : interpolate(frame, [0, BRIDGE_FRAMES - 1], [1, 0], {extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill
      style={{
        background: '#0C100D',
        opacity: alpha,
        pointerEvents: 'none',
      }}
    />
  );
};

const SceneView: React.FC<{
  scene: Scene;
  totalFrames: number;
  sceneIndex: number;
  totalScenes: number;
  palette: RenderProps['style']['palette'];
  channelName: string;
  logoPath?: string | null;
}> = ({scene, totalFrames, sceneIndex, totalScenes, palette, channelName, logoPath}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const progress = frame / Math.max(totalFrames - 1, 1);

  // Animated caption: find active word segment by local scene time.
  // word_segments are rebased to scene-local time (start=0), so compare
  // against frame/fps directly — NOT audio_offset_sec + frame/fps.
  const localTimeSec = frame / fps;
  const segments = (scene.word_segments ?? [])
    .filter((s) => Number.isFinite(s.start) && Number.isFinite(s.end) && s.end > s.start)
    .map((s) => ({
      ...s,
      start: Math.max(0, s.start),
      end: Math.max(0.001, s.end),
    }))
    .sort((a, b) => a.start - b.start);
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
    const safeStart = Math.max(0, chunkStartFrame);
    const safeEnd = Math.max(safeStart + 1, chunkEndFrame);
    const chunkFrames = Math.max(1, safeEnd - safeStart);
    const fadeFrames = Math.min(CHUNK_FADE, Math.max(1, Math.floor(chunkFrames / 2)));
    const fadeInEnd = safeStart + fadeFrames;
    const fadeOutStart = Math.max(fadeInEnd, safeEnd - fadeFrames);
    if (frame <= safeStart) {
      captionChunkAlpha = 0;
    } else if (frame < fadeInEnd) {
      captionChunkAlpha = (frame - safeStart) / Math.max(1, fadeInEnd - safeStart);
    } else if (frame <= fadeOutStart) {
      captionChunkAlpha = 1;
    } else if (frame < safeEnd) {
      captionChunkAlpha = 1 - (frame - fadeOutStart) / Math.max(1, safeEnd - fadeOutStart);
    } else {
      captionChunkAlpha = 0;
    }
  } else if (prevSegment) {
    // Between chunks: dim slightly to signal "gap" without jarring text switch.
    captionChunkAlpha = 0.65;
  }

  const opacity       = interpolate(frame, [0, FADE_IN], [0, 1], {extrapolateRight: 'clamp'});
  const headlineY     = interpolate(frame, [4, FADE_IN + 8], [20, 0], {extrapolateRight: 'clamp'});
  const headlineAlpha = interpolate(frame, [4, FADE_IN + 8], [0, 1], {extrapolateRight: 'clamp'});
  const captionAlpha  = interpolate(frame, [FADE_IN, FADE_IN + 14], [0, 1], {extrapolateRight: 'clamp'});

  const layoutVariant = sceneIndex % 3;
  const headlineLeft = layoutVariant === 2 ? undefined : 56;
  const headlineRight = layoutVariant === 2 ? 56 : undefined;
  const headlineCenter = layoutVariant === 1;
  const headlineBottom = layoutVariant === 1 ? 200 : 190;
  const captionLeft = layoutVariant === 2 ? 80 : 56;
  const captionRight = layoutVariant === 2 ? 56 : 80;

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
        fontSize: 14, fontWeight: 700, letterSpacing: 1.8, textTransform: 'uppercase',
        color: '#F2F4EF',
        textShadow: '0 1px 6px rgba(0,0,0,0.55)',
        opacity: headlineAlpha * 0.55,
      }}>
        {channelName}
      </div>

      {logoPath ? <LogoWatermark logoPath={logoPath} /> : null}

      {/* Headline block — bottom-left, above caption */}
      <div style={{
        position: 'absolute',
        left: headlineLeft,
        right: headlineRight,
        bottom: headlineBottom,
        width: headlineCenter ? 980 : 920,
        marginLeft: headlineCenter ? 'auto' : undefined,
        marginRight: headlineCenter ? 'auto' : undefined,
        textAlign: headlineCenter ? 'center' : (layoutVariant === 2 ? 'right' : 'left'),
        opacity: headlineAlpha, transform: `translateY(${headlineY}px)`,
      }}>
        <div style={{
          width: 48,
          height: 5,
          backgroundColor: palette.accent,
          borderRadius: 3,
          marginBottom: 18,
          marginLeft: headlineCenter ? 'auto' : (layoutVariant === 2 ? 'auto' : 0),
          marginRight: layoutVariant === 2 ? 0 : undefined,
        }} />
        <div style={{
          fontSize: fitHeadline(scene.on_screen_text, 72, 56), fontWeight: 800, lineHeight: 1.08, color: '#FFFFFF',
          textShadow: '0 2px 12px rgba(0,0,0,0.7), 0 1px 5px rgba(0,0,0,0.85)',
          letterSpacing: -0.5,
        }}>
          {scene.on_screen_text}
        </div>
      </div>

      {/* Caption — lower-third, no box, text + left accent bar */}
      <div style={{
        position: 'absolute', left: captionLeft, right: captionRight, bottom: 58,
        display: 'flex', alignItems: 'flex-start', gap: 18,
        opacity: captionAlpha * (displaySegment ? captionChunkAlpha : 1),
      }}>
        <div style={{width: 5, minHeight: 44, backgroundColor: palette.accent, borderRadius: 3, flexShrink: 0, marginTop: 4}} />
        <div style={{
          fontSize: 38, fontWeight: 600, lineHeight: 1.34, color: 'rgba(255,255,255,0.93)',
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
  const logoPath = props.branding?.logo_path ?? null;
  const introVideoPath = props.branding?.intro_video_path ?? null;
  const outroVideoPath = props.branding?.outro_video_path ?? null;
  const introFrames = Math.max(0, Math.round((props.branding?.intro_sec ?? 0) * fps));
  const outroFrames = Math.max(0, Math.round((props.branding?.outro_sec ?? 0) * fps));
  let start = introFrames;
  const totalSceneFrames = props.scenes.reduce((acc, s) => acc + Math.round(s.duration_sec * fps), 0);
  const outroFrom = start + totalSceneFrames;
  return (
    <AbsoluteFill style={{backgroundColor: '#0C100D'}}>
      {props.audio.narration ? (
        <Sequence from={introFrames}>
          <Audio src={mediaSrc(props.audio.narration)} />
        </Sequence>
      ) : null}
      {introVideoPath && introFrames > 0 ? (
        <Sequence from={0} durationInFrames={introFrames}>
          <OffthreadVideo
            src={mediaSrc(introVideoPath)}
            style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover'}}
          />
        </Sequence>
      ) : logoPath && introFrames > 0 ? (
        <Sequence from={0} durationInFrames={introFrames}>
          <BrandCard
            logoPath={logoPath}
            title={props.channel.name}
            subtitle="Bienvenido"
            palette={props.style.palette}
          />
        </Sequence>
      ) : null}
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
              logoPath={logoPath}
            />
          </Sequence>
        );
      })}
      {logoPath && outroFrames > 0 ? (
        outroVideoPath ? (
          <Sequence from={start} durationInFrames={outroFrames}>
            <OffthreadVideo
              src={mediaSrc(outroVideoPath)}
              style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover'}}
            />
          </Sequence>
        ) : (
        <Sequence from={start} durationInFrames={outroFrames}>
          <BrandCard
            logoPath={logoPath}
            title="Gracias por ver"
            subtitle={props.channel.name}
            palette={props.style.palette}
          />
        </Sequence>
        )
      ) : null}

      {introFrames > 0 ? (
        <>
          <Sequence from={Math.max(0, introFrames - BRIDGE_FRAMES)} durationInFrames={BRIDGE_FRAMES}>
            <BridgeFade mode="out" />
          </Sequence>
          <Sequence from={introFrames} durationInFrames={BRIDGE_FRAMES}>
            <BridgeFade mode="in" />
          </Sequence>
        </>
      ) : null}

      {outroFrames > 0 ? (
        <>
          <Sequence from={Math.max(0, outroFrom - BRIDGE_FRAMES)} durationInFrames={BRIDGE_FRAMES}>
            <BridgeFade mode="out" />
          </Sequence>
          <Sequence from={outroFrom} durationInFrames={BRIDGE_FRAMES}>
            <BridgeFade mode="in" />
          </Sequence>
        </>
      ) : null}
    </AbsoluteFill>
  );
};
