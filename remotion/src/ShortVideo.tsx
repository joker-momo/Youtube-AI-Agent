/**
 * Vertical YouTube Short renderer (1080x1920).
 *
 * Spec v6 §11 + universal-template spec §16 — this is a native Short renderer,
 * NOT a cropped long-form video. It composes:
 *
 *   ShortBackground (cover-fit media + 3-layer overlay)
 *   + Layout from registry (short_hook / short_pain / ... / short_cta)
 *   + optional ShortProgress top bar
 *
 * One scene per ``Sequence``, durations come from each scene's ``duration_sec``.
 */
import React from 'react';
import {AbsoluteFill, Sequence, Audio, useVideoConfig, staticFile} from 'remotion';
import {RenderProps, Scene} from './render-props';
import {ShortBackground} from './shorts/ShortBackground';
import {pickShortLayout} from './shorts/ShortLayouts';
import {ShortProgress} from './shorts/ShortProgress';

function pickOverlayKey(scene: Scene): 'default' | 'dark' | 'bright' {
  const text = `${scene.visual_prompt || ''} ${scene.narration || ''}`.toLowerCase();
  if (/(night|sleep|dark|bed|noche|sueño|cama|insomnio|moonlight)/.test(text)) return 'dark';
  if (/(bright|sun|daylight|window|kitchen|sol|cocina)/.test(text)) return 'bright';
  return 'default';
}

function resolveAudio(p: string | null): string | undefined {
  if (!p) return undefined;
  if (/^(https?|data):/.test(p)) return p;
  try {
    return staticFile(p);
  } catch {
    return p;
  }
}

const ShortLogo: React.FC<{src?: string}> = ({src}) => {
  if (!src) return null;
  return (
    <img
      src={src}
      style={{
        position: 'absolute',
        top: 62,
        right: 62,
        width: 100,
        height: 'auto',
        opacity: 0.88,
        filter: 'drop-shadow(0 4px 12px rgba(0,0,0,0.6))',
      }}
    />
  );
};

const FontLoader: React.FC = () => (
  <style>
    {`
      @import url('https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,400;0,700;0,800;0,900;1,400;1,700;1,800;1,900&display=swap');
    `}
  </style>
);

export const ShortVideo: React.FC<RenderProps> = (props) => {
  const {fps} = useVideoConfig();
  const scenes = props.scenes || [];
  let cursor = 0;

  const narrationSrc = resolveAudio(props.audio?.narration ?? null);
  // Channel/brand logo (optional). We look for a conventional channel logo
  // file; falls back to nothing when absent. Path is staticFile-resolved.
  const logoSrc = ((): string | undefined => {
    try {
      return staticFile('channel_logo.png');
    } catch {
      return undefined;
    }
  })();

  const accentColor = props.style?.palette?.accent;

  return (
    <AbsoluteFill style={{backgroundColor: '#0b1020'}}>
      <FontLoader />
      {scenes.map((scene, i) => {
        const durFrames = Math.max(1, Math.round((scene.duration_sec || 1) * fps));
        const from = cursor;
        cursor += durFrames;
        const Layout = pickShortLayout(scene.layout);
        const bg = (scene as any).asset_refs?.background as string | undefined;
        return (
          <Sequence key={scene.id || i} from={from} durationInFrames={durFrames} name={scene.id || `scene-${i + 1}`}>
            <AbsoluteFill>
              <ShortBackground src={bg} overlay={pickOverlayKey(scene)} />
              <Layout
                on_screen_text={scene.on_screen_text}
                caption={scene.caption}
                layout_payload={scene.layout_payload}
                accentColor={accentColor}
              />
              <ShortLogo src={logoSrc} />
            </AbsoluteFill>
          </Sequence>
        );
      })}

      {/* Single narration track spanning the whole composition. */}
      {narrationSrc && <Audio src={narrationSrc} />}

      <ShortProgress />
    </AbsoluteFill>
  );
};
