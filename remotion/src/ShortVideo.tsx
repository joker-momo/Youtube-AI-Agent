/**
 * Vertical YouTube Short renderer (1080x1920).
 *
 * Spec v6 §11 + universal-template spec §16 — this is a native Short renderer,
 * NOT a cropped long-form video. It composes:
 *
 *   ShortBackground (cover-fit media + 3-layer overlay)
 *   + Layout from registry (short_hook / short_pain / ... / short_cta)
 *
 * One scene per ``Sequence``, durations come from each scene's ``duration_sec``.
 */
import React from 'react';
import {AbsoluteFill, Sequence, Audio, useVideoConfig, staticFile} from 'remotion';
import {RenderProps, Scene} from './render-props';
import {ShortBackground} from './shorts/ShortBackground';
import {pickShortLayout} from './shorts/ShortLayouts';
import {GraphicSceneRenderer, isGraphicScene} from './graphics/GraphicSceneRenderer';

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

export const ShortVideo: React.FC<RenderProps> = (props) => {
  const {fps} = useVideoConfig();
  const scenes = props.scenes || [];
  let cursor = 0;

  const narrationSrc = resolveAudio(props.audio?.narration ?? null);

  const accentColor = props.style?.palette?.accent;

  return (
    <AbsoluteFill style={{backgroundColor: '#0b1020'}}>
      {scenes.map((scene, i) => {
        const durFrames = Math.max(1, Math.round((scene.duration_sec || 1) * fps));
        const from = cursor;
        cursor += durFrames;
        const bg = (scene as any).asset_refs?.background as string | undefined;

        // Graphic scenes (layout: "graphic_*") route to the graphic renderer,
        // which validates the payload and throws for unsupported layouts.
        // This branch MUST come before pickShortLayout, whose unknown-layout
        // fallback would otherwise silently render a graphic scene as stock.
        if (isGraphicScene(scene)) {
          return (
            <Sequence key={scene.id || i} from={from} durationInFrames={durFrames} name={scene.id || `scene-${i + 1}`}>
              <GraphicSceneRenderer scene={scene} durationInFrames={durFrames} fps={fps} />
            </Sequence>
          );
        }

        const Layout = pickShortLayout(scene.layout);
        return (
          <Sequence key={scene.id || i} from={from} durationInFrames={durFrames} name={scene.id || `scene-${i + 1}`}>
            <AbsoluteFill>
              <ShortBackground
                src={bg}
                overlay={pickOverlayKey(scene)}
                motion={scene.motion}
                durationInFrames={durFrames}
              />
              <Layout
                on_screen_text={scene.on_screen_text}
                caption={scene.caption}
                layout_payload={scene.layout_payload as any}
                accentColor={accentColor}
              />
            </AbsoluteFill>
          </Sequence>
        );
      })}

      {/* Single narration track spanning the whole composition. */}
      {narrationSrc && <Audio src={narrationSrc} />}
    </AbsoluteFill>
  );
};
