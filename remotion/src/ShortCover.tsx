/**
 * Vertical Short cover (1080x1920) — spec v6 §12 + universal template §11.
 *
 * Primary cover renderer. Reuses the first scene's background and renders the
 * hook text in the spec's hook zone (y=460-760). No CTA. Logo in safe top-right.
 */
import React from 'react';
import {AbsoluteFill, staticFile} from 'remotion';
import {RenderProps} from './render-props';
import {ShortBackground} from './shorts/ShortBackground';
import {ShortHookLayout} from './shorts/ShortLayouts';

function pickOverlay(visual: string, narration: string): 'default' | 'dark' | 'bright' {
  const text = `${visual} ${narration}`.toLowerCase();
  if (/(night|sleep|dark|noche|sueño|cama)/.test(text)) return 'dark';
  if (/(bright|sun|daylight|sol|cocina)/.test(text)) return 'bright';
  return 'default';
}

export const ShortCover: React.FC<RenderProps> = (props) => {
  const first = (props.scenes && props.scenes[0]) || ({} as any);
  const layoutPayload = first.layout_payload || {};
  const cover_text = (layoutPayload as any).cover_text;
  const title =
    cover_text ||
    layoutPayload.title ||
    first.on_screen_text ||
    (props as any)?.seo?.title ||
    '';

  const bg = (first as any).asset_refs?.background as string | undefined;
  const overlay = pickOverlay(first.visual_prompt || '', first.narration || '');

  const logoSrc = ((): string | undefined => {
    try {
      return staticFile('channel_logo.png');
    } catch {
      return undefined;
    }
  })();

  return (
    <AbsoluteFill style={{backgroundColor: '#0b1020'}}>
      <ShortBackground src={bg} overlay={overlay} />
      <ShortHookLayout
        on_screen_text={String(title).toUpperCase()}
        caption={undefined}
        layout_payload={{
          title: String(title).toUpperCase(),
          subtitle: (layoutPayload as any).subtitle,
        }}
      />
      {logoSrc && (
        <img
          src={logoSrc}
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
      )}
    </AbsoluteFill>
  );
};
