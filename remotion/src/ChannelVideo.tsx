import React from 'react';
import {AbsoluteFill, Audio, Easing, Img, interpolate, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import {Video as MediaVideo} from '@remotion/media';
import {LayoutPayload, SceneLayout, SubtitleConfig, WordSegment} from './render-props';
import {mediaSrc, RenderProps, Scene} from './render-props';
import {ChannelVisualTimeline} from './ChannelVisualTimeline';
import {fitHeadline, fullFrame} from './styles';

const FADE_IN = 18;          // 0.6 s fade-in per scene
const FADE_OUT = 18;         // 0.6 s fade-out per scene (final scene → black)
const SCENE_XFADE = 15;      // 0.5 s cross-dissolve overlap between consecutive scenes
const BRIDGE_FRAMES = 18;    // 0.6 s bridge between intro/main/outro

// Subtle film grain (data-uri feTurbulence) — cinematic texture over the living bg.
const GRAIN =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

const defaultSubtitles: Required<SubtitleConfig> = {
  enabled: true,
  mode: 'word_highlight',
  words_per_page: 6,
  max_lines: 2,
  position: 'bottom',
  offset_sec: 0,
  font_size: 40,
  active_scale: 1.08,
  background_opacity: 0.58,
};

const clampNumber = (value: number | undefined, min: number, max: number, fallback: number) => {
  if (!Number.isFinite(value)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, value as number));
};

const resolveSubtitles = (config?: SubtitleConfig): Required<SubtitleConfig> => ({
  ...defaultSubtitles,
  ...(config ?? {}),
  words_per_page: Math.round(clampNumber(config?.words_per_page, 4, 14, defaultSubtitles.words_per_page)),
  max_lines: Math.round(clampNumber(config?.max_lines, 1, 3, defaultSubtitles.max_lines)),
  offset_sec: clampNumber(config?.offset_sec, -0.5, 0.5, defaultSubtitles.offset_sec),
  font_size: Math.round(clampNumber(config?.font_size, 28, 96, defaultSubtitles.font_size)),
  active_scale: clampNumber(config?.active_scale, 1, 1.4, defaultSubtitles.active_scale),
  background_opacity: clampNumber(config?.background_opacity, 0, 1, defaultSubtitles.background_opacity),
});

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
  <div style={{position: 'absolute', top: 36, right: 56, zIndex: 20}}>
    <Img
      src={mediaSrc(logoPath)}
      style={{
        height: 110,
        width: 'auto',
        objectFit: 'contain',
        filter: 'drop-shadow(0 8px 18px rgba(0,0,0,0.55))',
        opacity: 0.95,
      }}
    />
  </div>
);

// Long-form layouts only — short_* layouts are handled by ShortVideo / the
// shorts/ layout registry, so they are intentionally NOT in this map.
type RetentionLayout = 'hook' | 'subtitle' | 'checklist' | 'warning' | 'quote' | 'cta';

type RetentionTemplateProps = {
  scene: Scene;
  palette: RenderProps['style']['palette'];
  opacity: number;
  isLast: boolean;
  title: string;
  body: string;
  bullets: string[];
  cta: string;
  panelStyle: React.CSSProperties;
  line: WordSegment[];
  activeIndex: number | null;
  fallbackText: string;
  subtitles: Required<SubtitleConfig>;
  captionOpacity: number;
};

