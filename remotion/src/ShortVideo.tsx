/**
 * Vertical YouTube Short renderer (1080x1920).
 *
 * Spec v6 §11 + universal-template spec §16 — this is a native Short renderer,
 * NOT a cropped long-form video. It composes:
 *
 *   ShortBackground (cover-fit media + 3-layer overlay)
 *   + Layout from registry (short_hook / short_pain / ... / short_cta)
 *
 * Legacy renders keep one background per scene. When compiled
 * ``visual_schedule`` is present, VisualTimeline owns background media spans and
 * scene Sequences only place text/graphic overlays on compiled boundaries.
 */
import React from 'react';
import {AbsoluteFill, Sequence, Audio, useVideoConfig, staticFile} from 'remotion';
import {CompiledSceneBoundary, RenderProps, Scene} from './render-props';
import {ShortBackground} from './shorts/ShortBackground';
import {ShortReadabilityOverlay} from './shorts/ShortReadabilityOverlay';
import {VisualTimeline} from './shorts/VisualTimeline';
import {pickShortLayout} from './shorts/ShortLayouts';

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

function assertNoLegacyGraphicScenes(scenes: Scene[]): void {
  const leaked = scenes
    .filter((scene) => String(scene.layout || '').startsWith('graphic_'))
    .map((scene) => `${scene.id}:${scene.layout}`);
  if (leaked.length > 0) {
    throw new Error(
      `Graphic scenes must be converted to a ChatGPT image-backed layout before render: ${leaked.join(', ')}`,
    );
  }
}

function isGeneratedGraphicScene(scene: Scene): boolean {
  const sourceLayout = String(scene.generated_image_source_layout || '');
  return (
    sourceLayout.startsWith('graphic_') ||
    (scene.visual_type === 'graphic' && scene.background_mode === 'generated_image')
  );
}

export const ShortVideo: React.FC<RenderProps> = (props) => {
  const {fps} = useVideoConfig();
  const scenes = props.scenes || [];
  assertNoLegacyGraphicScenes(scenes);
  const schedule = props.visual_schedule;
  let cursor = 0;

  const narrationSrc = resolveAudio(props.audio?.narration ?? null);

  const accentColor = props.style?.palette?.accent;

  if (schedule) {
    if (schedule.fps !== fps) {
      throw new Error(`Visual schedule fps ${schedule.fps} does not match composition fps ${fps}`);
    }
    const sceneBoundaries = new Map<string, CompiledSceneBoundary>(
      schedule.scene_boundaries.map((boundary) => [boundary.scene_id, boundary]),
    );

    return (
      <AbsoluteFill style={{backgroundColor: '#0b1020'}}>
        <VisualTimeline schedule={schedule} />
        {scenes.map((scene, i) => {
          const sceneId = scene.id || `scene-${i + 1}`;
          const boundary = sceneBoundaries.get(sceneId);
          if (!boundary) {
            throw new Error(`Missing visual schedule boundary for scene ${sceneId}`);
          }

          const Layout = pickShortLayout(scene.layout);
          const isGeneratedGraphic = isGeneratedGraphicScene(scene);
          return (
            <Sequence
              key={sceneId}
              from={boundary.from_frame}
              durationInFrames={boundary.duration_in_frames}
              name={sceneId}
            >
              <AbsoluteFill>
                {!isGeneratedGraphic && <ShortReadabilityOverlay overlay={pickOverlayKey(scene)} />}
                {!isGeneratedGraphic && (
                  <Layout
                    on_screen_text={scene.on_screen_text}
                    caption={scene.caption}
                    layout_payload={scene.layout_payload as any}
                    accentColor={accentColor}
                  />
                )}
              </AbsoluteFill>
            </Sequence>
          );
        })}

        {/* Single narration track spanning the whole composition. */}
        {narrationSrc && <Audio src={narrationSrc} />}
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{backgroundColor: '#0b1020'}}>
      {scenes.map((scene, i) => {
        const durFrames = Math.max(1, Math.round((scene.duration_sec || 1) * fps));
        const from = cursor;
        cursor += durFrames;
        const bg = (scene as any).asset_refs?.background as string | undefined;

        const Layout = pickShortLayout(scene.layout);
        const isGeneratedGraphic = isGeneratedGraphicScene(scene);
        return (
          <Sequence key={scene.id || i} from={from} durationInFrames={durFrames} name={scene.id || `scene-${i + 1}`}>
            <AbsoluteFill>
              <ShortBackground
                src={bg}
                overlay={pickOverlayKey(scene)}
                motion={scene.motion}
                cropPlan={scene.crop_plan}
                durationInFrames={durFrames}
              />
              {!isGeneratedGraphic && (
                <Layout
                  on_screen_text={scene.on_screen_text}
                  caption={scene.caption}
                  layout_payload={scene.layout_payload as any}
                  accentColor={accentColor}
                />
              )}
            </AbsoluteFill>
          </Sequence>
        );
      })}

      {/* Single narration track spanning the whole composition. */}
      {narrationSrc && <Audio src={narrationSrc} />}
    </AbsoluteFill>
  );
};
