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


const FontLoader: React.FC = () => (
  <style>
    {`
      @import url('https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,600;0,800;0,900;1,600;1,800;1,900&family=Manrope:wght@600;800;900&display=swap');
      
      .premium-subtitle-span {
        font-family: 'Montserrat', 'Manrope', "Helvetica Neue", sans-serif;
        letter-spacing: 0.5px;
      }
    `}
  </style>
);

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

  const words = segments;
  const hasWords = words.length > 0;

  // Group words into logical pages of 10 words for high legibility (max 2 lines of 5 words)
  const wordsPerPage = 10;
  const pages: WordSegment[][] = [];
  for (let i = 0; i < words.length; i += wordsPerPage) {
    pages.push(words.slice(i, i + wordsPerPage));
  }

  // Find currently active word, or fall back strictly to the last spoken word during gaps to prevent snapping back
  const activeWordIdx = words.findIndex(w => localTimeSec >= w.start && localTimeSec < w.end);
  let targetWordIdx = 0;
  if (activeWordIdx !== -1) {
    targetWordIdx = activeWordIdx;
  } else {
    // Find the latest word that ended before current local time
    let lastEndedIdx = -1;
    for (let i = 0; i < words.length; i++) {
      if (localTimeSec >= words[i].end) {
        lastEndedIdx = i;
      }
    }
    targetWordIdx = lastEndedIdx !== -1 ? lastEndedIdx : 0;
  }

  const activePageIdx = Math.floor(targetWordIdx / wordsPerPage);
  const displayLine = pages[activePageIdx] || [];

  const opacity       = interpolate(frame, [0, FADE_IN], [0, 1], {extrapolateRight: 'clamp'});
  const headlineY     = interpolate(frame, [4, FADE_IN + 8], [20, 0], {extrapolateRight: 'clamp'});
  const headlineAlpha = interpolate(frame, [4, FADE_IN + 8], [0, 1], {extrapolateRight: 'clamp'});
  const captionAlpha  = interpolate(frame, [FADE_IN, FADE_IN + 14], [0, 1], {extrapolateRight: 'clamp'});

  const layoutVariant = sceneIndex % 3;
  const headlineLeft = layoutVariant === 2 ? undefined : 56;
  const headlineRight = layoutVariant === 2 ? 56 : undefined;
  const headlineCenter = layoutVariant === 1;
  const headlineBottom = layoutVariant === 1 ? 200 : 190;

  return (
    <AbsoluteFill style={{...fullFrame, opacity}}>
      <FontLoader />

      {/* Full-bleed background — video clip or photo */}
      {scene.asset_refs.background.endsWith('.mp4') ? (
        // OffthreadVideo is preferred over Video for server-side rendering (faster, frame-accurate).
        // loop: short Pexels clips (3-30s) repeat to fill the full scene duration.
        // No pan/zoom transform — the video itself has natural motion.
        <OffthreadVideo
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
