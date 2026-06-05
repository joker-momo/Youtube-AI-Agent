/**
 * Routes a graphic scene to its component after validating the payload
 * (spec v7 §12). Unsupported graphic layouts throw clearly — they must never
 * be silently rendered by the stock layout path.
 */
import React from 'react';
import type {Scene} from '../render-props';
import {parseGraphicPayload} from './graphic-payloads';
import {GraphicBackground} from './GraphicBackground';
import {GraphicChecklist} from './GraphicChecklist';
import {GraphicStepList} from './GraphicStepList';
import {GraphicPlateRatio} from './GraphicPlateRatio';
import {LabelCalloutGraphic} from './LabelCalloutGraphic';
import {ComparisonGraphic} from './ComparisonGraphic';
import {RoutineSplitGraphic} from './RoutineSplitGraphic';

export function GraphicSceneRenderer({
  scene,
  durationInFrames,
  fps,
}: {
  scene: Scene;
  durationInFrames: number;
  fps: number;
}) {
  const layout = scene.layout;

  if (typeof layout !== 'string' || !layout.startsWith('graphic_')) {
    throw new Error(`GraphicSceneRenderer received non-graphic layout: ${String(layout)}`);
  }

  const refs = (scene as {asset_refs?: {background?: string; background_video?: string}}).asset_refs;
  const backgroundSrc = refs?.background || refs?.background_video;

  // Throws (Zod / Error) for malformed payloads or unsupported layouts.
  const payload = parseGraphicPayload(layout, scene.layout_payload);

  return (
    <GraphicBackground src={backgroundSrc}>
      {layout === 'graphic_checklist' && (
        <GraphicChecklist payload={payload as never} durationInFrames={durationInFrames} fps={fps} />
      )}
      {layout === 'graphic_step_list' && (
        <GraphicStepList payload={payload as never} durationInFrames={durationInFrames} fps={fps} />
      )}
      {layout === 'graphic_plate_ratio' && (
        <GraphicPlateRatio payload={payload as never} durationInFrames={durationInFrames} fps={fps} />
      )}
      {layout === 'graphic_label_callout' && (
        <LabelCalloutGraphic payload={payload as never} durationInFrames={durationInFrames} fps={fps} />
      )}
      {layout === 'graphic_comparison' && (
        <ComparisonGraphic payload={payload as never} durationInFrames={durationInFrames} fps={fps} />
      )}
      {layout === 'graphic_routine_split' && (
        <RoutineSplitGraphic payload={payload as never} durationInFrames={durationInFrames} fps={fps} />
      )}
    </GraphicBackground>
  );
}

/** True when a scene should be routed through the graphic renderer. */
export function isGraphicScene(scene: Pick<Scene, 'layout'>): boolean {
  return typeof scene.layout === 'string' && scene.layout.startsWith('graphic_');
}