const SubtitleOverlay: React.FC<{
  line: WordSegment[];
  activeIndex: number | null;
  fallbackText: string;
  palette: RenderProps['style']['palette'];
  subtitles: Required<SubtitleConfig>;
  opacity: number;
}> = ({line, activeIndex, fallbackText, palette, subtitles, opacity}) => {
  const words = line.length > 0 ? line : fallbackText.split(/\s+/).filter(Boolean).slice(0, subtitles.words_per_page).map((text, i) => ({
    text,
    start: i,
    end: i + 0.5,
  }));
  if (!subtitles.enabled || words.length === 0) {
    return null;
  }

  return (
    <div
      style={{
        position: 'absolute',
        left: 120,
        right: 120,
        bottom: 40,
        zIndex: 30,
        display: 'flex',
        justifyContent: 'center',
        opacity,
      }}
    >
      <div
        style={{
          maxWidth: 1280,
          padding: '6px 10px',
          borderRadius: 12,
          background: `rgba(8, 12, 10, ${subtitles.background_opacity})`,
          boxShadow: '0 18px 52px rgba(0,0,0,0.38)',
          textAlign: 'center',
          lineHeight: 1.16,
        }}
      >
        {words.map((word, index) => {
          const isActive = activeIndex !== null && index === activeIndex;
          return (
            <span
              key={`${word.text}-${index}`}
              className="premium-subtitle-span"
              style={{
                display: 'inline-block',
                margin: '0 9px 8px',
                color: isActive ? palette.accent : '#FFFFFF',
                fontSize: isActive ? subtitles.font_size * subtitles.active_scale : subtitles.font_size,
                fontWeight: isActive ? 900 : 800,
                textShadow: '0 4px 16px rgba(0,0,0,0.72)',
                transform: isActive ? `scale(${subtitles.active_scale})` : 'scale(1)',
                transformOrigin: 'center bottom',
              }}
            >
              {word.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

const SubtitleLayoutTemplate: React.FC<RetentionTemplateProps> = ({
  line,
  activeIndex,
  fallbackText,
  palette,
  subtitles,
  captionOpacity,
}) => (
  <SubtitleOverlay
    line={line}
    activeIndex={activeIndex}
    fallbackText={fallbackText}
    palette={palette}
    subtitles={subtitles}
    opacity={captionOpacity}
  />
);

const HookOverlay: React.FC<RetentionTemplateProps> = ({title, body, panelStyle}) => (
  <div style={{...panelStyle, maxWidth: 1040}}>
    <div style={{fontSize: 76, fontWeight: 900, lineHeight: 1.02}}>{title}</div>
    <div style={{fontSize: 36, fontWeight: 800, marginTop: 20, maxWidth: 900}}>{body}</div>
  </div>
);

const ChecklistOverlay: React.FC<RetentionTemplateProps> = ({title, bullets, panelStyle, palette}) => (
  <div style={{...panelStyle, maxWidth: 900}}>
    <div style={{fontSize: 58, fontWeight: 900, marginBottom: 24}}>{title}</div>
    <div style={{display: 'flex', flexDirection: 'column', gap: 18}}>
      {bullets.map((bullet, index) => (
        <div key={`${bullet}-${index}`} style={{display: 'flex', alignItems: 'center', gap: 18, fontSize: 42, fontWeight: 800}}>
          <span style={{width: 34, height: 34, borderRadius: 17, background: palette.accent, color: '#102018', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 24, fontWeight: 900}}>
            {index + 1}
          </span>
          <span>{bullet}</span>
        </div>
      ))}
    </div>
  </div>
);

const WarningOverlay: React.FC<RetentionTemplateProps> = ({title, body, panelStyle, palette}) => (
  <div style={{...panelStyle, maxWidth: 980, bottom: 140}}>
    <div style={{fontSize: 66, fontWeight: 900, lineHeight: 1.05, color: palette.accent}}>{title}</div>
    <div style={{fontSize: 34, fontWeight: 800, marginTop: 18, maxWidth: 900}}>{body}</div>
  </div>
);

const QuoteOverlay: React.FC<RetentionTemplateProps> = ({body, panelStyle}) => (
  <div style={{...panelStyle, left: 180, right: 180, bottom: 150, textAlign: 'center'}}>
    <div style={{fontSize: 66, fontWeight: 900, lineHeight: 1.12}}>"{body}"</div>
  </div>
);

const CtaOverlay: React.FC<RetentionTemplateProps> = ({cta, body, panelStyle, palette, isLast}) => {
  if (!isLast) {
    return null;
  }
  return (
    <div style={{...panelStyle, textAlign: 'center', bottom: 128}}>
      <div style={{fontSize: 70, fontWeight: 900, lineHeight: 1.05}}>{cta}</div>
      <div style={{fontSize: 34, fontWeight: 800, marginTop: 18, color: palette.accent}}>{body}</div>
    </div>
  );
};

const layoutTemplates: Record<RetentionLayout, React.FC<RetentionTemplateProps>> = {
  hook: HookOverlay,
  subtitle: SubtitleLayoutTemplate,
  checklist: ChecklistOverlay,
  warning: WarningOverlay,
  quote: QuoteOverlay,
  cta: CtaOverlay,
};

const RetentionOverlay: React.FC<{
  scene: Scene;
  palette: RenderProps['style']['palette'];
  opacity: number;
  isLast: boolean;
  line: WordSegment[];
  activeIndex: number | null;
  fallbackText: string;
  subtitles: Required<SubtitleConfig>;
  captionOpacity: number;
}> = ({scene, palette, opacity, isLast, line, activeIndex, fallbackText, subtitles, captionOpacity}) => {
  const layout = scene.layout ?? 'subtitle';
  // Long-form scenes only ever carry the stock LayoutPayload.
  const payload = (scene.layout_payload ?? {}) as LayoutPayload;
  const title = payload.title || scene.on_screen_text || scene.caption;
  const body = payload.body || scene.caption || scene.narration;
  const bullets = (payload.bullets ?? []).slice(0, 4);
  const cta = payload.cta || '';

  const panelStyle: React.CSSProperties = {
    position: 'absolute',
    zIndex: 28,
    left: 92,
    right: 92,
    bottom: layout === 'hook' ? 132 : 118,
    opacity,
    color: '#FFFFFF',
    textShadow: '0 5px 22px rgba(0,0,0,0.72)',
  };

  // SceneLayout includes short_* values; long-form layoutTemplates only
  // handles legacy layouts, so cast & fall back to 'subtitle'.
  const Template = (layoutTemplates as any)[layout] ?? layoutTemplates.subtitle;
  return (
    <Template
      scene={scene}
      palette={palette}
      opacity={opacity}
      isLast={isLast}
      title={title}
      body={body}
      bullets={bullets}
      cta={cta}
      panelStyle={panelStyle}
      line={line}
      activeIndex={activeIndex}
      fallbackText={fallbackText}
      subtitles={subtitles}
      captionOpacity={captionOpacity}
    />
  );
};

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
      /* Fonts (Montserrat + Manrope) are preloaded via ./shorts/loadFonts,
         imported in Root.tsx — no @import here so the render pipeline never
         races against webfont CSS / falls back to Arial on first frames. */
      .premium-subtitle-span {
        font-family: 'Montserrat', 'Manrope', "Helvetica Neue", sans-serif;
        letter-spacing: 0.5px;
      }

      @keyframes grain {
        0%, 100% { transform:translate(0, 0); }
        10% { transform:translate(-1%, -2%); }
        20% { transform:translate(-3%, 1%); }
        30% { transform:translate(2%, -4%); }
        40% { transform:translate(-1%, 4%); }
        50% { transform:translate(-3%, 2%); }
        60% { transform:translate(3%, 0); }
        70% { transform:translate(0, 2%); }
        80% { transform:translate(1%, 4%); }
        90% { transform:translate(-2%, 2%); }
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
  showChannelNameOverlay?: boolean;
  logoPath?: string | null;
  subtitles: Required<SubtitleConfig>;
  isFirst?: boolean;
  isLast?: boolean;
  // When the compiled visual schedule owns the background layer, the per-scene
  // background is suppressed here so the continuous span clip shows through.
  hideBackground?: boolean;
  // Crossfade lead: frames this scene overlaps INTO the previous one. Time-based
  // animation is shifted by this so subtitles/text stay audio-aligned; only the
  // fade-in opacity uses the raw frame (to dissolve over the overlap).
  leadFrames?: number;
  // Hybrid graphic cards: fixed brand-gradient bg video path. When set, graphic
  // scenes render the card shrunk & centered over it instead of full-bleed.
  hybridCardBg?: string;
}> = ({scene, totalFrames, sceneIndex, totalScenes, palette, channelName, showChannelNameOverlay = false, logoPath, subtitles, isFirst = false, isLast = false, hideBackground = false, leadFrames = 0, hybridCardBg}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  // Audio-aligned scene time: the scene's Sequence starts `leadFrames` early so it
  // can dissolve over the previous scene, but narration/subtitles live on the
  // continuous audio timeline — so all time-based animation uses localFrame.
  const localFrame = frame - leadFrames;
  const progress = Math.max(0, localFrame) / Math.max(totalFrames - 1, 1);

  // Animated caption: find active word segment by local scene time.
  // word_segments are rebased to scene-local time (start=0), so compare
  // against frame/fps directly — NOT audio_offset_sec + frame/fps.
  const localTimeSec = localFrame / fps + subtitles.offset_sec;
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
  const wordsPerPage = subtitles.words_per_page;
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
  const activeIndexInPage = activeWordIdx !== -1 && activePageIdx === Math.floor(activeWordIdx / wordsPerPage)
    ? activeWordIdx - activePageIdx * wordsPerPage
    : null;
  // Cross-dissolve IN: an incoming scene (leadFrames>0) fades up over its overlap
  // with the previous scene; the first scene fades from black over FADE_IN. Uses the
  // raw `frame` (0 = first rendered frame) so the dissolve covers the overlap window.
  const opacity       = interpolate(frame, [0, leadFrames > 0 ? leadFrames : FADE_IN], [0, 1], {extrapolateRight: 'clamp'});
  const headlineY     = interpolate(localFrame, [4, FADE_IN + 8], [20, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const headlineAlpha = interpolate(localFrame, [4, FADE_IN + 8], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const captionAlpha  = interpolate(localFrame, [FADE_IN, FADE_IN + 14], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  // Scene-to-scene fade: fade-out black overlay appears in final FADE_OUT frames.
  // Suppressed when the compiled timeline owns the background, so a continuous
  // span clip is never interrupted by a black flash at an internal scene boundary.
  // No internal dip-to-black any more — scenes cross-dissolve (the incoming scene
  // fades in over the previous one's tail). Only the FINAL scene fades to black.
  const fadeOutAlpha = (isLast && !hideBackground)
    ? interpolate(localFrame, [totalFrames - FADE_OUT, totalFrames - 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})
    : 0;

  // Hybrid graphic scene: card floats centered on the brand-gradient bg, so the
  // strong bottom-gradient + left vignette (built for subtitle contrast) are
  // suppressed — they would muddy the card's lower edge.
  const isHybridGraphic = Boolean(hybridCardBg && scene.graphic?.image_ref);
  // Graphic card eases in / out ~0.5s over the (persistent) brand-gradient bg,
  // so it appears and disappears gradually instead of popping.
  const CARD_FADE = Math.round(fps * 0.5);
  const cardOpacity = isHybridGraphic
    ? interpolate(
        localFrame,
        [0, CARD_FADE, totalFrames - CARD_FADE, totalFrames],
        [0, 1, 1, 0],
        {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
      )
    : 1;
  // Card entrance: gentle rise (32px) + soft zoom (0.95->1) over ~0.6s, ease-out.
  // On the image ONLY (not the bg video). Calm/premium for the 45+ wellness brand.
  const cardEntranceFrames = Math.round(fps * 0.6);
  const cardEntrance = {easing: Easing.out(Easing.cubic), extrapolateRight: 'clamp' as const};
  const cardRise = isHybridGraphic ? interpolate(localFrame, [0, cardEntranceFrames], [32, 0], cardEntrance) : 0;
  const cardScale = isHybridGraphic ? interpolate(localFrame, [0, cardEntranceFrames], [0.95, 1], cardEntrance) : 1;
  // Living bg for graphic scenes: the scene's OWN matched b-roll (already fetched from
  // its visual_prompt), else the brand-gradient fallback. Blurred + darkened behind the
  // card so it feels alive without a Ken Burns move (calm/premium for 45+).
  const livingBgSrc =
    scene.asset_refs?.background && scene.asset_refs.background.endsWith('.mp4')
      ? scene.asset_refs.background
      : hybridCardBg;
  const layoutVariant = sceneIndex % 3;
  const headlineLeft = layoutVariant === 2 ? undefined : 56;
  const headlineRight = layoutVariant === 2 ? 56 : undefined;
  const headlineCenter = layoutVariant === 1;
  const headlineBottom = layoutVariant === 1 ? 200 : 190;

  return (
    <AbsoluteFill style={{...fullFrame, opacity}}>
      <FontLoader />

      {/* Full-bleed background — graphic ChatGPT image (graphic scenes), else
          video clip or photo. Suppressed when the compiled timeline owns the layer. */}
      {hideBackground ? null : scene.graphic?.image_ref ? (
        hybridCardBg ? (
          // Hybrid: fixed brand-gradient bg video full-bleed + the generated card
          // shrunk, centered, rounded with a soft drop shadow (bg motion shows
          // around the edges). Kills the "static slideshow" feel of graphic scenes.
          <>
            {/* Living bg: per-scene b-roll, fixed slight overscale (blur edges off-frame),
                natural color, NO Ken Burns — the footage itself plays. */}
            <AbsoluteFill style={{transform: 'scale(1.08)', filter: 'blur(4px) brightness(0.62) saturate(1.14)'}}>
              <MediaVideo
                src={mediaSrc(livingBgSrc ?? hybridCardBg)}
                muted
                style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover'}}
              />
            </AbsoluteFill>
            <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 40%, rgba(0,0,0,0) 46%, rgba(0,0,0,0.48) 100%)'}} />
            <AbsoluteFill style={{backgroundImage: GRAIN, backgroundSize: '180px 180px', opacity: 0.06, mixBlendMode: 'overlay'}} />
            {/* Card TRULY centered in the frame; sized (65%) so a 2-line subtitle clears it.
                Entrance-only (rise+zoom+fade), then locked. */}
            <AbsoluteFill style={{display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: cardOpacity}}>
              <Img
                src={mediaSrc(scene.graphic.image_ref)}
                style={{width: '76%', height: 'auto', objectFit: 'contain', borderRadius: 28, boxShadow: '0 18px 46px rgba(0,0,0,0.4)', transform: `translateY(${cardRise}px) scale(${cardScale})`}}
              />
            </AbsoluteFill>
          </>
        ) : (
          <Img
            src={mediaSrc(scene.graphic.image_ref)}
            style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover'}}
          />
        )
      ) : scene.asset_refs.background.endsWith('.mp4') ? (
        // WebCodecs <Media> Video (MediaVideo) — native HW decode, frame-accurate
        // for server-side rendering (benchmarked vs the old extract path: SSIM 0.987).
        // loop: short Pexels clips (3-30s) repeat to fill the full scene duration.
        // No pan/zoom transform — the video itself has natural motion.
        <MediaVideo
          src={mediaSrc(scene.asset_refs.background)}
          muted
          style={{
            position: 'absolute', width: '100%', height: '100%',
            objectFit: 'cover',
            filter: 'contrast(1.05) saturate(1.03) sepia(0.04) brightness(0.96)',
          }}
        />
      ) : (
        <Img
          src={mediaSrc(scene.asset_refs.background)}
          style={{
            position: 'absolute', width: '100%', height: '100%',
            objectFit: 'cover',
            transform: motionTransform(scene.motion, progress),
            filter: 'contrast(1.05) saturate(1.03) sepia(0.04) brightness(0.96)',
          }}
        />
      )}

      {/* Bottom gradient — only bottom 40% darkens, top stays natural.
          Skipped for hybrid-card scenes (card sits on its own bg). */}
      {isHybridGraphic ? null : (
        <div style={{
          position: 'absolute', inset: 0,
          background: 'linear-gradient(to top, rgba(10,16,13,0.88) 0%, rgba(10,16,13,0.5) 30%, transparent 58%)',
        }} />
      )}

      {/* Left vignette — subtle, helps text contrast.
          Skipped for hybrid-card scenes (card sits on its own bg). */}
      {isHybridGraphic ? null : (
        <div style={{
          position: 'absolute', inset: 0,
          background: 'linear-gradient(to right, rgba(10,16,13,0.42) 0%, transparent 52%)',
        }} />
      )}

      {/* Channel name — small, top-left. Hidden by default; opt in via
          branding.show_channel_name_overlay so the opening frame stays clean. */}
      {showChannelNameOverlay ? (
        <div style={{
          position: 'absolute', top: 36, left: 64,
          fontSize: 24, fontWeight: 700, letterSpacing: 2.2, textTransform: 'uppercase',
          color: '#F2F4EF',
          textShadow: '0 2px 8px rgba(0,0,0,0.6)',
          opacity: headlineAlpha * 0.75,
        }}>
          {channelName}
        </div>
      ) : null}

      {/* Logo moved OUT of the per-scene layer to a single persistent top-level
          overlay in ChannelVideo — per-scene it remounted on every cut and blinked,
          which made the video feel like a slideshow. */}

      {/* Cinematic Color Grading Overlay (warm sepia + teal-green shadow) */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(135deg, rgba(217, 140, 95, 0.08) 0%, rgba(47, 107, 87, 0.05) 100%)',
        mixBlendMode: 'overlay',
        pointerEvents: 'none', zIndex: 14
      }} />

      {/* Cinematic Procedural Film Grain Overlay */}
      <div style={{
        position: 'absolute',
        inset: '-50%',
        width: '200%',
        height: '200%',
        backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E")`,
        opacity: 0.045,
        mixBlendMode: 'overlay',
        pointerEvents: 'none',
        animation: 'grain 0.4s steps(6) infinite',
        zIndex: 15,
      }} />

      {/* Graphic scenes: the ChatGPT image already has the text baked in (high
          contrast), so DON'T overlay Remotion headline text on top — it would
          duplicate / clash. But DO show the narration subtitle band on top of every
          graphic scene EXCEPT the CTA (kept clean). Non-graphic (B-roll) scenes
          render the normal headline/subtitle overlay. */}
      {scene.graphic?.image_ref ? (
        scene.layout !== 'cta' ? (
          <SubtitleOverlay
            line={displayLine}
            activeIndex={activeIndexInPage}
            fallbackText={scene.on_screen_text || scene.caption || scene.narration}
            palette={palette}
            subtitles={subtitles}
            opacity={captionAlpha}
          />
        ) : null
      ) : (
        <RetentionOverlay
          scene={scene}
          palette={palette}
          opacity={headlineAlpha}
          isLast={isLast}
          line={displayLine}
          activeIndex={activeIndexInPage}
          fallbackText={scene.on_screen_text || scene.caption || scene.narration}
          subtitles={subtitles}
          captionOpacity={captionAlpha}
        />
      )}

      {/* Scene-to-scene fade-out: solid black overlay fades in over final FADE_OUT frames.
          Must be the LAST child so it composites on top of everything. */}
      {fadeOutAlpha > 0 ? (
        <AbsoluteFill style={{background: '#0C100D', opacity: fadeOutAlpha, zIndex: 100, pointerEvents: 'none'}} />
      ) : null}

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
  const subtitles = resolveSubtitles(props.render.subtitles);
  let start = introFrames;
  const totalSceneFrames = props.scenes.reduce((acc, s) => acc + Math.round(s.duration_sec * fps), 0);
  const outroFrom = start + totalSceneFrames;
  // When a compiled asset schedule is present, the visual timeline owns the
  // background layer (one continuous clip per span). Absent → legacy per-scene
  // background, frame-identical to before.
  const visualSchedule = props.visual_schedule ?? null;
  return (
    <AbsoluteFill style={{backgroundColor: '#0C100D'}}>
      {props.audio.narration ? (
        <Sequence from={introFrames}>
          <Audio src={mediaSrc(props.audio.narration)} />
        </Sequence>
      ) : null}
      {introVideoPath && introFrames > 0 ? (
        <Sequence from={0} durationInFrames={introFrames}>
          <MediaVideo
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
      {/* Background layer: continuous span clips. Rendered before the scene
          sequences so it sits behind their gradients/text. Scene-layer is shifted
          by introFrames, matching where the per-scene sequences start. */}
      {visualSchedule ? (
        <Sequence from={introFrames}>
          <ChannelVisualTimeline schedule={visualSchedule} />
        </Sequence>
      ) : null}
      {props.scenes.map((scene, i) => {
        const totalFrames = Math.round(scene.duration_sec * fps);
        // Overlap each scene (after the first) into the previous one's tail so they
        // cross-dissolve instead of dipping to black. Disabled when the compiled
        // schedule owns the background (already a continuous clip — no per-scene cut).
        const lead = (i === 0 || visualSchedule != null) ? 0 : SCENE_XFADE;
        const from = start - lead;
        start += totalFrames;
        return (
          <Sequence key={scene.id} from={from} durationInFrames={totalFrames + lead}>
            <SceneView
              scene={scene}
              totalFrames={totalFrames}
              leadFrames={lead}
              sceneIndex={i}
              totalScenes={props.scenes.length}
              palette={props.style.palette}
              channelName={props.channel.name}
              showChannelNameOverlay={props.branding?.show_channel_name_overlay ?? false}
              logoPath={logoPath}
              subtitles={subtitles}
              isFirst={i === 0}
              isLast={i === props.scenes.length - 1}
              hideBackground={visualSchedule != null}
              hybridCardBg={props.branding?.hybrid_card_bg}
            />
          </Sequence>
        );
      })}
      {/* Persistent logo — ONE continuous top-right overlay spanning every scene,
          mounted once (introFrames → end of scenes) so it never blinks on a scene
          cut. Sits above the scene layer (top-right). */}
      {logoPath ? (
        <Sequence from={introFrames} durationInFrames={totalSceneFrames}>
          <LogoWatermark logoPath={logoPath} />
        </Sequence>
      ) : null}
      {logoPath && outroFrames > 0 ? (
        outroVideoPath ? (
          <Sequence from={start} durationInFrames={outroFrames}>
            <MediaVideo
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
